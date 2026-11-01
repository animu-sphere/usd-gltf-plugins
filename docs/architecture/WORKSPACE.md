# Workspace architecture

Status: **partially started**. The registration scaffold is an OpenStrata
workspace member and supports both a root CMake build and a standalone bundle
build. The remaining components and dependency directions are planned.

## Proposed layout

```text
usd-gltf-plugins/
  .github/workflows/       CI
  cmake/                   shared build support
  docs/                    project documentation
  libs/
    gltfCore/              parsing and safe glTF access
    gltfTexture/           image resolution and conversion
    gltfUsd/               USD scene translation
  plugins/
    usdGltfFileFormat/     OpenUSD file-format entry point
  tools/
    gltfConvert/           optional later development/CLI tool
  tests/
    fixtures/
    unit/
    integration/
  third_party/             only if needed
```

The root contains CMake and OpenStrata workspace configuration, a CI matrix
and a generated GitHub Actions workflow. The Windows, Linux and macOS
pull-request cells passed the workspace build and both scaffold tests in
[run 41289600882](https://github.com/animu-sphere/usd-gltf-plugins/actions/runs/41289600882).
This does not verify the later importer or renderer support. CMake presets
and the remaining project and dependency configuration are still to be
established.

| Component | Responsibility | Boundary |
| --- | --- | --- |
| `gltfCore` | Parse using fastgltf; safely access glTF data, URIs and buffers | Does not create USD prims; avoid copying all fastgltf data into another model unnecessarily |
| `gltfTexture` | Resolve images, decode/convert KTX2, handle color-space information and caching | Does not build USD material networks |
| `gltfUsd` | Translate scene, mesh, transforms, materials and bindings | Does not register the plugin |
| `usdGltfFileFormat` | Register `SdfFileFormat`, invoke import and propagate diagnostics | Does not own the conversion implementation |
| `gltfConvert` | If needed, call the same conversion library from a CLI | Does not implement a separate conversion pipeline |

The intended direct dependencies are:

```text
usdGltfFileFormat -> gltfUsd -> gltfCore
                             -> gltfTexture
```

Image input must accommodate both bufferViews and external URIs without a
cycle between `gltfTexture` and `gltfUsd`. `usd-vrm-plugins` is a reference
only; no dependency on it or forced shared library is intended.
