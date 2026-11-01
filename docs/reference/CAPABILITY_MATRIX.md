# Capability matrix

This page records actual status, not roadmap commitments. The repository is
at an early implementation stage; only a limited import subset is verified.

| Capability | Status | Intended direction |
| --- | --- | --- |
| `.gltf` / `.glb` file-format registration and import | Node and triangle-based mesh subsets open and reload in local Windows tests; unsupported selected content fails explicitly | [Import pipeline](../architecture/IMPORT_PIPELINE.md) |
| Scene, nodes, transforms, meshes and vertex attributes | Scene selection, hierarchy, transforms, indexed/non-indexed triangles, strips and fans, positions, normals, UV sets and COLOR_0 data implemented for embedded and restricted local external buffers; points, lines and other attributes are not | [Scene conventions](../design/SCENE_CONVENTIONS.md) |
| Coordinate system and units | Direct imports author Y-up and meters; hierarchical transforms and composition into a differently configured stage tested locally on Windows | [Coordinate system and units](../design/SCENE_CONVENTIONS.md#coordinate-system-and-units) |
| MaterialX and UsdPreviewSurface PBR and bindings | Not implemented | [Materials](../design/MATERIALS.md) |
| PNG/JPEG, KTX2, image resolution and cache | Not implemented | [Textures](../design/TEXTURES.md) |
| Animation, skinning, morphs, cameras and lights | Not implemented | Later phase |
| glTF material extensions and export | Not implemented | Later phase |
| OpenStrata workspace and CI | Workspace build and registration test passed on Windows, Linux and macOS with OpenUSD 26.08 in [PR CI](https://github.com/animu-sphere/usd-gltf-plugins/actions/runs/41289600882); standalone bundle discovery verified locally on Windows | [Workspace](../architecture/WORKSPACE.md) |
| fastgltf dependency | 1.0.0 built with pinned simdjson and simdutf; glTF/GLB parsing smoke test passed on all three earlier PR CI platforms; used by the importer | [Import pipeline](../architecture/IMPORT_PIPELINE.md) |
| Platform, OpenUSD and renderer compatibility | No supported matrix or renderer verified | [Open decisions](../roadmap/README.md#decisions-to-verify) |

Update entries with evidence as features land. Do not equate node definitions
or successful builds with verified rendering support.
