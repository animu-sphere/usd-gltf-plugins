# Current

Unfinished work only; see the [task lifecycle](README.md#task-lifecycle). Tasks are in
implementation order, which investigation may change. Each feature follows the
[contract for adding a feature](../design/IMPLEMENTATION_POLICY.md#adding-a-gltf-feature).

## v0.18.0 — Image-based lighting

- Import image-based lighting as [ADR 0046](../adr/0046-image-based-lighting.md) decides, vendoring
  stb_image for the faces, with a display comparison. Verify against EnvironmentTest's glTF-IBL file.

### Release criteria

- No Khronos sample file ignores `EXT_lights_image_based`, and the behavior and its limits are
  documented and verified.
- The [release gate](README.md#release-gate) is satisfied.
