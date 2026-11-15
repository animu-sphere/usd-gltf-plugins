# Current

Unfinished work only; see the [task lifecycle](README.md#task-lifecycle). Tasks are in
implementation order, which investigation may change. Each feature follows the
[contract for adding a feature](../design/IMPLEMENTATION_POLICY.md#adding-a-gltf-feature).

## v0.10.0 — Animated node visibility

- Evaluate authoring `KHR_animation_pointer` visibility channels as USD visibility time samples,
  including inheritance and switching between animation variants. Record the supported pointers,
  validation rules, and diagnostics for other pointers in optional and required declarations.
- Import visibility channels in glTF and GLB. Cover static defaults, step transitions, nested nodes,
  mixed clips, invalid targets and sampler data, and failed reloads.
- Compare authored samples with CubeVisibility and LightVisibility, verify transitions in Storm,
  and update the sample baseline and capability matrix.

### Release criteria

- Both CubeVisibility and both LightVisibility files reproduce their visibility animations. Display
  checks verify the geometry and light transitions and camera access across the transitions.
- The [release gate](README.md#release-gate) is satisfied.
