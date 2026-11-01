# Scene conventions

Status: **partially implemented** for selected scenes, nodes, transforms and
a triangle-mesh subset including UVs and vertex colors with embedded or local
external buffers. Skin, camera and animation are not supported.

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
concepts. Initial duplication of a multiply referenced mesh is permitted,
but must not preclude a future shared USD representation.

Each mesh is an Xform below its node, containing one USD Mesh per glTF
primitive. This retains the primitive boundaries needed for future material
bindings. Shared mesh references are currently duplicated under each node.
Use stable index-based prim names such as `Node_12`, `Mesh_3`, `Primitive_0`, and
`Material_2`. glTF names can be empty, repeated, or invalid in a USD path;
retain original names as display names or equivalent metadata. Test name
stability. Do not prescribe `/Asset/rig` or `/Asset/anim` as mandatory:
their placement must be decided when UsdSkel and animation are designed.

The implemented mesh subset accepts indexed or non-indexed triangle
primitives with float POSITION, optional float NORMAL, optional TEXCOORD_n
and optional COLOR_0 accessors in embedded or local external buffers.
Accessors may be interleaved but not sparse. Indexed data uses glTF unsigned
byte, unsigned short or unsigned int indices. USD subdivision is disabled and
winding remains right-handed; absent normals are generated per face for flat shading.
Unsupported attributes, materials, other primitive modes, morphs and
compression fail with a diagnostic. External buffer URI restrictions are
described in the [import pipeline](../architecture/IMPORT_PIPELINE.md).
Material binding and a general mesh sharing policy remain open.

## Texture coordinates

Each TEXCOORD_n must use VEC2 floats, normalized unsigned bytes or normalized
unsigned shorts, with the same count as POSITION. Sets must begin at zero and
be consecutive. TEXCOORD_0 is authored as the vertex-interpolated
`texCoord2f[]` primvar `st`; subsequent sets use `st1`, `st2`, and so on.
Values are not clamped, so tiling outside the unit square is retained.

glTF's image origin is upper-left, while USD uses lower-left. Convert
`(u, v)` to `(u, 1 - v)` once when authoring the primvar, as described in
the [glTF specification](https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html)
and the [UsdPreviewSurface specification](https://openusd.org/release/spec_usdpreviewsurface.html).
Material and texture readers must consume these converted coordinates without
another flip. Image loading, texture transforms and renderer output remain
unverified; these tests establish the authored coordinate values only.

## Vertex colors

COLOR_0 accepts VEC3 or VEC4 floats, normalized unsigned bytes or normalized
unsigned shorts, with the same count as POSITION. Components must be finite
and in the glTF range [0, 1]. RGB is linear and is authored without a color
space conversion as the vertex-interpolated `color3f[]` primvar
`displayColor`. A VEC4's alpha is preserved as the vertex-interpolated
`float[]` primvar `gltf:colorAlpha`; VEC3 implies alpha 1 and has no alpha
primvar. Other color sets are not supported.

The importer does not author `displayOpacity` from COLOR_0: without an
explicit material, glTF uses an opaque default material and ignores alpha.
Future material graphs must multiply base color by RGB and, when needed,
alpha by `gltf:colorAlpha` (or 1 when absent), respecting the glTF material's
alpha mode. These data mappings are tested, but material graphs and renderer
behavior are not yet verified.

## Semantic differences requiring validation

- glTF is Y-up and uses meters. Explicitly establish and test USD `upAxis`,
  `metersPerUnit`, and any necessary conversions.
- Select the declared default scene. If no default is declared, select the
  first scene and emit a diagnostic; if no scenes exist or the default index
  is invalid, report an error and fail. Never merge scenes. The selection
  helper is connected to the file-format importer; see the
  [decision record](../architecture/SCENE_SELECTION.md).
- Test negative scales, transform parity, triangle winding and normals.
- Define mesh sharing and placement of animated nodes as part of later work.
- Define behavior for unsupported extensions, particularly required ones,
  as described in the [import pipeline](../architecture/IMPORT_PIPELINE.md).

See the [roadmap](../roadmap/README.md) for acceptance criteria.
