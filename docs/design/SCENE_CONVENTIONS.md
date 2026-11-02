# Scene conventions

This document defines what authored USD data means. The
[capability matrix](../reference/CAPABILITY_MATRIX.md) records what is
implemented.

## Structure and names

```text
/Asset                         Xform, defaultPrim
  /geo                         Scope
    /Node_0                    Xform
      /Mesh_0                  Xform
        /Primitive_0           Mesh
  /mtl                         Scope
    /Material_0                Material
```

Preserve the glTF node hierarchy below `/Asset/geo`; collect materials under
`/Asset/mtl` and bind them from geometry. Nodes and meshes are distinct
concepts. Duplicating a multiply referenced mesh under each node is permitted,
but must not preclude a future shared USD representation.

Each mesh is an Xform below its node, containing one USD Mesh per glTF
primitive. This retains the primitive boundaries needed for material bindings.
Use stable index-based prim names such as `Node_12`, `Mesh_3`, `Primitive_0`, and
`Material_2`. glTF names can be empty, repeated, or invalid in a USD path;
retain original names as display names or equivalent metadata. Test name
stability. Do not prescribe `/Asset/rig` or `/Asset/anim` as mandatory:
their placement must be decided when UsdSkel and animation are designed.

Mesh primitives use float POSITION and optional float NORMAL, TANGENT, TEXCOORD_n
and COLOR_0 accessors, or the [quantized formats](#quantized-attributes). Accessors
may be interleaved. Indices are glTF unsigned byte, unsigned short or unsigned int.
USD subdivision is disabled and winding remains right-handed; strips and fans are
expanded into triangles, including degenerate triangles, with alternating strip
winding. Absent normals are generated per face for flat shading.

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

## Scene selection

Select the declared default scene. If no default is declared, select the first
scene and emit a diagnostic; if no scenes exist or the default index is
invalid, report an error and fail. Never merge scenes. See the
[decision record](../architecture/SCENE_SELECTION.md).
