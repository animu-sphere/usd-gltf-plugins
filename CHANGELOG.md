# Changelog

All notable changes to `usd-gltf-plugins` are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). Supported scope is
recorded in the [capability matrix](docs/reference/CAPABILITY_MATRIX.md), not
here.

## [Unreleased]

### Added

- Default and factor-only metallic-roughness material networks and bindings,
  with OPAQUE, MASK and BLEND alpha and vertex colors.
- Base color textures from PNG and JPEG images in bufferViews, data URIs and local
  files. A package resolver serves embedded images from the source file.
- Metallic-roughness, occlusion and emissive textures, with color and non-color
  images read distinctly.
- Normal textures. Assets with TANGENT now import, retaining it as the
  `gltf_tangent` primvar; renderers derive the tangent frame.
- KTX2 images through `KHR_texture_basisu`, served to consumers as PNG, using the
  Basis Universal transcoder.
- Apache-2.0 license, installed with the plugin, and community health files.
- Install smoke test and documentation consistency test.
- Display check, `tests/check_display.py`, which renders the fixtures with Storm through
  both shader paths and compares pixels.
- Release workflow, which builds, verifies and packages the plugin on each CI platform
  and creates a draft GitHub release. Plugin packages now include `LICENSE`.
- clang-format configuration, applied to the project's C++ sources.

### Changed

- Author geometry with `UsdGeom` instead of direct `Sdf` specs. Meshes now have an
  `extent`, and `subdivisionScheme` has its schema's `uniform` variability.
- Rename the vertex alpha primvar `gltf:colorAlpha` to `gltf_colorAlpha`, because
  a colon breaks MaterialX shaders in Storm.
- Organize material networks in render-context NodeGraphs and use MaterialX
  `gltf_pbr` for the MaterialX surface.
- Author material networks and bindings with UsdShade on a worker-thread
  temporary stage, preserving reload and diagnostic behavior.
- Split CMake responsibilities into `cmake/`, `libs/*`, `plugins/*` and `tests/`.
- Gave each documented fact one owning document, and versioned the roadmap.
- Split the roadmap into current and backlog plans; finished tasks are deleted.
- Reject damaged PNG and JPEG images at import by checking their structure, because
  OpenUSD's image loader can crash on them. Limit images to 16384 pixels per side and
  external image files to 2 GiB. External images are now read during import.
