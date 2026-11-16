# Current

Unfinished work only; see the [task lifecycle](README.md#task-lifecycle). Tasks are in
implementation order, which investigation may change. Each feature follows the
[contract for adding a feature](../design/IMPLEMENTATION_POLICY.md#adding-a-gltf-feature).

## v0.11.0 — Material variants

- Evaluate how to map `KHR_materials_variants` to a USD variant set on `/Root`, so a scene-wide
  selection can change material bindings independently of the selected animation.
- Import material mappings in glTF and GLB. Cover the original material for unmapped primitives,
  shared mesh placements, multiple variants per mapping, invalid mappings and failed reloads.
- Compare variant selections and authored bindings with the Khronos samples. Verify representative
  switches in Storm with both shader paths, and update the capability matrix and sample baseline.

### Release criteria

- A user can switch between the original material bindings and imported material variants in USD.
- The [release gate](README.md#release-gate) is satisfied.
