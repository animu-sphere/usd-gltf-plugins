# Workspace architecture

## Layout

```text
usd-gltf-plugins/
  .github/workflows/       generated CI workflow, and the hand-written release workflow
  cmake/                   shared dependency setup for both build entry points
  docs/                    project documentation
  libs/
    gltfCore/              parsing and safe glTF access
    gltfTexture/           image resolution and conversion
    gltfUsd/               USD scene translation
  plugins/
    usdGltfFileFormat/     OpenUSD file-format entry point and bundle manifest
  scripts/                 release tooling
  tools/
    gltfConvert/           only if a CLI is needed
  tests/
    fixtures/
  third_party/             vendored dependencies
```

The root contains the CMake project, OpenStrata workspace configuration and CI
matrix. The workflow is generated from the matrix with `ost ci generate github`.

## Build entry points

Two CMake entry points produce the same library, registration and install
layout:

- The **repository root**, used by plain CMake and `ost build`.
- **`plugins/usdGltfFileFormat`**, used by the standalone bundle
  (`ost plugin build`). In this mode it adds the shared dependency setup, the
  libraries and the tests itself.

Each target is defined once, in its own directory's `CMakeLists.txt`. A change
to the build must keep both entry points working.

## Components

| Component | Responsibility | Boundary |
| --- | --- | --- |
| `gltfCore` | Parse using fastgltf; safely access glTF data, URIs and buffers | Does not depend on USD or create USD prims; avoid copying all fastgltf data into another model unnecessarily |
| `gltfTexture` | Accept bufferView, data URI and external URI images; decode and convert KTX2 and WebP; handle color-space information, caching and output asset paths | Owns image processing only; does not build USD material networks |
| `gltfUsd` | Translate scene, mesh, transforms, materials and bindings onto a temporary `SdfLayer` | Does not register the plugin |
| `usdGltfFileFormat` | Register `SdfFileFormat` and the `ArPackageResolver` for embedded images, invoke import and propagate diagnostics | Does not own the conversion implementation |
| `gltfConvert` | If needed, call the same conversion library from a CLI | Does not implement a separate conversion pipeline |

URI and path checks shared by buffers and images are in `gltfCore`
(`localFile.cpp`). External buffer loading is in the plugin (`externalBuffer.cpp`).

The direct dependencies are:

```text
usdGltfFileFormat -> gltfUsd -> gltfCore
                             -> gltfTexture
```

The CMake targets are `UsdGltfFileFormat`, `usd_gltf_usd`, `usd_gltf_texture` and
`usd_gltf_core`. `gltfTexture` depends on `gltfCore` and not on USD or `gltfUsd`.
`usd-vrm-plugins` is a reference only; no
dependency on it or forced shared library is intended.
