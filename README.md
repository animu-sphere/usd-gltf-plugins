# OpenUSD glTF Plugins

An independent OpenStrata Project repository for importing glTF 2.0
(`.gltf` / `.glb`) as ordinary OpenUSD scenes.

The intended primary workflow is `Usd.Stage.Open("model.glb")`. The importer
will use fastgltf and standard USD schemas, with MaterialX and
UsdPreviewSurface material paths. It will not require a custom USD schema or
an imaging plugin. Import comes first; export is a later phase.

**Status: initial design.** No plugin, build, installation procedure, or
supported runtime configuration is available in this repository yet. The
workflow above is a goal, not a working example.

Start with the [documentation index](docs/README.md), the
[implementation policy](docs/design/IMPLEMENTATION_POLICY.md), and the
[roadmap](docs/roadmap/README.md). Current implementation status is tracked
in the [capability matrix](docs/reference/CAPABILITY_MATRIX.md).

The structure and material implementation of
[usd-vrm-plugins](https://github.com/animu-sphere/usd-vrm-plugins) are
references, not dependencies.
