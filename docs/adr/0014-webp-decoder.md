# 0014: WebP decoder

Decision record, 2026-11-03. It extends the [image asset contract](0001-image-assets.md) to
`EXT_texture_webp`. The [capability matrix](../reference/CAPABILITY_MATRIX.md) records current
evidence.

## Context

OpenUSD's image loader reads PNG and JPEG, and is not built with WebP. Like KTX2, a WebP image must
therefore reach consumers converted to a standard image, through the package resolver that already
converts KTX2. Five files of the [sample baseline](0010-sample-baseline.md) require the extension,
three of them for no other cause.

## Options

| | libwebp v1.6.0, decoder only | stb_image or another single-file decoder |
| --- | --- | --- |
| Source to vendor | 100 files and 1.5 MB: upstream's `libwebpdecoder` set, unedited | None of the widely used ones decodes WebP |
| Formats | Lossy (VP8), lossless (VP8L), alpha (`ALPH`) and the extended header (`VP8X`) | |
| Maintenance | Reference implementation, fuzzed continuously upstream, with regular security releases | |
| License | BSD-3-Clause, with an additional patent grant | |

Writing a decoder was not considered: VP8 alone is a video codec's intra-frame format. The same
subset of the same release is already vendored by `usd-3dgs-plugins`, and its files were compared
byte for byte with the upstream tag before being copied.

## Decision

Vendor libwebp's decoder subset, as listed in the [third-party record](../../third_party/README.md),
and build it as a static library with hidden symbols. `WEBP_EXTERN` would otherwise give the API
default visibility on GCC and Clang, so it is defined as plain `extern`, as Zstandard's macros are for
[KTX2](0005-ktx2-decoder.md#decision). `WEBP_USE_THREAD` is defined as in upstream's build, so that
libwebp's one-time initialization takes a lock outside Windows when the resolver decodes on several
threads. Decoding itself is single-threaded.

Decoded pixels are non-premultiplied RGBA, written as an uncompressed PNG by the writer KTX2 already
uses. Entries are `images/<index>.png`, as for KTX2, and external WebP files are served through the
package too.

### Which images are accepted

| Case | Behavior |
| --- | --- |
| Texture with `EXT_texture_webp` and a plain `source` | The extension's image is used, and a failure in it fails the import rather than falling back. |
| Texture with both `KHR_texture_basisu` and `EXT_texture_webp` | The KTX2 image is used, and the WebP image is not read. |
| `EXT_texture_webp` naming an image that is not WebP | Fails. |
| Plain `source` naming a WebP image | Fails, because glTF allows only PNG and JPEG there. |
| Still lossy or lossless image, with or without alpha | Accepted. |
| Animation | Fails at import. |
| A chunk extending past the RIFF payload, or a RIFF size past the end of the file | Fails at import, as damaged. Bytes after the RIFF payload are accepted. |
| Width or height above 16384 | Fails at import. The format cannot store more than 16383 in a lossy image or 16384 in a lossless one, so this rejects nothing valid. |

The signature is `RIFF`, four bytes of size and `WEBP`. The image's `mimeType` is not used, as for
other formats.

ICC profiles, EXIF and XMP chunks are ignored. Decoded values are stored unchanged, and each material
slot states its own color space, as for PNG and KTX2.

### What import checks

Import walks the RIFF chunks and reads the bitstream header, without decoding. Unlike KTX2, every
chunk has a stated size, so a file cut short is found at import as damaged
[PNG and JPEG](0006-image-failures.md#decision) are. Damage inside a bitstream whose header is valid
is found when the image is read: the resolver warns and returns no asset, after the layer has been
published. The image loader only ever reads the PNG this project writes, so a damaged WebP cannot
reach it, unlike [a crafted PNG](0006-image-failures.md#limits).

## Evidence and limits

Fixtures were written by Pillow 12.3.0, which encodes with libwebp 1.6.0, from 16 by 16 images of
red, green, blue and white quadrants, the lower two with alpha 64 where alpha is used: lossless,
lossless with alpha, lossy at quality 90, lossy with alpha and a two-frame lossless animation. Their
layouts are `VP8L`, `VP8 `, `VP8X` with `ALPH`, and `VP8X` with `ANIM` and `ANMF`. Pillow's decoder,
the same release, decoded each image and rejected each damaged case as the tests expect, before the
plugin was built.

The plugin has been built and tested only by CI. Its display has not been checked, and no sample asset
has been run through it; the sample baseline still records the files that require the extension as
rejected. Decoding time and memory were not measured: an image at the size limit decodes to 1 GiB of
RGBA and is then copied into a PNG of the same size, as KTX2 is. The current
[texture design](../design/TEXTURES.md#cache-and-asset-lifetime) specifies scoped conversion reuse.

libwebp has not been fuzzed by this project. Hostile files rely on upstream's own checks and fuzzing,
and on the limits above.
