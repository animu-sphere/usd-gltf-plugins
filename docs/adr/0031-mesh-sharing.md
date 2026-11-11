# 0031 Mesh sharing

Decision record, 2026-11-11. The [capability matrix](../reference/CAPABILITY_MATRIX.md#mesh-sharing-experiment)
records the measurements and verification.

## Context

The importer converts each glTF mesh once and uses `SdfCopySpec` for additional placements.
[ADR 0024](0024-import-measurements.md) identified repeated point and topology arrays in
ABeautifulGame. It also distinguished the bytes read from each mesh occurrence from the bytes
the stage holds in memory. The experiment compared stage opening on that asset with two ways of
sharing entire mesh definitions.

## Options tested

- Keep an authored mesh subtree at each placement, as the importer already does.
- For each source mesh index used by several nodes, author one subtree under a class prim
  at `/Root/MeshLibrary`. Add an internal reference at each existing `Mesh_{index}` path.
  Keep the material networks in `/Root/Materials` and the bindings on the shared primitives.
  OpenUSD skips abstract class descendants during ordinary traversal, so the shared definitions
  need no `visibility`, `active` or `purpose` opinions. See the [OpenUSD class documentation](https://openusd.org/release/glossary.html#class).
- Use the same internal references and make each placement instanceable. The child primitives
  become instance proxies. Consumers must opt in to traversing them, and a local material edit
  below an instance requires disabling instancing on that placement. See the
  [OpenUSD instancing documentation](https://openusd.org/release/api/_usd__page__scenegraph_instancing.html).

The prototypes applied sharing only to repeated meshes on the static sample. Unique meshes
were authored directly below their nodes. The experiment used the normal conversion path and
measured `Usd.Stage.Open` with `tests/measure_import.py`.

## Decision

Keep an authored mesh subtree at each placement. The measured ranges for stage opening and
peak resident memory overlapped across all three builds. Internal references would add a new
composition dependency for each placement. Native instancing would also change ordinary stage
traversal and restrict local edits to mesh primitives.

Revisit sharing when a representative workload shows a measurable benefit. A future proposal
must verify material overrides, skin bindings and blend shapes per placement as well as
composition through the asset's default prim. A native instancing proposal also needs to
account for consumers that use ordinary traversal and for renderer support.
