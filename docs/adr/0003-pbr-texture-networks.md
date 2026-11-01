# 0003: Metallic-roughness, occlusion and emissive texture networks

Decision record, 2026-11-01. It extends the
[base color texture decision](0002-base-color-texture-networks.md), and image paths
follow [ADR 0001](0001-image-assets.md). The
[capability matrix](../reference/CAPABILITY_MATRIX.md) records current evidence.

## Decision

Each texture slot has its own image node, even when slots share an image, because
scale, bias and color space differ per slot.

| Slot | `preview` (`UsdUVTexture`) | `mtlx` |
| --- | --- | --- |
| Metallic-roughness | `metallicRoughnessTexture`, `raw`, `scale` (1, roughness, metallic, 1); `g` to `roughness`, `b` to `metallic` | `metallicRoughnessImage` (`ND_image_vector4`); `roughnessChannel` and `metallicChannel` (`ND_extract_vector4`, indices 1 and 2); `roughnessFactor` and `metallicFactor` multiplies when not 1 |
| Occlusion | `occlusionTexture`, `raw`, `scale` strength, `bias` 1 - strength; `r` to `occlusion` | `occlusionImage` (`ND_image_vector4`); `occlusionChannel` (`ND_extract_vector4`, index 0); `occlusionStrength` (`ND_mix_float` from 1) when strength is not 1 |
| Emissive | `emissiveTexture`, `sRGB`, `scale` the factor; `rgb` to `emissiveColor` | `emissiveImage` (`ND_image_color4`, `srgb_texture`); `emissiveRgb` (`ND_convert_color4_color3`); `emissiveFactor` multiply when not 1 |

Each UV set has one reader in each graph, named for its primvar (`stReader`,
`st1Reader`). This replaces the single `texCoordReader` of ADR 0002, because slots can
use different sets. A slot using a set the primitive lacks fails, as does an occlusion
strength outside 0 to 1. Normal textures still fail.

The glTF default emissive factor is black, so an emissive texture without a factor
stays black. Both graphs keep that multiply rather than guess what the author meant.

### Non-color images in MaterialX

The options for keeping data out of color transforms were:

- `raw` color space on `ND_image_color4`, matching Preview. MaterialX has no such
  space: it reports `Unsupported color space transform from raw to lin_rec709` at
  shader generation, although the pixels are right.
- `lin_rec709`, which is silent in Storm but states that the data is a color, so a
  renderer with another working space would transform it.
- No color space on `ND_image_color4`, which leaves a color image's interpretation to
  the renderer.
- A vector image, which no color space transform applies to.

Choose the vector image, with no color space authored.

### Occlusion differs between the paths

Occlusion is authored in both graphs as glTF specifies, without compensating for
either renderer behavior below:

- MaterialX 1.39's `gltf_pbr` declares an `occlusion` input and its implementation does
  not read it, so the connection has no visible effect there.
- Storm's UsdPreviewSurface multiplies direct as well as indirect light by `occlusion`,
  whereas glTF applies it to indirect light only, so it can look too strong.

Multiplying base color by occlusion would show something in MaterialX, but would also
darken direct light, which glTF leaves unoccluded, and would double up in a renderer
whose `gltf_pbr` does honor the input. No warning is issued,
because the input is valid and the behavior belongs to the renderer.

## Evidence and limits

[Manual visual checks](../reference/CAPABILITY_MATRIX.md#manual-visual-checks) cover
one renderer and simple images. They measure the color versus non-color reading, but not
general shading against glTF's reference. Filters and `KHR_texture_transform` remain
unapplied, as in ADR 0002.
