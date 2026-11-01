# Vendored dependencies

These source files are built locally and are not downloaded during CMake
configuration. The fastgltf directory contains its build files, headers,
sources, upstream README and license; its examples, tests and docs are omitted.

| Library | Version | Source |
| --- | --- | --- |
| fastgltf | 1.0.0 | [Release](https://github.com/spnda/fastgltf/releases/tag/v1.0.0), commit `0b800b4a584704f9e9add67c79c4c8732e552787` |
| simdjson | 5.1.0 | [Release](https://github.com/simdjson/simdjson/releases/tag/v5.1.0), `simdjson.h` and `simdjson.cpp` amalgamations |
| simdutf | 9.3.3 | [Release](https://github.com/simdutf/simdutf/releases/tag/v9.3.3), `simdutf.h` and `simdutf.cpp` amalgamations |
| Basis Universal | v2_50 | [Release](https://github.com/BinomialLLC/basis_universal/releases/tag/v2_50), commit `4d6fc70eaf62ad0558e63e8d97eb9766118327a6`; `transcoder/`, `zstd/zstddeclib.c`, `zstd/zstd.h`, `zstd/zstd_errors.h` and licenses |

The amalgamations match the SHA-256 hashes in fastgltf 1.0.0's
`cmake/dependencies.cmake`. When upgrading, update the source files, versions,
licenses, notices and build configuration together, and run the parser and
plugin tests. The combined [notices](NOTICES.txt) are included in installed
and packaged plugins.
