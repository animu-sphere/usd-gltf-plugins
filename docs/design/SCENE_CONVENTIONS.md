# Scene conventions

This document defines what authored USD data means. The
[capability matrix](../reference/CAPABILITY_MATRIX.md) records what is
implemented.

## Structure and names

```text
/Root                          Xform, defaultPrim
  /Scenes                      Scope
    /Scene_0                   Xform, or SkelRoot; the selected scene
      /Node_0                  Xform
        /Mesh_0                Xform
          /Primitive_0         Mesh
        /Camera_0              Camera
        /Light_0               SphereLight or DistantLight
        /Node_1                Xform
      /Node_2                  SkelRoot, a node where skins need one
        /Skeleton_0            Skeleton
        /Node_3                Xform
          /Mesh_1              Xform
            /Primitive_0       Mesh, skinned
  /Materials                   Scope
    /Material_0                Material
  /Animations                 Scope, only where needed
    /Animation_0               SkelAnimation, where one applies
```

The tree illustrates the kinds of prims. A skel root or `Animations` scope appears only when needed.
The selected scene is `Scene_{index}` below `/Root/Scenes`, with no transform of its own. It preserves
the glTF node hierarchy below it. The importer authors only the [selected scene](#scene-selection).
Materials are collected under `/Root/Materials` and bound from geometry. See the
[decision record](../adr/0026-scene-hierarchy.md).

Each node is an `Xform` with the node's local transform. Its mesh, camera and light are children of
that `Xform`, not substitutes for it. Each mesh is an `Xform` containing one USD Mesh per glTF
primitive, which retains the primitive boundaries needed for material bindings. Nodes and meshes are
distinct concepts: a mesh that several nodes use is converted once, at the first of them in
conversion order, and copied below the others, so that a shared representation can replace the
copies later.
No mesh definitions are authored where they would render, such as a visible `/Root/meshes`.

| Source | Prim name |
| --- | --- |
| Scene | `Scene_{sceneIndex}` |
| Node | `Node_{nodeIndex}` |
| Mesh | `Mesh_{meshIndex}` |
| Mesh primitive | `Primitive_{primitiveIndex}`, the index within its mesh |
| Camera | `Camera_{cameraIndex}` |
| Light | `Light_{lightIndex}` |
| Material | `Material_{materialIndex}` |
| Skin | `Skeleton_{skinIndex}` |
| Animation | `Animation_{animationIndex}` |
| Skeleton joint | A token of the node's `Node_{index}` path from the scene, such as `Node_0/Node_2` |

glTF names can be empty, repeated, or invalid in a USD path, so they are display names. Each prefix
names one kind, and each index occurs once below a parent, so names do not collide. A node that has a
prim has the path of its glTF parents, but a joint can be a skeleton joint rather than a prim.
Conversion keeps a map from source indices to USD paths and joints, and tests check names, parents
and world transforms.

## Skeletons and animation

Each glTF skin that the selected scene uses is a `UsdSkelSkeleton` named `Skeleton_{skinIndex}`. See
the [decision record](../adr/0028-skin-placement.md).

- The skeleton's space is the parent of its joints' common root when that root is a joint, otherwise
  the common root, or the scene. The skeleton prim is a child of that node's prim.
- Its joints are the skin's joints and the nodes between them and the space, parents first and
  siblings in glTF order. Each is the token of its node's path from the scene, so a node has the same
  token in every skeleton. `jointNames` holds the node's name, with `Node_{index}` as the fallback.
  Joint indices are remapped to this order.
- Rest transforms are the nodes' local transforms. Bind transforms are the inverses of the inverse
  bind matrices, in the skeleton's space. An absent inverse bind accessor supplies identity
  matrices. A node between joints has its rest transform in the skeleton's space.
- Skins that share joints are separate skeletons. A skin that several nodes use binds each of their
  meshes to one skeleton. Each skinned primitive binds independently with identity
  `geomBindTransform` and vertex joint index and weight primvars. Each consecutive pair of
  `JOINTS_n` and `WEIGHTS_n` accessors contributes four influences per vertex. The importer
  normalizes the weights together and uses those attributes on placements with a skin.

A joint node is only a skeleton joint, without an `Xform`, unless something must be below it: a mesh,
camera or light, a skeleton whose space it is, or a child that keeps a prim. A joint that keeps a prim
uses the same source transform and animation values for its `Xform` and skeleton joint.

For each skin, the deepest common ancestor of its skeleton prim and its skinned meshes is a
candidate skel root. Each node with an unskinned morphing mesh is also a candidate. Candidates below
another candidate are omitted. The remaining prims become `UsdSkelRoot` in place and keep their
paths, display names and transform operations.

A skinned node keeps its transform and animation for its other children; neither applies to its
skinned mesh, as glTF requires.

A node targeted by animation must use TRS in glTF. A node with a transform channel has
`translate` (`double3`), `orient` (`quatf`) and `scale` (`float3`) operations, in that order. Each has
the node's own value as its default, and an animated one also has time samples. See the
[decision record](../adr/0029-node-animation.md).

`/Root` has a variant set `animation` with a variant `Animation_{index}` for every glTF animation, and
selects `Animation_0`; an asset without animations has none. See the
[decision record](../adr/0030-animation-choice.md).

- A node that any animation moves has its operations and their order outside the variants, without
  values. Each variant authors the node's own values as defaults and its animation's channels as time
  samples. A node that no animation moves keeps a matrix.
- A variant with joint or morph values defines a `UsdSkelAnimation` `Animation_{index}` below
  `/Root/Animations`. It binds through `skel:animationSource` on skeletons with joints that it
  moves or with meshes that morph. Its translation, rotation and scale arrays are sampled at the
  union of the joint channels' sample times. An unanimated component uses its node's own value.
  At default time, skeletons use their rest transforms.
- A camera's or light's correction for scale below a node that any animation moves is inside each
  variant.
- `customData` on `/Root` inside a variant has a dictionary `gltf` with the animation's
  `endTimeCode` and, when it has one, its `name`.

- A time code is a glTF second times 24. A layer with an authored channel has `timeCodesPerSecond`
  and `framesPerSecond` of 24, `startTimeCode` 0 and `endTimeCode` at the last keyframe of any
  animation. Each animation starts at 0 and holds its values after its own end.
- `LINEAR` keyframes are time samples, which USD interpolates as glTF does. `STEP` adds the previous
  value 1e-7 time codes before each keyframe, which UsdStage holds rather than interpolates.
  `CUBICSPLINE` adds points of each segment, evaluated by glTF's equation, so that the lines between
  them stay within 1e-4 of the curve, in a quaternion's components or times the sampler's largest
  value, in at most 256 pieces. Rotations are normalized.
- Every animation is authored, each in its own variant and never merged. Channels without a node, or
  whose node is outside the selected scene, are ignored.
- A camera or light below an animated node keeps, for each animation, the correction for scale
  computed at its time 0. Its path must not have a scale that changes, nor a rotation that changes
  below a transform that is not a rotation and uniform scale.

### Morph targets

A morphing primitive has a child `BlendShape_{targetIndex}` for each target in glTF order.
`offsets` and `normalOffsets` carry POSITION and NORMAL deltas. A missing POSITION delta contributes
zero. The primitive's `skel:blendShapeTargets` points to these children, paired with
`skel:blendShapes` tokens `Node_{nodeIndex}_Target_{targetIndex}`. Primitives on the same node use
the same tokens. A second node that uses the mesh gets its own tokens and local targets.

Morphing precedes skinning. A skinned mesh uses its existing skeleton. A morphing mesh without a
skin binds to its node's `MorphSkeleton`, which supplies the animation source for UsdSkel consumers.
An identity joint gives the skel root a point from which to compute bounds. With no joint influences,
the mesh keeps the node's transform, including a matrix on a node that no animation targets.
Each morphing primitive's extent in mesh space encloses the neutral geometry and is padded using
the authored weight ranges across variants, for use by UsdSkel's joint-based bounds.

The default weights come from the node, then the mesh, then zeros. They are authored as the
default of `blendShapeWeights` in the same `UsdSkelAnimation` as joint values, or in
`/Root/Animations/Rest` when the asset has no animations. Each variant authors the defaults
and samples the union of its weight channels' sample times. A node with no weight channel in the
chosen variant keeps its stated weights at every time. A variant selection that resolves to no
variant leaves the blend shapes without an animation source.

Target accessors may be dense, sparse, or have an implicit zero base. With
`KHR_mesh_quantization`, POSITION deltas may use signed byte or short, normalized or not, and
NORMAL deltas normalized signed byte or short. Animation outputs are scalar floats or normalized
byte or short values, signed or unsigned, with one value per target per keyframe and an
incoming and outgoing group for CUBICSPLINE. Weights are used as supplied, including negative
weights and weights above one.

UsdSkel provides point and normal offsets. Other target attributes, including TANGENT, are rejected
with a diagnostic. A morphing primitive without source normals leaves normals unauthored so a
consumer can derive them from the deformed points. The resulting shading depends on the consumer.

Mesh primitives use float POSITION and optional float NORMAL, TANGENT, TEXCOORD_n
and COLOR_0 accessors, or the [quantized formats](#quantized-attributes). Accessors
may be interleaved. Indices are glTF unsigned byte, unsigned short or unsigned int.
USD subdivision is disabled and winding remains right-handed; strips and fans are
expanded into triangles, including degenerate triangles, with alternating strip
winding. Absent normals on primitives without morph targets are generated per face for flat shading.

## Quantized attributes

`KHR_mesh_quantization` adds these formats, and only for a file that lists it in
`extensionsRequired`, because its specification says it cannot be optional. Anything else
that is not float, including COLOR_0 beyond its own formats, is rejected.

| Attribute | Formats |
| --- | --- |
| POSITION | Byte, unsigned byte, short or unsigned short, each normalized or not |
| NORMAL | Normalized byte or short |
| TANGENT | Normalized byte or short |
| TEXCOORD_n | Byte, unsigned byte, short or unsigned short, and normalized byte or short |

Each is authored as the same floats that the same attribute would have, with no property
recording that it was quantized. An integer that is not normalized keeps its value, which a float
holds exactly. A normalized one is `c / 255` or `c / 65535` when unsigned, and `max(c / 127, -1)` or
`max(c / 32767, -1)` when signed, so the two lowest values of a signed type both mean -1.

The dequantizing scale and offset stay where the file puts them, in node transforms and
`KHR_texture_transform`. Values are not renormalized: a quantized normal or tangent can be slightly
off unit length, and the extension's specification expects a renderer to normalize it after
transforming it. Tangent W is not checked to be a sign, as for a float tangent.

## Coordinate system and units

glTF uses a right-handed coordinate system, Y-up and meters. A directly
opened glTF or GLB layer explicitly authors `upAxis = "Y"` and
`metersPerUnit = 1`, including after reload. The importer preserves mesh
coordinates and node transforms without an additional axis or unit
conversion. The mesh orientation is right-handed.

OpenUSD's up axis and linear units are stage-wide metadata. Referencing a
glTF asset into another stage does not change the host's metadata or
automatically correct the asset's coordinates. The assembler must apply a
corrective transform when the host uses different conventions. For example,
a +90-degree X rotation and a uniform scale of 100 map this asset into a
Z-up stage measured in centimeters. See OpenUSD's
[up-axis](https://openusd.org/release/api/group___usd_geom_up_axis__group.html)
and [linear-units](https://openusd.org/release/api/group___usd_geom_linear_units__group.html)
documentation.

Under an odd reflection, a world-space triangle's cross product changes sign
relative to its transformed normal; local topology stays right-handed and
normals transform by the inverse transpose.

## Texture coordinates

Each TEXCOORD_n must use VEC2 floats, normalized unsigned bytes or normalized
unsigned shorts, or a [quantized format](#quantized-attributes), with the same
count as POSITION. Sets must begin at zero and be consecutive. TEXCOORD_0 is authored as the vertex-interpolated
`texCoord2f[]` primvar `st`; subsequent sets use `st1`, `st2`, and so on.
Values are not clamped, so tiling outside the unit square is retained.

glTF's image origin is upper-left, while USD uses lower-left. Convert
`(u, v)` to `(u, 1 - v)` once when authoring the primvar, as described in
the [glTF specification](https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html)
and the [UsdPreviewSurface specification](https://openusd.org/release/spec_usdpreviewsurface.html).
Material and texture readers must consume these converted coordinates without
another flip.

## Tangents

TANGENT must use VEC4 floats or a [quantized format](#quantized-attributes), with the same count as
POSITION, and finite values. It is authored as the vertex-interpolated `float4[]` primvar `gltf_tangent`:
XYZ is the tangent and W the bitangent sign, as glTF defines them. Flipping V does
not change these values. Material graphs [do not read this primvar](../adr/0004-normal-textures-and-tangents.md#tangents).

## Vertex colors

COLOR_0 accepts VEC3 or VEC4 floats, normalized unsigned bytes or normalized
unsigned shorts, with the same count as POSITION. Components must be finite
and in the glTF range [0, 1]. RGB is linear and is authored without a color
space conversion as the vertex-interpolated `color3f[]` primvar
`displayColor`. A VEC4's alpha is preserved as the vertex-interpolated
`float[]` primvar `gltf_colorAlpha`; VEC3 implies alpha 1 and has no alpha
primvar.

The importer does not author `displayOpacity` from COLOR_0: without an
explicit material, glTF uses an opaque default material and ignores alpha.
Material graphs must multiply base color by RGB and, when needed, alpha by
`gltf_colorAlpha` (or 1 when absent), respecting the glTF material's alpha
mode.

UsdPreviewSurface graphs cannot multiply, so a primitive bound to a material
whose factor is not 1 also has the products as vertex-interpolated primvars:
`gltf_previewBaseColor` (`color3f[]`) and, for MASK and BLEND with vertex alpha,
`gltf_previewAlpha` (`float[]`). Primvars read by material graphs use
underscores rather than namespaces, because Storm's MaterialX shaders fail to
compile with a colon in a `geomprop` name.

## Cameras

A node's camera is a `UsdGeomCamera` named after the camera's index, below the node, so a camera that
several nodes use is authored under each. Both look along local -Z with +Y up, so no axis changes.
See the [decision record](../adr/0012-cameras.md).

glTF places a camera by the rotation and translation of its node's world transform, and ignores its
scale, while a USD renderer views through the inverse of the prim's whole transform. When the node's
transform, accumulated from the asset's root, holds a scale or shear, the camera prim has a transform
that cancels it. A scale that the assembler applies above the asset is a change of units, and scales
the view with the scene. A node whose transform is degenerate, reflected or sheared beyond a rotation
has no view in glTF, and is rejected: its columns, each divided by its length, must have a
determinant within 0.001 of 1.

| glTF | USD |
| --- | --- |
| `perspective` | `projection = "perspective"` |
| `yfov` | `focalLength = verticalAperture / (2 tan(yfov / 2))` |
| `aspectRatio` | `verticalAperture = horizontalAperture / aspectRatio`, where `horizontalAperture` is `GfCamera`'s default, 20.955. Undefined, it is 1. |
| `znear`, `zfar` | `clippingRange`. Undefined `zfar`, an infinite projection, is 1,000,000, USD's own fallback. |
| `orthographic` | `projection = "orthographic"`, with no focal length |
| `xmag`, `ymag` | `horizontalAperture = 20 xmag` and `verticalAperture = 20 ymag`, since an aperture is in tenths of a unit and a magnification is half the width |
| `znear`, `zfar` | `clippingRange` |
| `name` | The prim's display name |

An undefined aspect ratio means the viewport's, which USD cannot author. A renderer that adjusts the
aperture's width to the viewport keeps `yfov` at any shape, and one that fits the aperture inside it
keeps `yfov` in a viewport at least as wide as tall, as usdview does. Other differences of aspect
ratio are adapted by the renderer, as glTF also leaves to the client. No depth of field is authored.

Values the specification forbids are rejected, as are a `yfov` of pi or more, which no aperture holds,
a negative magnification, which would mirror the view, a `znear` that is not below the far plane of
an infinite projection, and values beyond a float. A camera that no selected node uses is not
checked.

## Lights

A node's `KHR_lights_punctual` light is a UsdLux light named after the light's index, below the node,
so a light that several nodes use is authored under each. Both shine along local -Z, so no axis
changes. See the [decision record](../adr/0013-lights.md).

glTF places a light at its node's origin and shines it along the node's transform applied to -Z,
without scaling its properties. When the node's transform, accumulated from the asset's root, holds
a scale, shear or reflection, the light prim has a transform that cancels it, keeping that axis. A
scale that the assembler applies above the asset is a change of units, and scales the sphere with
the scene. A node whose axes are zero or lie in one plane is rejected.

| glTF | USD |
| --- | --- |
| `directional` | `DistantLight`, with `inputs:angle = 0` and `inputs:normalize = true` |
| `point` | `SphereLight`, with `inputs:radius = 0.001`, `treatAsPoint = true` and `inputs:normalize = false` |
| `spot` | The same, with `ShapingAPI` |
| `intensity` of a directional light, in lux | `inputs:intensity`, unchanged: UsdLux defines a normalized distant light of angle 0 by its illuminance |
| `intensity` of a point or spot light, in candela | `inputs:intensity = intensity / (pi radius^2)`: UsdLux defines a sphere by its luminance, which times its projected area is candela |
| `color` | `inputs:color`, linear |
| `range` | Not authored, with a warning. The light falls off with the inverse square of distance and does not reach zero. |
| `outerConeAngle` | `inputs:shaping:cone:angle`, in degrees |
| `innerConeAngle` | `inputs:shaping:cone:softness = 1 - innerConeAngle / outerConeAngle`, so that the falloff starts at the inner angle. Its curve differs from the one glTF recommends. |
| `name` | The prim's display name |

Values the specification forbids are rejected: a color component outside 0 to 1, a negative
intensity, a range that is not positive, and cone angles other than
`0 <= innerConeAngle < outerConeAngle <= pi / 2`. So are an intensity or range of the wrong type,
which the parser would otherwise read as 1 or as absent, and an intensity beyond a float once divided
by the area. A light that no
selected node uses is not checked.

## Scene selection

Select the declared default scene. If no default is declared, select the first
scene and emit a diagnostic; if no scenes exist or the default index is
invalid, report an error and fail. Never merge scenes. See the
[decision record](../architecture/SCENE_SELECTION.md).
