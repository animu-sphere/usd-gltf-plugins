# Current

Unfinished work only; see the [task lifecycle](README.md#task-lifecycle). Tasks are in
implementation order, which investigation may change. Each feature follows the
[contract for adding a feature](../design/IMPLEMENTATION_POLICY.md#adding-a-gltf-feature).

## v0.12.0 — Diffuse transmission

- Evaluate `KHR_materials_diffuse_transmission` against a pinned Khronos specification and the
  MaterialX and UsdPreviewSurface shader paths. Record the proposed representation and fallback
  in an ADR.
- If the representation passes the evaluation, import the factors and textures in glTF and GLB.
  Verify the authored data against the Khronos samples and check backlit controls in Storm.

### Release criteria

- The supported diffuse transmission behavior and shader limits are documented and verified.
- The [release gate](README.md#release-gate) is satisfied.
