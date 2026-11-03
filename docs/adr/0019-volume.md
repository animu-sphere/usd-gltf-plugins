# 0019: Volume

Decision record, 2026-11-03. It extends the networks of [ADR 0018](0018-transmission.md).
[MATERIALS](../design/MATERIALS.md#volume) specifies the result, and the
[capability matrix](../reference/CAPABILITY_MATRIX.md) records current evidence.

## Context

`KHR_materials_volume` gives the medium behind a transmissive surface a thickness and an absorption, so
that light passing through is refracted and attenuated, as through a glass block or colored liquid.
Its thickness is `thicknessFactor` times the G channel of `thicknessTexture`, at least 0 and 0 by
default, in the coordinate space of the mesh; a thickness of 0 is glTF's thin material, without
refraction. Light travelling a distance `d` through the medium keeps `attenuationColor ^ (d /
attenuationDistance)` of itself. `attenuationColor` is in 0 to 1 and white by default, and
`attenuationDistance` is above 0 and infinite by default, in world space; either default attenuates
nothing. The extension only applies to light that the surface transmits, through
`KHR_materials_transmission` or `KHR_materials_diffuse_transmission`; on any other material it has no
effect.

What the two shaders can express:

- MaterialX `gltf_pbr` (1.39.5, the version OpenUSD 26.08 builds) has `thickness`, defaulting to 0,
  and the uniform `attenuation_distance`, without a default, and `attenuation_color`, defaulting to
  white: glTF's parameters by name and meaning. Its graph computes an absorption of
  `-ln(attenuation_color) / attenuation_distance` into an `anisotropic_vdf`, and connects that node to
  nothing; no node reads `thickness`. The same holds on MaterialX's main branch on the day of this
  record. So no renderer that uses this graph draws the volume, and a renderer that refracts through
  `gltf_pbr`'s transmission does so whatever the thickness.
- Storm draws `gltf_pbr`'s transmission as transparency, without refraction
  ([ADR 0018](0018-transmission.md)), so it could not draw a volume either.
- UsdPreviewSurface has no transmission and no volume.

## Options

- **Keep the extension unsupported:** an optional volume warns and is ignored, and a required one
  fails, though glTF specifies the thin material as what a client without it shows, and that is what
  every path here draws anyway.
- **Approximate the attenuation by tinting the base color** by `attenuationColor ^ (thickness /
  attenuationDistance)`. That also tints the diffuse and reflected light, which the medium does not
  touch; the path length through a mesh is not its thickness; and Storm averages the tint
  ([ADR 0018](0018-transmission.md)). It would look like a volume without being one.
- **Author the volume in `gltf_pbr`'s inputs,** which are glTF's, and state that no renderer in reach
  draws them, so the result is drawn as the thin material that glTF specifies for a client without the
  extension.

## Decision

Author the volume in MaterialX, when the material has a transmission above 0, a thickness factor above
0, and is not unlit:

| glTF | `preview` (UsdPreviewSurface) | `mtlx` (`gltf_pbr`) |
| --- | --- | --- |
| `thicknessFactor` | Not expressible; ignored with a warning | `thickness` |
| `thicknessTexture` G | Not expressible; ignored | `thickness`, from a vector image's channel 1 times the factor |
| `attenuationDistance`, when given | Not expressible; ignored | `attenuation_distance`, uniform |
| `attenuationColor`, when a distance is given | Not expressible; ignored | `attenuation_color`, uniform |

In MaterialX a factor of 1 needs no multiply. The texture follows the
[texture transform](0011-texture-transform.md) and wrap modes of every slot. Without an
`attenuationDistance`, glTF's infinite default attenuates nothing, so neither attenuation input is
authored: `attenuation_distance` has no default, and leaving both out keeps an infinite value out of
the MaterialX document.

At a thickness factor of 0, on a material without a transmission above 0, and on an unlit material,
nothing is authored and the texture is not resolved, so a damaged image there does not fail the import;
an unlit material warns that the extension is ignored. The values are still checked, and the texture's
UV set must exist on the primitive, as for unlit fallbacks.

A negative thickness factor, an attenuation distance of 0 or less, and an attenuation color with a
component outside 0 to 1 fail for the material that uses them. A value of the extension that is not an
object, a field of the wrong type, an `attenuationColor` that is not an array of three numbers, and a
number too large for a float fail with a diagnostic naming the extension. fastgltf accepts an
`attenuationColor` of fewer than three numbers, keeping 1 for the rest, so a volume is checked even when
fastgltf accepts it.

## Evidence and limits

- Tests assert the MaterialX inputs, their variability and the texture network, that Preview authors
  nothing, a thin volume, a volume without a transmission, an unlit material, and each failure,
  optional and required. The sample check compares each volume and its texture slot.
- The [display check](../reference/CAPABILITY_MATRIX.md#display-checks) renders the transmission
  check's quad with a thickness, a red attenuation and a thickness image, and expects each to look like
  the thin quad in both paths. That shows that the authored inputs do not break the material, not that
  a volume is drawn.
- No renderer in reach draws the volume: through MaterialX 1.39.5's `gltf_pbr` and through
  UsdPreviewSurface, a thick, absorbing material looks thin and clear, as glTF's fallback does. A
  renderer that implements `gltf_pbr` itself, or a later MaterialX that connects the volume, may draw it.
- glTF gives the thickness in the mesh's space and the attenuation distance in world space, in meters.
  A stage that references the asset with a scale or another `metersPerUnit` scales the mesh, and with it
  the thickness, but nothing converts the distance.
- `KHR_materials_diffuse_transmission` is not implemented, so a volume behind a diffuse transmission is
  ignored with it.
