# Current

Unfinished work only; see the [task lifecycle](README.md#task-lifecycle). Tasks are in
implementation order, which investigation may change. Each feature follows the
[contract for adding a feature](../design/IMPLEMENTATION_POLICY.md#adding-a-gltf-feature).

## v0.10.0 — Animated node visibility

- Run the release workflow dry run and repeat the display and sample checks on the commit to tag.

### Release criteria

- Both CubeVisibility and both LightVisibility files reproduce their visibility animations. Display
  checks verify the geometry and light transitions and camera access across the transitions.
- The [release gate](README.md#release-gate) is satisfied.
