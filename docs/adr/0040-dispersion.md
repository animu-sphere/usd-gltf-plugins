# 0040: Dispersion

Decision record, 2026-11-17. It extends the networks of [ADR 0019](0019-volume.md).
[MATERIALS](../design/MATERIALS.md#dispersion) specifies the result, and the
[capability matrix](../reference/CAPABILITY_MATRIX.md) records current evidence.

## Context

`KHR_materials_dispersion` separates the colors of light that a volume refracts, as in a gemstone.
This record reads the
[specification](https://github.com/KhronosGroup/glTF/tree/24f4d3c04780de1258e19cfd944f54b648367c04/extensions/2.0/Khronos/KHR_materials_dispersion)
at KhronosGroup/glTF `24f4d3c`, ratified. Its one value, `dispersion`, is 20 over the Abbe number,
at least 0 and 0 by default, which means none; 1 is about the strongest of common materials, and
larger values are valid. The ior of `KHR_materials_ior` is the index at the central wavelength, and
the dispersion spreads it across wavelengths. The extension builds on `KHR_materials_volume`: a thin
material does not refract, so it has nothing to disperse. It must not be used with
`KHR_materials_unlit`. A client without it draws the volume without dispersion.

What the two shaders can express:

- MaterialX `gltf_pbr` (2.0.1 in 1.39.5, the version OpenUSD 26.08 builds) has `dispersion`, a float
  defaulting to 0. MaterialX's own `standard_surface_to_gltf_pbr` graph converts an Abbe number to it
  as 20 over the number, glTF's meaning. No node of `gltf_pbr`'s graph reads it, as none reads its
  volume inputs ([ADR 0019](0019-volume.md)).
- Storm draws `gltf_pbr`'s transmission as transparency, without refraction
  ([ADR 0018](0018-transmission.md)), so it could not disperse either.
- UsdPreviewSurface has no transmission, no volume and no dispersion.

## Options

- **Keep the extension unsupported:** an optional dispersion warns and is ignored, and a required one
  fails, though every path draws what glTF specifies for a client without it, and `gltf_pbr` has
  glTF's input.
- **Approximate it,** for example by tinting the transmission. Dispersion moves colors apart by angle;
  no constant tint shows that.
- **Author `gltf_pbr`'s `dispersion`** where the volume is authored, as ADR 0019 authors the volume's
  own inputs, and leave Preview without one.

## Decision

Author `dispersion` in MaterialX, on a material whose volume is authored:

| glTF | `preview` (UsdPreviewSurface) | `mtlx` (`gltf_pbr`) |
| --- | --- | --- |
| `dispersion` | Not expressible; ignored with a warning | `dispersion` |

It is authored where it is above 0, on a material with a transmission above 0 and a thickness factor
above 0 that is not unlit: the condition under which ADR 0019 authors `thickness`. Elsewhere it is not
authored, without a warning, because a thin material has nothing to disperse; on an unlit material it
warns that the extension is ignored. The ior is authored as without the extension.

A negative dispersion, including one that a float would round to 0, a value of the extension that is
not an object, a field of the wrong type, and a number too large for a float fail the import with a
diagnostic naming the extension, even where the dispersion would not be authored.

## Evidence and limits

- Tests assert the MaterialX input, that Preview authors nothing, that nothing else changes, the
  defaults, a thin material, a material without transmission, an unlit material, and each failure,
  optional and required. The sample check compares the dispersion of each material.
- The [display check](../reference/CAPABILITY_MATRIX.md#display-checks) renders the volume check's
  quad with a thickness of 1 and dispersions of 1 and 2.04, and expects each to look like the quad
  without one in each path. That shows the authored input does not break the material, not that a
  dispersion is drawn.
- No renderer in reach draws the dispersion: through MaterialX 1.39.5's `gltf_pbr` and through
  UsdPreviewSurface, every material draws as glTF's fallback does. A renderer that implements
  `gltf_pbr` itself, or a later MaterialX that reads the input, may draw it.
