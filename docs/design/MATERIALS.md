# Material design

Status: **adopted direction**, with node graphs and renderer compatibility
still to be verified.

Each glTF material maps to one `UsdShadeMaterial` under `/Asset/mtl` with
both a MaterialX `standard_surface`-based path and a UsdPreviewSurface
fallback. Use render-context-specific surface outputs as appropriate; verify
the actual output names and connections against the targeted OpenUSD and
MaterialX versions. Standard schemas and shaders must suffice: no custom
schema, shader, or imaging plugin is required.

Share image data and conversion parameters where possible, but use separate
nodes where the two paths require incompatible node interfaces. The
existence of a node definition does not establish renderer support: verify
rendering in each target renderer as well as network structure.

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

Distinguish color data (base color and emissive) from non-color data
(metallic, roughness, normal and occlusion). Apply `KHR_texture_transform`
to UVs. Document approximations: glTF and MaterialX BRDFs do not match
exactly.

Extensions including clearcoat, transmission, ior, sheen, specular and
unlit are later work, each requiring an explicit capability entry and tests.
Do not silently claim complete support for unsupported features. Required
extension handling is governed by the
[import pipeline](../architecture/IMPORT_PIPELINE.md).
