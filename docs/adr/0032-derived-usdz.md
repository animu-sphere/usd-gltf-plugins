# 0032 Derived USDZ

Decision record, 2026-11-12. The [import pipeline](../architecture/IMPORT_PIPELINE.md#derived-usdz)
defines the packaging contract.

## Context

The file format imports a layer on demand. Some texture paths refer to PNG or
JPEG assets served by the glTF package resolver, including converted KTX2 and
WebP. Putting the glTF itself in a USDZ leaves those paths subject to the nested
resolver behavior in [ADR 0025](0025-packaged-sources.md).

With OpenUSD 26.08, passing the glTF to `UsdUtils.CreateNewUsdzPackage` put the
glTF in the archive as its first entry. Passing a native USD layer with the
original asset paths also left dependencies on the source. Materializing the
image bytes into a staging directory and rewriting paths to those files let
the same utility package the complete asset.

## Decision

Use a Python packaging command over the registered file format and
[OpenUSD's USDZ writer](https://openusd.org/release/api/class_sdf_zip_file_writer.html).
The file format already runs the conversion library and supplies its resolver,
so the command needs no additional binary or plugin ABI. Copying the imported
layer specs preserves the animation variant set and its contents. Flattening
the composed stage would resolve the current variant selection.

The staged layer and images already have their final archive paths. Adding those
files explicitly lets the command check that each write succeeds, including the
root layer. Dependency discovery through `UsdUtils.CreateNewUsdzPackage` can
skip an unresolved asset with a warning.

Reuse the existing [scoped image cache](../design/TEXTURES.md#cache-and-asset-lifetime)
while collecting the image assets. Separate command invocations read the inputs
again, so packaging also picks up changes to external resources.
