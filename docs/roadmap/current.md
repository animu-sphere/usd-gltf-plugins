# Current

Unfinished work only; see the [task lifecycle](README.md#task-lifecycle). Tasks are in
implementation order, which investigation may change. Each feature follows the
[contract for adding a feature](../design/IMPLEMENTATION_POLICY.md#adding-a-gltf-feature).

## v0.9.0 — Node visibility

### Release criteria

- Both CubeVisibility and both LightVisibility files open and preserve their static visibility.
  Display checks verify hidden geometry and light behavior and access to cameras below hidden nodes.
- The [release gate](README.md#release-gate) is satisfied.
