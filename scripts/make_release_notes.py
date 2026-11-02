# SPDX-License-Identifier: Apache-2.0
"""Checks a release tag against the repository, and renders the release notes.

With --tag, the tag must be vX.Y.Z or vX.Y.Z-suffix, X.Y.Z must be the version of the project
and of the bundle, and the changelog must have the section the tag needs: [X.Y.Z] with a date
for a release, or a nonempty [Unreleased] for a pre-release. Without --tag this is a dry run
of the project's version, which takes [X.Y.Z] once the changelog has one, and [Unreleased] before.

Package files are named by X.Y.Z, because the suffix belongs to the tag and not to the bundle.
"""

import argparse
import pathlib
import re
import sys
import tomllib

ROOT = pathlib.Path(__file__).resolve().parent.parent
TAG = re.compile(r"v(\d+\.\d+\.\d+)(-[0-9A-Za-z][0-9A-Za-z.-]*)?")
MANIFEST_VERSION = re.compile(r"^plugin:\s*$.*?^\s+version:\s*(\S+)", re.M | re.S)
REPOSITORY = "https://github.com/animu-sphere/usd-gltf-plugins"

NOTES = """# usd-gltf-plugins {tag}

Opens glTF 2.0 (`.gltf` and `.glb`) files as ordinary OpenUSD scenes through `Usd.Stage.Open`.

- **Supported subset and known constraints:** [capability matrix]({blob}/docs/reference/CAPABILITY_MATRIX.md)
- **Test environments:** [tested environments]({blob}/docs/reference/CAPABILITY_MATRIX.md#tested-environments)
- **Display results:** [display checks]({blob}/docs/reference/CAPABILITY_MATRIX.md#display-checks)

{changes}

## Files

| File | Contents |
| --- | --- |
| `usdGltfFileFormat-{version}-<target>.tar.zst` | The plugin for one OpenUSD runtime target |
| `usdGltfFileFormat-{version}-<target>.manifest.json` | Its OpenStrata manifest |
| `usdGltfFileFormat-{version}-<target>.sbom.spdx.json` | Its software bill of materials |
| `usd-gltf-plugins-{source}-src.tar.gz` | The source at this tag |
| `package-pins.json` | The digests to pin each package on GHCR |
| `SHA256SUMS` | Checksums of the files above |

## Checksums

```text
{checksums}
```
"""


def fail(message):
    sys.exit(f"error: {message}")


def versions():
    project = tomllib.loads((ROOT / "openstrata.toml").read_text(encoding="utf-8"))["project"]["version"]
    manifest = (ROOT / "plugins" / "usdGltfFileFormat" / "openstrata.plugin.yaml").read_text(encoding="utf-8")
    match = MANIFEST_VERSION.search(manifest)
    if not match:
        fail("the bundle manifest has no plugin version")
    if match[1] != project:
        fail(f"openstrata.toml has version {project}, but the bundle manifest has {match[1]}")
    return project


def changelog_section(name):
    """Returns the heading and body of one section of the changelog, or None."""
    lines = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8").splitlines()
    for start, line in enumerate(lines):
        if line.startswith(f"## [{name}]"):
            end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
            return line, "\n".join(lines[start + 1:end]).strip()
    return None


def changes_for(version, prerelease):
    name = "Unreleased" if prerelease else version
    section = changelog_section(name)
    if not section:
        fail(f"CHANGELOG.md has no [{name}] section")
    heading, body = section
    if not body:
        fail(f"the [{name}] section of CHANGELOG.md is empty")
    if not prerelease and not re.search(r"\d{4}-\d{2}-\d{2}", heading):
        fail(f"the changelog heading has no release date: {heading}")
    return body


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tag", help="the tag being released; omit for a dry run")
    parser.add_argument("--github-output", action="store_true", help="print the results as GitHub step outputs")
    parser.add_argument("--checksums", type=pathlib.Path, help="a SHA256SUMS file to include")
    parser.add_argument("--out", type=pathlib.Path, help="where to write the notes")
    arguments = parser.parse_args()

    version = versions()
    if arguments.tag:
        match = TAG.fullmatch(arguments.tag)
        if not match:
            fail(f"tag {arguments.tag} is not vX.Y.Z or vX.Y.Z-suffix")
        if match[1] != version:
            fail(f"tag {arguments.tag} does not match the project version {version}")
        tag, prerelease = arguments.tag, bool(match[2])
    else:
        tag, prerelease = f"v{version}", changelog_section(version) is None
    changes = changes_for(version, prerelease)

    if arguments.github_output:
        print(f"version={version}\ntag={tag}\nis_release={'true' if arguments.tag else 'false'}")
        print(f"prerelease={'true' if prerelease else 'false'}")
    if arguments.out:
        checksums = arguments.checksums.read_text(encoding="utf-8").strip() if arguments.checksums else ""
        ref = tag if arguments.tag else "main"
        notes = NOTES.format(tag=tag, version=version, source=tag[1:], changes=changes, checksums=checksums,
                             blob=f"{REPOSITORY}/blob/{ref}")
        arguments.out.write_text(notes, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
