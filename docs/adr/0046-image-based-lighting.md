# 0046: Image-based lighting

Decision record, 2026-11-23. The import it decides is not implemented yet: the
[current plan](../roadmap/current.md) tracks it, and the
[capability matrix](../reference/CAPABILITY_MATRIX.md) will record its evidence.

## Context

[`EXT_lights_image_based`](https://github.com/KhronosGroup/glTF/tree/main/extensions/2.0/Vendor/EXT_lights_image_based)
is a vendor extension, supported by Babylon.js and written by Adobe Dimension. The glTF root holds an
array of lights, and a scene may reference one. A light has:

- `specularImages`, required: one or more levels of a prefiltered cube map, each an array of six image
  indices in the order +X, -X, +Y, -Y, +Z, -Z. Each level halves the one before, and the levels map
  evenly to roughness 0 to 1. The full chain should not be given.
- `specularImageSize`, required: the size of the first level's square, power-of-two faces.
- `irradianceCoefficients`, required: nine RGB spherical harmonic coefficients, up to l = 2.
- `rotation`, a quaternion "of the IBL environment", default identity, and `intensity`, a brightness
  multiplier of at least 0, default 1.

A JPEG face holds LDR color. A PNG face with alpha holds RGBD, "so that the alpha channel contains a
value that, when the RGB channels are divided by it, can produce values greater than 1". A PNG
without alpha holds LDR color. The maps are said to be "illuminance in lux". Each face is flipped
"to correspond with the way glTF references texture space", with a figure for the orientation.

The specification does not state the color space of the faces, the RGBD formula, the frame or
direction of the rotation, the basis and scaling of the coefficients, a fallback for clients without
the extension, or whether it may be required.

EnvironmentTest's glTF-IBL file is the only Khronos sample with the extension. Its light has five
levels, 256 to 16 pixels, of PNG faces in the binary buffer, a rotation of 90 degrees about +Y and an
intensity of 1. The +X and +Z faces of the first three levels, and +X of the fourth, have alpha; the
others are RGB.

What USD offers:

- `UsdLuxDomeLight` reads one texture, `inputs:texture:file`, whose `inputs:texture:format` may be
  `latlong`, `mirroredBall`, `angular` or `cubeMapVerticalCross`. Its pole is +Y, and its latlong
  follows OpenEXR: longitude 0 is +Z and longitude pi/2 is +X. `DomeLight_1` adds `poleAxis`, whose
  fallback `scene` follows the stage's `upAxis`.
- Storm ignores `texture:format` and reads a latlong (`hdSt/shaders/domeLight.glslfx`). It computes
  its own irradiance and prefiltered maps from that texture, and takes no coefficients.
- UsdLux has no input for spherical harmonics or for prefiltered levels.

The importer serves images through `UsdGltfPackageResolver` unchanged, or converted from KTX2 and WebP
on request ([ADR 0001](0001-image-assets.md)). It has no PNG or JPEG decoder, and checks their
structure at import without decoding ([ADR 0006](0006-image-failures.md)). OpenUSD's Hio reads
Radiance `.hdr` with its built-in stb_image, and `.exr` with its OpenEXR plugin.

## Investigation

These conventions were measured on the sample, with the faces decoded as in [Decoding](#decoding),
on Windows with OpenUSD 26.08.

**Face orientation.** Under the OpenGL cube map convention, with row 0 at the top of each image, each
face's border pixels were compared with the pixels just across the cube's edge. The same transform
was applied to all six faces. The mean absolute difference of `log(1 + value)` over all borders:

| Transform | Seam difference |
| --- | --- |
| Rows reversed | 0.0075 |
| Rotated 180 degrees | 0.069 |
| None | 0.172 |
| Columns reversed, or any transpose or quarter turn | 0.21 to 0.22 |

Only reversing the rows joins the faces. The side faces then have the sky at the top; as stored, it
is at the bottom. This reads the specification's flip as top to bottom.

**Axes.** Projecting the first level onto the real spherical harmonic basis, all 27 signs agree with
the file's coefficients in that frame. None of the 47 other signed axis permutations fits the
coefficients better.

**Rotation.** The brightest point, the sun, is at +X in the cube's frame, near the horizon. Babylon.js,
in a right-handed scene, uses the rotation's matrix to transform the direction that it looks up. Read
that way, the environment seen in world direction `w` is the cube's value at `q w`, and the sun is
14 degrees from the direction behind the file's camera. Read the other way, it is 165 degrees from
it. The sample's screenshot shows the sun's highlight near the middle of each metal sphere, which
fits only the first reading. The screenshot's renderer is not stated.

**Coefficients.** The file's band 0 term is 0.86 to 1.11 times the first level's projection, per
channel, and so is the l = 2, m = 0 term. The other band 1 and 2 terms are 1.6 to 8 times larger.
The coefficients therefore do not describe these images. One explanation is a source brighter than
the images hold, but the sample does not show it.

**Storm.** A latlong colored by each direction's dominant axis lit a mirror sphere seen from +Z.
The sphere reflected +Z at its center, +Y above, +X to the right, -X to the left and -Y below, as
UsdLux defines. At roughness 0 the reflection was strongly blurred.

## Decision

### Prim

The selected scene's light is a `DomeLight` named `ImageBasedLight_{lightIndex}` below `Scene_{index}`.
`DomeLight` rather than `DomeLight_1`, because its pole is +Y in its own frame, as in glTF. The
fallback `poleAxis` of `DomeLight_1` would follow the up axis of whichever stage references the asset,
while the prim's frame already follows the reference's transform.

The light's `name` is the prim's display name. `inputs:texture:format` is `latlong`. The dome lights
the whole stage, as other UsdLux lights do, including in a stage that references the asset.

### Texture

`inputs:texture:file` is `./<source name>[lights/<light index>.hdr]`, a Radiance RGBE image that
`UsdGltfPackageResolver` converts on request from the first level, like KTX2. No file is written.

- Its size is 4N by 2N pixels, where N is `specularImageSize`, but at most 8192 by 4096.
- Each pixel's value is the cube's at the direction of the pixel's center by OpenEXR's latlong, in the
  cube's frame. The cube is sampled bilinearly within a face, clamped at its edges, with each face's
  rows reversed as the [investigation](#investigation) found.
- RGBE keeps about 1% relative precision, near what 8-bit faces hold.

Only the first level is decoded. The others are prefiltered for roughness, which the renderer does
itself from the latlong, and a dome light has no input for them. They are still checked, as stated
under [failure](#compatibility-and-failure).

### Decoding

A face's value per channel is `(c / max)^2.2 / (a / max)`, where `max` is 255, or 65535 for 16-bit
PNG. `a` is the alpha channel, or `max` for a JPEG or a PNG without alpha. Gray channels give equal
RGB. A pixel whose alpha is 0 is black.

This inverts Babylon.js's encoder, which writes `(value * D)^(1/2.2)` with `D` in the alpha channel,
and its decoder, `pow(rgb, 2.2) / a`. It also gives the specification's LDR reading of an image
without alpha. The power 2.2 is Babylon.js's, not the exact sRGB curve. The sample cannot tell
them apart: their fits to the coefficients differ by less than 1%.

### Rotation

The quaternion is normalized, and its inverse is the prim's `xformOp:orient`. The environment that
the dome then shows in world direction `w` is the cube's value at `q w`. USD orders a quaternion
`(w, x, y, z)`, and glTF `[x, y, z, w]`.

### Intensity

`inputs:intensity` is `intensity`, unchanged, with white `inputs:color` and no exposure. UsdLux makes
the dome's radiance the texture value times its intensity, so a mirror reflects a texel of 1 as a
luminance of 1 nit, as Babylon.js reflects it as 1. The specification's "illuminance in lux" is not a
unit that a map of directions can hold, and Babylon.js does not apply it either.

### Not authored

- `irradianceCoefficients`: no USD schema takes them, and renderers integrate the texture instead. On
  the sample, the [coefficients disagree](#investigation) with the images, so diffuse lighting differs
  from a client that uses them. They are still checked.
- Lights that the selected scene does not reference, which are not checked either, as with
  [punctual lights](0013-lights.md).

### Compatibility and failure

The extension defines no fallback. A glTF client without it shows the asset under its own lighting,
and nothing in the file says what that should be. Until now this importer did the same, and warned.

- Optional, the light is imported as above.
- Required, the import fails naming it, because fastgltf rejects required extensions that it does not
  know, as for [XMP](0045-xmp-metadata.md).

The parser reads the extension from the JSON, beside fastgltf. The import fails, without publishing a
layer, when:

- The extension is not in `extensionsUsed`, the root's `lights` is not an array, or the selected
  scene's `light` is not an index into it.
- The selected light's `rotation` is not four numbers from -1 to 1 with a nonzero length, its
  `intensity` is not a finite number of at least 0, or its `irradianceCoefficients` are not nine
  arrays of three finite numbers.
- `specularImageSize` is not a power of two, from 1 to 16384, or `specularImages` is not a nonempty
  array of at most `log2(specularImageSize) + 1` arrays of six image indices.
- A face is not PNG or JPEG, fails the [structure check](0006-image-failures.md), or is not a square
  of `specularImageSize` halved once for each level before it.

A face that decodes badly despite its structure makes the request fail. The resolver resolves the
latlong to nothing and warns, and the renderer uses its fallback, as with a damaged KTX2 image.

### Dependencies

stb_image 2.34 decodes the faces, built with only PNG and JPEG and with `STBI_MAX_DIMENSIONS` at 16384,
the [image limit](0006-image-failures.md#decision). It runs only in the resolver, after the structure
check, and never at import. The importer writes RGBE itself, in a few lines.

- [Wuffs](https://github.com/google/wuffs) is memory-safe by construction, and stb_image's header
  recommends it where security is a priority. Its JPEG decoder is not in a released version yet. Use
  it once one is, for both formats.
- OpenUSD's Hio decodes these formats with stb_image too, so a renderer that reads the latlong already
  trusts the same kind of decoder with the same bytes. `gltfTexture` cannot use Hio, which is part of
  OpenUSD's imaging and outside the [workspace's](../architecture/WORKSPACE.md) dependencies.
- An OpenEXR latlong would need an encoder dependency, and gives nothing that 8-bit faces need.

## Verification

The import pull request adds:

- Tests of the prim, its transform and inputs, each value type and boundary, each failure with the
  layer preserved on reload, the required case, a scene without a light, and reopening through `usda`
  and `usdc`.
- A gltfTexture unit test of decoding and the latlong: each face's center and edges, RGBD and LDR
  pixels, 16-bit and gray PNG, and the size limit.
- A [display check](0007-display-check.md) of a mirror sphere under a cube whose faces each have one
  color, rotated, in Storm.
- The [sample check](0010-sample-baseline.md) of EnvironmentTest's glTF-IBL file, comparing the dome
  with its light, and a display comparison with its screenshot.

## Limits

- Storm blurs a mirror's reflection of the dome at roughness 0. Several dome lights in one stage, and
  renderers other than Storm, were not checked.
- Prefiltering is the renderer's, so roughness may look different from a client that uses the given
  levels.
- The sample's coefficients disagree with its images, and are the only ones seen. A file whose
  coefficients describe a different environment lights differently here.
- An asset referenced into a stage brings its dome light, which lights the rest of the stage. Several
  such assets bring several domes.
- The face orientation and rotation rest on one sample and on Babylon.js's code, not on the
  specification's text.
