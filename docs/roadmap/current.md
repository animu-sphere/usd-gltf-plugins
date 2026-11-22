# Current

Unfinished work only; see the [task lifecycle](README.md#task-lifecycle). Tasks are in
implementation order, which investigation may change. Each feature follows the
[contract for adding a feature](../design/IMPLEMENTATION_POLICY.md#adding-a-gltf-feature).

## v0.16.0 — Animated material properties

- Decide in an ADR how `KHR_animation_pointer` channels that target material properties map to USD,
  for example time samples on the inputs of both shader networks inside the `animation` variants.
  Cover factors such as `baseColorFactor` and `emissiveFactor`, `KHR_texture_transform` offset,
  rotation and scale, values that UsdPreviewSurface folds into constants or primvars, material copies
  such as the vertex color and unlit variants, pointers to properties the importer does not author,
  and interpolation.
- Animate material properties by that decision. Verify against AnimatedColorsCube,
  AnimationPointerUVs and PotOfCoalsAnimationPointer, with a display check over time where display
  is claimed.

### Release criteria

- No Khronos sample file ignores a `KHR_animation_pointer` channel to a material property that the
  importer authors, and the behavior and its limits are documented and verified.
- The [release gate](README.md#release-gate) is satisfied.
