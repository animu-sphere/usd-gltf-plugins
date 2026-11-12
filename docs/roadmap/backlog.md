# Backlog

Later versions, in planned order. Nothing here has started; see the
[task lifecycle](README.md#task-lifecycle). The next release is in
[current.md](current.md).

## Later versions

Prioritize extensions by actual asset demand, the size and license of their dependencies, and the
cost of distributing them on every platform. Begin USD-to-glTF export only after defining a separate
specification and quality goals, without complicating the importer's verification or ABI boundary.

- Further material extensions, one pull request each after investigating renderer representation.
- KTX 1.0 images, reaching consumers as standard images like KTX2. No glTF extension
  references KTX 1.0, so this first needs an ADR on how an asset would use them.

## v1.0.0 conditions

This does not mean all of glTF. The project can state what its compatibility guarantee covers (glTF
subset, OpenUSD, OS and compiler, ABI, image asset persistence and failure policy), with sufficient
regression tests and a reproducible release process.

## Decisions for later versions

Record the investigation and outcome in an ADR before relying on these:

- Importing several scenes of one file, and how a user chooses between them. Only the selected scene
  is authored until then.
- Resource resolution through custom asset resolvers and shared parent directories, with its
  security model. [ADR 0025](../adr/0025-packaged-sources.md) decides only sources inside packages.
