# 0017: Clearcoat

Decision record, 2026-11-03. It extends the networks of the
[factor-only decision](../architecture/MATERIAL_FACTORS.md), [ADR 0003](0003-pbr-texture-networks.md)
and [ADR 0004](0004-normal-textures-and-tangents.md). [MATERIALS](../design/MATERIALS.md#clearcoat)
specifies the result, and the [capability matrix](../reference/CAPABILITY_MATRIX.md) records current
evidence.

## Context

`KHR_materials_clearcoat` adds an uncolored dielectric layer over the core material. Its weight is
`clearcoatFactor` times the R channel of `clearcoatTexture`, and its roughness
`clearcoatRoughnessFactor` times the G channel of `clearcoatRoughnessTexture`; both factors default
to 0 and are in 0 to 1. A factor of 0 disables the layer. Its Fresnel term has a fixed ior of 1.5, an
F0 of 0.04, whatever `KHR_materials_ior` says. Its normal is `clearcoatNormalTexture`, with a scale,
or else the geometric normal, not the base normal map. glTF blends the base and the layer as
`mix(base, layer, clearcoat * fresnel(1.5, N.V))`, so the coat dims the base as it reflects.

What the two shaders can express:

- MaterialX `gltf_pbr` (1.39.5, the version OpenUSD 26.08 builds) has `clearcoat`,
  `clearcoat_roughness` and `clearcoat_normal`, defaulting to 0, 0 and the geometric normal. It layers a
  `dielectric_bsdf` of ior 1.5, weighted by `clearcoat`, over the rest of the material. The `layer`
  node dims the base by the coat's directional albedo rather than by the Fresnel term of each direction.
- UsdPreviewSurface has `clearcoat` and `clearcoatRoughness`, defaulting to 0 and 0.01, and no
  clearcoat normal: its specification says the coat uses the same normal as the first lobe. Storm's
  `previewSurface.glslfx` for OpenUSD 26.08 computes the coat's F0 as `R^2` with
  `R = (1 - ior) / (1 + ior)` from the material's `ior`, F90 as 1, and adds `clearcoat` times that lobe
  to the lit result, without dimming the base. A dome light's reflection does the same.

## Options

- **Ignore the extension, with a warning,** which shows the base alone.
- **Bake the coat into the base,** which neither shader can do: a second lobe is not a first one.
- **Author the coat's inputs in both shaders,** keeping what each cannot express as a stated limit.

## Decision

Author the coat's inputs in both shaders, when the material has a clearcoat factor above 0 and is not
unlit:

| glTF | `preview` (UsdPreviewSurface) | `mtlx` (`gltf_pbr`) |
| --- | --- | --- |
| `clearcoatFactor`, and `clearcoatTexture` R | `clearcoat`, from a raw `UsdUVTexture` scaled by the factor | `clearcoat`, from a vector image's channel 0 times the factor |
| `clearcoatRoughnessFactor`, and `clearcoatRoughnessTexture` G | `clearcoatRoughness`, always authored, from a raw `UsdUVTexture` scaled by the factor | `clearcoat_roughness`, always authored, from a vector image's channel 1 times the factor |
| `clearcoatNormalTexture` and its scale | Not expressible; ignored with a warning | `clearcoat_normal`, from a normal map as for the base normal |
| No `clearcoatNormalTexture` | The base normal, with its map | The geometric normal, by default |

In MaterialX a factor of 1 needs no multiply. Textures follow the
[texture transform](0011-texture-transform.md) and wrap modes of every slot. At a factor of 0, and on
an unlit material, nothing is authored and the textures are not resolved, so a damaged image there does
not fail the import; an unlit material warns that the extension is ignored. Their values are still
checked, and each texture's UV set must exist on the primitive, as for unlit fallbacks.

A factor outside 0 to 1 fails for the material that uses it. A value of the extension that is not an
object, a field of the wrong type, and a number too large for a float fail with a diagnostic naming
the extension, where fastgltf gave none. A material with a clearcoat and an ior other than 1.5 warns,
because Storm's coat takes that ior.

## Evidence and limits

- Tests assert both networks for factors, the default roughness, a disabled coat, an unlit material,
  each texture, the clearcoat normal, and each failure, optional and required. The sample check
  compares each enabled coat's inputs and texture slots.
- The [display check](../reference/CAPABILITY_MATRIX.md#display-checks) renders a black quad with a coat
  under a light, and a coat over a base of ior 1 in each path.
- The equivalence with glTF is read from the shaders' sources, not displayed. Through MaterialX, the
  base is dimmed by directional albedo, not by the Fresnel term of each direction.
- Through Storm's UsdPreviewSurface the coat adds its reflection without dimming the base, so a coated
  material is brighter than glTF's, most at grazing angles; it follows the base normal map and ignores
  `clearcoatNormalTexture`; and its F0 comes from the material's ior, not 1.5.
- Other renderers may read UsdPreviewSurface's clearcoat differently, or not at all.
