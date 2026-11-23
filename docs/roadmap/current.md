# Current

Unfinished work only; see the [task lifecycle](README.md#task-lifecycle). Tasks are in
implementation order, which investigation may change. Each feature follows the
[contract for adding a feature](../design/IMPLEMENTATION_POLICY.md#adding-a-gltf-feature).

## v0.18.0 — Image-based lighting

- Decide in an ADR how `EXT_lights_image_based` maps to USD, for example as a `UsdLuxDomeLight` below
  the selected scene. Cover the prefiltered cubemap's faces and their orientation, and the conversion
  to the latlong texture that a dome light reads. Also cover mip levels beyond the first, RGBD PNG and
  LDR JPEG decoding, `rotation` against the dome's axis, `intensity` in lux, and the irradiance
  coefficients that a dome light cannot hold. State what a client without the extension shows, since
  the extension defines no fallback.
- Import image-based lighting by that decision, with a display comparison. Verify against
  EnvironmentTest's glTF-IBL file.

### Release criteria

- No Khronos sample file ignores `EXT_lights_image_based`, and the behavior and its limits are
  documented and verified.
- The [release gate](README.md#release-gate) is satisfied.
