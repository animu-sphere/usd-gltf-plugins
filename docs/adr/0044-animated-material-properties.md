# 0044: Animated material properties

Decision record, 2026-11-22. It decides how `KHR_animation_pointer` channels that target material
properties become USD. It keeps the timeline, interpolation and validation of
[node animation](0029-node-animation.md), the `animation` variants of
[ADR 0030](0030-animation-choice.md) and the pointer handling of
[animated node visibility](0037-animated-node-visibility.md). The
[material design](../design/MATERIALS.md) specifies the result, the
[capability matrix](../reference/CAPABILITY_MATRIX.md) records what is implemented, and the
[roadmap](../roadmap/current.md) holds the conversion.

## Context

[`KHR_animation_pointer`](https://github.com/KhronosGroup/glTF/tree/main/extensions/2.0/Khronos/KHR_animation_pointer)
targets a property of the [glTF Asset Object Model](https://github.com/KhronosGroup/glTF/blob/main/specification/2.0/ObjectModel.adoc)
by JSON pointer. Under `/materials/{}` it lists `alphaCutoff`, `emissiveFactor`, `normalTexture/scale`,
`occlusionTexture/strength`, the three `pbrMetallicRoughness` factors, the factors of every ratified
material extension the importer reads, and the `offset`, `rotation` and `scale` of
`KHR_texture_transform` on the texture slots of the core material and of those extensions. Diffuse
transmission is not in it. Each is a float, float2, float3 or float4.
The pointer must name a property that the asset defines, explicitly or as a default inside an object that
is present, so a transform pointer needs the slot's `KHR_texture_transform` object. An output may be
float or an integer type, normalized or not, and interpolation happens after conversion to float. One
animation must not target a property twice. The README states no valid range for animated values beyond
"valid for the property being animated".

Three Khronos samples animate materials, each in one optional animation with `LINEAR` float samplers:

| Sample | Targets |
| --- | --- |
| AnimatedColorsCube | `baseColorFactor` of one material, 151 keyframes over 2.5 s |
| AnimationPointerUVs | 103 channels to the `offset`, `rotation` or `scale` of 99 texture slots of 78 materials, two keyframes over 6 s: offset by (-1, 1), rotation by 90°, scale from 1 to 3. 12 channels target `KHR_materials_diffuse_transmission` textures, which the importer does not author, and 4 repeat another channel's property with the same keyframes, which the extension forbids |
| PotOfCoalsAnimationPointer | The `rotation` of a normal texture, 0 to 2π over 4 s, and of a volume thickness texture, 2π to 0 |

Every targeted transform object exists in these files. The importer ignores all these channels today,
with a warning.

The converted networks do not hold glTF's values one to one:

- UsdPreviewSurface folds values together. Its emissive color is the factor times
  [the strength](0035-emissive-strength.md), a texture's `scale` and `bias` carry factors, and
  [a texture transform](0011-texture-transform.md#from-the-extensions-coordinates-to-st) is substituted
  into `st`, so its translation holds `ox + sy sin r` and `1 - oy - sy cos r`. With COLOR_0 the base
  color factor is multiplied into a primvar on each mesh.
- Both paths author some inputs only for some values: a clearcoat, sheen, transmission, iridescence or
  anisotropy only above 0, a specular weight or color, or an emissive strength, only where it differs
  from 1, a texture transform only where a field differs from its default, and Preview's opacity
  threshold only for a cutoff above 0.
- One glTF material can be several USD materials: with and without vertex colors, and the `_Unlit` and
  `_NoNormalTexture` copies for [points and lines](0042-points-and-lines.md).

A USD attribute's time samples are interpolated linearly by the stage. An animated value must therefore
be authored so that its linear interpolation stays close to glTF's value of the converted input, not of
the glTF property. MaterialX also has a `time` node, which outputs the renderer's current time, so a
graph could compute a value from time without samples.

## Experiment

[`tests/probe_material_animation.py`](../../tests/probe_material_animation.py) converts one-quad files
whose material property moves linearly between keyframes at 0 and 2 seconds, and animates the
converted networks in a session layer, by each candidate below. usdrecord renders each animated stage
at time codes 0, 6, 12, 24, 36, 47 and 48 in one run, as a player would. Each frame is compared with
the static file at that time's value, converted and rendered on its own, at nine points of the quad. It
ran with Storm on Windows with OpenUSD 26.08.

- **Keyframes:** each input whose converted value differs between the keyframes gets its two
  converted values as time samples.
- **Pieces:** the converted values at 16 equal pieces of the segment.
- **Pivot:** a texture transform is converted about the point (0, 1). A node that subtracts (0, 1)
  comes before it, and its translation becomes `(ox, 1 - oy)`, so each glTF value maps to one input by
  an affine function, sampled at the keyframes only.
- **Time node:** MaterialX's `base_color` is mixed from the two colors by `time / 2` seconds, clamped,
  with no time samples.

The largest difference of any channel, in 8-bit display values, over every frame:

| Case | Keyframes | Pieces | Pivot | Time node |
| --- | --- | --- | --- | --- |
| `baseColorFactor` | 0 in both paths | 0 | | 162: every frame equals time 0 |
| `baseColorFactor` with a texture | 0 in both | 0 | | |
| `emissiveFactor` and `emissiveStrength` together | MaterialX 0, Preview 10 | 0 | | |
| Texture `rotation` | MaterialX 216, Preview 213 | 3 | MaterialX 0, Preview 153, also at time 0 | |
| `offset`, `rotation` and `scale` together | MaterialX 199, Preview 197 | 1 | MaterialX 0, Preview 209, also at time 0 | |

Storm drew every time-sampled input at the frame's time, in both paths and through one run, including
inputs of nodes inside the node graphs: `UsdUVTexture.scale`, `UsdTransform2d` and the MaterialX
`multiply`, `rotate2d` and `add` nodes. Keyframes alone are exact where an input is an affine function
of one animated property, and wrong where it is not: a product of two animated values, or the
translation that a rotation moves. Pieces were within the comparison's tolerance of 3 everywhere.

The pivot was exact in MaterialX. In Preview, Storm drew the quad untextured at every time, including
time 0, without a diagnostic: it does not draw a texture whose `st` passes through a second
`UsdTransform2d`.

The time node gave the same image at every frame as at time 0. MaterialX's hardware shader generator
names its time uniforms `u_time` and `u_frame`. Neither name occurs in Storm's library, `usd_hdSt`,
which does name other MaterialX uniforms such as `u_envMatrix` and `u_viewPosition`.

For the samples, keyframes alone would move a rotated texture by up to 0.29 of its size in
AnimationPointerUVs, and up to 2 in PotOfCoalsAnimationPointer, whose rotation's translation is the
same at 0 and 2π. Staying within 1e-4 of the texture's size, at a scale of 1, takes 56 pieces of a 90°
segment, 112 of a 180° one and 223 of a full turn.

## Decision

**Which pointers.** Read `/materials/{index}/...` for every float property of the Object Model whose
material is used in the selected scene, where the index is a canonical, in-range decimal index as for
nodes. The channel is checked as [ADR 0037](0037-animated-node-visibility.md) checks a visibility
channel: `path: "pointer"`, no `target.node`, and a property pointer. Its output has the property's
type, SCALAR, VEC2, VEC3 or VEC4, of float or integer components converted to float as the extension
says, with one value per keyframe, or three for `CUBICSPLINE`. A pointer whose enclosing object is
absent, a property targeted twice in one animation with different keyframes, and a keyframe value
that the static conversion would reject, such as a roughness above 1, are invalid, and fail the
import whether or not the extension is required. A channel that repeats another's property with the
same interpolation, times and values changes nothing, and is ignored with a warning, so that
AnimationPointerUVs opens. A material outside the selected scene is not sampled.

**What the channel animates.** The value of every input at a time is the converted value of the
material with its animated properties at their glTF values at that time, computed by the same functions
as the static conversion, in every USD material made from that glTF material. The networks keep the
structure of the static conversion, with these changes:

- An input that exists only for some values is authored when the stated value or any keyframe would
  author it. Its value elsewhere is the one that draws the same as leaving it out, such as a clearcoat
  of 0 or an emissive strength of 1. A texture that a value of 0 would leave unread is read.
- An animated texture transform authors its nodes, as [ADR 0011](0011-texture-transform.md) specifies,
  even where its stated fields are all defaults. The normal map warning in that decision applies when
  the stated value or any keyframe rotates or mirrors the image.
- An animated anisotropy rotation is not reduced modulo a turn, so its samples do not wrap.
- A connected input's own value is not drawn, so no animation changes it, as with a base color that a
  texture or COLOR_0 supplies to UsdPreviewSurface.

**Time samples.** Each input's samples follow [ADR 0029](0029-node-animation.md) on the union of the
keyframes of the channels it depends on: `STEP` holds 1e-7 time codes before each keyframe, and a
segment is cut into equal pieces until the lines between them stay within 1e-4 of the input's value at
the middle of each piece, times the larger of 1 and the input's largest value in the segment. That
leaves an input that is affine in one `LINEAR` property with its keyframes alone, and cuts a translation
moved by a rotation, a product of two animated values, and a `CUBICSPLINE` curve. A segment is cut into
at most 256 pieces, with a warning when it needs more. Values between keyframes are not clamped.
Each input then leaves out the samples that lie on the line between the ones it keeps, within 1e-6 of
the same scale, so a rotation keeps its keyframes while the translation it moves keeps its pieces.

**Where the samples are.** As for node visibility, an animated input has no value outside the
`animation` variants. Every variant authors its stated converted value as the default, and the
animation with a channel on it authors the time samples. Its keyframes count towards the animation's
and the layer's end time codes. Material networks remain under `/Root/Materials`, shared by every
`materialVariant` choice.

**Not animated, with a warning.**

- A property that conversion leaves out of both networks: one of an extension that it ignores with a
  warning, such as diffuse transmission, whether or not the Object Model lists it, a lit property of an
  unlit material, or `alphaCutoff` outside `MASK`. It changes nothing authored, so it is skipped even
  when the pointer extension is required.
- UsdPreviewSurface's per-mesh products of the base color factor and COLOR_0. Animating them would
  sample a primvar on every mesh. Preview keeps the stated factor on primitives with COLOR_0, and
  MaterialX animates it.

**Not supported.** These are reported, skipped when `KHR_animation_pointer` is optional, and rejected
when it is required, as other unsupported pointers are:

- A pointer to a non-material property other than node visibility, or to a material property that the
  Object Model does not list and the bullet above does not cover, such as `texCoord` or `alphaMode`.
- An `ior` keyframe of 0, which the conversion maps to 1000000, and an `attenuationDistance` whose
  stated value is the infinite default, which the conversion leaves unauthored. Neither has a value
  that USD can interpolate to.
- `alphaCutoff` keyframes of 0 on a `MASK` material, which UsdPreviewSurface reads as no masking.

**Rejected alternatives.**

- Authoring glTF's keyframes on the converted inputs alone. It is exact for most factors and wrong by
  up to the texture's size for a rotation.
- Converting animated transforms about the pivot (0, 1). It is exact in MaterialX with the keyframes
  alone, but Storm cannot draw it in UsdPreviewSurface, and two transform structures would differ
  between the paths for one glTF value.
- MaterialX's `time` node. Storm does not supply its time. It would also read the renderer's time,
  without the layer offset and scale of a reference, could not follow the `animation` variant chosen,
  needs nodes per keyframe to interpolate, and UsdPreviewSurface has no equivalent.

## Consequences

- AnimatedColorsCube, AnimationPointerUVs and PotOfCoalsAnimationPointer animate their materials,
  except AnimationPointerUVs's 12 diffuse transmission channels, which it warns about.
- An animated input's value exists only inside the `animation` variants.
- The translation of a texture transform rotated by an animation has on the order of a hundred time
  samples per keyframe segment in each path, depending on the angle and scale. Each translation in
  PotOfCoalsAnimationPointer has 257, and each rotation 2. Factors that are animated alone keep their
  keyframes, and importing AnimationPointerUVs took no measurably longer.
- A material used with COLOR_0 animates its base color factor in MaterialX only.

## Limits

- A selection that names no `animation` variant leaves animated inputs at the shaders' own defaults,
  without a diagnostic, as [ADR 0030](0030-animation-choice.md) leaves animated nodes.
- The tolerance is checked at the middle of each piece, not bounded.
- The experiment and the [display check](../reference/CAPABILITY_MATRIX.md#display-checks) rendered
  with Storm only, at whole time codes, on one Windows machine. Other renderers may not update
  material inputs over time.
- The display of `CUBICSPLINE` and `STEP` material channels was not compared; their sampling is that of
  node animation.
