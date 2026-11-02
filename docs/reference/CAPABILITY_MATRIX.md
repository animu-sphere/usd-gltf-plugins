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
- **Local Windows:** run locally on Windows x86-64 with MSVC and OpenUSD 26.08.

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
| Extensions | Experimental | `KHR_texture_basisu` is implemented; see its row. Any other required extension fails. Other optional extensions warn and are ignored, except that a selected node using `KHR_lights_punctual` is rejected. | `tests/test_file_format.py::test_failures_report_diagnostics`, `tests/test_file_format.py::test_node_order_and_optional_extension_warning`, `tests/test_file_format.py::test_optional_lights_outside_selected_scene_and_required_lights`, `tests/test_file_format.py::test_unsupported_features_fail_without_publishing` |
| usdview/Storm display | Experimental | The [display check](#display-checks) of simple fixtures through both shader paths, run on one Windows machine. Not general renderer support. | `tests/check_display.py`, `tests/fixtures/reflections.gltf`, `tests/fixtures/alpha_modes.gltf`, `tests/fixtures/base_color_textures.gltf`, `tests/fixtures/pbr_textures.gltf`, `tests/fixtures/normal_textures.gltf`, `tests/fixtures/ktx2_textures.gltf` |
| OpenStrata workspace build and tests | Verified (Windows x86-64, Linux x86-64, macOS arm64; OpenUSD 26.08) | The environments listed under [tested environments](#tested-environments). | [tag workflow 41388405224](https://github.com/animu-sphere/usd-gltf-plugins/actions/runs/41388405224) |
| Standalone OpenStrata bundle | Verified (Windows x86-64, Linux x86-64, macOS arm64; OpenUSD 26.08) | `ost plugin test` confirms discovery of the file format, not a successful import. | `plugins/usdGltfFileFormat/openstrata.plugin.yaml`, [tag workflow 41388405224](https://github.com/animu-sphere/usd-gltf-plugins/actions/runs/41388405224) |
| Root CMake build, install and registration | Verified (Windows x86-64, Linux x86-64, macOS arm64; OpenUSD 26.08) | A separate process registers the installed plugin and opens a stage. | `tests/test_install_smoke.py::test_installed_plugin_registers_and_opens_a_stage`, [tag workflow 41388405224](https://github.com/animu-sphere/usd-gltf-plugins/actions/runs/41388405224) |
| Release package | Verified (Windows x86-64, Linux x86-64, macOS arm64; OpenUSD 26.08) | A bundle package extracted outside the repository registers, opens fixtures, serves KTX2 images and carries its licenses, and packaging twice gives one digest. It works only with the exact runtime it was built for. | `tests/check_package.py`, [tag workflow 41388405224](https://github.com/animu-sphere/usd-gltf-plugins/actions/runs/41388405224) |
| Materials and bindings | Experimental | Default materials and explicit factor-only metallic-roughness materials, with OPAQUE, MASK or BLEND alpha and optional vertex colors: Preview and MaterialX networks and bindings, including `doubleSided`. Network structure is tested in CI; display has the [display check](#display-checks) of alpha and vertex colors. See the [decision record](../architecture/MATERIAL_FACTORS.md). | `tests/test_file_format.py::test_default_material_with_and_without_vertex_color`, `tests/test_file_format.py::test_factor_materials_and_bindings`, `tests/test_file_format.py::test_alpha_modes`, `tests/test_file_format.py::test_explicit_material_vertex_colors`, `tests/test_file_format.py::test_alpha_modes_view_fixture`, `tests/test_file_format.py::test_material_failures_preserve_layer` |
| Base color textures | Experimental | Factor, alpha modes, UV set and wrap modes in both networks. Filters and `KHR_texture_transform` are not applied. With COLOR_0, Preview omits the vertex color and warns. Tested in CI, with the [display check](#display-checks) on Windows. See the [decision record](../adr/0002-base-color-texture-networks.md). | `tests/test_file_format.py::test_base_color_image_sources_and_networks`, `tests/test_file_format.py::test_base_color_sampler_uv_set_factor_and_alpha`, `tests/test_file_format.py::test_base_color_texture_with_vertex_colors`, `tests/test_file_format.py::test_base_color_textures_view_fixture`, `tests/test_file_format.py::test_material_failures_preserve_layer` |
| Metallic-roughness, occlusion and emissive textures | Experimental | G = roughness, B = metallic and occlusion R as non-color data, and emissive as color, with factors, strength, UV sets and wrap modes in both networks. Filters and `KHR_texture_transform` are not applied. Occlusion has no visible effect through MaterialX `gltf_pbr` and also darkens direct light through Storm's UsdPreviewSurface. Tested in CI, with the [display check](#display-checks) on Windows. See the [decision record](../adr/0003-pbr-texture-networks.md). | `tests/test_file_format.py::test_metallic_roughness_occlusion_and_emissive_networks`, `tests/test_file_format.py::test_pbr_textures_without_terms_they_do_not_need`, `tests/test_file_format.py::test_pbr_textures_view_fixture`, `tests/test_file_format.py::test_pbr_texture_failures_preserve_layer`, `tests/test_file_format.py::test_material_failures_preserve_layer` |
| Normal textures and tangents | Experimental | Scale, UV sets and wrap modes in both networks, as non-color data. Renderers derive the tangent frame, so a map baked against other tangents can differ, and filters and `KHR_texture_transform` are not applied. TANGENT is validated and retained as a primvar that no network reads. Tested in CI, with the [display check](#display-checks) on Windows of uniform images on flat quads. See the [decision record](../adr/0004-normal-textures-and-tangents.md). | `tests/test_file_format.py::test_normal_texture_networks`, `tests/test_file_format.py::test_materials_without_a_normal_texture_author_no_normal`, `tests/test_file_format.py::test_normal_textures_view_fixture`, `tests/test_file_format.py::test_pbr_texture_failures_preserve_layer`, `tests/test_file_format.py::test_tangents_are_retained`, `tests/test_file_format.py::test_tangent_failures_preserve_layer` |
| PNG and JPEG images | Experimental | bufferView, data URI and restricted local file images, served by a package resolver or a layer-relative path; embedded images resolve after moving an asset and through references. No image is written during import. See the [decision record](../adr/0001-image-assets.md). Import checks structure without decoding: damaged files, sides above 16384 and external files above 2 GiB fail. Not found: a PNG crafted with valid chunks and invalid data, which [crashes OpenUSD's image loader](../adr/0006-image-failures.md#limits) with or without this plugin; damage inside a JPEG scan; and external files damaged after import. Loader behavior was observed on Windows with Storm only. | `tests/image.cpp`, `tests/test_file_format.py::test_base_color_image_sources_and_networks`, `tests/test_file_format.py::test_images_resolve_after_moving_and_through_references`, `tests/test_file_format.py::test_package_resolver_rejects_invalid_entries`, `tests/test_file_format.py::test_texture_failures_preserve_layer`, `tests/test_file_format.py::test_damaged_and_oversized_images_fail_import`, `tests/test_file_format.py::test_unusual_but_valid_images_are_accepted`, `tests/test_file_format.py::test_checks_do_not_decode_image_data`, `tests/test_file_format.py::test_image_damaged_after_import_is_not_served`, `tests/test_file_format.py::test_external_image_file_size_limit` |
| KTX2 (`KHR_texture_basisu`) | Experimental | ETC1S and UASTC LDR 4x4 single 2D images, up to 16384 per side, from bufferViews, data URIs and restricted local files, served as PNG by the package resolver and decoded on each read, without a cache. Other codecs, cubemaps and arrays are rejected. Import reads the header only, so a damaged body fails when read, with a warning, after the layer is published. Tested in CI, with the [display check](#display-checks) on Windows. See the [decision record](../adr/0005-ktx2-decoder.md). | `tests/image.cpp`, `tests/test_file_format.py::test_ktx2_images_are_served_as_png`, `tests/test_file_format.py::test_ktx2_is_preferred_over_its_fallback_and_does_not_warn`, `tests/test_file_format.py::test_ktx2_file_resolves_after_moving_and_on_reload`, `tests/test_file_format.py::test_ktx2_failures_preserve_layer`, `tests/test_file_format.py::test_ktx2_orientation_required_by_the_extension_is_accepted`, `tests/test_file_format.py::test_damaged_ktx2_body_fails_when_read`, `tests/test_file_format.py::test_ktx2_textures_view_fixture` |
| Point and line primitives, other vertex attributes, sparse accessors | Unsupported | Rejected with a diagnostic. | `tests/test_file_format.py::test_mesh_failures_and_reload_preservation` |
| Skins, cameras, lights and morphs | Unsupported | Rejected when used in the selected scene, including morph targets and node or mesh morph weights. | `tests/test_file_format.py::test_unsupported_features_fail_without_publishing`, `tests/test_file_format.py::test_optional_lights_outside_selected_scene_and_required_lights` |
| Animation | Unsupported | Any asset with animations is rejected, including animations outside the selected scene. | `tests/test_file_format.py::test_unsupported_features_fail_without_publishing`, `tests/test_file_format.py::test_animation_outside_selected_scene_is_rejected` |
| Export | Unsupported | Not implemented. | |
| General renderer compatibility | Unsupported | No renderer, including Storm, has a general compatibility claim. | |

## Tested environments

[ADR 0009](../adr/0009-release-targets.md) sets the targets for release packages. These are the
environments where the evidence above was produced.

| Environment | OpenUSD | Result |
| --- | --- | --- |
| Windows x86-64 (`windows-2022`), Python 3.13 | 26.08, OpenStrata cy2026 `usd` | All 8 CTest tests passed at `0a0bc02` in [tag workflow 41388405224](https://github.com/animu-sphere/usd-gltf-plugins/actions/runs/41388405224) |
| Linux x86-64 (`ubuntu-24.04`), Python 3.13 | 26.08, OpenStrata cy2026 `usd` | Same run |
| macOS arm64 (`macos-15`), Python 3.13 | 26.08, OpenStrata cy2026 `usd` | Same run |

The CI configuration is pinned in [openstrata.ci.yaml](../../openstrata.ci.yaml).

## Display checks

[`tests/check_display.py`](../../tests/check_display.py) renders the fixtures below with
Storm through MaterialX and UsdPreviewSurface, and compares pixels. The
[README](../../README.md#development) has the command, and
[ADR 0007](../adr/0007-display-check.md) explains the comparisons and their limits.

Last result: 395 comparisons passed, none failed, at `0a0bc02`, on Windows with OpenUSD 26.08, the
OpenStrata lookdev runtime and an NVIDIA RTX A5000. Two runs produced byte-identical
images. This checks simple fixtures, not general shading correctness.

The [reflection fixture](../../tests/fixtures/reflections.gltf) has four front-facing
triangles: no reflection, a reflected parent, a reflected child, and two reflections. The
first two use supplied normals and the next two use generated normals. With usdview's
backface culling all four are visible and lit alike, and a fifth, deliberately reversed
triangle disappears. Without culling all five are visible. This fixture is rendered through
the engine call that usdview makes, not through usdview, and with the default material's
MaterialX path only.

The [alpha fixture](../../tests/fixtures/alpha_modes.gltf) has vertex alpha falling from 1
at the left of each quad to 0 at the right, in front of a red backdrop. Both paths show,
from the left: two green quads cut at their centers (MASK, with vertex alpha alone and with
a factor), a whole green quad (MASK with cutoff 0), a green quad fading into the backdrop
(BLEND), a half-transparent quad fading likewise (BLEND with a factor), and a whole green
quad (OPAQUE with a factor). The yellow factor leaves green unchanged, so this checks alpha
and a multiply that keeps the vertex color.

The [base color texture fixture](../../tests/fixtures/base_color_textures.gltf) uses an
image with red, green, blue and white quadrants, from upper left to lower right. Both paths
show, from the left: the image upright from a data URI; from a bufferView with a yellow
factor, so blue turns black and white yellow; from an external PNG; an external JPEG under
BLEND, fully opaque because it has no alpha; a MASK cut at the image's alpha edge; a BLEND
with a factor of 0.5; mirrored in S and clamped in T; and turned upside down by TEXCOORD_1.
In the last quad, a green COLOR_0 multiplies the image in MaterialX only, which is the
documented difference.

The [PBR texture fixture](../../tests/fixtures/pbr_textures.gltf) shows through both paths,
from the left: metallic-roughness quadrants, with the metal half darker and a change of
shade between the smooth left and rough right of the metal; the same image with metallic
factor 0 and roughness factor 0.5, leaving only a roughness edge; occlusion, unoccluded on
the left; occlusion at strength 0.5; the emissive image upright; and the emissive image with
a yellow factor upside down through TEXCOORD_1. Both occlusion quads are dark on the right
through UsdPreviewSurface (black, then midway) and uniform through MaterialX, which is the
[documented difference](../adr/0003-pbr-texture-networks.md#occlusion-differs-between-the-paths).

Color and non-color reading are measured on assets the check generates, each rendered alone
so that geometry and view are the same. An image value of 188 is 0.737 read as data and
0.503 read as sRGB. Center pixels are given as MaterialX then UsdPreviewSurface. Compare
down a path, not across: the two shaders differ in brightness, most for metallic.

| Case | Image | Factor of the correct reading | Factor of the wrong reading |
| --- | --- | --- | --- |
| Base color | 189, 186 | 189, 186 | 223, 220 |
| Roughness from G | 233, 231 | 233, 231 | 247, 247 |
| Metallic from B | 207, 133 | 207, 133 | 215, 155 |
| One image as base color and metallic-roughness | 193, 183 | 193, 183 | |
| Emissive | 190, 190 | 190, 190 | |

Through UsdPreviewSurface, occlusion of 188 and a black image at strength 0.263 both take 228
down to 199. Through MaterialX both stay at the unoccluded 231.

The [normal texture fixture](../../tests/fixtures/normal_textures.gltf) has one quad per
case, each with a uniform image tilting the normal. Its
[reference](../../tests/fixtures/normal_textures_reference.gltf) has no normal textures, and
each quad supplies the expected result as NORMAL. Both are rendered through both paths from
the [same off-axis camera](../../tests/fixtures/normal_textures_view.usda), whose light
separates opposite tilts. Center pixels, map then reference:

| Case | Expected | MaterialX | UsdPreviewSurface |
| --- | --- | --- | --- |
| Flat | +Z | 188, 188 | 189, 188 |
| Tilt +X | +X | 209, 209 | 209, 209 |
| Tilt -X | -X | 117, 116 | 124, 123 |
| Tilt +Y | +Y | 217, 217 | 217, 217 |
| Tilt -Y | -Y | 103, 102 | 110, 109 |
| Tilt +X at scale 0.5 | Half tilt +X | 205, 205 | 205, 205 |
| Tilt +Y, UV rotated | -X | 135, 134 | 140, 139 |
| Tilt +X, U mirrored | -X | 139, 139 | 144, 143 |
| Tilt +Y, U mirrored | +Y | 223, 223 | 223, 223 |
| Tilt +Y, rotated in UV 1, base color in UV 0 | -X | 148, 147 | 151, 151 |
| Tilt +Y, rotated in UV 0, base color in UV 1 | -X | 152, 151 | 155, 154 |
| Tilt +Y, rotated in UV 1 alone | -X | 155, 155 | 158, 158 |
| Tilt +X under a reflected node | Mirrored +X | 159, 158 | 161, 161 |
| Tilt +X without NORMAL | +X | 196, 195 | 196, 195 |
| Tilt +Y with TANGENT W of -1 | +Y, so TANGENT is unused | 237, 236 | 237, 237 |

Map and reference agree within 1 in every case. Pixel values also vary across the row
because of the position of the light, so compare within a row of this table only.

The scale row cannot separate scales from this view. A generated quad tilted 45 degrees
toward the light does: at scale 0.5 it equals its reference at 220 through MaterialX and 217
through UsdPreviewSurface, and at scale 1 it is 198 and 196.

The [KTX2 fixture](../../tests/fixtures/ktx2_textures.gltf) has six quads showing red,
green, blue and white quadrants from upper left to lower right: a PNG as the reference, then
ETC1S, ETC1S with mips and UASTC with Zstandard, then ETC1S and UASTC with alpha of 0.25 in
the lower half under BLEND. Both paths show all six upright. Channels that should be high, at
the quadrant centers, are 250 to 251 for the reference and 251 to 254 for KTX2 through
MaterialX, and 247 to 248 and 248 to 252 through UsdPreviewSurface. The two alpha quads
agree within 3. Swapping their materials once showed that the part above 1 follows position,
through the light, and not the codec.

Multiple primitives sharing one POSITION accessor, on a generated asset, display correctly.
Storm warns, because each mesh authors the accessor's whole vertex array and its topology uses
only part of it.

The Khronos FlightHelmet sample, which is not in this repository and not part of the check,
was once rendered through both paths and looked correct by eye. All of its images use KTX2,
including normal and metallic-roughness data.

## Sample assets

From [glTF-Sample-Assets](https://github.com/KhronosGroup/glTF-Sample-Assets) at commit `8f49239`,
which is not in this repository: 87 files in the variants of 34 chosen models, on Windows with
OpenUSD 26.08. This was run once, by hand, and is not part of CI.

- **Opened: 63.** They include DamagedHelmet, FlightHelmet with PNG and with KTX2 images, Sponza,
  ABeautifulGame, BoomBox and the texture and orientation test models. Every mesh has a bound
  material, and every asset path resolves and opens.
- **Rejected with a diagnostic: 24.** Of these, 7 were read: extensions the file requires (Draco and
  quantization), animations, and nodes with a camera. Other than those read, the 24 were not
  examined one by one. All variants of Duck, ToyCar, MultiUVTest and CesiumMilkTruck are rejected.
- **By eye only:** 9 assets rendered through `usdrecord` showed nothing visibly wrong. No pixel was
  compared.
