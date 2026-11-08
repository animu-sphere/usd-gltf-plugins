# Vendored dependencies

These source files are built locally and are not downloaded during CMake
configuration. The fastgltf directory contains its build files, headers,
sources, upstream README and license; its examples, tests and docs are omitted.

| Library | Version | Source |
| --- | --- | --- |
| fastgltf | 1.0.1 | [Release](https://github.com/spnda/fastgltf/releases/tag/v1.0.1), commit `87cfbceeeda127ccc9092694279437b700046119` |
| simdjson | 5.1.0 | [Release](https://github.com/simdjson/simdjson/releases/tag/v5.1.0), `simdjson.h` and `simdjson.cpp` amalgamations |
| simdutf | 9.3.3 | [Release](https://github.com/simdutf/simdutf/releases/tag/v9.3.3), `simdutf.h` and `simdutf.cpp` amalgamations |
| Basis Universal | v2_50 | [Release](https://github.com/BinomialLLC/basis_universal/releases/tag/v2_50), commit `4d6fc70eaf62ad0558e63e8d97eb9766118327a6`; `transcoder/`, `zstd/zstddeclib.c`, `zstd/zstd.h`, `zstd/zstd_errors.h` and licenses |
| libwebp | v1.6.0 | [Tag](https://github.com/webmproject/libwebp/releases/tag/v1.6.0), commit `4fa21912338357f89e4fd51cf2368325b59e9bd9`; the decoder subset below |

libwebp keeps only the files upstream's `libwebpdecoder` is built from, unedited:
`src/dec/`, the decoding and common files of `src/dsp/` and `src/utils/` with every SIMD
variant, the headers `decode.h`, `encode.h`, `format_constants.h`, `mux_types.h` and `types.h`
from `src/webp/`, and `AUTHORS`, `COPYING` and `PATENTS`. The encoder, `demux/`, `mux/`,
`sharpyuv/` and the autotools files are omitted. Each SIMD file compiles to nothing on a target
without its instruction set, so no architecture flag is needed.

The amalgamations match the SHA-256 hashes in fastgltf 1.0.1's
`cmake/dependencies.cmake`. When upgrading, update the source files, versions,
licenses, notices and build configuration together, and run the parser and
plugin tests. The combined [notices](NOTICES.txt) are included in installed
and packaged plugins.
