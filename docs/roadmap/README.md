# Roadmap

Status: phase 0's scaffold criterion is verified; later phases and a supported
configuration matrix are not. Phases describe intended sequencing, not release
dates.

| Phase | Work | Completion criterion |
| --- | --- | --- |
| 0 | OpenStrata workspace, CMake, fastgltf and OpenUSD | Minimal plugin loads locally and in CI |
| 1 | `.gltf` / `.glb`, scenes, nodes, meshes and transforms | `Usd.Stage.Open()` produces `/Asset/geo` |
| 2 | UsdShade, MaterialX, UsdPreviewSurface, UVs and bindings | `/Asset/mtl`, both shading paths and basic PBR tests pass |
| 3 | PNG/JPEG, KTX2, URIs and caching | Embedded GLB KTX2 is converted to a standard image and rendered |
| 4 | Cameras/lights, animation, skinning and morphs | Representative assets convert to standard USD schemas |
| 5 | Extensions, optimization, CLI and export investigation | Capability matrix, regression tests and distribution procedure are in place |

## Initial milestone acceptance

The initial import milestone spans phases 0–3 and is complete only when:

1. OpenUSD recognizes both `.gltf` and `.glb`, and `Usd.Stage.Open()` succeeds.
2. `/Asset` is the default prim, and `/Asset/geo` and `/Asset/mtl` exist.
3. Basic mesh geometry, node hierarchy and transforms are preserved.
4. glTF PBR materials and their bindings are authored as UsdShade materials.
5. Both MaterialX and UsdPreviewSurface paths are verified.
6. Representative PNG, JPEG and KTX2 assets can be displayed.
7. Unsupported extensions and malformed data produce diagnostics without
   abnormal termination; unsupported required extensions are not ignored.

## Decisions to verify

Record the investigation and outcome in ADRs before relying on these
decisions in implementation or support claims:

- Target OpenUSD versions and distributions, C++20/fastgltf ABI and compiler
  compatibility; pinning and enabled fastgltf extensions.
- MaterialX and UsdPreviewSurface node graphs, render contexts and actual
  support in target renderers.
- Texture storage, cache lifetime and portable persistent asset paths.
- Mesh sharing, animated-node placement, units, up axis and UV orientation.
- Current OpenStrata configuration/CI schemas, dependency acquisition,
  supported platform matrix and test infrastructure.

See the [capability matrix](../reference/CAPABILITY_MATRIX.md) for actual
implementation status.

Phase 0's scaffold criterion is met: the workspace build, registration test
and fastgltf parsing smoke test passed locally on Windows and in the Windows,
Linux and macOS pull-request cells of
[run 41289600882](https://github.com/animu-sphere/usd-gltf-plugins/actions/runs/41289600882).
The [main-branch run for the merge](https://github.com/animu-sphere/usd-gltf-plugins/actions/runs/41289759544)
also passed on all three platforms. These results predate the scene-selection
test and do not verify importing or establish a general compatibility matrix.
The [scene selection decision](../architecture/SCENE_SELECTION.md) is recorded
and is connected to the importer. End-to-end local Windows tests cover both
formats, hierarchy, transforms, selection, triangle meshes, UV sets and
vertex color data with embedded and restricted local external buffers, and
failed/successful reload.
The [layer authoring decision](../architecture/LAYER_AUTHORING.md) was revised
to temporary `SdfLayer` authoring after reload exposed an unsafe change-block
interaction. Mesh support is limited to triangle primitives with
positions, optional normals, UV coordinate sets and COLOR_0; the remaining
phase 1 work is not implemented. Vertex color alpha is retained as data, not
applied as opacity; material and renderer behavior remains unverified.
External buffer URI restrictions and their security limitations are described
in the [import pipeline](../architecture/IMPORT_PIPELINE.md).
These newer tests have not yet been verified in the other CI cells.
