# 0004: Normal textures and tangents

Decision record, 2026-11-01. It extends the
[PBR texture decision](0003-pbr-texture-networks.md), and image paths follow
[ADR 0001](0001-image-assets.md). The
[capability matrix](../reference/CAPABILITY_MATRIX.md) records current evidence.

## Decision

| Purpose | `preview` | `mtlx` |
| --- | --- | --- |
| Image | `normalTexture` (`UsdUVTexture`, `raw`) | `normalImage` (`ND_image_vector4`, no color space) |
| To a vector | `scale` (2s, 2s, 2, 1), `bias` (-s, -s, -1, 0); `rgb` to `normal` | `normalXyz` (`ND_convert_vector4_vector3`); `normalMap` (`ND_normalmap_float`, `scale` s when not 1) to `normal` |

Here s is the glTF `scale`, which applies to X and Y only. Both graphs compute glTF's
`normalize((2 * image - 1) * (s, s, 1))`. A slot using a UV set the primitive lacks
fails, as does a non-finite scale. Any finite scale is accepted, because glTF does not
limit it.

Coordinates are not adjusted for the
[flipped V](../design/SCENE_CONVENTIONS.md#texture-coordinates). Flipping makes V
increase toward the image's top, which is the +Y of glTF's normal texture, so a frame
derived from `st` already has the right handedness.

### Tangents

Decision revised 2026-11-13 after testing the Khronos reference shaders and the installed
OpenUSD and MaterialX runtime. Preserve the mesh-local frame in USD and use the
renderer-derived frame in both shading networks.

- UsdPreviewSurface exposes a tangent-space normal, with the renderer responsible for its
  basis. It has no tangent, bitangent or anisotropy input.
- MaterialX `normalmap` accepts explicit world-space tangents and bitangents.
  `gltf_pbr` accepts a world-space tangent and rotates it around the shading normal.
  Storm can read a vector primvar, but its generated `transformvector` shader passed the
  vector through in the tested runtime. Connecting mesh-local vectors would give incorrect
  shading under transformed nodes. Pretransforming the data would freeze it in the glTF
  scene's coordinate system, causing errors under a USD reference, an animated transform,
  or deformation. The importer requires ordinary USD composition.
- The Khronos Sample Renderer transforms tangent XYZ by the model matrix, transforms the
  geometric normal by its inverse transpose, and computes the world bitangent with
  `cross(N, T) * W`. It applies anisotropy direction in the geometric tangent plane.
  MaterialX's built-in rotation uses the shading normal. A connection that supplies only
  the tangent would still lose W in anisotropy rotation and can differ when a normal map
  tilts the shading normal.

Keep supplied TANGENT as vertex-interpolated `gltf_tangent`. Validate its component
type, count and finiteness as before. For a triangle primitive whose active material uses
a normal map, clearcoat normal map or anisotropy, generate a separate
`gltf_generatedTangent` primvar when a supplied frame cannot be used. A supplied tangent
requires a supplied NORMAL. Otherwise use the available normals, including the flat
normals the importer authors when glTF omits NORMAL.

Use the reference MikkTSpace algorithm with the base normal texture's UV set, including
any `texCoord` override. In the absence of that texture, use the active clearcoat normal
texture's set, the active anisotropy texture's set, or UV set 0, in that order. Compute in
glTF's original UV convention before the V conversion and before sampling transforms.
Store the returned XYZ and negate the returned sign, as Khronos does, to account for
normal-map +Y pointing opposite to increasing glTF V. Store the frame per face corner
so mirrored UVs can share a position index and keep distinct signs. Both primvars use
`B = cross(N, T) * W` in mesh space.
The generated frame describes the base mesh. Deforming it is the consumer's responsibility.
Omit it when the inputs cannot define a frame, including a missing UV set or normals
that the consumer must compute after deformation. The renderer's fallback remains
available in these cases. Skip generation for position or UV magnitudes above 1e8,
more than 1024 corners at the same position, or a face count outside MikkTSpace's
integer range. These bounds protect the generator's float math and limit the cost
and recursion depth of its grouping pass.

Frame connections can be reconsidered when the target renderer can transport the
explicit frame through USD composition. Preview's standard interface still delegates
its normal frame to the renderer.

## Evidence and limits

[Manual visual checks](../reference/CAPABILITY_MATRIX.md#display-checks) compared
each normal map with a flat quad whose supplied NORMAL is the expected result, through
both paths in Storm, with uniform images on flat quads. This establishes direction,
handedness, scale and UV set selection in one renderer. It does not compare a baked
map on a curved mesh with glTF's reference, or measure the frame difference above.

Why Storm's MaterialX frame follows the normal texture's UV set was not determined. Its
shader text derives the frame from one named primvar, and both orders of two sets gave
the expected result. Treat that as observed, not as guaranteed for more sets.

The [tangent frame probe](../../tests/probe_tangent_frames.py) evaluates the Khronos
renderer shaders for explicit tangents and the renderer's MikkTSpace Wasm output for
generated tangents. It compares Storm against shading controls with those directions.
The [capability matrix](../reference/CAPABILITY_MATRIX.md#tangent-frame-comparison)
records the results.

## Sources

- [glTF 2.0 mesh attributes](https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html#meshes)
- [UsdPreviewSurface specification](https://openusd.org/release/spec_usdpreviewsurface.html)
- [MaterialX 1.39.5 normalmap GLSL](https://github.com/AcademySoftwareFoundation/MaterialX/blob/v1.39.5/libraries/stdlib/genglsl/mx_normalmap.glsl)
- [MaterialX 1.39.5 gltf_pbr](https://github.com/AcademySoftwareFoundation/MaterialX/blob/v1.39.5/libraries/bxdf/gltf_pbr.mtlx)
- [Khronos Sample Renderer vertex shader](https://github.com/KhronosGroup/glTF-Sample-Renderer/blob/cc3d5ff52217a63161c5c876677292546c01564a/source/Renderer/shaders/primitive.vert)
- [Khronos Sample Renderer normal and anisotropy shaders](https://github.com/KhronosGroup/glTF-Sample-Renderer/blob/cc3d5ff52217a63161c5c876677292546c01564a/source/Renderer/shaders/material_info.glsl)
