# 0018: Transmission

Decision record, 2026-11-03. It extends the networks of the
[factor-only decision](../architecture/MATERIAL_FACTORS.md) and [ADR 0003](0003-pbr-texture-networks.md).
[MATERIALS](../design/MATERIALS.md#transmission) specifies the result, and the
[capability matrix](../reference/CAPABILITY_MATRIX.md) records current evidence.

## Context

`KHR_materials_transmission` lets light pass through the dielectric part of a material, as through
glass, which glTF's alpha cannot express: alpha is coverage, and removes the reflection with the
surface. Its amount is `transmissionFactor` times the R channel of `transmissionTexture`, 0 by
default and in 0 to 1. glTF mixes the dielectric's diffuse term toward a specular transmission tinted
by the base color, `mix(diffuse, specular_btdf * baseColor, transmission)`, under the same Fresnel
reflection; a metal transmits nothing. Without `KHR_materials_volume` the material is infinitely thin,
so transmitted light is not refracted. The transmission's roughness is the material's.

What the two shaders can express:

- MaterialX `gltf_pbr` (1.39.5, the version OpenUSD 26.08 builds) has `transmission`, defaulting to 0.
  Its graph mixes `diffuse_bsdf` toward a `dielectric_bsdf` in transmission mode, tinted by `base_color`
  with the material's `ior` and roughness, by `transmission`, under the dielectric reflection, and mixes
  that with the metal by `metallic`: glTF's structure. Its `thickness`, `attenuation_distance` and
  `attenuation_color` belong to `KHR_materials_volume`, and this graph reads none of them.
- Storm generates `gltf_pbr`'s GLSL with `hwTransmissionRenderMethod` set to `TRANSMISSION_OPACITY`
  (`hdSt/materialXFilter.cpp` for OpenUSD 26.08), and gives a `gltf_pbr` whose `transmission` is
  authored other than 0, or connected, the translucent material tag. With that method, MaterialX's
  `mx_surface_transmission` returns the tint, and the surface adds the transmission response to its
  transparency rather than its color. Storm's shader writes an alpha of 1 minus the mean of the
  transparency's channels, so the scene behind is blended through, not refracted or blurred, and a
  colored tint dims it equally in every channel.
- UsdPreviewSurface has no transmission. Its `opacity` in the default `transparent` mode, which Storm's
  `previewSurface.glslfx` for OpenUSD 26.08 applies to the diffuse term alone, keeping the specular one,
  looks like a thin transmission. This project already authors glTF's alpha there, under each alpha
  mode's rules.

## Options

- **Ignore the extension, with a warning,** which shows an opaque material in both paths.
- **Approximate it with Preview's `opacity`,** as 1 minus the transmission of the dielectric part, times
  alpha. Preview has no multiply node, so a texture or vertex alpha could not be combined with it; it
  would make OPAQUE materials translucent and change what MASK cuts; and it would lose the tint.
- **Author `transmission` in MaterialX,** where `gltf_pbr` has glTF's structure, and ignore it in
  Preview with a warning, keeping Preview's opacity as glTF's alpha.

## Decision

Author `transmission` in MaterialX, when the material has a transmission factor above 0 and is not
unlit:

| glTF | `preview` (UsdPreviewSurface) | `mtlx` (`gltf_pbr`) |
| --- | --- | --- |
| `transmissionFactor` | Not expressible; ignored with a warning | `transmission` |
| `transmissionTexture` R | Not expressible; ignored | `transmission`, from a vector image's channel 0 times the factor |
| No `KHR_materials_volume` | | `thickness` keeps its default of 0 |

In MaterialX a factor of 1 needs no multiply. The texture follows the
[texture transform](0011-texture-transform.md) and wrap modes of every slot. At a factor of 0, and on
an unlit material, nothing is authored and the texture is not resolved, so a damaged image there does
not fail the import; an unlit material warns that the extension is ignored. Their values are still
checked, and the texture's UV set must exist on the primitive, as for unlit fallbacks. Alpha, its mode
and cutoff apply as without the extension, in both paths.

A factor outside 0 to 1 fails for the material that uses it. A value of the extension that is not an
object, a field of the wrong type, and a number too large for a float fail with a diagnostic naming
the extension, where fastgltf gave none.

## Evidence and limits

- Tests assert the MaterialX input and its texture network, that Preview authors nothing, a disabled
  transmission, an unlit material, and each failure, optional and required. The sample check compares
  each transmission and its texture slot.
- The [display check](../reference/CAPABILITY_MATRIX.md#display-checks) renders a white quad in front of
  a green emissive backdrop in darkness, with transmissions of 0, 0.5 and 1, an image, a metal and a red
  tint.
- The equivalence with glTF is read from `gltf_pbr`'s source, not displayed. Through Storm, the
  transmission is transparency: the scene behind is not refracted, not blurred by roughness, and is
  dimmed by the mean of the tint rather than filtered by it, so a red glass passes some green. Storm
  sorts and blends it with other translucent surfaces as it does alpha.
- A MaterialX renderer that refracts may bend light by `ior` through `gltf_pbr`'s transmission, where
  glTF's thin material does not.
- Through UsdPreviewSurface a transmissive material is opaque, unless its alpha says otherwise.
- `KHR_materials_volume` is not implemented, so a thick, absorbing material is drawn as a thin one.
