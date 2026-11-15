# Current

Unfinished work only; see the [task lifecycle](README.md#task-lifecycle). Tasks are in
implementation order, which investigation may change. Each feature follows the
[contract for adding a feature](../design/IMPLEMENTATION_POLICY.md#adding-a-gltf-feature).

## v0.8.0: Emissive strength

- Evaluate [`KHR_materials_emissive_strength`](https://github.com/KhronosGroup/glTF/tree/main/extensions/2.0/Khronos/KHR_materials_emissive_strength)
  in MaterialX and UsdPreviewSurface. Document how each shader path combines the multiplier with
  the emissive factor and linearized texture, and record any renderer limits.
- Import the extension when required or optional. Cover missing strength, zero, values above one,
  factor-only and textured materials, invalid values, and the exclusion with `KHR_materials_unlit`.
  Verify the authored values in both shader paths and that a failed reload preserves the layer.
- Compare Storm output with equivalent emission controls for both shader paths. Check emissive
  values in the Khronos samples and update the sample baseline and capability matrix.

### Release criteria

- Both PlaysetLightTest files open, their authored emission agrees with glTF, and they draw in
  Storm through both shader paths.
- The [release gate](README.md#release-gate) is satisfied.
