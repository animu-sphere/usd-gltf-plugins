# OpenUSD glTF Plugins

**Open glTF and GLB files directly as OpenUSD stages.**

[![CI](https://github.com/animu-sphere/usd-gltf-plugins/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/animu-sphere/usd-gltf-plugins/actions/workflows/ci.yml?query=branch%3Amain)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-4b8bbe.svg)](LICENSE)

![Damaged Helmet glTF opened in usdview, with its USD scene and materials in the Stage Tree](docs/images/showcase/hero-damagedhelmet.gif)

An independent OpenStrata Project repository for reading glTF 2.0 (`.gltf` and
`.glb`) with OpenUSD tools. Powered by fastgltf, the plugin represents imported
content using standard USD schemas; it needs no custom USD schema or imaging
plugin.

## Gallery

**A Beautiful Game** — a glTF scene in usdview.

![A chess set from A Beautiful Game open in usdview](docs/images/showcase/a-beautiful-game.png)

**Fox** — explore an imported scene, skeleton, animation and materials in the
USD Stage Tree.

![Fox in usdview with the scene hierarchy and skeletal animation visible](docs/images/showcase/animated-fox.png)

**Iridescence Metallic Spheres** — a material test scene in usdview.

![A grid of metallic spheres from the Iridescence Metallic Spheres sample in usdview](docs/images/showcase/iridescence-metallic-spheres.png)

**Flight Helmet** — textured materials in usdview.

![A textured Flight Helmet model open in usdview](docs/images/showcase/materials-flighthelmet.png)

These are captures, not general rendering-fidelity guarantees. Appearance and
material support depend on the renderer; see the
[capability matrix](docs/reference/CAPABILITY_MATRIX.md) and
[showcase credits and capture notes](docs/images/showcase/README.md).

## Quick start

Build and install the plugin with CMake 3.22 or newer, a C++20 compiler, and an
OpenUSD development installation with its Python modules. fastgltf and its
dependencies are [vendored](third_party/README.md); CMake does not download them.

```sh
cmake -S . -B build -DCMAKE_PREFIX_PATH=<openusd-prefix>
cmake --build build --config Release
cmake --install build --config Release --prefix <install-prefix>
```

Set `PXR_PLUGINPATH_NAME` to
`<install-prefix>/plugin/resources/usdGltfFileFormat` so OpenUSD can discover
the installed plugin. Then open a model in usdview:

```sh
usdview path/to/model.glb
```

Or open it from Python:

```python
from pxr import Usd

stage = Usd.Stage.Open("model.glb")
print(stage.GetDefaultPrim().GetPath())  # /Root
```

See the [development guide](docs/DEVELOPMENT.md) for other build and test
workflows.

## Compatibility and limitations

The plugin is read-only and in early development. Many real-world assets are
rejected with a diagnostic, and rendering may differ between delegates.
The [capability matrix](docs/reference/CAPABILITY_MATRIX.md) is the
authoritative source for supported features, limitations, evidence, and tested
environments.

## Documentation

- [Documentation index](docs/README.md)
- [Development guide](docs/DEVELOPMENT.md)
- [Workspace architecture](docs/architecture/WORKSPACE.md)
- [Implementation policy](docs/design/IMPLEMENTATION_POLICY.md)
- [Roadmap](docs/roadmap/README.md)
- [Changelog](CHANGELOG.md)

## Contributing

See the [contributing guide](CONTRIBUTING.md) and the
[Code of Conduct](CODE_OF_CONDUCT.md). Please report vulnerabilities privately
as described in the [security policy](SECURITY.md).

## License and asset credits

The project is licensed under the [Apache License 2.0](LICENSE). Vendored
dependency licenses are in the [third-party notices](third_party/NOTICES.txt).
Both are installed under `share/licenses/usd-gltf-plugins`.

Showcase images depict third-party models with their own licenses, including
non-commercial terms for Damaged Helmet. See
[showcase credits](docs/images/showcase/README.md) before reusing them.
