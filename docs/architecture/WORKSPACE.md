# Workspace architecture

## Layout

```text
usd-gltf-plugins/
  CMakeLists.txt           workspace root: finds OpenUSD, adds every member, workspace tests
  CMakePresets.json        plain-CMake preset
  VERSION                  the version every member mirrors
  openstrata.toml          OpenStrata members
  openstrata.ci.yaml       CI matrix
  .github/workflows/       generated source CI, and the hand-written CI entry, plain-CMake,
                           docs and release workflows
  cmake/                   helpers and vendored-dependency setup shared by the members
  docs/                    project documentation
  libs/
    gltfCore/              parsing and safe glTF access
    gltfTexture/           image resolution and conversion
    gltfUsd/               USD scene translation
  plugins/
    usdGltfFileFormat/     OpenUSD file format and package resolver bundle
  scripts/                 packaging, release and CI tooling
  tools/
    gltfConvert/           only if a CLI is needed
  tests/                   workspace tests and checks, and their fixtures
  third_party/             vendored dependencies
```

Each library has the same shape:

```text
libs/<library>/
  CMakeLists.txt           its own CMake project
  openstrata.library.yaml  its OpenStrata identity and library requirements
  include/<library>/       its interface, included as "<library>/<header>.h"
  src/                     its implementation and private headers
  tests/                   its unit tests
```

The bundle keeps its sources in `src/`, its `plugInfo.json` template in
`plugin/resources/usdGltfFileFormat/` and its manifest in `openstrata.plugin.yaml`.

## Members and builds

The members are listed in `openstrata.toml`. Each is a CMake project of its own, and each is
built in three ways. The reasons are in [ADR 0027](../adr/0027-plugin-workspace.md).

| Build | Entry point | What it builds |
| --- | --- | --- |
| Plain CMake, `ost build`, `ost test` | The repository root | Every member, the libraries' unit tests and the workspace tests |
| `ost plugin build plugins/usdGltfFileFormat` | The bundle | The bundle, with the libraries added from source and linked statically |
| `ost library build libs/<library>` | The library | The library and its tests, with the libraries it requires as installed packages |

The plugin's library goes to `lib/` and its registration to
`plugin/resources/usdGltfFileFormat/`, and installs to the same layout. In the root build these are
below the build directory, and in the standalone bundle build below that build's directory. Every
`ost plugin` command reads the stage that `ost plugin build` installs to; `view` and `test-view` do
since OpenStrata 0.23.20, and read the bundle's own directory before it.

A library installs its development package (headers, archives, the vendored archives it links,
and `lib/cmake/<library>/`) only when it is the top-level project, or when
`USD_GLTF_INSTALL_LIBRARIES` is on. The bundle declares no library requirement, because
OpenStrata would put the libraries' install trees in the bundle's package.

Each target is defined once, in its own directory's `CMakeLists.txt`. A change to the build must
keep all three builds working.

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
usdGltfFileFormat -> gltfUsd -> gltfTexture -> gltfCore
                  -> gltfTexture
                  -> gltfCore
```

The CMake targets are `UsdGltfFileFormat`, `gltfUsd::gltfUsd`, `gltfTexture::gltfTexture` and
`gltfCore::gltfCore`, and the library descriptors declare the same edges between the libraries.
`gltfCore` and `gltfTexture` do not depend on USD, and `gltfTexture` does not depend on `gltfUsd`.
`usd-vrm-plugins` is a reference only; no dependency on it or forced shared library is intended.
