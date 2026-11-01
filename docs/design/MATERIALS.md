# Material design

Each glTF material maps to one `UsdShadeMaterial` under `/Asset/mtl` with
both a MaterialX `standard_surface`-based path and a UsdPreviewSurface
fallback. Use render-context-specific surface outputs as appropriate; verify
the actual output names and connections against the targeted OpenUSD and
MaterialX versions. Standard schemas and shaders must suffice: no custom
schema, shader, or imaging plugin is required.

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
| Normal texture | Tangent-space normal map and its scale |
| Occlusion texture | R channel and strength; approximate or explicitly report unsupported behavior according to shader capability |
| Emissive factor and texture | Emissive color / emission |
| Alpha mode and cutoff | Preserve OPAQUE, MASK and BLEND semantics where possible |
| `doubleSided` | Keep UsdGeom sidedness and material behavior consistent |

For vertex colors, multiply base color by the linear `displayColor` primvar
and use the retained `gltf:colorAlpha` primvar when present (otherwise 1)
for alpha, subject to the material's alpha mode. See
[scene conventions](SCENE_CONVENTIONS.md#vertex-colors).

Distinguish color data (base color and emissive) from non-color data
(metallic, roughness, normal and occlusion). Texture readers consume the
primvar's [converted coordinates](SCENE_CONVENTIONS.md#texture-coordinates)
without another flip. Apply `KHR_texture_transform` to UVs. Document
approximations: glTF and MaterialX BRDFs do not match exactly.

Each extension, including clearcoat, transmission, ior, sheen, specular and
unlit, requires its own explicit capability entry and tests. Do not silently
claim complete support for unsupported features. Required extension handling
is governed by the
[import pipeline](../architecture/IMPORT_PIPELINE.md).
