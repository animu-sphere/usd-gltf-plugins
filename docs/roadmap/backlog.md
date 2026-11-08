# Backlog

Later versions, in planned order. Nothing here has started; see the
[task lifecycle](README.md#task-lifecycle). The next release is in
[current.md](current.md).

## v0.4.0: optimization and developer tools

- If needed, a CLI calling the same conversion library, not a separate importer.
- Explicit packaging of derived USD and converted images, so that a portable result does not depend
  on resolving images at runtime; reusable cache operation.
- Measurements on one set of samples: import latency, peak RSS, mesh duplicate rate, and KTX2 decode
  count and time.
- Shared meshes and image caches, with a baseline and an improvement target from those measurements,
  verifying thread safety, lifetime, and stage and resolver identity.
- Fuzz and property tests, with malicious URIs, damaged accessors and images, and very large input.
  A serious security problem is fixed without waiting for this version.
- More OpenUSD versions and compilers, added only for combinations whose ABI was verified.

**Completion criteria:** reproducible conversion and distribution procedures
exist, and optimizations are based on measurements.

## v0.5.0 and later

Prioritize extensions by actual asset demand, the size and license of their dependencies, and the
cost of distributing them on every platform. Begin USD-to-glTF export only after defining a separate
specification and quality goals, without complicating the importer's verification or ABI boundary.

- `KHR_draco_mesh_compression`, decided independently of mesh quantization.
- Further material extensions, one pull request each after investigating renderer representation.
- Supplied and generated tangent frames for normal maps and anisotropy, including handedness,
  mirrored and nonuniform transforms, and agreement with a glTF reference renderer. Revise
  [ADR 0004](../adr/0004-normal-textures-and-tangents.md#tangents) before changing the frame contract.
- KTX 1.0 images, reaching consumers as standard images like KTX2. No glTF extension
  references KTX 1.0, so this first needs an ADR on how an asset would use them.

## v1.0.0 conditions

This does not mean all of glTF. The project can state what its compatibility guarantee covers (glTF
subset, OpenUSD, OS and compiler, ABI, image asset persistence and failure policy), with sufficient
regression tests and a reproducible release process.

## Decisions for later versions

Record the investigation and outcome in an ADR before relying on these:

- Mesh sharing and animated-node placement.
- Resource resolution through custom asset resolvers and shared parent directories, with its
  security model. [ADR 0025](../adr/0025-packaged-sources.md) decides only sources inside packages.
