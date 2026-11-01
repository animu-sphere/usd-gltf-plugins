# Backlog

Later versions, in planned order. Nothing here has started; see the
[task lifecycle](README.md#task-lifecycle). The next release is in
[current.md](current.md).

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

## Decisions for later versions

Record the investigation and outcome in an ADR before relying on these:

- `ArResolver` integration for buffer and image URIs.
- Mesh sharing and animated-node placement.
