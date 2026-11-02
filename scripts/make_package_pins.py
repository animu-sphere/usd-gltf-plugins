# SPDX-License-Identifier: Apache-2.0
"""Writes the pin table for the packages a release pushes to GHCR.

A consumer needs two digests per target: the archive digest, which it names as the `artifact`, and
the OCI manifest digest to pull it from, which changes on every republish. This writes both, from
the rows that were actually pushed, as package-pins.json and a Markdown section for the notes.

Rows are tab-separated: `kind name version target archive_digest [oci_digest]`. A dry run passes
rows without an OCI digest and no --published, and its table names no source.
"""

import argparse
import json
import pathlib
import sys

BUNDLE = "usdGltfFileFormat"


def fail(message):
    sys.exit(f"error: {message}")


def read_rows(path, version, repository, published):
    targets = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        fields = line.split("\t")
        if len(fields) not in (5, 6):
            fail(f"{path}:{number}: expected 5 or 6 fields, got {len(fields)}")
        kind, name, row_version, target, artifact = fields[:5]
        oci = fields[5] if len(fields) == 6 else ""
        if (kind, name) != ("bundle", BUNDLE):
            fail(f"{path}:{number}: unexpected package {kind} {name}")
        if row_version != version:
            fail(f"{path}:{number}: {name} is version {row_version}, the release is {version}")
        if published and not oci:
            fail(f"{path}:{number}: {name} {target} was published but has no OCI digest")
        if target in targets:
            fail(f"{path}:{number}: {name} {target} appears twice")
        targets[target] = {"artifact": artifact}
        if oci:
            targets[target]["source"] = f"oci://{repository}@{oci}"
            targets[target]["tag"] = f"oci://{repository}:{name}-{version}-{target}"
    return targets


def render(targets, version, published):
    major, minor = (int(part) for part in version.split(".")[:2])
    lines = ["## Packages on GHCR", ""]
    if not published:
        lines += ["> Dry run: nothing was pushed, so no source is named. The digests are this build's.", ""]
    lines += ["Pull by the OCI digest and name the archive digest as the `artifact`.", "",
              "```yaml", "requires:", "  bundles:", f"    - id: {BUNDLE}",
              f'      version: ">={major}.{minor},<{major}.{minor + 1}"', "      artifact:", "        targets:"]
    for target, entry in sorted(targets.items()):
        lines += [f"          {target}:", f"            digest: {entry['artifact']}"]
        if "source" in entry:
            lines.append(f"            source: {entry['source']}")
    lines += ["```", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--rows", type=pathlib.Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--targets", type=int, required=True, help="how many targets must be present")
    parser.add_argument("--out", type=pathlib.Path, required=True)
    parser.add_argument("--published", action="store_true")
    arguments = parser.parse_args()

    targets = read_rows(arguments.rows, arguments.version, arguments.repository, arguments.published)
    if len(targets) != arguments.targets:
        fail(f"the pin table has {len(targets)} target(s) {sorted(targets)}, expected {arguments.targets}")
    arguments.out.mkdir(parents=True, exist_ok=True)
    document = {"version": arguments.version, "published": arguments.published,
                "repository": arguments.repository, "bundles": {BUNDLE: targets}}
    (arguments.out / "package-pins.json").write_text(json.dumps(document, indent=2, sort_keys=True) + "\n",
                                                     encoding="utf-8", newline="\n")
    (arguments.out / "package-pins.md").write_text(render(targets, arguments.version, arguments.published),
                                                   encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
