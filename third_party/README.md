# Vendored dependencies

These source files are built locally and are not downloaded during CMake
configuration. The fastgltf directory contains its build files, headers,
sources, upstream README and license; its examples, tests and docs are omitted.

| Library | Version | Source |
| --- | --- | --- |
| [fastgltf](https://github.com/spnda/fastgltf) | 1.0.1 | [Release](https://github.com/spnda/fastgltf/releases/tag/v1.0.1), commit `87cfbceeeda127ccc9092694279437b700046119` |
| [simdjson](https://github.com/simdjson/simdjson) | 5.1.0 | [Release](https://github.com/simdjson/simdjson/releases/tag/v5.1.0), `simdjson.h` and `simdjson.cpp` amalgamations |
| [simdutf](https://github.com/simdutf/simdutf) | 9.3.3 | [Release](https://github.com/simdutf/simdutf/releases/tag/v9.3.3), `simdutf.h` and `simdutf.cpp` amalgamations |
| [Basis Universal](https://github.com/BinomialLLC/basis_universal) | v2_50 | [Release](https://github.com/BinomialLLC/basis_universal/releases/tag/v2_50), commit `4d6fc70eaf62ad0558e63e8d97eb9766118327a6`; `transcoder/`, `zstd/zstddeclib.c`, `zstd/zstd.h`, `zstd/zstd_errors.h` and licenses |
| [libwebp](https://github.com/webmproject/libwebp) | v1.6.0 | [Tag](https://github.com/webmproject/libwebp/releases/tag/v1.6.0), commit `4fa21912338357f89e4fd51cf2368325b59e9bd9`; the decoder subset below |
| [Draco](https://github.com/google/draco) | 1.5.9 | [Release](https://github.com/google/draco/releases/tag/1.5.9), commit `554628821c3448319d7d4ae48db170b8c63e1aaf`; the glTF mesh decoder subset below |

libwebp keeps only the files upstream's `libwebpdecoder` is built from, unedited:
`src/dec/`, the decoding and common files of `src/dsp/` and `src/utils/` with every SIMD
variant, the headers `decode.h`, `encode.h`, `format_constants.h`, `mux_types.h` and `types.h`
from `src/webp/`, and `AUTHORS`, `COPYING` and `PATENTS`. The encoder, `demux/`, `mux/`,
`sharpyuv/` and the autotools files are omitted. Each SIMD file compiles to nothing on a target
without its instruction set, so no architecture flag is needed.

Draco keeps the unedited native sources for mesh decoding and their headers from `src/draco/`,
with upstream `AUTHORS` and `LICENSE` (Apache-2.0). The tag's source archive has SHA-256
`5b84e0e212ccc7b03ace681760a65a66fe2481a5f54df65154cfc8f0cbb87148`. The local
[`draco_features.h`](../cmake/draco/draco/draco_features.h) matches upstream's `DRACO_GLTF_BITSTREAM`
configuration. The source subset includes the shared point-cloud base that mesh decoding uses.
The encoder API, point-cloud codecs, command-line tools, transcoder and Wasm are omitted.

The amalgamations match the SHA-256 hashes in fastgltf 1.0.1's
`cmake/dependencies.cmake`. When upgrading, update the source files, versions,
licenses, notices and build configuration together, and run the parser and
plugin tests. The combined [notices](NOTICES.txt) are included in installed
and packaged plugins.
