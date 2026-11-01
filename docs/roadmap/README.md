# Roadmap

This is the only version plan: scope and completion criteria per version,
without dates. The current state is in the
[capability matrix](../reference/CAPABILITY_MATRIX.md).

## v0.1.0: practical import of static PBR glTF

### Required work

1. Keep the existing `.gltf` / `.glb`, default scene, `/Asset/geo`,
   `/Asset/mtl`, hierarchy, transform, mesh, UV, vertex color and reload
   behavior, and add regression tests for it.
2. Author a `UsdShadeMaterial` under `/Asset/mtl` and an appropriate binding on
   each primitive, including glTF's default behavior when no material is
   assigned.
3. Convert metallic-roughness base color, metallic, roughness, emissive, alpha
   mode and cutoff, and `doubleSided`. Define the multiplication with vertex
   colors and the handling of alpha. Fix any inexact approximation in documents
   and tests.
4. Generate both MaterialX `standard_surface`-based and UsdPreviewSurface
   networks, and verify render-context outputs and fallback. A network's
   existence alone does not establish display.
5. Handle PNG and JPEG from bufferViews, data URIs and permitted external URIs.
   Connect base color, metallic-roughness (G = roughness, B = metallic), normal,
   emissive and occlusion (R) where feasible. Test UV sets, image orientation,
   and color versus non-color data.
6. Convert KTX2 through `KHR_texture_basisu` (ETC1S and UASTC as handled by the
   chosen library) to a standard image path. **Before starting on a decoder,**
   decide the cache key, storage location, lifetime, concurrency and disposal,
   reopening, and resolution after moving or distributing an asset in an ADR.
7. Fail safely on invalid URIs, out-of-range accessors, corrupt images, oversized
   data and unsupported required extensions, without publishing an incomplete
   layer. Add the missing negative tests for skins, cameras, lights, morphs and
   animations.
8. Run CI on Windows, Linux and macOS with explicitly stated OpenUSD/OpenStrata
   environments. Add integration tests for glTF/GLB, embedded and external
   images, KTX2, bindings, MaterialX and Preview, alpha, UVs, reload and failure,
   plus a reproducible Storm display check.
9. Provide usage and installation instructions, a project license, third-party
   licenses, and the supported scope with its evidence.

### Implementation order

1. Documents, CMake and test organization.
2. Factor-only materials and bindings.
3. Verification of both shader paths.
4. PNG/JPEG and image paths.
5. KTX2, caching and packaging.
6. Cross-cutting tests and release.

The MaterialX and Preview order may change, but the texture lifetime and cache
contract must be decided before KTX2 implementation.

### Release criteria

Representative static and textured glTF/GLB assets open through
`Usd.Stage.Open()`, with hierarchy, bindings and both shader paths authored
correctly. Images resolve after reload, and KTX2 displays through standard
images. CI on the target platforms and the stated renderer tests succeed.

Until then, use a pre-release such as `v0.1.0-alpha.1` rather than presenting
the work as complete. Do not claim support for all of glTF.

## v0.2.0: wider static scene support

- `KHR_texture_transform`, UV and material boundary conditions, and a policy
  for ignoring, approximating or rejecting optional extensions.
- `KHR_materials_unlit`, clearcoat, transmission, IOR and similar extensions,
  each as an individual unit of verification.
- Cameras and `KHR_lights_punctual` through standard USD schemas.
- Wider external resource resolution through `ArResolver`, as need and safety
  are verified.
- Shared meshes and load performance in large scenes, based on measurements.

**Completion criteria:** each feature has fixtures, tests of USD structure and
meaning, and renderer tests where display compatibility is claimed. The
capability matrix reflects the scope and constraints.

## v0.3.0: animation and deformation

- glTF animation samplers, interpolation and time mapping.
- Skeletons, skins and joints through `UsdSkel`; morph targets and weights.
- Tests of multiple clips, skins combined with node transforms, per-time
  evaluation and reload.
- Placement such as `/Asset/rig` and `/Asset/anim` is decided by implementation
  and composition experiments; it is not a required structure today.

**Completion criteria:** representative animated models are consistent at
multiple times, and unsupported interpolation and deformation are explicit.

## v0.4.0: optimization and developer tools

- If needed, a CLI calling the same conversion library, not a separate importer.
- Explicit packaging of derived USD and converted images; reusable cache
  operation.
- Shared meshes, performance and memory measurement, fuzz and property tests for
  malformed input, and verification across OpenUSD versions.

**Completion criteria:** reproducible conversion and distribution procedures
exist, and optimizations are based on measurements.

## v0.5.0 and later

Prioritize extensions by actual asset demand. Begin USD-to-glTF export only after
defining a separate specification and quality goals.

## v1.0.0 conditions

The project can state what its compatibility guarantee covers (glTF subset,
OpenUSD, OS and compiler, ABI, and image asset persistence), with sufficient
regression tests and an established release process.

## Release gate

Every release must satisfy these checks:

- [ ] Required conditions for the version were confirmed on the stated OS,
      OpenUSD and renderer.
- [ ] The capability matrix shows implementation, limitations and test evidence,
      and no other document repeats progress.
- [ ] `.gltf`, `.glb`, reload and failure, both shaders, bindings, PNG/JPEG and
      KTX2 were confirmed end to end, as applicable to the version.
- [ ] Saved images resolve after reopening, and the constraints on moving and
      packaging can be explained.
- [ ] Failure tests exist for invalid URIs, corrupt or huge buffers and images,
      and required extensions.
- [ ] Root CMake, standalone OpenStrata, CI, install and discovery, and inclusion
      of third-party licenses were verified.
- [ ] CI and renderer checks were confirmed at the release commit. Past successes
      are not treated as a current guarantee.
- [ ] The tag and release notes briefly state changes, the supported subset, known
      constraints and test environments, without copying the capability matrix.

## Decisions to verify

Record the investigation and outcome in an ADR before relying on these in
implementation or support claims:

- Target OpenUSD versions and distributions, C++20/fastgltf ABI and compiler
  compatibility; pinning and enabled fastgltf extensions.
- MaterialX and UsdPreviewSurface node graphs, render contexts and actual support
  in target renderers.
- Texture storage, cache lifetime and portable persistent asset paths.
- `ArResolver` integration for buffer and image URIs.
- Mesh sharing and animated-node placement.
- Renderer compatibility with the authored stage metrics and UV orientation.
- Supported platform matrix and KTX decoder selection.
