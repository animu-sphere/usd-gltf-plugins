# 0023: Anisotropy

Decision record, 2026-11-08. It extends the material networks of
[ADR 0003](0003-pbr-texture-networks.md), with the renderer-derived tangent frame of
[ADR 0004](0004-normal-textures-and-tangents.md#tangents).
[MATERIALS](../design/MATERIALS.md#anisotropy) specifies the result, and the
[capability matrix](../reference/CAPABILITY_MATRIX.md) records the evidence.

## Context

`KHR_materials_anisotropy` elongates the base specular reflection in a direction in the mesh's
tangent/bitangent plane. `anisotropyStrength` defaults to 0 and is in 0 to 1; `anisotropyRotation`
defaults to 0 and is a counter-clockwise angle in radians, without a numeric range restriction.
The texture is linear data: B multiplies strength, and RG remaps from 0 to 1 into -1 to 1, supplying
a direction to rotate by the factor. Without a texture the direction is +X and the texture strength
is 1. The directional alpha roughness is `mix(roughness^2, 1, strength^2)`, and the perpendicular
alpha roughness stays `roughness^2`. The extension excludes unlit and specular-glossiness materials.

MaterialX `gltf_pbr` 1.39.5 has `anisotropy_strength`, `anisotropy_rotation` and `tangent`. Its graph
computes that roughness pair and rotates the tangent counter-clockwise about the shading normal,
using the radians-to-degrees conversion and MaterialX's rotation convention. The base metal,
dielectric, thin film and transmission consume the resulting tangent; the clearcoat keeps its own.
UsdPreviewSurface has no anisotropy input.

The importer retains TANGENT as `gltf_tangent`, but neither shading path consumes it. The mesh's
authored direction and W sign can therefore differ from the frame MaterialX derives. This matters
even on a flat quad: changing a connected tangent changes the reflection, whereas changing only
the retained glTF attribute does not. Node definitions alone cannot establish glTF appearance.

## Options

- Keep the extension unsupported until both shading paths can reproduce the supplied frame.
  Preview has no such interface, so this would also prevent using MaterialX's existing inputs.
- Change ordinary roughness or base color in Preview. Neither can represent a directional lobe.
- Author MaterialX's inputs with the existing renderer-derived frame, explicitly warning about
  that approximation and Preview's isotropic fallback.
- Add supplied and generated tangent frames to MaterialX, including handedness, transforms and
  normal-map consistency. This is a separate change to the frame contract of ADR 0004.

## Decision

Use MaterialX's inputs with the existing renderer-derived frame, and keep Preview's base material.
Warn about both the frame approximation and Preview's ignored anisotropy on each active material.
Do not claim that the result preserves supplied tangent directions or handedness.

| glTF | MaterialX | UsdPreviewSurface |
| --- | --- | --- |
| Strength without a texture | `anisotropy_strength` | Ignored with a warning |
| Strength with a texture | Linear image B times strength, omitting multiplication at 1 | Ignored |
| Rotation without a texture | `anisotropy_rotation`, reduced modulo a full turn | Ignored |
| Texture direction and rotation | `atan2(2G - 1, 2R - 1)` plus the reduced factor rotation | Ignored |

Reducing the finite factor angle keeps the shader's radians-to-degrees multiplication finite even
for a very large input. `atan2` needs no direction normalization. Select angle 0 for an exactly zero
RG vector, a deterministic +X fallback where a direction cannot be recovered. Both paths retain
base roughness and metallic values. Textures use the normal image-resolution path, UV sets,
transforms and wrap modes. UV transforms move samples, without rotating the encoded RG vectors.

A strength of 0 authors no anisotropy and reads no anisotropy image. An unlit material does the same,
with a warning. Both cases still validate input values and referenced UV sets. Optional and required
extensions follow the same contract. Reject strengths outside 0 to 1, non-finite rotations, numbers
that overflow a float, malformed texture info, non-object extensions and fields of the wrong type.
Failed import publishes no partial layer; failed reload preserves the prior layer.

## Verification and limits

- GLTF and GLB fixtures check defaults, boundaries, negative and large angles, metals, unlit
  materials, each image source, B strength multiplication, RG conversion, UV transforms and wrap
  modes. Invalid input checks include diagnostics and reload preservation.
- The display check compares zero and active strengths, opposite rotations, texture B against a
  uniform strength, and RG direction plus rotation against uniform angles in the same frame.
- The separate `probe_anisotropy.py` authors session-layer shader inputs after import, to distinguish
  renderer support from extension import and demonstrate the supplied-tangent limitation. Its
  connected tangent primvar is in world coordinates on an identity mesh; it does not test transforms.
- The sample check compares image networks and material inputs with each asset's values. It does
  not establish that the renderer-derived frame matches the sample's supplied frame.
- These renderer comparisons do not compare the angular BRDF, normal-mapped curved surfaces,
  mirrored frames or environment lighting with a glTF reference renderer. A renderer may derive a
  different frame or use another UV set; no such equivalence is promised.

## Sources

- [Khronos extension specification](https://github.com/KhronosGroup/glTF/tree/main/extensions/2.0/Khronos/KHR_materials_anisotropy)
- [Khronos property schema](https://github.com/KhronosGroup/glTF/blob/main/extensions/2.0/Khronos/KHR_materials_anisotropy/schema/material.KHR_materials_anisotropy.schema.json)
- [MaterialX 1.39.5 gltf_pbr](https://github.com/AcademySoftwareFoundation/MaterialX/blob/v1.39.5/libraries/bxdf/gltf_pbr.mtlx)
- [MaterialX 1.39.5 rotation implementation](https://github.com/AcademySoftwareFoundation/MaterialX/blob/v1.39.5/libraries/stdlib/genglsl/mx_rotate_vector3.glsl)
- [UsdPreviewSurface specification](https://openusd.org/release/spec_usdpreviewsurface.html)
