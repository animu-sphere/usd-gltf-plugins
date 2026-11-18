# Current

Unfinished work only; see the [task lifecycle](README.md#task-lifecycle). Tasks are in
implementation order, which investigation may change. Each feature follows the
[contract for adding a feature](../design/IMPLEMENTATION_POLICY.md#adding-a-gltf-feature).

## v0.13.0 — Core geometry gaps

- Evaluate morph target TANGENT deltas. UsdSkel blend shapes carry only point and normal offsets,
  so record in an ADR how a retained or generated tangent relates to a morphed mesh, then import
  AnimatedMorphCube by that decision.

### Release criteria

- No Khronos sample file is rejected for sparse accessors or morph target TANGENT deltas, and the
  behavior and its limits are documented and verified.
- The [release gate](README.md#release-gate) is satisfied.
