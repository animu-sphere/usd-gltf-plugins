# 0002: Base color texture networks

Decision record, 2026-11-01. It extends the
[factor-only material decision](../architecture/MATERIAL_FACTORS.md). The
[capability matrix](../reference/CAPABILITY_MATRIX.md) records current evidence.

## Decision

A base color texture adds these nodes to the existing material graphs. Image paths
follow [ADR 0001](0001-image-assets.md).

| Purpose | `preview` | `mtlx` |
| --- | --- | --- |
| Coordinates | `texCoordReader` (`UsdPrimvarReader_float2`) | `texCoordReader` (`ND_geompropvalue_vector2`) |
| Image | `baseColorTexture` (`UsdUVTexture`, `sourceColorSpace` `sRGB`) | `baseColorImage` (`ND_image_color4`, `file` color space `srgb_texture`) |
| Channels | `rgb` and `a` outputs | `baseColorRgb` (`ND_convert_color4_color3`), `baseColorA` (`ND_extract_color4`, index 3) |
| Factor | `scale` input, all four components | `baseColorFactor` and `alphaFactor` multiplies, when not 1 |
| Vertex color | None; see below | `baseColorVertex` and `alphaVertex` multiplies, before the factors |

[ADR 0003](0003-pbr-texture-networks.md) later renamed the readers per UV set.
Readers use the primvar for the texture's `texCoord`: `st` or `st<n>`. A primitive
without that set fails rather than sampling a fallback coordinate. Primvar values are
[already flipped](../design/SCENE_CONVENTIONS.md#texture-coordinates), so no node
flips again. Alpha channels are read under the
[alpha mode rules](../architecture/MATERIAL_FACTORS.md#decision); OPAQUE reads none.

Sampler wrap modes map to `repeat`, `mirror` and `clamp` in Preview and `periodic`,
`mirror` and `clamp` in MaterialX. Filters are not authored: `UsdUVTexture` has no
filter input, so the two paths would otherwise differ. glTF's nearest and mipmap
choices are lost, which can be visible.

### Vertex colors with a texture

The factor-only approach of baking products into primvars cannot express a texture
multiplied by COLOR_0, and UsdPreviewSurface has no multiply node. The options were:

- Reject the combination, losing valid assets in every renderer, including those
  that use the exact MaterialX network.
- Approximate per vertex by averaging the texture, which is wrong and needs decoding.
- Keep MaterialX exact and let Preview show the texture and factor without COLOR_0.

Choose the last, as an explicit fallback: conversion warns once per material, and no
product primvars are authored for it. MaterialX multiplies base color by RGB, and for
MASK and BLEND multiplies alpha by the vertex alpha.

## Evidence and limits

Both paths compiled and displayed in Storm, as described under
[manual visual checks](../reference/CAPABILITY_MATRIX.md#display-checks). That
is one renderer, one set of simple images, and comparison by eye.

`KHR_texture_transform` remains ignored with the existing optional extension warning.
[ADR 0011](0011-texture-transform.md) later applied it. Other texture slots still fail. A normal map will need a decision on tangents, because
textured assets commonly carry a TANGENT attribute, which is currently rejected.
[ADR 0004](0004-normal-textures-and-tangents.md) later made that decision.
