# Capability matrix

This page is the only record of what is implemented and verified. It makes no
future commitments; see the [roadmap](../roadmap/README.md) for those. A past
result here is not a current guarantee: re-verify at each release.

## States

| State | Meaning |
| --- | --- |
| `Unsupported` | Not implemented. Selected content of this kind is rejected unless the row says otherwise. |
| `Experimental` | Implemented or observed, but the evidence is too limited to rely on. |
| `Verified (scope)` | Evidence exists within the stated scope only. |

Scopes used below:

- **USD data, CI:** tests assert the authored USD data in all
  [CI environments](#tested-environments). This says nothing about how a
  renderer displays the result.
- **Local Windows:** run locally on Windows x86-64 with MSVC and OpenUSD 26.08;
  not yet confirmed by CI.

CI success is not evidence of display compatibility.

## Capabilities

| Capability | State | Supported subset and limitations | Evidence |
| --- | --- | --- | --- |
| `.gltf` / `.glb` registration and import | Verified (USD data, CI) | Read only; writing and editing are declared unsupported. Eager conversion on read. | `tests/test_file_format.py::test_registration_and_capabilities` |
| Scene selection | Verified (USD data, CI) | Declared default scene, or scene 0 with a warning. No scenes or an invalid default fail. Scenes are never merged. | `tests/scene_selection.cpp`, `tests/test_file_format.py::test_first_scene_fallback_warns`, `tests/test_file_format.py::test_failures_report_diagnostics` |
| Node hierarchy and transforms | Verified (USD data, CI) | TRS and matrix nodes. Invalid quaternions and perspective matrices fail. Multiply referenced meshes are duplicated, not shared. | `tests/test_file_format.py::test_scene_hierarchy_and_transforms`, `tests/test_file_format.py::test_node_order_and_optional_extension_warning` |
| Triangle-based meshes | Verified (USD data, CI) | Indexed and non-indexed TRIANGLES, TRIANGLE_STRIP and TRIANGLE_FAN with float POSITION. Interleaved accessors are accepted. | `tests/test_file_format.py::test_triangle_meshes`, `tests/test_file_format.py::test_triangle_strips_and_fans`, `tests/test_file_format.py::test_triangle_strip_and_fan_index_types_and_nonindexed_counts`, `tests/test_file_format.py::test_mesh_schema_attributes` |
| Normals | Verified (USD data, CI) | Supplied float NORMAL, or generated per face when absent. | `tests/test_file_format.py::test_flat_normals_and_negative_scale` |
| Texture coordinate sets | Verified (USD data, CI) | Consecutive TEXCOORD_n. Authored values; sampling through them is covered by the texture rows. | `tests/test_file_format.py::test_texture_coordinates`, `tests/test_file_format.py::test_texture_coordinate_failures_preserve_layer` |
| Vertex colors | Verified (USD data, CI) | COLOR_0 primvar data; alpha is retained. Default and factor-only materials consume RGB, and alpha only for MASK and BLEND. | `tests/test_file_format.py::test_vertex_colors`, `tests/test_file_format.py::test_vertex_color_failures_preserve_layer`, `tests/test_file_format.py::test_default_material_with_and_without_vertex_color`, `tests/test_file_format.py::test_explicit_material_vertex_colors` |
| Embedded and local external buffers | Verified (USD data, CI) | Embedded and restricted local paths only; see the [external resource policy](../architecture/IMPORT_PIPELINE.md#external-resource-policy). The link-escape test skips where the host cannot create directory links. | `tests/test_file_format.py::test_external_buffers_and_reload`, `tests/test_file_format.py::test_external_buffer_uri_rejections`, `tests/test_file_format.py::test_external_buffers_with_unicode_and_mixed_glb_data`, `tests/test_file_format.py::test_unselected_external_buffer_is_not_loaded`, `tests/test_file_format.py::test_external_buffer_symlink_escape_is_rejected` |
| Coordinate system and units | Verified (USD data, CI) | Y-up and meters authored on direct open and reload; referencing into a Z-up centimeter stage. Not every transform combination. | `tests/test_file_format.py::test_coordinate_system_and_reference_composition` |
| Reflections, winding and normals | Verified (USD data, CI) | Indexed and non-indexed triangles with odd and even reflections in hierarchical transforms and a mirrored reference. | `tests/test_file_format.py::test_hierarchical_reflection_winding_and_normals`, `tests/test_file_format.py::test_reflection_view_fixture` |
| Failure and reload behavior | Verified (USD data, CI) | Tested failures emit a diagnostic and do not publish a partial layer. Not proven for every USD error; [the transfer itself is not atomic](../architecture/LAYER_AUTHORING.md#decision). | `tests/test_file_format.py::test_failed_reload_does_not_publish_partial_stage`, `tests/test_file_format.py::test_mesh_failures_and_reload_preservation`, `tests/test_file_format.py::test_nonfinite_position_is_rejected` |
| Extensions | Experimental | Any required extension fails. Optional extensions warn and are ignored, except that a selected node using `KHR_lights_punctual` is rejected. No extension is implemented. | `tests/test_file_format.py::test_failures_report_diagnostics`, `tests/test_file_format.py::test_node_order_and_optional_extension_warning`, `tests/test_file_format.py::test_optional_lights_outside_selected_scene_and_required_lights`, `tests/test_file_format.py::test_unsupported_features_fail_without_publishing` |
| usdview/Storm display | Experimental | Four [manual checks](#manual-visual-checks) of simple fixtures on Windows. Not general renderer support. | `tests/fixtures/reflections.gltf`, `tests/fixtures/alpha_modes.gltf`, `tests/fixtures/base_color_textures.gltf`, `tests/fixtures/pbr_textures.gltf` |
| OpenStrata workspace build and tests | Verified (Windows x86-64, Linux x86-64, macOS arm64; OpenUSD 26.08) | The environments listed under [tested environments](#tested-environments). | [CI run 41297241072](https://github.com/animu-sphere/usd-gltf-plugins/actions/runs/41297241072) |
| Standalone OpenStrata bundle | Verified (Local Windows) | `ost plugin test --up-to 2` confirms discovery of the file format, not a successful import. | `plugins/usdGltfFileFormat/openstrata.plugin.yaml` |
| Root CMake build, install and registration | Verified (Local Windows) | A separate process registers the installed plugin and opens a stage. | `tests/test_install_smoke.py::test_installed_plugin_registers_and_opens_a_stage` |
| Materials and bindings | Experimental | Default materials and explicit factor-only metallic-roughness materials, with OPAQUE, MASK or BLEND alpha and optional vertex colors: Preview and MaterialX networks and bindings, including `doubleSided`. Network structure has been tested locally; display has one [manual Storm check](#manual-visual-checks) of alpha and vertex colors. See the [decision record](../architecture/MATERIAL_FACTORS.md). | `tests/test_file_format.py::test_default_material_with_and_without_vertex_color`, `tests/test_file_format.py::test_factor_materials_and_bindings`, `tests/test_file_format.py::test_alpha_modes`, `tests/test_file_format.py::test_explicit_material_vertex_colors`, `tests/test_file_format.py::test_alpha_modes_view_fixture`, `tests/test_file_format.py::test_material_failures_preserve_layer` |
| Base color textures | Experimental | Factor, alpha modes, UV set and wrap modes in both networks. Filters and `KHR_texture_transform` are not applied. With COLOR_0, Preview omits the vertex color and warns. Normal textures fail, and so do assets with TANGENT. Tested locally, with one [manual Storm check](#manual-visual-checks). See the [decision record](../adr/0002-base-color-texture-networks.md). | `tests/test_file_format.py::test_base_color_image_sources_and_networks`, `tests/test_file_format.py::test_base_color_sampler_uv_set_factor_and_alpha`, `tests/test_file_format.py::test_base_color_texture_with_vertex_colors`, `tests/test_file_format.py::test_base_color_textures_view_fixture`, `tests/test_file_format.py::test_material_failures_preserve_layer` |
| Metallic-roughness, occlusion and emissive textures | Experimental | G = roughness, B = metallic and occlusion R as non-color data, and emissive as color, with factors, strength, UV sets and wrap modes in both networks. Filters and `KHR_texture_transform` are not applied. Occlusion has no visible effect through MaterialX `gltf_pbr` and also darkens direct light through Storm's UsdPreviewSurface. Tested locally, with one [manual Storm check](#manual-visual-checks). See the [decision record](../adr/0003-pbr-texture-networks.md). | `tests/test_file_format.py::test_metallic_roughness_occlusion_and_emissive_networks`, `tests/test_file_format.py::test_pbr_textures_without_terms_they_do_not_need`, `tests/test_file_format.py::test_pbr_textures_view_fixture`, `tests/test_file_format.py::test_pbr_texture_failures_preserve_layer`, `tests/test_file_format.py::test_material_failures_preserve_layer` |
| PNG and JPEG images | Experimental | bufferView, data URI and restricted local file images, served by a package resolver or a layer-relative path; embedded images resolve after moving an asset and through references. No image is written during import. Import checks the signature only, so a damaged image body is reported at render time. Image dimensions and size are not limited. See the [decision record](../adr/0001-image-assets.md). | `tests/image.cpp`, `tests/test_file_format.py::test_base_color_image_sources_and_networks`, `tests/test_file_format.py::test_images_resolve_after_moving_and_through_references`, `tests/test_file_format.py::test_package_resolver_rejects_invalid_entries`, `tests/test_file_format.py::test_texture_failures_preserve_layer` |
| KTX2 (`KHR_texture_basisu`) | Unsupported | No decoder or image cache exists. A required declaration fails. | |
| Point and line primitives, other vertex attributes, sparse accessors | Unsupported | Rejected with a diagnostic. | `tests/test_file_format.py::test_mesh_failures_and_reload_preservation` |
| Skins, cameras, lights and morphs | Unsupported | Rejected when used in the selected scene, including morph targets and node or mesh morph weights. | `tests/test_file_format.py::test_unsupported_features_fail_without_publishing`, `tests/test_file_format.py::test_optional_lights_outside_selected_scene_and_required_lights` |
| Animation | Unsupported | Any asset with animations is rejected, including animations outside the selected scene. | `tests/test_file_format.py::test_unsupported_features_fail_without_publishing`, `tests/test_file_format.py::test_animation_outside_selected_scene_is_rejected` |
| Export | Unsupported | Not implemented. | |
| General renderer compatibility | Unsupported | No renderer, including Storm, has a general compatibility claim. | |

## Tested environments

There is no supported platform matrix yet. These are the environments where
the evidence above was produced.

| Environment | OpenUSD | Result |
| --- | --- | --- |
| Windows x86-64 (`windows-2022`), Python 3.13 | 26.08, OpenStrata cy2026 `usd` | All 5 CTest tests passed at `0420cbd` in [CI run 41297241072](https://github.com/animu-sphere/usd-gltf-plugins/actions/runs/41297241072) |
| Linux x86-64 (`ubuntu-24.04`), Python 3.13 | 26.08, OpenStrata cy2026 `usd` | Same run |
| macOS arm64 (`macos-15`), Python 3.13 | 26.08, OpenStrata cy2026 `usd` | Same run |

The `install_smoke` and `docs_consistency` tests were added after this run.
The CI configuration is pinned in [openstrata.ci.yaml](../../openstrata.ci.yaml).

## Manual visual checks

On Windows with OpenUSD 26.08 and the OpenStrata lookdev runtime, usdview's
Storm renderer with OpenGL and backface culling was used to view the
[reflection fixture](../../tests/fixtures/reflections.gltf). It shows four
front-facing triangles: no reflection, a reflected parent, a reflected child,
and two reflections. The first two use supplied normals and the next two use
generated normals. All four were visible with culling enabled; a fifth,
deliberately reversed triangle disappeared. With culling disabled all five were
visible. This checks a simple front view and default lighting, not general
shading correctness, other camera angles, materials or other renderers.

To repeat the check after building the plugin bundle and installing the lookdev
runtime (the `usd` profile alone does not provide usdview):

```powershell
ost plugin view --profile lookdev plugins\usdGltfFileFormat (Join-Path $PWD 'tests\fixtures\reflections.gltf')
```

In usdview, use **Viewer > Cull Backfaces (GL)** to toggle culling. With the
default front view, the rightmost triangle is the reversed control.

The [alpha fixture](../../tests/fixtures/alpha_modes.gltf) was rendered with
`usdrecord` and Storm on the same runtime, through MaterialX and, in a copy with
`outputs:mtlx:surface` removed from each material, through UsdPreviewSurface.
Vertex alpha falls from 1 at the left of each quad to 0 at the right, in front of
a red backdrop. Both paths compiled and showed, from the left: two green quads
cut at their centers (MASK, with vertex alpha alone and with a factor), a whole
green quad (MASK with cutoff 0), a green quad fading into the backdrop (BLEND), a
half-transparent quad fading likewise (BLEND with a factor), and a whole green
quad (OPAQUE with a factor). The yellow factor leaves green unchanged, so this
checks alpha and a multiply that keeps the vertex color, not general shading.
Compare the two paths by eye; pixels were not measured.

To repeat the MaterialX render after building the workspace:

```powershell
ost env cy2026 --profile lookdev --shell powershell | Out-String | Invoke-Expression
$env:PXR_PLUGINPATH_NAME = "$PWD\build\cy2026-windows-x86_64-py313-usd\plugin\resources\usdGltfFileFormat;$env:PXR_PLUGINPATH_NAME"
usdrecord --imageWidth 1400 tests\fixtures\alpha_modes.gltf alpha_modes.png
```

The [base color texture fixture](../../tests/fixtures/base_color_textures.gltf) was
rendered the same way through both paths. Its image has red, green, blue and white
quadrants, from upper left to lower right. Both paths compiled and showed, from the
left: the image upright from a data URI; from a bufferView with a yellow factor, so
blue turns black and white yellow; from an external PNG; an external JPEG under BLEND,
fully opaque because it has no alpha; a MASK cut at the image's alpha edge; a BLEND
with a factor of 0.5; mirrored in S and clamped in T; and turned upside down by
TEXCOORD_1. In the last quad, a green COLOR_0 multiplies the image in MaterialX only,
which is the documented difference. Compare the two paths by eye.

Color space was measured once with a throwaway asset, not a fixture: a gray texture of
sRGB 188 (linear 0.503) beside a factor of 0.5, both rough and non-metallic. The
rendered pixels were equal within each path, at 188 through MaterialX and 186 through
UsdPreviewSurface. A raw reading of the texture would have been much brighter.

To repeat this render, follow the commands above with this input:

```powershell
usdrecord --imageWidth 1500 tests\fixtures\base_color_textures.gltf base_color_textures.png
```

The [PBR texture fixture](../../tests/fixtures/pbr_textures.gltf) was rendered the same
way through both paths. Both compiled and showed, from the left: metallic-roughness
quadrants, with the metal lower half darker and a change of shade between the smooth
left and rough right; the same image with metallic factor 0 and roughness factor 0.5,
leaving only a roughness edge; occlusion, unoccluded on the left; occlusion at strength 0.5; the emissive image
upright; and the emissive image with a yellow factor upside down through TEXCOORD_1.
Both occlusion quads are dark on the right through UsdPreviewSurface (black, then
midway) and uniform through MaterialX, which is the
[documented difference](../adr/0003-pbr-texture-networks.md#occlusion-differs-between-the-paths).

Color and non-color reading were measured once with throwaway assets, not fixtures,
each rendered alone so that geometry and view were the same. An image value of 188 is
0.737 read as data and 0.503 read as sRGB. Center pixels, MaterialX then
UsdPreviewSurface:

| Case | Image | Equal factor | Factor at 0.503 |
| --- | --- | --- | --- |
| Roughness from G | 144, 143 | 144, 143 | 169, 169 |
| Metallic from B | 104, 100 | 104, 100 | 117, 113 |
| One image as base color and metallic-roughness | 193, 183 | 193, 183 | |

An emissive image of 188 equaled an emissive factor of 0.503 at 190 through both paths.
Through UsdPreviewSurface, occlusion of 188 and a black image at strength 0.263 both
took 251 down to 219, which is what multiplying by 0.737 gives. Through MaterialX they
stayed within 1 of the unoccluded 254.

To repeat the fixture render, follow the commands above with this input:

```powershell
usdrecord --imageWidth 1500 tests\fixtures\pbr_textures.gltf pbr_textures.png
```

Multiple primitives sharing one POSITION accessor were also rendered. They
displayed correctly, but Storm warns because each mesh authors the accessor's
whole vertex array and its topology uses only part of it.
