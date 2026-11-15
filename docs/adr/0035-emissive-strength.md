# 0035 Emissive strength

Decision record, 2026-11-15. The [material conventions](../design/MATERIALS.md#emissive-strength)
specify the mapping. The [capability matrix](../reference/CAPABILITY_MATRIX.md#display-checks)
records the renderer evidence.

## Context

The Khronos extension adds a scalar multiplier to the core glTF emission term. A material can
use it with its emissive factor alone or with a texture whose RGB is decoded from sRGB. It can
raise emission above 1 for HDR rendering. [Khronos specification][gltf]

MaterialX `gltf_pbr` already multiplies its `emissive` color by a uniform `emissive_strength`.
UsdPreviewSurface exposes a single `emissiveColor`, and `UsdUVTexture` can scale its sampled
color. [MaterialX 1.39.5 glTF PBR definition][mtlx] · [UsdPreviewSurface specification][preview]

## Decision

Use the native strength input in MaterialX to preserve the independent multiplier for editing.
Fold the multiplier into the color factor in Preview. Since the factor and multiplier are both
linear and uniform, combining them before storing the texture scale gives the same emitted RGB
as multiplying the decoded sample by each value in turn. The existing texture nodes still carry
the color space, UV selection, transforms and wrap modes. Any renderer that evaluates these
inputs can use the emitted color without another shader node or a rewritten texture.

Validate the extension in the input JSON, including the unlit exclusion when the object is
empty. Check the sign of the original number so that a negative value that rounds to zero
still fails. A malformed extension fails the import under the existing layer publication policy.
[Import pipeline](../architecture/IMPORT_PIPELINE.md)

## Display limits

The emission term supplies source color to the renderer. HDR output, tone mapping, bloom and
illumination from emissive surfaces depend on the renderer and its settings. The Storm display
comparison uses 8-bit output and compares each shader path with its own emission controls.

[gltf]: https://github.com/KhronosGroup/glTF/tree/main/extensions/2.0/Khronos/KHR_materials_emissive_strength
[mtlx]: https://github.com/AcademySoftwareFoundation/MaterialX/blob/v1.39.5/libraries/bxdf/gltf_pbr.mtlx
[preview]: https://openusd.org/release/spec_usdpreviewsurface.html
