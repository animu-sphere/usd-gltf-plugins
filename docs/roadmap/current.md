# Current

Unfinished work only; see the [task lifecycle](README.md#task-lifecycle). Tasks are in
implementation order.

## v0.1.0: practical import of static PBR glTF

### Decisions to verify

Record the investigation and outcome in an ADR before relying on these in
implementation or support claims:

- Target OpenUSD versions and distributions, C++20/fastgltf ABI and compiler
  compatibility; pinning and enabled fastgltf extensions.
- Renderer compatibility with the authored stage metrics and UV orientation, and
  actual MaterialX and UsdPreviewSurface support in target renderers.
- Supported platform matrix.

### Release criteria

Representative static and textured glTF/GLB assets open through
`Usd.Stage.Open()`, with hierarchy, bindings and both shader paths authored
correctly. Images resolve after reload, and KTX2 displays through standard
images. CI on the target platforms and the stated renderer tests succeed, and the
[release gate](README.md#release-gate) is satisfied.

Until then, use a pre-release such as `v0.1.0-alpha.1` rather than presenting
the work as complete. Do not claim support for all of glTF.
