# 0022: Iridescence

Decision record, 2026-11-08. It extends the networks of the
[factor-only decision](../architecture/MATERIAL_FACTORS.md) and
[ADR 0003](0003-pbr-texture-networks.md). [MATERIALS](../design/MATERIALS.md#iridescence)
specifies the result, and the [capability matrix](../reference/CAPABILITY_MATRIX.md) records evidence.

## Context

`KHR_materials_iridescence` changes the base reflection through a dielectric thin film. Its strength
is `iridescenceFactor` times the R channel of `iridescenceTexture`, defaulting to 0. The film's own
`iridescenceIor` defaults to 1.3 and is at least 1. Its thickness in nanometers interpolates from
`iridescenceThicknessMinimum` to `iridescenceThicknessMaximum` by the G channel of
`iridescenceThicknessTexture`, or uses the maximum without that texture. The endpoints default to
100 and 400, are nonnegative, and may descend. Both textures are linear data. The extension applies
to metals and dielectrics, below the sheen and clearcoat, and excludes unlit materials.

MaterialX `gltf_pbr` 1.39.5 has `iridescence`, uniform `iridescence_ior`, and
`iridescence_thickness`. Its graph mixes ordinary and thin-film generalized Schlick reflection by
the strength, separately for metals and dielectrics. Both thin-film lobes consume the thickness in
nanometers and film ior. Its thickness defaults to 100, unlike glTF's untextured 400.
UsdPreviewSurface has no thin-film inputs.

## Options

- Keep the extension unsupported despite MaterialX's matching inputs.
- Tint Preview's base color as a substitute. A constant tint cannot reproduce interference that
  depends on angle and changes the base material even where the film would not.
- Author MaterialX's inputs and let Preview draw the base, with a warning.

## Decision

Use `gltf_pbr`'s thin film on lit materials with a strength factor above 0:

| glTF | MaterialX | UsdPreviewSurface |
| --- | --- | --- |
| Factor times strength texture R | `iridescence`, non-color image and optional multiply | Ignored with a warning |
| Film ior | Uniform `iridescence_ior`, separate from the base `ior` | Ignored |
| Thickness without a texture | `iridescence_thickness` set to the maximum, including 0 | Ignored |
| Thickness with a texture | `ND_mix_float`, minimum on `bg`, maximum on `fg`, non-color image G on `mix` | Ignored |

Always author the active film's ior and thickness, avoiding the different shader thickness default.
A strength factor of 1 needs no multiply. Textures use the same UV sets, transforms and wrap modes as
other slots. Equal thickness endpoints fix the thickness and need no image or mix. A strength factor
of 0 reads neither image and authors no film; an unlit material does the same, warning that the
extension is ignored. These cases still validate values and the primitive's UV sets. An image that
is not read cannot fail import through image damage.

Optional and required extensions follow the same contract. Reject strengths outside 0 to 1, film
iors below 1, negative thicknesses and non-finite values. Reject a non-object extension, fields of
the wrong type, malformed texture info and numbers that overflow a float, naming the extension in
the diagnostic. Failed conversion publishes no partial layer; failed reload retains the prior layer.

## Verification and limits

- Generated GLTF and GLB fixtures check defaults, active and disabled films, boundary values,
  metals, unlit materials, uniform ior, all three image sources, raw channels, factor multiplication,
  descending ranges, UV transforms and wrap modes. Failures check diagnostics and layer preservation.
- The display check compares a black dielectric quad under a point light with and without a film,
  half strength against the mean of those reflections, different thicknesses and iors, each texture
  against its uniform equivalent, a descending thickness range and a film over metal. Metal uses a
  weaker light to keep the reflection below saturation. Preview must always match the bare base.
- The sample check compares film inputs and texture networks with the asset's own values.
- The renderer checks verify these relationships within MaterialX. They do not compare the BRDF,
  angular response or environment lighting against a glTF reference renderer, so they do not establish
  equivalent appearance. Other renderers may implement the thin-film lobe differently.

## Sources

- [Khronos extension specification](https://github.com/KhronosGroup/glTF/tree/main/extensions/2.0/Khronos/KHR_materials_iridescence)
- [Khronos property schema](https://github.com/KhronosGroup/glTF/blob/main/extensions/2.0/Khronos/KHR_materials_iridescence/schema/material.KHR_materials_iridescence.schema.json)
- [MaterialX 1.39.5 gltf_pbr](https://github.com/AcademySoftwareFoundation/MaterialX/blob/v1.39.5/libraries/bxdf/gltf_pbr.mtlx)
- [UsdPreviewSurface specification](https://openusd.org/release/spec_usdpreviewsurface.html)
