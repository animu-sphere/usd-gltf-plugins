# OpenUSD glTF Plugins

[![CI](https://github.com/animu-sphere/usd-gltf-plugins/actions/workflows/ost-source-ci.yml/badge.svg?branch=main)](https://github.com/animu-sphere/usd-gltf-plugins/actions/workflows/ost-source-ci.yml?query=branch%3Amain)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-4b8bbe.svg)](LICENSE)

An independent OpenStrata Project repository for opening glTF 2.0 (`.gltf` /
`.glb`) files as ordinary OpenUSD scenes through `Usd.Stage.Open`, using
fastgltf and standard USD schemas. It requires no custom USD schema or imaging
plugin.

**This project is in early development and many real-world assets are rejected
with a diagnostic.** The [capability matrix](docs/reference/CAPABILITY_MATRIX.md)
lists what is supported, its limitations and the evidence.

## Quick start

You need CMake 3.22 or newer, a C++20 compiler, and an OpenUSD development
installation with its Python modules. fastgltf and its dependencies are
[vendored](third_party/README.md); CMake does not download them.

```sh
cmake -S . -B build -DCMAKE_PREFIX_PATH=<openusd-prefix>
cmake --build build --config Release
cmake --install build --config Release --prefix <install-prefix>
```

Register the plugin by setting `PXR_PLUGINPATH_NAME` to
`<install-prefix>/plugin/resources/usdGltfFileFormat`, then open a file:

```python
from pxr import Usd

stage = Usd.Stage.Open("model.glb")
print(stage.GetDefaultPrim().GetPath())  # /Asset
```

## Development

Run the tests with the matching OpenUSD `pxr` modules and runtime libraries on
the environment's search paths:

```sh
ctest --test-dir build -C Release --output-on-failure
```

With OpenStrata and its cy2026 USD runtime (versions are pinned in the
[CI matrix](openstrata.ci.yaml)), build and test the workspace or the standalone
plugin bundle:

```sh
ost build
ost test

ost plugin build plugins/usdGltfFileFormat
ost plugin test plugins/usdGltfFileFormat --up-to 2
```

To check display with Storm after `ost build`, which needs a GPU and the lookdev runtime
and is not part of `ost test`:

```powershell
ost env cy2026 --profile lookdev --shell powershell | Out-String | Invoke-Expression
python tests\check_display.py build\cy2026-windows-x86_64-py313-usd
```

It exits nonzero if a comparison fails, and keeps images and logs in `display_check` in
the build directory.

## Documentation

- [Documentation index](docs/README.md)
- [Implementation policy](docs/design/IMPLEMENTATION_POLICY.md)
- [Roadmap](docs/roadmap/README.md)
- [Changelog](CHANGELOG.md)

## Contributing

See the [contributing guide](CONTRIBUTING.md) and the
[Code of Conduct](CODE_OF_CONDUCT.md). Please report vulnerabilities privately
as described in the [security policy](SECURITY.md).

## Compatibility and licenses

[Tested environments](docs/reference/CAPABILITY_MATRIX.md#tested-environments)
are listed in the capability matrix.

This project is licensed under the [Apache License 2.0](LICENSE). Vendored
dependency licenses are in the [third-party notices](third_party/NOTICES.txt).
Both are installed under `share/licenses/usd-gltf-plugins`.
