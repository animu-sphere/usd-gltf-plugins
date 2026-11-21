# 0042: Points and lines

Decision record, 2026-11-21. It extends the [mesh conventions](../design/SCENE_CONVENTIONS.md#points-and-lines)
and the [material networks](../design/MATERIALS.md#points-and-lines). The
[capability matrix](../reference/CAPABILITY_MATRIX.md) records current evidence.

## Context

A glTF primitive's `mode` can be `POINTS`, `LINES`, `LINE_LOOP` or `LINE_STRIP`. Points need at least
one vertex, lines a non-zero even number, and strips and loops at least two; a loop adds a segment from
the last vertex to the first. The importer rejected all four, which was the only reason the
MeshPrimitiveModes files and PrimitiveModeNormalsTest failed.

The [specification](https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html#point-and-line-materials)
defines no size or style for them. It recommends widths of one pixel in viewport space, standard
lighting when NORMAL is present, without normal textures unless TANGENT is too, and, without NORMAL,
no lighting: the base color, times COLOR_0 when present, plus the emissive value. Flat normals, which a
triangle without NORMAL has, are not defined for points or lines.

What USD and Storm offer:

- `UsdGeomPoints` and linear `UsdGeomBasisCurves` are point-based gprims, with `normals`, primvars and
  material bindings like a mesh, but without indices: each vertex of a curve or point is its own
  element. A linear curve with `wrap` `periodic` is closed by a segment from its last vertex to its
  first, and Storm draws that segment.
- Widths are object-space diameters, with no fallback in either schema and no unit for pixels. In
  OpenUSD 26.08, Storm draws a point without widths one unit wide, and one of width 0 at its smallest
  raster size. It draws linear curves without widths as one-pixel lines at every refinement level,
  and with widths, when refined, as ribbons that vanish at width 0.
- In those one-pixel lines, Storm shades with a normal facing the camera, not the authored one. Neither
  shader has an unlit mode: [ADR 0015](0015-unlit.md) emits the base color from the lit shaders instead.

## Options

- **Keep rejecting:** three sample files fail.
- **Expand them to triangles,** for example camera-independent quads or tiny spheres: this invents
  geometry and a size that glTF does not define.
- **Points and linear curves** with the primitive's vertices, and a material that follows the
  specification's lighting rules.

## Decision

Author points and linear curves:

| glTF mode | USD prim `Primitive_{index}` | Topology |
| --- | --- | --- |
| `POINTS` | `UsdGeomPoints` | One point per index |
| `LINES` | `UsdGeomBasisCurves`, `linear`, `nonperiodic` | A curve of 2 vertices per index pair |
| `LINE_STRIP` | `UsdGeomBasisCurves`, `linear`, `nonperiodic` | One curve of every index |
| `LINE_LOOP` | `UsdGeomBasisCurves`, `linear`, `periodic` | One curve of every index |

- Vertices are authored in index order, so the value at element `k` of `points`, `normals` and every
  primvar is that of vertex `indices[k]`, and a repeated index repeats its values. Without indices, the
  vertices are used in order. Index and vertex counts are checked as glTF states them, and indices as
  for triangles.
- NORMAL, TANGENT, TEXCOORD_n and COLOR_0 are authored as for a mesh, with the same formats and
  names, vertex-interpolated. No normals are generated.
- Points have the constant width 0, which says that glTF gives them no size in the scene, and lets
  Storm draw them at its smallest size, near the recommended pixel. Curves have no widths, so Storm
  draws them one pixel wide; a width of 0 would make its refined ribbons vanish.
- The extent is that of the points.
- `doubleSided` is not authored, because it has no meaning without faces.

The material follows the specification's rules:

- **Without NORMAL**, the primitive is unlit. Its material is bound as an unlit variant,
  `Material_{index}_Unlit` (or `_VertexColor_Unlit`, `DefaultUnlit`, `DefaultVertexColorUnlit`), with
  ADR 0015's networks, and the emissive factor times `KHR_materials_emissive_strength` added to the
  emitted base color: a MaterialX `add` node, and in UsdPreviewSurface the sum as the constant, the
  base color texture's `bias`, or the per-primitive `gltf_previewBaseColor` primvar. The emissive
  factor is the emission only without an emissive texture: with one, both networks leave the emission
  out, with a warning. A `KHR_materials_unlit` material is bound unchanged, as on a triangle.
- **With NORMAL**, the material is lit, without its normal and clearcoat normal textures: a material
  that has either is bound as `Material_{index}_NoNormalTexture` (or `_VertexColor_NoNormalTexture`).
  With TANGENT as well, glTF asks for the normal texture, but neither network has a tangent frame for a
  point or a line, so the texture is left out with a warning.

Morph targets, skins and `KHR_draco_mesh_compression` on points and lines fail with a diagnostic.

## Evidence and limits

- Tests author each mode, indexed and not, with every attribute, a periodic loop, an index repeated
  and the unlit, unlit-with-emission and lit material variants. Wrong counts, out-of-range indices,
  morph targets, skins and Draco fail and keep the existing layer.
- The [sample check](../reference/CAPABILITY_MATRIX.md#sample-assets) compares each point and curve
  vertex with the file, the curve topology and the bound material variant. MeshPrimitiveModes and
  PrimitiveModeNormalsTest open and draw through both shader paths.
- The [display check](../reference/CAPABILITY_MATRIX.md#display-checks) draws unlit points and lines in
  darkness with their base color, vertex colors and emission, and lit lines under a light.
- Storm draws a point of width 0 on one multisample of a pixel, so points look dim: a quarter of their
  color in the display check's renders. Lit lines in Storm are shaded with a normal facing the camera,
  so authored normals do not change them. A renderer that draws a zero width as nothing draws no points, and one with a default curve
  width of one unit draws thick tubes. Widths are left for a consumer to author.
- A consumer reading `normals` on curves can take them as a ribbon orientation, which UsdGeom gives
  them; with no widths, the ribbon has no defined width.
