# 0025: Sources inside packages

Decision record, 2026-11-09. It narrows the
[external resource policy](../architecture/IMPORT_PIPELINE.md#external-resource-policy), which
specifies the result, and the open point that [ADR 0001](0001-image-assets.md) left untested. The
[capability matrix](../reference/CAPABILITY_MATRIX.md) records evidence.

## Context

The plan for v0.2.0 included wider resolution of external resources through `ArResolver`, after a
decision on scenarios and security. This record decides one scenario: a `.gltf` or `.glb` opened
inside a package, such as `scene.usdz[model.glb]`. Custom asset resolvers, shared files in parent
directories and remote URIs are not decided here.

The file format opens its source through `ArGetResolver().OpenAsset`, so a packaged source already
opens. Its resources did not follow it. Measured on Windows with OpenUSD 26.08, using a usdz holding
`BoxTextured` from the Khronos samples, stub layers and generated fixtures:

- A packaged `.glb` with an embedded image imported without error, and authored
  `./scene.usdz[model.glb[images/0.png]]`, which resolves to nothing. The textures were silently
  unresolved.
- Even the correct nested path `scene.usdz[model.glb[images/0.png]]` did not resolve, and
  `OpenAsset` returned null, while a sibling entry such as `scene.usdz[model.bin]` opened.
  `UsdGltfPackageResolver` was not called at all, whereas it is called for a `.glb` on disk. A usdz
  nested in a usdz did resolve.
- OpenUSD's dispatching resolver chooses the package resolver for each nesting level from the
  extension of the enclosing container with its innermost bracket removed. For
  `scene.usdz[model.glb]` that is `usdz`, so the usdz resolver receives `images/0.png` and looks for
  it in the zip. A non-usdz package inside a usdz is never served by its own resolver. This was
  read from the `dev` branch of `pxr/usd/ar/resolver.cpp`, and agrees with the measurement.
- `ArResolver::CreateIdentifier` anchors to the outermost package path, so `../x.png` relative to
  `scene.usdz[model.gltf]` became a file beside the usdz on disk. Ar cannot anchor a resource
  inside the package for the plugin.
- The local resolution took the directory of `…/scene.usdz[model.gltf]` as the source's
  directory, which is the usdz's directory on disk. A packaged `.gltf` referencing `model.bin` or
  `tex.png` read files of those names beside the package, not inside it.

The [usdz specification](https://openusd.org/release/spec_usdz.html) allows only USD, image (png,
jpeg, exr, avif) and audio files in a package. A `.gltf`, `.glb` or `.bin` entry is outside it,
although OpenUSD reads such an entry.

## Options

- Resolve resources inside the package: external buffers and PNG or JPEG siblings through
  `OpenAsset` on paths the plugin joins itself. Embedded, KTX2 and WebP images would still fail,
  since they need the package resolver that Ar does not call, so only a layout the specification
  does not allow would work, and the most common one, a `.glb` with embedded images, would not.
- Keep the behavior, recording it as a limit. Textures stay silently unresolved, and the package
  boundary is not one.
- Read only the source's own bytes, and fail when anything else is needed.

## Decision

A source inside a package reads only its own bytes: buffers in its GLB binary chunk and data URIs.
When the selected scene uses any image, whether embedded, a data URI or external, or any external
buffer, import fails with a diagnostic and publishes no layer. Resources that the selected scene
does not use are not read, as elsewhere, so they cause no failure.

A source is inside a package when its resolved path is package-relative
(`ArIsPackageRelativePath`). The check comes before any file is resolved, so nothing beside the
package is read. Factor-only and default materials, meshes, cameras and lights still import.

A self-contained USD package is made by converting first, then packaging the USD and its images
with OpenUSD, as `usd-vrm-plugins` does with `UsdUtilsCreateNewUsdzPackage`. Explicit packaging of
derived USD and converted images is planned for v0.4.0 in the [backlog](../roadmap/backlog.md).

## Verification and limits

- Generated GLTF and GLB sources inside a usdz, with copies of every resource both inside the
  package and beside it on disk, check that factor-only sources open and that embedded, data URI and
  external images and external buffers fail with their diagnostics. Without the change, all of the
  latter imported.
- The routing of nested package paths was observed with OpenUSD 26.08 and read from the `dev`
  branch. A later OpenUSD that routes them to the inner package's resolver would let embedded images
  be served, and would justify revisiting this decision; nothing here tests for that.
- Custom resolvers whose resolved paths are not filesystem paths, and are not package-relative,
  are unchanged: local resolution fails for them as before.

## Sources

- [usdz specification](https://openusd.org/release/spec_usdz.html)
- [OpenUSD `pxr/usd/ar/resolver.cpp`](https://github.com/PixarAnimationStudios/OpenUSD/blob/dev/pxr/usd/ar/resolver.cpp)
- [OpenUSD `pxr/usd/sdf/usdzResolver.cpp`](https://github.com/PixarAnimationStudios/OpenUSD/blob/dev/pxr/usd/sdf/usdzResolver.cpp)
