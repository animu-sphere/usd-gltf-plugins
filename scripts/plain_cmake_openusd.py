# SPDX-License-Identifier: Apache-2.0
"""Fetches the OpenUSD install of one openstrata.ci.yaml cell, without ost.

.github/workflows/plain-cmake.yml builds the workspace as someone without OpenStrata would, against
an ordinary OpenUSD install. So that it does not pin an OpenUSD of its own, this reads the cell's
`runtime_artifact` digest and `runtime_remote` reference, downloads that archive anonymously over
HTTPS, checks the digest, and extracts it. The archive is an OpenUSD install prefix with
pxrConfig.cmake at its root.

    python scripts/plain_cmake_openusd.py --cell workspace-pr-linux --out <dir>

Prints `target=`, `apt=` (the cell's host packages) and `python=` (the Python the runtime's bindings
are built for) as GitHub step outputs. Adapted from usd-vrm-plugins' scripts/plain_cmake_inputs.py.
"""

import argparse
import hashlib
import json
import pathlib
import re
import shutil
import subprocess
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
CI_MANIFEST = ROOT / "openstrata.ci.yaml"
ARCHIVE_MEDIA_TYPE = "application/vnd.openstrata.artifact.archive.v1+tar+zstd"


def fail(message):
    sys.exit(f"error: {message}")


def cell(name):
    """The pins of one cell, read without a YAML parser, so nothing has to be installed first."""
    text = CI_MANIFEST.read_text(encoding="utf-8")
    match = re.search(rf"^  - name: {re.escape(name)}\n((?:    .*\n|\s*\n)*)", text, re.M)
    if not match:
        fail(f"{CI_MANIFEST.name} has no cell named {name!r}")
    body = match[1]

    def field(pattern):
        found = re.search(pattern, body, re.M)
        if not found:
            fail(f"cell {name!r} has no match for {pattern!r}")
        return found[1]

    apt = re.search(r"^\s+apt:\s*\[([^\]]*)\]", body, re.M)
    python = re.search(r"^\s+host_python:\s*\"([^\"]+)\"", body, re.M)
    return {
        "runtime_artifact": field(r"^\s+runtime_artifact:\s*(sha256:[0-9a-f]{64})"),
        "uri": field(r"^\s+uri:\s*(oci://\S+)"),
        "apt": [package.strip() for package in apt[1].split(",")] if apt else [],
        "python": python[1] if python else "",
    }


class Registry:
    """An anonymous pull from a public OCI registry such as GHCR."""

    def __init__(self, uri):
        match = re.fullmatch(r"oci://([^/]+)/([^@]+)@(sha256:[0-9a-f]{64})", uri)
        if not match:
            fail(f"not a digest-pinned OCI reference: {uri}")
        self.host, self.repository, self.manifest_digest = match.groups()
        token_url = f"https://{self.host}/token?scope=repository:{self.repository}:pull"
        with urllib.request.urlopen(token_url, timeout=60) as response:
            self.token = json.load(response)["token"]

    def get(self, path, accept=None):
        headers = {"Authorization": f"Bearer {self.token}"}
        if accept:
            headers["Accept"] = accept
        request = urllib.request.Request(f"https://{self.host}/v2/{self.repository}/{path}", headers=headers)
        return urllib.request.urlopen(request, timeout=600)

    def manifest(self):
        with self.get(f"manifests/{self.manifest_digest}", "application/vnd.oci.image.manifest.v1+json") as response:
            body = response.read()
        check(self.manifest_digest, hashlib.sha256(body).hexdigest())
        return json.loads(body)

    def blob_to(self, digest, destination):
        sha = hashlib.sha256()
        with self.get(f"blobs/{digest}") as response, destination.open("wb") as out:
            while chunk := response.read(1 << 20):
                sha.update(chunk)
                out.write(chunk)
        check(digest, sha.hexdigest())


def check(expected, actual_hex):
    if expected != f"sha256:{actual_hex}":
        fail(f"{expected} hashes to sha256:{actual_hex}")


def extract(archive, out):
    """Extracts a .tar.zst with Python's zstd support (3.14 and later), or else with tar."""
    try:
        import tarfile
        from compression import zstd  # noqa: F401  (only probes for the module)
    except ImportError:
        tar = shutil.which("tar")
        if not tar:
            fail("no tar on PATH")
        result = subprocess.run([tar, "--zstd", "-xf", str(archive), "-C", str(out)])
        if result.returncode != 0:
            fail(f"tar could not extract {archive}; it needs zstd on PATH")
        return
    with tarfile.open(archive, "r:zst") as contents:
        contents.extractall(out, filter="tar")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cell", required=True, help="the openstrata.ci.yaml cell whose runtime to fetch")
    parser.add_argument("--out", type=pathlib.Path, required=True, help="where to extract the install")
    arguments = parser.parse_args()

    found = cell(arguments.cell)
    registry = Registry(found["uri"])
    layer = next((entry for entry in registry.manifest().get("layers", [])
                  if entry.get("digest") == found["runtime_artifact"]), None)
    if layer is None:
        fail(f"{found['uri']} carries no layer {found['runtime_artifact']}")
    if layer.get("mediaType") != ARCHIVE_MEDIA_TYPE:
        fail(f"layer {found['runtime_artifact']} is {layer.get('mediaType')}, not an archive")
    title = layer.get("annotations", {}).get("org.opencontainers.image.title", "")
    target = re.fullmatch(r"openstrata-(.+)\.tar\.zst", title)
    if not target:
        fail(f"cannot read a target out of the archive name {title!r}")

    out = arguments.out
    out.mkdir(parents=True, exist_ok=True)
    archive = out.parent / f".{out.name}.tar.zst"
    registry.blob_to(found["runtime_artifact"], archive)
    extract(archive, out)
    archive.unlink()
    if not (out / "pxrConfig.cmake").exists():
        fail(f"{title} extracted into {out} with no pxrConfig.cmake at its root")

    print(f"target={target[1]}")
    print(f"apt={' '.join(found['apt'])}")
    print(f"python={found['python']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
