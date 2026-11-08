# 0021: Sheen

Decision record, 2026-11-08. It extends the networks of the
[factor-only decision](../architecture/MATERIAL_FACTORS.md), [ADR 0003](0003-pbr-texture-networks.md)
and [ADR 0020](0020-specular.md). [MATERIALS](../design/MATERIALS.md#sheen) specifies the result, and the
[capability matrix](../reference/CAPABILITY_MATRIX.md) records current evidence.

## Context

`KHR_materials_sheen` layers a back-scattering lobe for cloth over the base. Its color is
`sheenColorFactor` times the RGB of `sheenColorTexture`, which is sRGB-encoded, and its roughness
`sheenRoughnessFactor` times the A channel of `sheenRoughnessTexture`; each factor is 0 to 1 and 0 by
default, and a black color factor disables the layer. The lobe is the color times a "Charlie"
distribution of `alpha = sheenRoughness^2`, with the Charlie visibility as the reference and the simpler
`1 / (4 * (NdotL + NdotV - NdotL * NdotV))` allowed in its place, at the cost of energy conservation. The
base is dimmed by `1 - max3(sheenColor) * E`, the lobe's directional albedo, taken at the view and the
light or, more simply, at the view alone. A clearcoat is layered over the sheen. The extension must not
be used with `KHR_materials_unlit`.

What the two shaders can express:

- MaterialX `gltf_pbr` (1.39.5, the version OpenUSD 26.08 builds) has `sheen_color`, a color defaulting to
  black, and `sheen_roughness`, a float defaulting to 0. Its graph weights a `sheen_bsdf` by the color's
  largest component, tints it by the color over that, and gives it a roughness of `sheen_roughness`
  squared, then layers it over the base, under the clearcoat. MaterialX's GLSL `sheen_bsdf`, in its
  default `conty_kulla` mode, is the Charlie distribution with the simpler visibility, a roughness of at
  least 0.005, and a base dimmed by its weight times its albedo at the view, from a fit of its own lobe.
  Under environment light it adds the irradiance times that albedo.
- UsdPreviewSurface has no sheen and no input that could approximate one.

## Options

- **Keep the extension unsupported:** an optional sheen warns and is ignored, and a required one fails,
  though `gltf_pbr` has glTF's inputs.
- **Approximate it in Preview,** for example by lightening the diffuse color. A sheen is a grazing-angle
  lobe, so any constant change would be wrong head-on, where glTF's sheen adds nothing.
- **Author the color and roughness in `gltf_pbr`,** which layers glTF's lobe, and leave Preview without
  one, which then draws the base alone, as a client without the extension would.

## Decision

Author the sheen in MaterialX, on a material that is not unlit:

| glTF | `preview` (UsdPreviewSurface) | `mtlx` (`gltf_pbr`) |
| --- | --- | --- |
| `sheenColorFactor` | Not expressible; ignored with a warning | `sheen_color` |
| `sheenColorTexture` RGB | Not expressible; ignored | `sheen_color`, from an sRGB color image's RGB times the factor |
| `sheenRoughnessFactor` | Not expressible; ignored | `sheen_roughness` |
| `sheenRoughnessTexture` A | Not expressible; ignored | `sheen_roughness`, from a vector image's channel 3 times the factor |

The color is authored where its factor is not black, and the roughness where the color is and its factor
is not 0, the default of both glTF and `gltf_pbr`. In MaterialX a factor of 1 needs no multiply. Textures
follow the [texture transform](0011-texture-transform.md) and wrap modes of every slot. The inputs are
authored on a metal too, because glTF layers the sheen over the whole base.

A black color factor disables the layer, so neither texture is read; a roughness factor of 0 fixes the
roughness, so its texture is not read; and on an unlit material nothing is authored and no texture is
read, with a warning that the extension is ignored. A damaged image that is not read does not fail the
import. The values are still checked, and each texture's UV set must exist on the primitive, as for
unlit fallbacks.

A color component or a roughness outside 0 to 1 fails for the material that uses them. A value of the
extension that is not an object, a field of the wrong type, a `sheenColorFactor` that is not an array of
three numbers, and a number too large for a float fail with a diagnostic naming the extension. fastgltf
accepts a `sheenColorFactor` of fewer than three numbers, keeping 0 for the rest, so a sheen is checked
even when fastgltf accepts it, as a specular is.

## Evidence and limits

- Tests assert the MaterialX inputs and texture networks, their color spaces and channels, that Preview
  authors nothing, the defaults, a metal, an unlit material, textures that are not read, and each
  failure, optional and required. The sample check compares the sheen inputs and texture slots.
- The [display check](../reference/CAPABILITY_MATRIX.md#display-checks) renders a black quad of ior 1,
  which reflects nothing itself, under a directional light at 75 degrees from its normal, because a
  Charlie lobe adds nothing where the half vector is the normal. Through MaterialX it compares a color
  of 0.5 with half of white, a red color with red only, a rough sheen with a smoother one, and each
  texture with its factor; through Preview, every case with the quad without a sheen.
- `gltf_pbr`'s lobe uses the simpler visibility that glTF allows, and dims the base by its albedo at
  the view alone, so it is neither glTF's reference lobe nor energy conserving.
- Through UsdPreviewSurface no sheen is drawn: a cloth draws as its base.
- Other renderers may read `gltf_pbr`'s sheen inputs differently.
