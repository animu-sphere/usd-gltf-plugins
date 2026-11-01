# OpenUSD glTF Plugins

An independent OpenStrata Project repository for importing glTF 2.0
(`.gltf` / `.glb`) as ordinary OpenUSD scenes.

The intended primary workflow is `Usd.Stage.Open("model.glb")`. The importer
will use fastgltf and standard USD schemas, with MaterialX and
UsdPreviewSurface material paths. It will not require a custom USD schema or
an imaging plugin. Import comes first; export is a later phase.

**Status: early implementation.** A file-format registration scaffold is
available, but importing is not implemented and opening a glTF file fails
with a diagnostic. The workflow above is a goal, not a working example.

## Development build

With CMake 3.22 or newer, a C++20 compiler and a compatible OpenUSD
development installation:

```sh
cmake -S . -B build -DCMAKE_PREFIX_PATH=<openusd-prefix>
cmake --build build --config Release
ctest --test-dir build -C Release --output-on-failure
```

Tests require Python with the matching OpenUSD `pxr` modules and runtime
libraries on the environment's search paths. The generated registration is
under `build/plugin/resources/usdGltfFileFormat`; `cmake --install build
--config Release --prefix <install-prefix>` installs the library and
registration together. Set `PXR_PLUGINPATH_NAME` to the corresponding
`plugin/resources/usdGltfFileFormat` directory to register it.

The scaffold has been tested locally with the OpenStrata cy2026 Windows
x86-64 Python 3.13 USD runtime (OpenUSD 26.08) and MSVC. Other configurations
and CI have not yet been verified.

Start with the [documentation index](docs/README.md), the
[implementation policy](docs/design/IMPLEMENTATION_POLICY.md), and the
[roadmap](docs/roadmap/README.md). Current implementation status is tracked
in the [capability matrix](docs/reference/CAPABILITY_MATRIX.md).

The structure and material implementation of
[usd-vrm-plugins](https://github.com/animu-sphere/usd-vrm-plugins) are
references, not dependencies.
