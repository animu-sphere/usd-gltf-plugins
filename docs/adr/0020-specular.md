# 0020: Specular

Decision record, 2026-11-08. It extends the networks of the
[factor-only decision](../architecture/MATERIAL_FACTORS.md), [ADR 0003](0003-pbr-texture-networks.md)
and [ADR 0016](0016-ior.md). [MATERIALS](../design/MATERIALS.md#specular) specifies the result, and the
[capability matrix](../reference/CAPABILITY_MATRIX.md) records current evidence.

## Context

`KHR_materials_specular` changes the reflection of a material's dielectric part. Its weight is
`specularFactor` times the A channel of `specularTexture`, in 0 to 1 and 1 by default, and its color
`specularColorFactor` times the RGB of `specularColorTexture`, which is sRGB-encoded; the color factor
is at least 0, with no upper bound, and white by default. glTF's dielectric F0 is
`min(((ior - 1) / (ior + 1))^2 * color, 1) * weight` and its F90 is `weight`, so a weight of 0 removes
the reflection and a color above 1 can raise F0 beyond the ior's. A metal is unaffected.

What the two shaders can express:

- MaterialX `gltf_pbr` (1.39.5, the version OpenUSD 26.08 builds) has `specular`, a float defaulting to
  1, and `specular_color`, a color defaulting to white. Its graph computes the dielectric's F0 as
  `min(F0 of the ior * specular_color, 1) * specular` and its F90 as `specular`, glTF's terms, for both
  its reflection and its iridescent reflection; the metal's lobe reads neither.
- Storm's UsdPreviewSurface for OpenUSD 26.08 has `useSpecularWorkflow` and `specularColor`. In the
  metallic workflow, which every material here uses, F0 comes from `ior` and the base color, and
  `specularColor` is ignored. The specular workflow takes `specularColor` as F0 with an F90 of 1, and
  ignores `metallic`. UsdPreviewSurface has no multiply node, so two textures cannot be combined, and
  a `UsdUVTexture` scale cannot clamp.

## Options

- **Keep the extension unsupported:** an optional specular warns and is ignored, and a required one
  fails, though `gltf_pbr` has glTF's inputs.
- **Switch Preview to the specular workflow** for a material of metallic 0, with `specularColor` the F0
  that glTF computes from the factors. The F90 would stay 1 where glTF's is the weight, so a weight of 0
  would still reflect at grazing angles; textures could not be multiplied together or clamped; a
  metallic texture or factor would have to fall back to the metallic workflow; and the material's
  network would change shape with its values. It would be exact only for factors, at a weight of 1.
- **Author the weight and color in `gltf_pbr`,** which computes glTF's terms, and leave Preview in the
  metallic workflow, which then draws the default specular that glTF specifies for a client without the
  extension.

## Decision

Author the specular in MaterialX, on a material that is not unlit:

| glTF | `preview` (UsdPreviewSurface) | `mtlx` (`gltf_pbr`) |
| --- | --- | --- |
| `specularFactor` | Not expressible; ignored with a warning | `specular` |
| `specularTexture` A | Not expressible; ignored | `specular`, from a vector image's channel 3 times the factor |
| `specularColorFactor` | Not expressible; ignored | `specular_color` |
| `specularColorTexture` RGB | Not expressible; ignored | `specular_color`, from an sRGB color image's RGB times the factor |

Each input is authored only where it differs from the default of 1: where its factor is not 1 or it
has a texture. In MaterialX a factor of 1 needs no multiply. Textures follow the
[texture transform](0011-texture-transform.md) and wrap modes of every slot. The inputs are authored
on a metal too, because `gltf_pbr`, as glTF, applies them only to the dielectric part, whose share a
metallic texture can vary.

A weight of 0 fixes the weight and removes the color's effect, so neither texture is read; a color
factor of 0 fixes the color, so its texture is not read; and on an unlit material nothing is authored
and no texture is read, with a warning that the extension is ignored. A damaged image that is not read
does not fail the import. The values are still checked, and each texture's UV set must exist on the
primitive, as for unlit fallbacks.

A weight outside 0 to 1 and a negative color component fail for the material that uses them. A value
of the extension that is not an object, a field of the wrong type, a `specularColorFactor` that is not
an array of three numbers, and a number too large for a float fail with a diagnostic naming the
extension. fastgltf accepts a `specularColorFactor` of fewer than three numbers, keeping 1 for the
rest, so a specular is checked even when fastgltf accepts it, as a volume is.

## Evidence and limits

- Tests assert the MaterialX inputs and texture networks, their color spaces and channels, that
  Preview authors nothing, the defaults, a metal, an unlit material, textures that are not read, and
  each failure, optional and required. The sample check compares each specular input and texture slot.
- The [display check](../reference/CAPABILITY_MATRIX.md#display-checks) renders the ior check's black
  quad and compares, through MaterialX, a weight of 0.5 and a color of 2 with the iors that give the
  same F0, a weight of 0 with black, a red color with red only, and each texture with its factor;
  through Preview, every case with the default.
- Through UsdPreviewSurface every specular draws as glTF's default: a material whose specular dims or
  tints its reflection draws a white reflection of the ior's F0.
- `gltf_pbr` dims its base by the reflection's directional albedo rather than by glTF's per-direction
  Fresnel term, as for [the clearcoat](0017-clearcoat.md#evidence-and-limits).
- Other renderers may read `gltf_pbr`'s specular inputs differently.
