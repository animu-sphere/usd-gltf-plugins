# SPDX-License-Identifier: Apache-2.0
"""Proves that a packaged bundle works without the source tree or the build tree.

Given a directory that `ost plugin package` wrote, this imports the package, extracts it outside
the repository, and opens fixtures in a session whose only plugin is the extracted one:

    python tests/check_package.py plugins/usdGltfFileFormat/dist/plugins/usdGltfFileFormat/<version>/<target>

`ost plugin test --from-package` confirms discovery. This adds an import, images resolved through
the packaged resolver, a KTX2 image decoded by the packaged transcoder, and the licenses.
"""

import argparse
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
BUNDLE = ROOT / "plugins" / "usdGltfFileFormat"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def run(*command):
    return subprocess.run(command, check=True, text=True, stdout=subprocess.PIPE, errors="replace").stdout


def check_inside_session(package):
    """Runs in the session, where the extracted package is the only source of the plugin."""
    from pxr import Ar, Plug, Usd, UsdShade

    plugin = Plug.Registry().GetPluginWithName("UsdGltfFileFormat")
    assert plugin is not None, "the plugin is not registered"
    assert package in pathlib.Path(plugin.path).resolve().parents, f"loaded from {plugin.path}"

    resources = package / "plugin" / "resources" / "usdGltfFileFormat"
    for name in ("LICENSE", "NOTICES.txt"):
        assert (resources / name).is_file(), f"{name} is not in the package"

    stage = Usd.Stage.Open(str(FIXTURES / "reflections.gltf"))
    assert stage and stage.GetDefaultPrim().GetPath() == "/Root", "a .gltf file did not open"
    assert any(prim.GetTypeName() == "Mesh" for prim in stage.Traverse()), "no mesh was imported"

    stage = Usd.Stage.Open(str(FIXTURES / "ktx2_textures.gltf"))
    assert stage, "a .gltf file with KTX2 images did not open"
    decoded = 0
    for prim in stage.Traverse():
        if prim.IsA(UsdShade.Shader) and UsdShade.Shader(prim).GetShaderId() == "UsdUVTexture":
            asset = UsdShade.Shader(prim).GetInput("file").Get()
            resolved = asset.resolvedPath
            assert resolved, f"{asset.path} did not resolve"
            data = Ar.GetResolver().OpenAsset(Ar.ResolvedPath(resolved))
            assert data and bytes(data.GetBuffer())[:8] == PNG_SIGNATURE, f"{resolved} is not served as PNG"
            # The fixture's first image is a plain PNG file, and the rest are KTX2 in the resolver.
            decoded += "[images/" in resolved
    assert decoded == 5, f"expected 5 KTX2 images through the resolver, found {decoded}"
    print(f"The package at {package} opened fixtures and served {decoded} KTX2 images.")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("directory", type=pathlib.Path, help="the directory holding manifest.json and the archive")
    parser.add_argument("--platform", default="cy2026")
    parser.add_argument("--profile", default="usd")
    parser.add_argument("--inside-session", type=pathlib.Path, help=argparse.SUPPRESS)
    arguments = parser.parse_args()

    if arguments.inside_session:
        check_inside_session(arguments.inside_session.resolve())
        return 0

    ost = shutil.which("ost")
    if not ost:
        sys.exit("ost was not found.")
    directory = arguments.directory.resolve()
    digest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))["archive_digest"]
    run(ost, "artifact", "import", str(directory), "--json")
    with tempfile.TemporaryDirectory(prefix="gltf-package-") as temporary:
        package = pathlib.Path(temporary).resolve() / "package"
        if ROOT in package.parents:
            sys.exit(f"The temporary directory {package} is inside the repository.")
        run(ost, "artifact", "extract", digest, str(package))
        # --no-inject keeps the bundle's build tree off the discovery path. The bundle argument
        # then only selects the runtime.
        session = subprocess.run(
            [ost, "plugin", "run", str(BUNDLE), "--no-inject", "--plugin-path", str(package),
             "--target", arguments.platform, "--profile", arguments.profile, "--",
             "python", str(pathlib.Path(__file__).resolve()), str(directory), "--inside-session", str(package)],
            cwd=temporary, check=False)
    return session.returncode


if __name__ == "__main__":
    sys.exit(main())
