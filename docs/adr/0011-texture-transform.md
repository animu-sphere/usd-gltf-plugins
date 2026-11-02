# 0011: Texture transforms

Decision record, 2026-11-03. It extends the texture networks of
[ADR 0002](0002-base-color-texture-networks.md), [ADR 0003](0003-pbr-texture-networks.md) and
[ADR 0004](0004-normal-textures-and-tangents.md). The
[capability matrix](../reference/CAPABILITY_MATRIX.md) records current evidence.

## Decision

`KHR_texture_transform` is applied in both networks, per texture slot, between the coordinate reader
and the image. Readers stay shared by UV set, so one image under different transforms needs nothing
more. A transform of all defaults authors no nodes.

| Purpose | `preview` | `mtlx` |
| --- | --- | --- |
| Scale | `<slot>TextureTransform` (`UsdTransform2d`) | `<slot>ImageScale` (`ND_multiply_vector2`) |
| Rotation | The same node | `<slot>ImageRotate` (`ND_rotate2d_vector2`) |
| Translation | The same node | `<slot>ImageTranslate` (`ND_add_vector2`) |

The extension's `texCoord` replaces the texture's own, and a primitive without that set fails like any
other missing set.

### From the extension's coordinates to `st`

The extension defines its matrix in glTF's coordinates, where V points down from the image's upper
left. The [primvar holds `(u, 1 - v)`](../design/SCENE_CONVENTIONS.md#texture-coordinates), so
substituting it gives, for rotation `r`, scale `(sx, sy)` and offset `(ox, oy)`:

```text
s' = sx cos(r) s - sy sin(r) t + ox + sy sin(r)
t' = sx sin(r) s + sy cos(r) t + 1 - oy - sy cos(r)
```

This is scale, then a counter-clockwise rotation by `r`, then a translation, which is the order
`UsdTransform2d` applies. Its rotation is `r` in degrees, unchanged in sign, and its translation is
the constant terms.

MaterialX could use `place2d`, but it divides by its scale, which cannot express a valid scale of 0.
`transformmatrix` is one node, but USD can only hold its matrix as doubles and the direction of its
multiplication would need its own evidence. Three nodes state each step. `rotate2d` turns clockwise,
so its amount is `-r`.

### Normal maps

A normal map's X and Y are relative to the image, so a rotated or mirrored image should also turn
them. How a renderer does that depends on where its tangent frame comes from:

- Storm's UsdPreviewSurface path derives it from the transformed coordinates, and equals a reference
  with the transform baked into UVs.
- Storm's MaterialX path derives it from the primvar without the transform, as
  [ADR 0004](0004-normal-textures-and-tangents.md#evidence-and-limits) also observed, so X and Y do not
  turn with the image.

The options for the second were:

- Reject the combination, which loses 9 sample files that now open, in every renderer.
- Compensate in the graph, by turning the image's vector or passing `normalmap` a turned `tangent` and
  `bitangent`. It takes at least seven nodes, needs world-space inputs, and would be wrong for a
  renderer that derives its frame from the transformed coordinates.
- Keep the direct graph, and warn.

Choose the last, as with [vertex colors in ADR 0002](0002-base-color-texture-networks.md#vertex-colors-with-a-texture):
conversion warns once per material whose normal map is rotated or mirrored. A scale of positive values
changes neither direction, so it does not warn. A rotation by a whole turn does warn, because the
check is on the authored value.

The glTF reference does not settle what is correct here. The extension does not mention tangents, and
a TANGENT attribute, [which no network reads](0004-normal-textures-and-tangents.md#tangents), would by
its own definition not turn with the image.

### Malformed values

fastgltf rejects a value beyond a float and a pair with a wrong element, but reads a field of the
wrong type, such as a string `rotation`, as absent. That would show the texture untransformed, so the
importer reads these fields from the JSON itself and fails. A translation that overflows after the
substitution above also fails. Any finite rotation and scale is accepted, including 0 and negative
values, because the extension does not limit them.

## Evidence and limits

The [display check](../reference/CAPABILITY_MATRIX.md#display-checks) compares each quad of a fixture
with a reference that bakes the extension's matrix into UVs, in code that does not use the plugin's
formula, and with named colors. The [sample check](0010-sample-baseline.md) compares authored nodes
with that matrix at points in glTF's coordinates, using each node's own definition.

Each change below was built into the plugin, and both checks run against it. The sample column is
the three files of the TextureTransform models:

| Change to the plugin | Failed display comparisons | Sample files that differ |
| --- | --- | --- |
| Preview rotation reversed | 15 | 3 |
| MaterialX rotation reversed | 12 | 3 |
| The sine term of the translation left out | 2 | 3 |
| V offset reversed | 8 | 3 |
| The cosine term of the translation using X's scale | 2 | 0 |
| `texCoord` override ignored | 20 | 0 |

The fifth differs only under unequal scales, which those three samples do not use, so only the fixture
finds it. No sample file that opens uses the override. The sample check's comparison finds it on the
fixture, with 4 mismatches, and none without the defect.

Not established:

- A rotation combined with unequal scales shears the tangent frame in a path that derives it from the
  transformed coordinates. It was not measured.
- Animating a transform through `KHR_animation_pointer` is not supported, like all animation.
- A transform on a texture of an extension that is ignored, such as clearcoat, has no effect, and its
  fields are still checked.
- Other renderers were not run.
