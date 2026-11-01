# 0005: KTX2 decoder

Decision record, 2026-11-01. It implements the KTX2 path of
[ADR 0001](0001-image-assets.md#ktx2). The
[capability matrix](../reference/CAPABILITY_MATRIX.md) records current evidence.

## Options

| | Basis Universal v2_50 transcoder | KTX-Software v4.4.2 (`libktx`) |
| --- | --- | --- |
| Source to vendor | `transcoder/` and a single-file Zstandard decoder, 5.1 MiB | 2200 files and 125 MiB in the repository, 89 MiB of it under `external/`, reduced by hand |
| Build | One C++ and one C source file | Its own CMake project and options |
| ETC1S and UASTC to RGBA | Yes, through `ktx2_transcoder` | Yes, through its own copy of the same transcoder |
| License | Apache-2.0, with BSD for Zstandard | Several, per component |

KTX-Software was compared from its source tree and was not built. Its extra value is
writing, Vulkan and OpenGL upload and other formats, none of which this path uses.

## Decision

Use the Basis Universal transcoder, vendored like the other dependencies. Only the
transcoder and the Zstandard decoder are included, and the library is linked statically
with hidden symbols, so that a Zstandard loaded by the host process cannot replace them.

Decoding the first mip level produces RGBA, which is written as an uncompressed PNG.
Compression would cost time for a result that is only held until the image loader has
read it, and a PNG writer of our own adds no dependency. Hio generates its own mips.

### Which images are accepted

| Case | Behavior |
| --- | --- |
| Texture with `KHR_texture_basisu` and a plain `source` | The extension's image is used, and a failure in it fails the import rather than falling back. |
| `KHR_texture_basisu` naming an image that is not KTX2 | Fails. |
| Plain `source` naming a KTX2 image | Fails, because glTF allows only PNG and JPEG there. |
| ETC1S or UASTC LDR 4x4, single 2D image | Accepted. |
| Other codecs, cubemaps, arrays and volumes | Fail at import. |
| `KTXorientation` other than `rd` | Fails at import, because the extension requires `rd`. |
| Width or height above 16384 | Fails at import, by the transcoder's own limit, with the same message as a damaged file. A small file can declare a large image, and this keeps the decoded allocation near 1 GiB. A build check fails if an upgrade raises the limit. |

External KTX2 files are also served through the package, as `images/<index>.png`,
because the renderer cannot read them directly. They follow the same
[external resource policy](../architecture/IMPORT_PIPELINE.md#external-resource-policy)
when checked and when read.

The transfer function in the file is not used. Decoded values are stored unchanged, and
each material slot states its own color space, as for PNG. A file whose transfer function
disagrees with its slot is not diagnosed.

Import reads the header, level index and metadata. It does not transcode, so a damaged
body is found when the image is read: the resolver warns and returns no asset, after the
layer has been published. [ADR 0006](0006-image-failures.md) covers image failures in
general.

## Evidence and limits

The transcoder and plugin were built and tested locally on Windows with MSVC, and the
library grew from 1.2 MB to 2.6 MB. Each read decodes again, because nothing is cached.
Images of 2048 by 2048 from the sample below took about 60 ms each, on one machine, and
produced 16 MiB of PNG. This is not a measurement of a full scene or of other hardware.

The transcoder's `BASISD_SUPPORT_*` macros were tried to remove unused GPU formats. That
combination did not compile, so the default configuration is kept. Other codecs are still
compiled in, and are unreachable because import and the resolver both reject them.

Fixtures were written by the `basisu` tool of the same release, from 16 by 16 images of
red, green, blue and white quadrants: `-etc1s`, `-etc1s -mipmap`, `-uastc`,
`-uastc -ktx2_no_zstandard` and `-xuastc_ldr_4x4`. To check another encoder, the Khronos
FlightHelmet sample was decoded and rendered through both paths. It has 5 images with
BasisLZ and 10 with Zstandard supercompression, and the one whose writer metadata was read
came from `ktx create` v4.4.2. That sample is not in this repository.

The transcoder has not been fuzzed by this project. Hostile files rely on its own checks
and on the limits above.
