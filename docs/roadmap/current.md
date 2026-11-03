# Current

Unfinished work only; see the [task lifecycle](README.md#task-lifecycle). Tasks are in
implementation order, which investigation may change. Each feature follows the
[contract for adding a feature](../design/IMPLEMENTATION_POLICY.md#adding-a-gltf-feature).

## v0.2.0: wider static scene support

### First

- `KHR_lights_punctual`: directional, point and spot through `UsdLux`, stating units, intensity,
  attenuation and cone conversion. USD data tests and display tests are separate.

### Next

- WebP images through `EXT_texture_webp`, reaching consumers as standard images under the
  [image asset contract](../adr/0001-image-assets.md), with the decoder selected in an ADR. Covers
  fallback selection, MIME type and signature, size limits, damaged data, embedded and external
  images, moving, referencing and reloading, and third-party licenses.
- `KHR_materials_unlit`, checked per renderer. A path that cannot express it warns or is recorded as
  a limit.
- `KHR_materials_ior`, stating its relation to the existing PBR parameters, in both networks.

### If measurements and time allow

Each of these moves to the [backlog](backlog.md) if it does not fit this version.

- `KHR_materials_clearcoat`, `KHR_materials_transmission` and similar extensions, one pull request
  each, after investigating what renderers can express.
- Wider external resource resolution through `ArResolver`, after an ADR decides the scenarios and
  the security model.
- Measurements of load performance and mesh duplication in large scenes, weighing sharing against
  its complexity.

### Release criteria

Each adopted feature has fixtures, tests of USD structure and meaning, tests of failure, and renderer
tests where display compatibility is claimed. Sample assets are recorded as opened, verified in USD
meaning and rendered, each separately, so that opening does not imply support. The capability matrix
and release record agree on the scope and constraints. The [release gate](README.md#release-gate) is
satisfied.

Until then, use a pre-release such as `v0.2.0-alpha.1` rather than presenting the work as complete,
and narrow the scope or wait. Do not claim support for all of glTF.
