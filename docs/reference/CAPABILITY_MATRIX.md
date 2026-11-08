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
| Node hierarchy and transforms | Verified (USD data, CI) | TRS and matrix nodes. Invalid quaternions and perspective matrices fail. A quaternion within the tolerance of unit length is normalized. Multiply referenced meshes are duplicated, not shared. | `tests/test_file_format.py::test_scene_hierarchy_and_transforms`, `tests/test_file_format.py::test_rotations_are_normalized_within_the_tolerance`, `tests/test_file_format.py::test_node_order_and_optional_extension_warning` |
| Cameras | Verified (USD data, CI) | Perspective, finite and infinite, and orthographic, below each node that uses them, with the node's scale cancelled as glTF ignores it. An undefined aspect ratio is authored as 1 and keeps `yfov` only where the renderer matches the viewport's height, and an infinite projection ends at 1,000,000. Degenerate, reflected and sheared nodes and values outside the specification fail. Compared as a renderer combines view and projection, not rendered. See the [decision record](../adr/0012-cameras.md). | `tests/test_file_format.py::test_cameras`, `tests/test_file_format.py::test_cameras_reference_into_a_scaled_stage`, `tests/test_file_format.py::test_camera_failures_preserve_layer`, `tests/test_file_format.py::test_cameras_outside_selected_scene_are_not_checked` |
| Lights (`KHR_lights_punctual`) | Experimental | Directional, point and spot, optional or required, below each node that uses them, with the node's scale, shear and reflection cancelled. A directional light is a distant light in lux. A point or spot light is a sphere of 1 millimeter whose intensity gives glTF's candela, so a renderer that ignores `treatAsPoint` draws a small sphere. The cone's softness approximates glTF's spot falloff, and Storm ignores the cone. `range` is ignored, with a warning. Degenerate nodes and values outside the specification fail. Tested in CI, with the [display check](#display-checks) on Windows. See the [decision record](../adr/0013-lights.md). | `tests/test_file_format.py::test_lights`, `tests/test_file_format.py::test_lights_reference_into_a_scaled_stage`, `tests/test_file_format.py::test_light_range_is_ignored_with_a_warning`, `tests/test_file_format.py::test_light_failures_preserve_layer`, `tests/test_file_format.py::test_lights_required_and_outside_selected_scene`, `tests/check_display.py` |
| Triangle-based meshes | Verified (USD data, CI) | Indexed and non-indexed TRIANGLES, TRIANGLE_STRIP and TRIANGLE_FAN with float or quantized POSITION. Interleaved accessors are accepted. | `tests/test_file_format.py::test_triangle_meshes`, `tests/test_file_format.py::test_triangle_strips_and_fans`, `tests/test_file_format.py::test_triangle_strip_and_fan_index_types_and_nonindexed_counts`, `tests/test_file_format.py::test_mesh_schema_attributes` |
| Normals | Verified (USD data, CI) | Supplied float or quantized NORMAL, or generated per face when absent. | `tests/test_file_format.py::test_flat_normals_and_negative_scale` |
| Mesh quantization (`KHR_mesh_quantization`) | Verified (USD data, CI) | Every format the extension adds to POSITION, NORMAL, TANGENT and TEXCOORD_n, authored as floats, when the file lists it in `extensionsRequired`. Without that, quantized data fails. Normals are not renormalized, so a renderer must normalize them, which Storm was [seen to do](#display-checks) on two samples only. Morph targets remain unsupported. | `tests/test_file_format.py::test_quantized_attribute_formats`, `tests/test_file_format.py::test_quantized_values_keep_their_range_and_precision`, `tests/test_file_format.py::test_quantized_attributes_interleaved_with_node_transform`, `tests/test_file_format.py::test_quantization_declared_without_quantized_data`, `tests/test_file_format.py::test_quantized_failures_preserve_layer` |
| Texture coordinate sets | Verified (USD data, CI) | Consecutive TEXCOORD_n. Authored values; sampling through them is covered by the texture rows. | `tests/test_file_format.py::test_texture_coordinates`, `tests/test_file_format.py::test_texture_coordinate_failures_preserve_layer` |
| Vertex colors | Verified (USD data, CI) | COLOR_0 primvar data; alpha is retained. Default and factor-only materials consume RGB, and alpha only for MASK and BLEND. | `tests/test_file_format.py::test_vertex_colors`, `tests/test_file_format.py::test_vertex_color_failures_preserve_layer`, `tests/test_file_format.py::test_default_material_with_and_without_vertex_color`, `tests/test_file_format.py::test_explicit_material_vertex_colors` |
| Embedded and local external buffers | Verified (USD data, CI) | Embedded and restricted local paths only; see the [external resource policy](../architecture/IMPORT_PIPELINE.md#external-resource-policy). The link-escape test skips where the host cannot create directory links. | `tests/test_file_format.py::test_external_buffers_and_reload`, `tests/test_file_format.py::test_external_buffer_uri_rejections`, `tests/test_file_format.py::test_external_buffers_with_unicode_and_mixed_glb_data`, `tests/test_file_format.py::test_unselected_external_buffer_is_not_loaded`, `tests/test_file_format.py::test_external_buffer_symlink_escape_is_rejected` |
| Coordinate system and units | Verified (USD data, CI) | Y-up and meters authored on direct open and reload; referencing into a Z-up centimeter stage. Not every transform combination. | `tests/test_file_format.py::test_coordinate_system_and_reference_composition` |
| Reflections, winding and normals | Verified (USD data, CI) | Indexed and non-indexed triangles with odd and even reflections in hierarchical transforms and a mirrored reference. | `tests/test_file_format.py::test_hierarchical_reflection_winding_and_normals`, `tests/test_file_format.py::test_reflection_view_fixture` |
| Failure and reload behavior | Verified (USD data, CI) | Tested failures emit a diagnostic and do not publish a partial layer. Not proven for every USD error; [the transfer itself is not atomic](../architecture/LAYER_AUTHORING.md#decision). | `tests/test_file_format.py::test_failed_reload_does_not_publish_partial_stage`, `tests/test_file_format.py::test_mesh_failures_and_reload_preservation`, `tests/test_file_format.py::test_nonfinite_position_is_rejected` |
| Extensions | Experimental | `EXT_texture_webp`, `KHR_lights_punctual`, `KHR_materials_clearcoat`, `KHR_materials_ior`, `KHR_materials_sheen`, `KHR_materials_specular`, `KHR_materials_transmission`, `KHR_materials_unlit`, `KHR_materials_volume`, `KHR_mesh_quantization`, `KHR_texture_basisu` and `KHR_texture_transform` are implemented; see their rows. Any other required extension fails, and the diagnostic names it. Other optional extensions warn and are ignored. See the [policy](../architecture/IMPORT_PIPELINE.md#failure-policy). | `tests/test_file_format.py::test_failures_report_diagnostics`, `tests/test_file_format.py::test_node_order_and_optional_extension_warning`, `tests/test_file_format.py::test_unsupported_features_fail_without_publishing` |
| usdview/Storm display | Experimental | The [display check](#display-checks) of simple fixtures through both shader paths, run on one Windows machine. Not general renderer support. | `tests/check_display.py`, `tests/fixtures/reflections.gltf`, `tests/fixtures/alpha_modes.gltf`, `tests/fixtures/base_color_textures.gltf`, `tests/fixtures/pbr_textures.gltf`, `tests/fixtures/normal_textures.gltf`, `tests/fixtures/texture_transforms.gltf`, `tests/fixtures/ktx2_textures.gltf` |
| OpenStrata workspace build and tests | Verified (Windows x86-64, Linux x86-64, macOS arm64; OpenUSD 26.08) | The environments listed under [tested environments](#tested-environments). | [tag workflow 41388405224](https://github.com/animu-sphere/usd-gltf-plugins/actions/runs/41388405224) |
| Standalone OpenStrata bundle | Verified (Windows x86-64, Linux x86-64, macOS arm64; OpenUSD 26.08) | `ost plugin test` confirms discovery of the file format, not a successful import. | `plugins/usdGltfFileFormat/openstrata.plugin.yaml`, [tag workflow 41388405224](https://github.com/animu-sphere/usd-gltf-plugins/actions/runs/41388405224) |
| Root CMake build, install and registration | Verified (Windows x86-64, Linux x86-64, macOS arm64; OpenUSD 26.08) | A separate process registers the installed plugin and opens a stage. | `tests/test_install_smoke.py::test_installed_plugin_registers_and_opens_a_stage`, [tag workflow 41388405224](https://github.com/animu-sphere/usd-gltf-plugins/actions/runs/41388405224) |
| Release package | Verified (Windows x86-64, Linux x86-64, macOS arm64; OpenUSD 26.08) | A bundle package extracted outside the repository registers, opens fixtures, serves KTX2 images and carries its licenses, and packaging twice gives one digest. It works only with the exact runtime it was built for. | `tests/check_package.py`, [tag workflow 41388405224](https://github.com/animu-sphere/usd-gltf-plugins/actions/runs/41388405224) |
| Materials and bindings | Experimental | Default materials and explicit factor-only metallic-roughness materials, with OPAQUE, MASK or BLEND alpha and optional vertex colors: Preview and MaterialX networks and bindings, including `doubleSided`. Network structure is tested in CI; display has the [display check](#display-checks) of alpha and vertex colors. See the [decision record](../architecture/MATERIAL_FACTORS.md). | `tests/test_file_format.py::test_default_material_with_and_without_vertex_color`, `tests/test_file_format.py::test_factor_materials_and_bindings`, `tests/test_file_format.py::test_alpha_modes`, `tests/test_file_format.py::test_explicit_material_vertex_colors`, `tests/test_file_format.py::test_alpha_modes_view_fixture`, `tests/test_file_format.py::test_material_failures_preserve_layer` |
| Unlit materials (`KHR_materials_unlit`) | Experimental | Optional or required. The base color term, with its factor, texture, vertex colors and alpha, is emitted through both networks, which reflect nothing: Preview is a black metal and MaterialX `gltf_pbr` a black dielectric with `specular` 0. Alpha modes, cutoff and `doubleSided` apply. Fallback metallic-roughness, normal, occlusion and emissive properties are not authored, and their images not read; their values and UV sets are still checked. Through Storm's UsdPreviewSurface a dome light still adds a reflection, read from its shader source. A value that is not an object fails. Tested in CI, with the [display check](#display-checks) on Windows. See the [decision record](../adr/0015-unlit.md). | `tests/test_file_format.py::test_unlit_materials`, `tests/test_file_format.py::test_unlit_textures_and_vertex_colors`, `tests/test_file_format.py::test_unlit_failures_preserve_layer`, `tests/check_display.py` |
| Index of refraction (`KHR_materials_ior`) | Experimental | Optional or required. An ior other than 1.5 is authored as the `ior` input of UsdPreviewSurface and MaterialX `gltf_pbr`, uniform in the latter, whose dielectric F0 is glTF's `((ior - 1) / (ior + 1))^2`, read from their sources. An ior of 0 is authored as 1000000, an F0 of 0.999996. Through `gltf_pbr` a little of a rough material's base color still shows at an ior of 0, read from its source. An unlit material authors none, with a warning. A value below 1 other than 0, one too large for a float, a field of the wrong type and a value that is not an object fail. Tested in CI, with the [display check](#display-checks) on Windows, and compared in the [sample check](#sample-assets). See the [decision record](../adr/0016-ior.md). | `tests/test_file_format.py::test_ior_materials`, `tests/test_file_format.py::test_ior_failures_preserve_layer`, `tests/check_display.py` |
| Specular (`KHR_materials_specular`) | Experimental | Optional or required. A weight and color other than 1 are authored as MaterialX `gltf_pbr`'s `specular` and `specular_color`, with the weight texture's A as non-color data and the color texture's RGB as sRGB, factors, UV sets, transforms and wrap modes. `gltf_pbr` computes glTF's dielectric F0 of `min(F0 of the ior * color, 1) * weight` and F90 of the weight, and leaves a metal alone, read from its source. UsdPreviewSurface's metallic workflow has neither and ignores them, with a warning, drawing glTF's default specular. A weight of 0 reads neither texture, a color factor of 0 does not read the color texture, and an unlit material authors none and reads no specular image, with a warning; their values and UV sets are still checked. A weight outside 0 to 1, a negative color component, a number too large for a float, a color that is not three numbers, a field of the wrong type and a value that is not an object fail. Tested in CI, with the [display check](#display-checks) on Windows; the [sample check](#sample-assets) compares it. See the [decision record](../adr/0020-specular.md). | `tests/test_file_format.py::test_specular_materials`, `tests/test_file_format.py::test_specular_textures`, `tests/test_file_format.py::test_specular_failures_preserve_layer`, `tests/check_display.py` |
| Sheen (`KHR_materials_sheen`) | Experimental | Optional or required. A color other than black is authored as MaterialX `gltf_pbr`'s `sheen_color`, and with it a roughness other than 0 as `sheen_roughness`, with the color texture's RGB as sRGB and the roughness texture's A as non-color data, factors, UV sets, transforms and wrap modes. `gltf_pbr` weights a Charlie lobe of roughness `sheen_roughness` squared by the color's largest component and layers it over the base, under the clearcoat, as glTF does; MaterialX's lobe uses the simpler visibility glTF allows and dims the base by its albedo at the view alone, read from its source. UsdPreviewSurface has no sheen and ignores it, with a warning, drawing the base alone. A black color reads neither texture, a roughness of 0 does not read the roughness texture, and an unlit material authors none and reads no sheen image, with a warning; their values and UV sets are still checked. A color component or roughness outside 0 to 1, a number too large for a float, a color that is not three numbers, a field of the wrong type and a value that is not an object fail. Tested in CI, with the [display check](#display-checks) on Windows; the [sample check](#sample-assets) compares it. See the [decision record](../adr/0021-sheen.md). | `tests/test_file_format.py::test_sheen_materials`, `tests/test_file_format.py::test_sheen_textures`, `tests/test_file_format.py::test_sheen_failures_preserve_layer`, `tests/check_display.py` |
| Clearcoat (`KHR_materials_clearcoat`) | Experimental | Optional or required. A factor above 0 is authored as the clearcoat weight and roughness of UsdPreviewSurface and MaterialX `gltf_pbr`, with their textures' R and G as non-color data, factors, UV sets, transforms and wrap modes, and `clearcoatNormalTexture` as `gltf_pbr`'s `clearcoat_normal` only; Preview ignores it, with a warning. `gltf_pbr` layers a dielectric of ior 1.5 over the base, as glTF does, read from its source. Through Storm's UsdPreviewSurface the coat adds its reflection without dimming the base, follows the base normal map, and takes the material's ior, with a warning when that is not 1.5, read from its source. A factor of 0 and an unlit material author nothing and read no clearcoat image, the latter with a warning; their values and UV sets are still checked. A factor outside 0 to 1, one too large for a float, a field of the wrong type and a value that is not an object fail. Tested in CI, with the [display check](#display-checks) on Windows, and compared in the [sample check](#sample-assets). See the [decision record](../adr/0017-clearcoat.md). | `tests/test_file_format.py::test_clearcoat_materials`, `tests/test_file_format.py::test_clearcoat_textures`, `tests/test_file_format.py::test_clearcoat_failures_preserve_layer`, `tests/check_display.py` |
| Transmission (`KHR_materials_transmission`) | Experimental | Optional or required. A factor above 0 is authored as MaterialX `gltf_pbr`'s `transmission`, with its texture's R as non-color data, factor, UV set, transform and wrap modes. `gltf_pbr` mixes its diffuse term toward a transmission tinted by the base color, under the dielectric reflection, as glTF does, read from its source; without a volume, `thickness` keeps its default of 0, glTF's thin material. Storm draws it as transparency, read from its source: the scene behind is blended through without refraction or blur, and dimmed by the tint's mean rather than filtered by it. UsdPreviewSurface has no transmission and ignores it, with a warning; its `opacity` remains glTF's alpha. A factor of 0 and an unlit material author nothing and read no transmission image, the latter with a warning; their values and UV sets are still checked. A factor outside 0 to 1, one too large for a float, a field of the wrong type and a value that is not an object fail. Tested in CI, with the [display check](#display-checks) on Windows, and compared in the [sample check](#sample-assets). See the [decision record](../adr/0018-transmission.md). | `tests/test_file_format.py::test_transmission_materials`, `tests/test_file_format.py::test_transmission_textures`, `tests/test_file_format.py::test_transmission_failures_preserve_layer`, `tests/check_display.py` |
| Volume (`KHR_materials_volume`) | Experimental | Optional or required. On a material with a transmission above 0, a thickness factor above 0 is authored as MaterialX `gltf_pbr`'s `thickness`, with its texture's G as non-color data, factor, UV set, transform and wrap modes, and a given attenuation distance and color as the uniform `attenuation_distance` and `attenuation_color`; without a distance, neither is authored. `gltf_pbr` 1.39.5 connects none of these, read from its source, so no renderer in reach draws the volume: every path draws the thin material that glTF specifies for a client without the extension. UsdPreviewSurface has no volume and ignores it, with a warning. A thickness of 0, a material without transmission and an unlit material author nothing and read no thickness image, the last with a warning; their values and UV sets are still checked. Nothing converts the world-space attenuation distance when a stage scales the asset. A negative thickness, a distance of 0 or less, a color component outside 0 to 1, a number too large for a float, a color that is not three numbers, a field of the wrong type and a value that is not an object fail. Tested in CI, with the [display check](#display-checks) on Windows, and compared in the [sample check](#sample-assets). See the [decision record](../adr/0019-volume.md). | `tests/test_file_format.py::test_volume_materials`, `tests/test_file_format.py::test_volume_textures`, `tests/test_file_format.py::test_volume_failures_preserve_layer`, `tests/check_display.py` |
| Base color textures | Experimental | Factor, alpha modes, UV set and wrap modes in both networks. Filters are not applied. With COLOR_0, Preview omits the vertex color and warns. Tested in CI, with the [display check](#display-checks) on Windows. See the [decision record](../adr/0002-base-color-texture-networks.md). | `tests/test_file_format.py::test_base_color_image_sources_and_networks`, `tests/test_file_format.py::test_base_color_sampler_uv_set_factor_and_alpha`, `tests/test_file_format.py::test_base_color_texture_with_vertex_colors`, `tests/test_file_format.py::test_base_color_textures_view_fixture`, `tests/test_file_format.py::test_material_failures_preserve_layer` |
| Metallic-roughness, occlusion and emissive textures | Experimental | G = roughness, B = metallic and occlusion R as non-color data, and emissive as color, with factors, strength, UV sets and wrap modes in both networks. Filters are not applied. Occlusion has no visible effect through MaterialX `gltf_pbr` and also darkens direct light through Storm's UsdPreviewSurface. Tested in CI, with the [display check](#display-checks) on Windows. See the [decision record](../adr/0003-pbr-texture-networks.md). | `tests/test_file_format.py::test_metallic_roughness_occlusion_and_emissive_networks`, `tests/test_file_format.py::test_pbr_textures_without_terms_they_do_not_need`, `tests/test_file_format.py::test_pbr_textures_view_fixture`, `tests/test_file_format.py::test_pbr_texture_failures_preserve_layer`, `tests/test_file_format.py::test_material_failures_preserve_layer` |
| Normal textures and tangents | Experimental | Scale, UV sets and wrap modes in both networks, as non-color data. Renderers derive the tangent frame, so a map baked against other tangents can differ, and filters are not applied. TANGENT is validated and retained as a primvar that no network reads. Tested in CI, with the [display check](#display-checks) on Windows of uniform images on flat quads. See the [decision record](../adr/0004-normal-textures-and-tangents.md). | `tests/test_file_format.py::test_normal_texture_networks`, `tests/test_file_format.py::test_materials_without_a_normal_texture_author_no_normal`, `tests/test_file_format.py::test_normal_textures_view_fixture`, `tests/test_file_format.py::test_pbr_texture_failures_preserve_layer`, `tests/test_file_format.py::test_tangents_are_retained`, `tests/test_file_format.py::test_tangent_failures_preserve_layer` |
| Texture transforms (`KHR_texture_transform`) | Experimental | Offset, rotation, scale and `texCoord` override, optional or required, in every texture slot through both networks, including one image under different transforms and values of 0 and below. A rotated or mirrored normal map's frame does not turn through Storm's MaterialX path, with a warning. A field of the wrong type and a translation that overflows fail. Tested in CI, with the [display check](#display-checks) on Windows. See the [decision record](../adr/0011-texture-transform.md). | `tests/test_file_format.py::test_texture_transform_networks`, `tests/test_file_format.py::test_texture_transform_uv_set_and_identity`, `tests/test_file_format.py::test_texture_transform_diagnostics`, `tests/test_file_format.py::test_texture_transform_failures_preserve_layer`, `tests/test_file_format.py::test_texture_transforms_view_fixture` |
| PNG and JPEG images | Experimental | bufferView, data URI and restricted local file images, served by a package resolver or a layer-relative path; embedded images resolve after moving an asset and through references. No image is written during import. See the [decision record](../adr/0001-image-assets.md). Import checks structure without decoding: damaged files, sides above 16384 and external files above 2 GiB fail. Not found: a PNG crafted with valid chunks and invalid data, which [crashes OpenUSD's image loader](../adr/0006-image-failures.md#limits) with or without this plugin; damage inside a JPEG scan; and external files damaged after import. Loader behavior was observed on Windows with Storm only. | `tests/image.cpp`, `tests/test_file_format.py::test_base_color_image_sources_and_networks`, `tests/test_file_format.py::test_images_resolve_after_moving_and_through_references`, `tests/test_file_format.py::test_package_resolver_rejects_invalid_entries`, `tests/test_file_format.py::test_texture_failures_preserve_layer`, `tests/test_file_format.py::test_damaged_and_oversized_images_fail_import`, `tests/test_file_format.py::test_unusual_but_valid_images_are_accepted`, `tests/test_file_format.py::test_checks_do_not_decode_image_data`, `tests/test_file_format.py::test_image_damaged_after_import_is_not_served`, `tests/test_file_format.py::test_external_image_file_size_limit` |
| KTX2 (`KHR_texture_basisu`) | Experimental | ETC1S and UASTC LDR 4x4 single 2D images, up to 16384 per side, from bufferViews, data URIs and restricted local files, served as PNG by the package resolver and decoded on each read, without a cache. Other codecs, cubemaps and arrays are rejected. Import reads the header only, so a damaged body fails when read, with a warning, after the layer is published. Tested in CI, with the [display check](#display-checks) on Windows. See the [decision record](../adr/0005-ktx2-decoder.md). | `tests/image.cpp`, `tests/test_file_format.py::test_ktx2_images_are_served_as_png`, `tests/test_file_format.py::test_ktx2_is_preferred_over_its_fallback_and_does_not_warn`, `tests/test_file_format.py::test_ktx2_file_resolves_after_moving_and_on_reload`, `tests/test_file_format.py::test_ktx2_failures_preserve_layer`, `tests/test_file_format.py::test_ktx2_orientation_required_by_the_extension_is_accepted`, `tests/test_file_format.py::test_damaged_ktx2_body_fails_when_read`, `tests/test_file_format.py::test_ktx2_textures_view_fixture` |
| WebP (`EXT_texture_webp`) | Experimental | Still lossy and lossless images, with or without alpha, from bufferViews, data URIs and restricted local files, served as PNG by the package resolver and decoded on each read, without a cache. Used over a plain source, and not over KTX2 when a texture has both. Animations are rejected. Import checks the RIFF chunks and bitstream header, so a truncated file fails import; damage inside a valid bitstream fails when read, with a warning, after the layer is published. Tested in CI only: display has not been checked, and no sample asset has been run. See the [decision record](../adr/0014-webp-decoder.md). | `tests/image.cpp`, `tests/test_file_format.py::test_webp_images_are_served_as_png`, `tests/test_file_format.py::test_webp_is_used_over_its_fallback_and_does_not_warn`, `tests/test_file_format.py::test_ktx2_is_preferred_over_webp`, `tests/test_file_format.py::test_webp_file_resolves_after_moving_and_on_reload`, `tests/test_file_format.py::test_webp_failures_preserve_layer`, `tests/test_file_format.py::test_damaged_webp_body_fails_when_read` |
| Point and line primitives, other vertex attributes, sparse accessors | Unsupported | Rejected with a diagnostic. | `tests/test_file_format.py::test_mesh_failures_and_reload_preservation` |
| Skins and morphs | Unsupported | Rejected when used in the selected scene, including morph targets and node or mesh morph weights. | `tests/test_file_format.py::test_unsupported_features_fail_without_publishing` |
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

Last result: 639 comparisons passed, none failed, with the sheen added, on Windows with OpenUSD 26.08,
the OpenStrata lookdev runtime and an NVIDIA RTX A5000. The result before it, with the specular added,
passed 621 twice, and was the first with the unlit, ior, clearcoat, transmission, volume and specular
comparisons. An earlier result, on
`7de8b8f` with lights added, gave byte-identical images in two runs. This checks simple fixtures, not
general shading correctness.

The [reflection fixture](../../tests/fixtures/reflections.gltf) has four front-facing
triangles: no reflection, a reflected parent, a reflected child, and two reflections. The
first two use supplied normals and the next two use generated normals. With usdview's
backface culling all four are visible and lit alike, and a fifth, deliberately reversed
triangle disappears. Without culling all five are visible. This fixture is rendered through
the engine call that usdview makes, not through usdview, and with the default material's
MaterialX path only.

Lights are checked on a generated matte gray quad, of base color 0.8, lit by one glTF light 1 meter
above its center, through the same engine call but without usdview's light at the camera. Without a
light the quad is black. A point light of 1.5 candela gives its center 97 through MaterialX and 95
through UsdPreviewSurface, where a Lambertian surface under 1.5 lux would give 97 if a nit is a pixel
of 1, as UsdLux defines. In each path, a point light of four times the candela at twice the height, a
directional light of 1.5 lux and a spot light whose cone covers the quad all match it within 3, and a
point light of the same candela at twice the height gives 24. A spot light turned away from the quad
lights it the same, because Storm reads no shaping on a sphere light. A red light gives a red quad.

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

The [texture transform fixture](../../tests/fixtures/texture_transforms.gltf) has 22 quads, compared
with a [reference](../../tests/fixtures/texture_transforms_reference.gltf) that has no extension and
bakes the extension's matrix into its UVs. At 16 points on each quad:

| Cases | MaterialX | UsdPreviewSurface |
| --- | --- | --- |
| Defaults, each field alone, all three at once, rotation before scale, negative scale, mirror, clamp, `texCoord` override with and without a transform, one image under two transforms, emissive, normal map with an offset, a stretch, or a Y tilt mirrored in U | Identical, or within 1 | Identical |
| Normal map rotated either way, or an X tilt mirrored in U | Apart by at least 5, the [documented limit](../adr/0011-texture-transform.md#normal-maps) | Identical |
| A scale of 0 | The image's corner, where its reference is black because baked UVs have no frame | Identical |

Named colors at quadrant centers also check 11 of the cases directly, so that a wrong reference could not
hide a wrong result.

The Khronos TextureTransformTest and TextureTransformMultiTest samples, which are not part of this
check, were rendered through both paths and judged by eye. TextureTransformTest matches its published
screenshot, with each arrow pointing at its green marker. TextureTransformMultiTest shows matching rows
of checks, except that its occlusion row is blank through MaterialX, as
[ADR 0003 documents](../adr/0003-pbr-texture-networks.md#occlusion-differs-between-the-paths), and its
normal row shades differently between columns through MaterialX, as above.

The [KTX2 fixture](../../tests/fixtures/ktx2_textures.gltf) has six quads showing red,
green, blue and white quadrants from upper left to lower right: a PNG as the reference, then
ETC1S, ETC1S with mips and UASTC with Zstandard, then ETC1S and UASTC with alpha of 0.25 in
the lower half under BLEND. Both paths show all six upright. Channels that should be high, at
the quadrant centers, are 250 to 251 for the reference and 251 to 254 for KTX2 through
MaterialX, and 247 to 248 and 248 to 252 through UsdPreviewSurface. The two alpha quads
agree within 3. Swapping their materials once showed that the part above 1 follows position,
through the light, and not the codec.

Unlit materials are checked on a generated quad of base color 0.5, through the same engine call as
lights and without usdview's light at the camera, where a pixel is the linear value. Expected: 128
in darkness, the same under a point light, the same with fallback properties that would make a lit
quad red, metallic, smooth and bumped, and an image of 188 equal to a factor of 0.503.

The index of refraction is checked on a generated black dielectric quad of roughness 0.5, the same
way, under a point light between it and the camera, so that at its center the reflection is F0 times
one specular lobe. Expected: an explicit ior of 1.5 the same as none, an ior of 1 black, an ior of 2
brighter than 1.5, and an ior of 0 the same as a white metal.

The specular is checked on the same quad, under the same light. On Windows with OpenUSD 26.08, the
OpenStrata lookdev runtime and an NVIDIA RTX A5000, through MaterialX, the default reflects 78 in each
channel, an explicit weight of 1 and white color the same, a weight of 0 black, and a weight of 0.5 39,
the same as an ior whose F0 is 0.02. A color of 2 reflects 157, the same as an ior whose F0 is 0.08,
and a red color (78, 0, 0). A weight image alpha of 188 matches a factor of 0.737, and a color image of
188 a factor of 0.503, read as sRGB. Through Storm's UsdPreviewSurface every case reflects 79, the
default.

The sheen is checked on a generated black quad of ior 1 and roughness 1, which reflects nothing itself,
under a directional light at 75 degrees from its normal, because a Charlie lobe adds nothing where the
half vector is the normal. On the same machine, through MaterialX, a black sheen is black, a white sheen
of roughness 0.5 reflects 104 in each channel, one of 0.5 52, half as much, and a red one (104, 0, 0). A
roughness of 1 reflects 230, a roughness image alpha of 188 matches a factor of 0.737, and a color image
of 188 a factor of 0.503, read as sRGB. Over a base of 0.125 gray, which reflects 126, a white sheen
gives 225. Through Storm's UsdPreviewSurface every case is the quad without a sheen: black, and 126 over
the gray base.

The clearcoat is checked on a generated black quad of roughness 1, under the same light, with a coat of
roughness 0.5. Expected in each path: a coat of factor 0 the same as none, a coat of factor 1 brighter,
one of 0.5 between them, a rough coat different from a smooth one, and a coat image of 188 the same as
a factor of 0.737. Over a base of ior 1, which reflects nothing itself, MaterialX's coat is expected to
reflect as a base of ior 1.5 and roughness 0.5, and Storm's UsdPreviewSurface coat to reflect nothing,
because it takes the material's ior.

The transmission is checked on a generated white dielectric quad of roughness 0.5 in darkness, in front
of a green emissive backdrop twice its size, where an opaque quad is black. Expected in each path: a
transmission of 0 and a metal with a transmission of 1 the same as none, and an image of 188 the same
as a factor of 0.737. Through MaterialX, a transmission of 1 is expected to show the green backdrop, one
of 0.5 less, and a red tint some green, less than white, because Storm dims by the tint's mean where
glTF's red would pass none. Through Storm's UsdPreviewSurface, every transmission is expected to stay
black.

The volume is checked on the same scene, with a transmission of 1: a thickness of 1, a red attenuation
at a distance of 0.1, and a thickness image are each expected to look like the thin quad in each path,
because `gltf_pbr` 1.39.5 does not connect its volume inputs. glTF would refract the backdrop and absorb
its green. This shows that the authored inputs do not break the material, not that a volume is drawn.

Multiple primitives sharing one POSITION accessor, on a generated asset, display correctly.
Storm warns, because each mesh authors the accessor's whole vertex array and its topology uses
only part of it.

The Khronos FlightHelmet sample, which is not in this repository and not part of the check,
was once rendered through both paths and looked correct by eye. All of its images use KTX2,
including normal and metallic-roughness data.

The Khronos Avocado and Lantern samples, which are not part of the check, were rendered at 256 pixels
through both paths from their float and `KHR_mesh_quantization` files, whose normals are bytes and so
not exactly unit length. The mean difference is at most 0.07 of 255, and no more than 0.04% of pixels
differ by over 8, the largest by 59. Where those pixels are was not examined. No fixture checks this.

## Sample assets

[`tests/check_samples.py`](../../tests/check_samples.py) checks every file of
[glTF-Sample-Assets](https://github.com/KhronosGroup/glTF-Sample-Assets) at commit `8f49239`, which is
not in this repository: 339 files in 150 models. The result for each file is in
[`tests/sample_baseline.json`](../../tests/sample_baseline.json), and
[ADR 0010](../adr/0010-sample-baseline.md) explains what is compared and its limits.

Last result for opening and meaning: with the sheen added, on Windows with OpenUSD 26.08 and the
OpenStrata lookdev runtime. The 17 files that change all use `KHR_materials_sheen`, which they now
compare instead of ignoring: the SheenTestGrid and SpecularSilkPouf files, which require it, open and
are verified, 9 others go from partial to verified, and the GlamVelvetSofa and SheenChair files remain
partial, because they also use `KHR_materials_variants`. No file's meaning differs. The result
before it, with the specular added, was the first with the clearcoat, transmission, volume and specular
comparisons, and every file it did not change matched the ior result on Linux x86-64 with OpenUSD 26.08
from the `usd-core` wheel, which for every file whose result WebP or unlit materials do not change matched the result on
`7de8b8f` on Windows with the same runtime and an NVIDIA RTX A5000, also the last render. It is not part
of CI. NodePerformanceTest's GLB times out, because its 10,000 materials read 20,000 embedded images, and
outside a resolver cache scope each read parses the 38 MB file again, about 0.1 second each.

| Result | Files | What it means |
| --- | --- | --- |
| Opened | 243 | The stage opens. No file crashed, and one timed out after 600 seconds. |
| Rejected | 95 | Each has a diagnostic naming a cause the file really has. |
| Meaning verified | 176 | USD agrees with values decoded from the file, in all that is compared, and the file uses no ignored extension. |
| Meaning partial | 67 | The same, but the file uses an optional extension that is ignored, with a warning. What it changes is not compared. |
| Meaning differs | 0 | |
| Rendered | 236 | Storm drew something through both shader paths. No pixel was judged, so this is not evidence of correct display. The 17 files that use a sheen were rendered with it, on the same machine with an NVIDIA RTX A5000. The 7 files that WebP, unlit materials and the clearcoat open have not been rendered, and those that use an ior, a clearcoat, a transmission, a volume or a specular were last rendered without it. |

Why files are rejected, read from each file. Most files have several causes, so a count of files that
have a cause is not what supporting it would open:

| Cause | Files with it | Files with only it |
| --- | --- | --- |
| Animations | 69 | 31 |
| Skin | 24 | 0 |
| Requires `KHR_draco_mesh_compression` | 18 | 11 |
| Morph targets | 10 | 2 |
| Requires a material extension | 5 | 5 |
| Requires `KHR_node_visibility` | 4 | 0 |
| Requires meshopt compression | 5 | 2 |
| Points or lines | 3 | 3 |
| Sparse accessor | 2 | 2 |

Ignored optional extensions, in files that opened, include `KHR_materials_iridescence` (16),
`KHR_materials_variants` (15) and `KHR_materials_anisotropy` (13).
