"""Checks the documentation rules in docs/README.md that a machine can verify.

This validates the links, states and evidence that the documents declare. It
does not decide from the code whether a capability is supported.
"""

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
MATRIX = ROOT / "docs" / "reference" / "CAPABILITY_MATRIX.md"
ROADMAP = ROOT / "docs" / "roadmap" / "README.md"
CHANGELOG = ROOT / "CHANGELOG.md"
MATRIX_HEADER = ["Capability", "State", "Supported subset and limitations", "Evidence"]
STATE = re.compile(r"Unsupported|Experimental|Verified \(.+\)")
LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)")
HEADING = re.compile(r"(#{1,6})\s+(.*\S)\s*$")
STATUS_LINE = re.compile(r"\**Status\b[^|]*:")
RELEASE_HEADING = re.compile(r"v\d+\.\d+")


def documents():
    return [*sorted(ROOT.glob("*.md")), ROOT / "third_party" / "README.md", *sorted((ROOT / "docs").rglob("*.md"))]


def prose_lines(path):
    """Yields (line number, line) outside fenced code blocks."""
    fenced = False
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.lstrip().startswith("```"):
            fenced = not fenced
        elif not fenced:
            yield number, line


def anchors(path):
    """Returns the heading anchors GitHub generates for a Markdown file."""
    result = set()
    for _, line in prose_lines(path):
        match = HEADING.match(line)
        if match:
            text = re.sub(r"`|\*", "", match[2]).lower()
            result.add(re.sub(r"[^\w\- ]", "", text).replace(" ", "-"))
    return result


def check_links(path):
    for number, line in prose_lines(path):
        for target in LINK.findall(line):
            if re.match(r"[a-z][a-z0-9+.-]*:", target):
                continue
            file_part, _, anchor = target.partition("#")
            resolved = (path.parent / file_part).resolve() if file_part else path
            if not resolved.exists():
                yield f"{path.relative_to(ROOT)}:{number}: broken link {target}"
            elif anchor and resolved.suffix == ".md" and anchor not in anchors(resolved):
                yield f"{path.relative_to(ROOT)}:{number}: missing anchor {target}"


def check_single_owner(path):
    for number, line in prose_lines(path):
        location = f"{path.relative_to(ROOT)}:{number}"
        if STATUS_LINE.match(line):
            yield f"{location}: status belongs in the capability matrix, not a document header"
        if path != MATRIX and "/actions/runs/" in line:
            yield f"{location}: CI run evidence belongs in the capability matrix"
        heading = HEADING.match(line)
        if path not in (ROADMAP, CHANGELOG) and heading and RELEASE_HEADING.match(heading[2]):
            yield f"{location}: release scope belongs in the roadmap"


def table_cells(line):
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def check_evidence_token(token, location):
    file_part, _, test_name = token.partition("::")
    path = ROOT / file_part
    if not path.is_file():
        yield f"{location}: evidence file does not exist: {file_part}"
    elif test_name and test_name not in path.read_text(encoding="utf-8"):
        yield f"{location}: evidence test does not exist: {token}"


def check_matrix():
    rows = 0
    in_table = False
    for number, line in prose_lines(MATRIX):
        if not line.startswith("|"):
            in_table = False
            continue
        cells = table_cells(line)
        if cells == MATRIX_HEADER:
            in_table = True
            continue
        if not in_table or set(cells[0]) <= {"-", ":"}:
            continue
        location = f"{MATRIX.relative_to(ROOT)}:{number}"
        if len(cells) != len(MATRIX_HEADER):
            yield f"{location}: expected {len(MATRIX_HEADER)} columns"
            continue
        rows += 1
        state, evidence = cells[1], cells[3]
        if not STATE.fullmatch(state):
            yield f"{location}: invalid state '{state}'"
        tokens = re.findall(r"`([^`]+)`", evidence)
        if state != "Unsupported" and not tokens and not LINK.search(evidence):
            yield f"{location}: state '{state}' requires evidence"
        for token in tokens:
            yield from check_evidence_token(token, location)
    if not rows:
        yield f"{MATRIX.relative_to(ROOT)}: no capability rows found"


def check_roadmap_headings():
    seen = set()
    for number, line in prose_lines(ROADMAP):
        heading = HEADING.match(line)
        if heading and RELEASE_HEADING.match(heading[2]):
            version = heading[2].split()[0]
            if version in seen:
                yield f"{ROADMAP.relative_to(ROOT)}:{number}: duplicate release heading {version}"
            seen.add(version)


def main():
    errors = []
    for path in documents():
        errors += check_links(path)
        errors += check_single_owner(path)
    errors += check_matrix()
    errors += check_roadmap_headings()
    print("\n".join(errors) if errors else "Documentation checks passed.")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
