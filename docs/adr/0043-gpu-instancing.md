# 0043: GPU instancing

Decision record, 2026-11-22. It extends the [scene conventions](../design/SCENE_CONVENTIONS.md#gpu-instancing).
The [capability matrix](../reference/CAPABILITY_MATRIX.md) records current evidence.

## Context

[`EXT_mesh_gpu_instancing`](https://github.com/KhronosGroup/glTF/tree/main/extensions/2.0/Vendor/EXT_mesh_gpu_instancing)
draws a node's mesh once for each element of its `attributes`: `TRANSLATION` (VEC3 float), `ROTATION`
(VEC4 float, or normalized byte or short, an XYZW quaternion) and `SCALE` (VEC3 float), all optional,
with one count, and custom attributes whose names start with `_`. Each instance is
`NodeWorld * T * R * S`, so it applies in the node's space, before the node's transform. The extension
applies only to the node's mesh: it says nothing of the node's camera, light or children, skins or morph
targets, and it supports no per-instance material or animation. It has no fallback, so a file should
require it, and a client that implements it does not draw the mesh at the node itself. The importer
parsed none of this, so SimpleInstancing drew one cube instead of 125, with a warning.

What USD and Storm offer:

- `UsdGeomPointInstancer` draws its prototypes once per element of `protoIndices`, transformed by
  `positions`, `orientations` or `orientationsf`, and `scales`, composed as scale, then orientation, then
  position, within the instancer's space. `orientationsf` holds float quaternions; `orientations` is
  `quath`, about three significant digits.
- In OpenUSD 26.08, Storm draws a prototype below its instancer only through the instancer, honors
  `orientationsf`, and `UsdGeomBBoxCache` bounds an instancer by its instances. A plain
  `UsdPrim` traversal still visits the prototype's prims, at the prototype's own place.
- [ADR 0031](0031-mesh-sharing.md) kept a copy of a mesh at each placement, and left native instancing
  for a later review. Native instancing makes one prim per instance, which is what this extension avoids.

## Options

- **Ignore the extension,** as before: the mesh is drawn once, where glTF does not draw it.
- **One `Xform` per instance,** with a copy or an instanceable reference of the mesh: correct, but a
  prim per instance, for an extension whose files can hold many thousands.
- **A `UsdGeomPointInstancer`** below the node, whose one prototype is the mesh.

## Decision

A node with the extension and a mesh has a `UsdGeomPointInstancer` named `Instances` instead of a
`Mesh_{index}` child. The mesh is its only prototype, `Instances/Mesh_{index}`, authored as at any other
placement, including a copy of a mesh that another node converted first:

```text
/Node_0                  Xform, the node's transform
  /Instances             PointInstancer
    /Mesh_0              Xform, the prototype
      /Primitive_0       Mesh
  /Camera_0, /Light_0, /Node_1, ...   as before, not instanced
```

- `protoIndices` is 0 for each instance. `positions` is TRANSLATION, or the origin for every instance
  without it. ROTATION is `orientationsf`, normalized, and SCALE is `scales`; each is authored only when
  the file has it. `extent` bounds the instances.
- The instancer has no transform, so the node's transform, animation and visibility apply after each
  instance's, as glTF composes them. The node's camera, light and children are not instanced.
- The extension is supported optional or required. It has no fallback to fall back from.
- Attributes are read as vertex attributes are, interleaved or sparse. Their counts must match. A ROTATION
  must have unit length within the node tolerance plus the rounding of its format, two steps and a
  squared step, about 0.017 for a byte. Values must be finite.
- A custom `_` attribute fails with a diagnostic, as custom vertex attributes already do. Another name
  fails as unsupported.
- A skin or morph targets on an instanced node fail with a diagnostic: the extension does not define
  them, and nothing checked that UsdSkel deforms a prototype.
- The extension on a node without a mesh is ignored with a warning, as the extension gives it no effect.
- Material variants bind the prototype's primitives as they bind any placement.

## Evidence and limits

- Tests author every combination of attributes, with float, byte and short rotations, optional and
  required, in glTF and GLB, and compare each instance's world transform and the extent with
  independently composed values. A shared mesh, a child node, an animated node and material variants on an
  instanced node are checked. Count mismatches, custom and unknown attributes, wrong formats, a rotation
  off unit length, non-finite values, skins and morph targets fail and keep the existing layer.
- The [display check](../reference/CAPABILITY_MATRIX.md#display-checks) draws two instances below a node
  with a non-uniform scale, and finds them where glTF puts them and nothing where the node itself, an
  unrotated instance, or the opposite order of transforms would put the mesh.
- The [sample check](../reference/CAPABILITY_MATRIX.md#sample-assets) compares SimpleInstancing's
  instancer with the file, and it draws through both shader paths.
- A consumer that traverses prims without instancer support sees the prototype once at the node, and none
  of its instances. `fastgltf` cannot tell an empty `attributes` object from no extension, so such a node
  draws its mesh once. Custom attributes could later become per-instance primvars, but no consumer would
  read them.
