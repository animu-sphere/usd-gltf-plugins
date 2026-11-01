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

The renderer derives the tangent frame in both paths. A TANGENT attribute is validated
and retained as a primvar, but no shading network reads it. The options were:

- Keep rejecting TANGENT, which loses many valid assets.
- Accept and discard it, which drops data that a consumer could not recover.
- Retain it without using it for shading.
- Read it in the MaterialX graph, through `ND_geompropvalue_vector3` and the `tangent`
  and `bitangent` inputs of `normalmap`.
- Generate MikkTSpace tangents when absent, as glTF asks, and read them likewise.

Choose retention. Reading tangents would be possible only in MaterialX, since
UsdPreviewSurface has no tangent input, so the two paths would use different frames. A
material used with and without TANGENT would also need two graphs and two `Material`
prims, as vertex colors do. Generated tangents would add a dependency and still reach
only one path.

Derived frames can differ from those a normal map was baked against. This most affects
maps baked for smooth meshes against interpolated tangents, for example across UV
seams. No warning is issued, because the same difference applies to an asset without
TANGENT, so a warning would describe the attribute's presence rather than a defect.

Validation requires VEC4 floats with the same count as POSITION, and finite values. It
does not check unit length or the sign of W, because nothing consumes them and failing
an otherwise usable asset on them would gain nothing.

## Evidence and limits

[Manual visual checks](../reference/CAPABILITY_MATRIX.md#display-checks) compared
each normal map with a flat quad whose supplied NORMAL is the expected result, through
both paths in Storm, with uniform images on flat quads. This establishes direction,
handedness, scale and UV set selection in one renderer. It does not compare a baked
map on a curved mesh with glTF's reference, or measure the frame difference above.

Why Storm's MaterialX frame follows the normal texture's UV set was not determined. Its
shader text derives the frame from one named primvar, and both orders of two sets gave
the expected result. Treat that as observed, not as guaranteed for more sets.
