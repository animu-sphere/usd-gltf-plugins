# Changelog

All notable changes to `usd-gltf-plugins` are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). Supported scope is
recorded in the [capability matrix](docs/reference/CAPABILITY_MATRIX.md), not
here.

## [Unreleased]

### Added

- Default and initial opaque factor-only metallic-roughness material networks
  and bindings.
- Apache-2.0 license, installed with the plugin, and community health files.
- Install smoke test and documentation consistency test.
- clang-format configuration, applied to the project's C++ sources.

### Changed

- Organize material networks in render-context NodeGraphs and use MaterialX
  `gltf_pbr` for the MaterialX surface.
- Author material networks and bindings with UsdShade on a worker-thread
  temporary stage, preserving reload and diagnostic behavior.
- Split CMake responsibilities into `cmake/`, `libs/*`, `plugins/*` and `tests/`.
- Gave each documented fact one owning document, and versioned the roadmap.
