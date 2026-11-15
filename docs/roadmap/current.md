# Current

Unfinished work only; see the [task lifecycle](README.md#task-lifecycle). Tasks are in
implementation order, which investigation may change. Each feature follows the
[contract for adding a feature](../design/IMPLEMENTATION_POLICY.md#adding-a-gltf-feature).

## v0.9.0 — Node visibility

- Evaluate the USD representation of static
  [`KHR_node_visibility`](https://github.com/KhronosGroup/glTF/tree/main/extensions/2.0/Khronos/KHR_node_visibility)
  for geometry and punctual lights, including camera access below hidden nodes and inheritance
  when a child requests visibility below a hidden parent. Record the decision and renderer limits.
- Import optional and required declarations in glTF and GLB. Cover the default, explicit booleans,
  nested nodes, shared meshes, lights and cameras, invalid values and failed reloads. Apply the
  optional-extension policy to visibility animation channels in the sample files.
- Compare authored visibility with the sample glTF. Check geometry and light contributions in
  Storm, and update the sample baseline and capability matrix.

### Release criteria

- Both CubeVisibility and both LightVisibility files open and preserve their static visibility.
  Display checks verify hidden geometry and light behavior and access to cameras below hidden nodes.
- The [release gate](README.md#release-gate) is satisfied.
