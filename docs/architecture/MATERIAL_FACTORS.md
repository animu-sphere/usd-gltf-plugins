# Factor-only material decision

Decision record, 2026-11-01. The [capability matrix](../reference/CAPABILITY_MATRIX.md)
records current implementation and evidence; this is not a renderer support claim.

## Decision

For explicitly assigned metallic-roughness materials without textures, author
one `Material` per glTF material index below `/Asset/mtl` and bind it to each
selected mesh primitive. A material used by primitives with COLOR_0 gets a
second `Material_N_VertexColor`, since its graphs differ. Preserve material names
as display names and apply `doubleSided` to each bound mesh.

Each material contains `preview` and `mtlx` NodeGraphs, each with a terminal
Shader named `surface`, following usd-vrm-plugins. The universal surface
output connects through `preview.outputs:surface` to `preview/surface`
(`UsdPreviewSurface`), using the metallic workflow and mapping base color,
metallic, roughness and emissive factors to `diffuseColor`, `metallic`,
`roughness` and `emissiveColor`. The `mtlx` surface output connects through
`mtlx.outputs:surface` to `mtlx/surface` (`ND_gltf_pbr_surfaceshader`), with
`base_color`, `metallic`, `roughness` and `emissive` mapped from the corresponding
glTF factors. The node's default `emissive_strength` is 1. Auxiliary shader
nodes are placed in the corresponding NodeGraph. Renderer implementations and
the Preview BRDF may differ from glTF's reference shading.

`alpha_mode` is OPAQUE (0), MASK (1) or BLEND (2). OPAQUE ignores base color
alpha and authors no alpha inputs. MASK and BLEND author `alpha` and Preview
`opacity`, and MASK adds `alpha_cutoff` and `opacityThreshold`. A negative or
non-finite cutoff fails. A MASK cutoff of 0 authors no Preview opacity, because
glTF renders it opaque while a Preview threshold of 0 would blend.

For primitives without a material index, bind a shared default material using
the glTF default factors, single-sided and opaque. Where COLOR_0 exists, a
separate default material reads `displayColor` into each shader's base color
using `displayColorReader` nodes (`UsdPrimvarReader_float3` and
`ND_geompropvalue_color3`), with white fallbacks. COLOR_0 alpha is ignored,
as required for OPAQUE.

An explicit material with COLOR_0 multiplies the factors by the vertex values.
In `mtlx`, `ND_multiply_color3` and `ND_multiply_float` nodes (`baseColorFactor`
and `alphaFactor`) follow the readers when the factor is not 1. UsdPreviewSurface
has no multiply node, so `preview` reads per-mesh product primvars instead. The
alternatives were rejecting tinted vertex colors, which loses valid assets, or
ignoring the factor in Preview, which is wrong; baking is exact for factors and
costs a second vertex array only when the factor is not 1. It cannot extend to a
texture multiplied by COLOR_0, which needs its own decision. Vertex alpha is read
only for MASK and BLEND, and a primitive without it falls back to the factor
alpha in Preview and 1 before the multiply in MaterialX.

Reject texture references; they require image handling and additional graph
work. Required extensions remain rejected, while optional extensions are warned
about and ignored under the existing import policy.

Material networks and bindings use `UsdShade` on the temporary authoring stage.
The conversion runs on a
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

Those are structural USD checks. They passed while `gltf:colorAlpha` made every
MaterialX graph reading it fail to compile in Storm, which is why these primvars
avoid colons: [structure alone does not establish display](../reference/CAPABILITY_MATRIX.md#manual-visual-checks).
Renderers may differ in their MaterialX support and shading approximations.
Texture reads require further decisions and verification.

References: [UsdPreviewSurface specification](https://openusd.org/release/spec_usdpreviewsurface.html),
[MaterialX glTF PBR definition](https://github.com/AcademySoftwareFoundation/MaterialX/blob/main/libraries/bxdf/gltf_pbr.mtlx),
and [glTF 2.0 metallic-roughness materials](https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html#materials).
