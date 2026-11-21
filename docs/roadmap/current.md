# Current

Unfinished work only; see the [task lifecycle](README.md#task-lifecycle). Tasks are in
implementation order, which investigation may change. Each feature follows the
[contract for adding a feature](../design/IMPLEMENTATION_POLICY.md#adding-a-gltf-feature).

## v0.15.0 — GPU instancing

- Decide in an ADR how `EXT_mesh_gpu_instancing` maps to USD, for example a `UsdGeomPointInstancer`
  whose prototype is the node's mesh. Cover the TRANSLATION, ROTATION and SCALE attributes and their
  formats, custom `_` attributes, the node's own transform and children, skins and morph targets,
  material variants, and the fallback when the extension is required or optional.
- Import instances by that decision. Verify against the Khronos samples that use the extension, with
  a display check where display is claimed.

### Release criteria

- No Khronos sample file ignores `EXT_mesh_gpu_instancing`, and the behavior and its limits are
  documented and verified.
- The [release gate](README.md#release-gate) is satisfied.
