# Current

Unfinished work only; see the [task lifecycle](README.md#task-lifecycle). Tasks are in
implementation order.

## v0.2.0: wider static scene support

- `KHR_texture_transform`, UV and material boundary conditions, and a policy
  for ignoring, approximating or rejecting optional extensions.
- `KHR_materials_unlit`, clearcoat, transmission, IOR and similar extensions,
  each as an individual unit of verification.
- WebP images through `EXT_texture_webp`, reaching consumers as standard images under
  the [image asset contract](../adr/0001-image-assets.md), with the decoder selected in
  an ADR.
- Cameras and `KHR_lights_punctual` through standard USD schemas.
- Wider external resource resolution through `ArResolver`, as need and safety
  are verified.
- Shared meshes and load performance in large scenes, based on measurements.

### Release criteria

Each feature has fixtures, tests of USD structure and
meaning, and renderer tests where display compatibility is claimed. The
capability matrix reflects the scope and constraints. The [release gate](README.md#release-gate) is satisfied.

Until then, use a pre-release such as `v0.2.0-alpha.1` rather than presenting
the work as complete. Do not claim support for all of glTF.
