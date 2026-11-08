# Material design

Each glTF material maps to one `UsdShadeMaterial` under `/Asset/mtl` with
both a MaterialX `gltf_pbr`-based path and a UsdPreviewSurface
fallback. Use render-context-specific surface outputs as appropriate; verify
the actual output names and connections against the targeted OpenUSD and
MaterialX versions. Standard schemas and shaders must suffice: no custom
schema, shader, or imaging plugin is required.

The initial factor-only mapping and its current limitations are recorded in
the [material factor decision](../architecture/MATERIAL_FACTORS.md), and textures
in the [base color](../adr/0002-base-color-texture-networks.md),
[other PBR texture](../adr/0003-pbr-texture-networks.md) and
[normal texture](../adr/0004-normal-textures-and-tangents.md) decisions.

Share image data and conversion parameters where possible, but use separate
nodes where the two paths require incompatible node interfaces. The
existence of a node definition does not establish renderer support: verify
rendering in each target renderer as well as network structure, and keep USD
graph tests separate from rendering checks.

Convert fastgltf materials to a small internal PBR representation, then branch
to the MaterialX and UsdPreviewSurface networks, rather than copying the whole
glTF data model.

| glTF input | Intended mapping / consideration |
| --- | --- |
| Base color factor and texture | Base/diffuse color and alpha |
| Metallic and roughness factors | Metallic and roughness |
| Metallic-roughness texture | G = roughness, B = metallic |
| Normal texture | Tangent-space normal map, with its scale on X and Y, in a frame [the renderer derives](../adr/0004-normal-textures-and-tangents.md#tangents) |
| Occlusion texture | R channel and strength, connected to each shader's occlusion input without compensating for [how renderers apply it](../adr/0003-pbr-texture-networks.md#occlusion-differs-between-the-paths) |
| Emissive factor and texture | Emissive color / emission |
| Alpha mode and cutoff | MaterialX `alpha`, `alpha_mode` and `alpha_cutoff`; Preview `opacity` and, for MASK, `opacityThreshold`. OPAQUE ignores alpha. A MASK cutoff of 0 is opaque in Preview, whose threshold of 0 means no masking |
| `doubleSided` | Keep UsdGeom sidedness and material behavior consistent |

For vertex colors, multiply base color by the linear `displayColor` primvar
and use the retained `gltf_colorAlpha` primvar when present (otherwise 1)
for alpha, subject to the material's alpha mode. MaterialX multiplies in its
graph. UsdPreviewSurface has no multiply node and reads the per-mesh products
described in the [scene conventions](SCENE_CONVENTIONS.md#vertex-colors), which
are exact because scaling by a constant does not change linear interpolation.
A material used with and without COLOR_0 needs two graphs and so two `Material`
prims. A texture multiplied by COLOR_0 cannot be baked this way: MaterialX
multiplies it exactly, and Preview omits COLOR_0 with a warning. See
[ADR 0002](../adr/0002-base-color-texture-networks.md).

Distinguish color data (base color and emissive) from non-color data
(metallic, roughness, normal and occlusion): Preview reads the latter as `raw`, and
MaterialX through [vector images](../adr/0003-pbr-texture-networks.md#non-color-images-in-materialx).
Texture readers consume the
primvar's [converted coordinates](SCENE_CONVENTIONS.md#texture-coordinates)
without another flip. `KHR_texture_transform` applies between the reader and the image, as in
[ADR 0011](../adr/0011-texture-transform.md). Document observed differences from glTF shading and
renderer-specific limitations.

## Unlit materials

A `KHR_materials_unlit` material emits its base color term, the same graph of factor, texture and
vertex color, through UsdPreviewSurface `emissiveColor` and MaterialX `emissive`, and zeroes what a
light would add: Preview is a black metal of roughness 1, and `gltf_pbr` a black dielectric with
`specular` 0. Alpha, its mode and cutoff, and `doubleSided` apply as to any material. The fallback
metallic-roughness, normal, occlusion and emissive properties are not authored. See
[ADR 0015](../adr/0015-unlit.md).

## Index of refraction

A `KHR_materials_ior` value other than 1.5 is authored as the `ior` input of both UsdPreviewSurface and
`gltf_pbr`, uniform in the latter, whose dielectric F0 is `((ior - 1) / (ior + 1))^2` as in glTF. It
does not change metallic, roughness, `specular` or any other input. An ior of 0, whose Fresnel term is
1 at every angle, is authored as 1000000, and a value below 1 other than 0 fails. An unlit material
authors none. See [ADR 0016](../adr/0016-ior.md).

## Specular

A `KHR_materials_specular` weight is authored as MaterialX `specular`, the factor times the A channel of
its texture, read as non-color data, and its color as `specular_color`, the factor times the RGB of its
texture, read as sRGB like base color. Each is authored only where it differs from the default of 1.
`gltf_pbr` computes glTF's dielectric F0 of `min(F0 of the ior * color, 1) * weight` and F90 of
`weight`, and leaves the metal's lobe alone. UsdPreviewSurface's metallic workflow has no specular
weight or color, so its network ignores the extension with a warning and draws glTF's default specular.
A weight of 0 reads neither texture, a color factor of 0 does not read the color texture, and an unlit
material authors none. See [ADR 0020](../adr/0020-specular.md).

## Iridescence

A `KHR_materials_iridescence` factor above 0 is authored as MaterialX `iridescence`, the factor times
its texture's R channel, read as non-color data. The film's `iridescenceIor` is the uniform
`iridescence_ior`, separate from the base `ior`. `iridescence_thickness` is the maximum thickness in
nanometers without a texture; with one, an `ND_mix_float` interpolates the minimum and maximum by its
G channel, also non-color data. A descending range is valid, and equal endpoints read no thickness
image. Always author the active film's thickness, because `gltf_pbr` defaults to 100 where glTF's
untextured film defaults to 400. UsdPreviewSurface has no thin film and ignores it with a warning.
A strength factor of 0 and an unlit material author no film and read neither image. See
[ADR 0022](../adr/0022-iridescence.md).

## Anisotropy

A `KHR_materials_anisotropy` strength above 0 is authored as MaterialX `anisotropy_strength`,
the factor times its texture's B channel, read as non-color data. `anisotropy_rotation` is the
counter-clockwise factor angle in radians, reduced modulo a full turn to keep shader arithmetic
finite, plus `atan2(2G - 1, 2R - 1)` when a texture supplies its direction. A zero direction uses
angle 0. Without a texture, strength and rotation are uniform values. UV transforms move samples
without rotating the encoded direction vectors. UsdPreviewSurface has no anisotropy and ignores
it with a warning. MaterialX uses the existing renderer-derived tangent frame, with a warning:
supplied glTF tangents and their handedness are retained as data but do not control shading, so
the resulting direction can differ from glTF. A strength of 0 and an unlit material author no
anisotropy and read no anisotropy image. See [ADR 0023](../adr/0023-anisotropy.md).

## Sheen

A `KHR_materials_sheen` color factor other than black is authored as MaterialX `sheen_color`, the factor
times the RGB of its texture, read as sRGB like base color, and a roughness factor other than 0 as
`sheen_roughness`, the factor times the A channel of its texture, read as non-color data. `gltf_pbr`
weights its sheen lobe by the color's largest component, gives it a Charlie roughness of
`sheen_roughness` squared, and layers it over the base and under the clearcoat, as glTF does.
UsdPreviewSurface has no sheen, so its network ignores the extension with a warning and draws the base
alone. A black color factor reads neither texture, a roughness factor of 0 does not read the roughness
texture, and an unlit material authors none. See [ADR 0021](../adr/0021-sheen.md).

## Clearcoat

A `KHR_materials_clearcoat` layer with a factor above 0 is authored as UsdPreviewSurface `clearcoat`
and `clearcoatRoughness`, and MaterialX `clearcoat`, `clearcoat_roughness` and `clearcoat_normal`. Its
weight is the factor times the R channel of its texture, and its roughness the factor times the G
channel of its own, read as non-color data like metallic and roughness; both roughness inputs are
always authored, because Preview's default is 0.01. `clearcoatNormalTexture` is a normal map on
`clearcoat_normal` only: Preview has no clearcoat normal, so its coat follows the base normal and the
texture is ignored with a warning. Without that texture, `gltf_pbr`'s coat uses the geometric normal,
as glTF's does. A factor of 0 and an unlit material author nothing. See
[ADR 0017](../adr/0017-clearcoat.md) for how each shader layers the coat.

## Transmission

A `KHR_materials_transmission` factor above 0 is authored as MaterialX `transmission`, the factor times
the R channel of its texture, read as non-color data. `gltf_pbr` mixes its diffuse term toward a
transmission tinted by the base color, as glTF does; without `KHR_materials_volume` the material is
thin, and `thickness` keeps its default of 0. UsdPreviewSurface has no transmission, so its network
ignores the extension with a warning, and its `opacity` remains glTF's alpha. A factor of 0 and an
unlit material author nothing. See [ADR 0018](../adr/0018-transmission.md) for how Storm draws it.

## Volume

A `KHR_materials_volume` thickness factor above 0, on a material with a transmission above 0, is
authored as MaterialX `thickness`, the factor times the G channel of its texture, read as non-color data.
A given `attenuationDistance` is authored as the uniform `attenuation_distance`, with `attenuationColor`
as the uniform `attenuation_color`; without one, glTF's infinite default attenuates nothing, and neither
is authored. These are `gltf_pbr`'s own volume inputs, which its graph does not yet connect, so the
material is drawn thin, as glTF's fallback is. UsdPreviewSurface has no volume, so its network ignores
the extension with a warning. A thickness of 0, a material without transmission and an unlit material
author nothing. See [ADR 0019](../adr/0019-volume.md).

## Other extensions

Each other extension
requires its own explicit capability entry and tests. Do not silently
claim complete support for unsupported features. Required extension handling
is governed by the
[import pipeline](../architecture/IMPORT_PIPELINE.md).
