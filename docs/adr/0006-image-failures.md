# 0006: Damaged and oversized images

Decision record, 2026-11-02. It replaces the image limits first stated in
[ADR 0001](0001-image-assets.md#limits). The
[capability matrix](../reference/CAPABILITY_MATRIX.md) records current evidence.

## Context

ADR 0001 checked only the signature at import, on the assumption that the renderer's
image loader would report a damaged body. That assumption was tested on Windows with
OpenUSD 26.08, by rendering images with `usdrecord` and Storm from a plain `.usda` layer,
**without this plugin**. Each case ran three times. PNG and JPEG were 256 by 256, and
"crash" is an access violation, `0xC0000005`, that ends the process.

| Image | Result |
| --- | --- |
| PNG cut anywhere in or after its first IDAT, or without IEND | Crash |
| PNG with no IDAT | Crash |
| PNG with valid chunks and invalid compressed data, row count or row filter | Crash |
| PNG cut after IHDR, IHDR not first, or invalid format in IHDR | Error, fallback color |
| PNG with a wrong checksum and correct data | Correct image, no diagnostic |
| JPEG cut in the scan header | Crash |
| JPEG cut between the frame header and the scan | No diagnostic, different colors on each run |
| JPEG without quantization or Huffman tables | No diagnostic, uniform gray |
| JPEG cut in the scan | No diagnostic, wrong colors |
| JPEG cut before the frame header, lossless, arithmetic, 12-bit or zero-sized | Error, fallback color |

Natural corruption is not always a crash. Through the plugin, inverting 64 bytes in the
middle of a PNG displayed black, and in a JPEG changed the color slightly, both without a
diagnostic.

Size was measured with the plugin, as peak memory of the whole process:

| Image | File | Peak memory | Result |
| --- | --- | --- | --- |
| Valid PNG of 8192 by 8192 | 261 KB | 1.35 GiB | Rendered |
| Valid PNG of 16384 by 16384 | 1 MB | 4.10 GiB | Rendered |
| Valid PNG of 20000 by 20000 | 1.6 MB | 0.45 GiB | Error, fallback color |
| JPEG declaring 65500 by 65500 | 3.6 KB | 16.46 GiB | Crash |

A KTX2 image with a damaged body was also rendered through the plugin. The resolver warned
and Storm used its fallback color, so that path already fails safely.

The crashes are in OpenUSD and also affect plain USD layers, so this project cannot remove
them. It can stop its own imports from reaching them.

## Options

- **Keep the signature check.** A truncated download crashes the viewer.
- **Decode each image at import**, with a vendored decoder such as stb_image or with an
  inflate of our own. This finds everything the loader would fail on. It also costs a full
  decode and its memory on every open, including for tools that never display an image,
  and moves the exposure to a decoder into every `Usd.Stage.Open`.
- **Check structure without decoding.** Each format has enough structure to find natural
  damage, at the cost of reading the bytes.

## Decision

Check structure at import, without decoding. Any failure prevents layer publication, like
other malformed data.

| Format | Required |
| --- | --- |
| PNG | IHDR first, with a valid format and a nonzero size. Every chunk within the file with a correct checksum. A palette if the format uses one. Image data, and IEND. |
| JPEG | Well-formed segments up to the first scan. An 8-bit baseline, extended or progressive Huffman frame with a nonzero size and 1, 3 or 4 components. Quantization and Huffman tables before the first scan, and an end marker after it. |
| KTX2 | As in [ADR 0005](0005-ktx2-decoder.md#which-images-are-accepted). A damaged body still fails when read, safely. |

Bytes after a PNG's IEND or a JPEG's end marker are accepted, since the loader ignores them.

**Size:** width and height are limited to 16384 in every format, the limit KTX2 already had.
It is checked before the loader allocates. External image files are read whole, and are
limited to 2 GiB before that read. An embedded image is limited only by its buffer, which is
already in memory by then.

**Later reads:** the package resolver repeats the check, because a source file can change
after import and a saved layer can reach the resolver without an import. A failed check
resolves to nothing. Opening it also warns. Storm then uses its fallback color.

External images are now read during import. On one Windows machine with a cached file and
noise images, which compress worst, this added about 2.2 ms per MiB of PNG and 0.7 ms per
MiB of JPEG: 28 ms for a PNG of 2048 by 2048, 12.7 MiB in size. The check does not use more
memory than the file.

## Limits

- **Crafted PNG files can still crash the loader.** A PNG with valid chunks and invalid
  compressed data passes, because only decoding can find it. Natural damage cannot produce
  it: truncation breaks the structure, and other changes break a checksum. Such a file
  crashes plain USD too, as shown above, so treat assets from untrusted sources accordingly.
- **Damage inside a JPEG scan is not found**, because a scan has no checksum. It displays
  wrongly without a diagnostic, and has not crashed in these tests.
- **External images are checked once.** The active asset resolver serves them directly, so
  a file damaged after import reaches the loader.
- Rejecting a file the loader would display is deliberate: a PNG with a wrong checksum, and a
  JPEG with no end marker. These are damaged, and the policy prefers a diagnostic.
- All observations are from one platform, OpenUSD version and renderer. Other loaders may
  behave differently, and may accept images above the limit. Neither decoder has been
  fuzzed by this project.
