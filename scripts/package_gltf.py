# SPDX-License-Identifier: Apache-2.0
"""Convert a local glTF asset into a self-contained USDZ."""

import argparse
import os
from pathlib import Path
import tempfile

from pxr import Ar, Sdf, Tf, UsdUtils


def stage_layer(source, directory):
    resolver = Ar.GetResolver()
    source_path = source.as_posix()
    context = resolver.CreateDefaultContextForAsset(source_path)
    with Ar.ResolverContextBinder(context), Ar.ResolverScopedCache():
        imported = Sdf.Layer.FindOrOpen(source_path)
        if not imported:
            raise RuntimeError(f"cannot import {source}")

        # Copy the layer specs so animation variants survive and the imported layer stays intact.
        layer = Sdf.Layer.CreateAnonymous(".usdc")
        layer.TransferContent(imported)
        (directory / "textures").mkdir()
        images = {}

        def copy_image(path):
            if not path:
                return path
            image_path = path
            if Ar.IsPackageRelativePath(path):
                _, image_path = Ar.SplitPackageRelativePathInner(path)
            suffix = Path(image_path).suffix.lower()
            if suffix not in {".png", ".jpg", ".jpeg"}:
                raise RuntimeError(f"unexpected asset path {path!r} in the imported layer")

            resolved = resolver.Resolve(imported.ComputeAbsolutePath(path))
            if resolved in images:
                return "./" + images[resolved]
            image = resolver.OpenAsset(resolved) if resolved else None
            size = image.GetSize() if image else 0
            if not size:
                raise RuntimeError(f"cannot read image {path!r} from {source}")

            filename = f"textures/{len(images)}{suffix}"
            # Bound the Python allocation when copying a large external image.
            chunk_size = 1024 * 1024
            with (directory / filename).open("wb") as output:
                for offset in range(0, size, chunk_size):
                    count = min(chunk_size, size - offset)
                    chunk = image.Read(count, offset)
                    if len(chunk) != count:
                        raise RuntimeError(f"short read for image {path!r} from {source}")
                    output.write(chunk)
            images[resolved] = filename
            return "./" + filename

        UsdUtils.ModifyAssetPaths(layer, copy_image, keepEmptyPathsInArrays=True)
        root = "scene.usdc"
        if not layer.Export((directory / root).as_posix()):
            raise RuntimeError("cannot export the derived USD layer")
        return [root, *images.values()]


def package_gltf(source, destination):
    source = source.absolute()
    destination = destination.absolute()
    if source.suffix.lower() not in {".gltf", ".glb"} or not source.is_file():
        raise ValueError("source must be a local .gltf or .glb file")
    if destination.suffix.lower() != ".usdz":
        raise ValueError("destination must have a .usdz extension")

    with tempfile.TemporaryDirectory(prefix=".gltf-", dir=destination.parent) as temp:
        directory = Path(temp)
        entries = stage_layer(source, directory)
        if len(entries) > 65535:
            raise RuntimeError("too many files for a USDZ archive")
        package = directory / "package.usdz"
        with Sdf.ZipFileWriter.CreateNew(package.as_posix()) as writer:
            if not writer:
                raise RuntimeError("cannot create the USDZ")
            for entry in entries:
                if writer.AddFile((directory / entry).as_posix(), entry) != entry:
                    raise RuntimeError(f"cannot add {entry} to the USDZ")
            if not writer.Save():
                raise RuntimeError("cannot save the USDZ")
        os.replace(package, destination)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="local .gltf or .glb file")
    parser.add_argument("destination", type=Path, help="output .usdz file, replaced on success")
    args = parser.parse_args()
    try:
        package_gltf(args.source, args.destination)
    except (OSError, RuntimeError, Tf.ErrorException, ValueError) as error:
        parser.exit(1, f"{parser.prog}: {error}\n")


if __name__ == "__main__":
    main()
