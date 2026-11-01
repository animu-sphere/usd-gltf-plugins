# Factor-only material decision

Decision record, 2026-11-01. The [capability matrix](../reference/CAPABILITY_MATRIX.md)
records current implementation and evidence; this is not a renderer support claim.

## Decision

For explicitly assigned, opaque metallic-roughness materials without textures
or vertex colors, author one `Material` per glTF material index below
`/Asset/mtl` and bind it to each selected mesh primitive. Preserve material
names as display names and apply `doubleSided` to each bound mesh.

Each material contains `preview` and `mtlx` NodeGraphs, each with a terminal
Shader named `surface`, following usd-vrm-plugins. The universal surface
output connects through `preview.outputs:surface` to `preview/surface`
(`UsdPreviewSurface`), using the metallic workflow and mapping base color,
metallic, roughness and emissive factors to `diffuseColor`, `metallic`,
`roughness` and `emissiveColor`. The `mtlx` surface output connects through
`mtlx.outputs:surface` to `mtlx/surface` (`ND_gltf_pbr_surfaceshader`), with
`base_color`, `metallic`, `roughness` and `emissive` mapped from the corresponding
glTF factors, and `alpha_mode` set to OPAQUE (0). The node's default
`emissive_strength` is 1. Auxiliary shader
nodes are placed in the corresponding NodeGraph. Base color alpha is ignored
for OPAQUE. Renderer implementations and the Preview BRDF may differ from
glTF's reference shading.

For primitives without a material index, bind a shared default material using
the glTF default factors, single-sided and opaque. Where COLOR_0 exists, a
separate default material reads `displayColor` into each shader's base color
using `displayColorReader` nodes (`UsdPrimvarReader_float3` and
`ND_geompropvalue_color3`), with white fallbacks. COLOR_0 alpha is ignored,
as required for OPAQUE.

Reject texture references, MASK and BLEND, and explicitly assigned materials
on primitives with COLOR_0; they require additional graph work to preserve
glTF behavior. Required extensions remain rejected, while optional extensions
are warned about and ignored under the existing import policy.

Material networks and bindings use `UsdShade` on the temporary authoring stage;
geometry is authored as Sdf specs on the same layer. The conversion runs on a
worker thread as described in the [layer authoring decision](LAYER_AUTHORING.md),
and is published only after the selected scene converts successfully.
Material definitions are created on demand and reused for all selected
primitives referring to the same index.

## Evidence and limits

On the local Windows OpenStrata cy2026/OpenUSD 26.08 runtime, Sdr registers
both shader IDs, the color reader IDs, and the inputs and outputs used above. The integration tests
check authored values, shader identifiers, surface connections and context
fallback, sidedness, material reuse, reference composition and reload for
both glTF and GLB; failure tests exercise unsupported material features and
invalid factors without publishing the temporary layer.

These are structural USD checks, not display tests. Renderers may differ in
their MaterialX support and shading approximations. The graph and behavior
for vertex color multiplication, alpha modes, texture reads and the default
material require further decisions and verification.

References: [UsdPreviewSurface specification](https://openusd.org/release/spec_usdpreviewsurface.html),
[MaterialX glTF PBR definition](https://github.com/AcademySoftwareFoundation/MaterialX/blob/main/libraries/bxdf/gltf_pbr.mtlx),
and [glTF 2.0 metallic-roughness materials](https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html#materials).
