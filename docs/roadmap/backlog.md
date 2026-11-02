# Backlog

Later versions, in planned order. Nothing here has started; see the
[task lifecycle](README.md#task-lifecycle). The next release is in
[current.md](current.md).

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

- KTX 1.0 images, reaching consumers as standard images like KTX2. No glTF extension
  references KTX 1.0, so this first needs a decision on how an asset would use them.

## v1.0.0 conditions

The project can state what its compatibility guarantee covers (glTF subset,
OpenUSD, OS and compiler, ABI, and image asset persistence), with sufficient
regression tests and an established release process.

## Decisions for later versions

Record the investigation and outcome in an ADR before relying on these:

- `ArResolver` integration for buffer and image URIs.
- Mesh sharing and animated-node placement.
