# Changelog

All notable changes to `usd-gltf-plugins` are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). Supported scope is
recorded in the [capability matrix](docs/reference/CAPABILITY_MATRIX.md), not
here.

## [Unreleased]

### Added

- Release packages on GHCR, with a pin table in each release.
- Sample check, `tests/check_samples.py`, which records whether every Khronos sample asset opens,
  agrees with the file in USD and draws, and compares that with a committed baseline.
- `KHR_texture_transform`, optional or required, in every texture slot through both shader paths,
  including its `texCoord` override.
- `KHR_mesh_quantization`: non-float POSITION, NORMAL, TANGENT and TEXCOORD, authored as floats.
- Perspective and orthographic cameras, as `UsdGeomCamera` prims below their nodes.
- `KHR_lights_punctual`: directional, point and spot lights, as UsdLux lights below their nodes.
- `EXT_texture_webp`: still lossy and lossless WebP images, with or without alpha, served as PNG
  through the package resolver by a vendored libwebp decoder.

### Changed

- A rejection names each unsupported required extension, and states the one skin, camera, light,
  morph, primitive mode or accessor cause it found rather than listing the possibilities.

### Fixed

- A rotation within the tolerance of unit length is normalized, and no longer scales and shears its
  node.

## [0.1.0] - 2026-11-02

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

### Fixed

- Intermittent crash on macOS while opening glTF files. Conversions now run one at a time on one
  thread for the process, instead of a new thread for each read.
- The plugin no longer exports the symbols of its vendored libraries on Linux and macOS.

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
