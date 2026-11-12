# 0033: Draco mesh decoder

Decision record, 2026-11-12. See the [capability matrix](../reference/CAPABILITY_MATRIX.md)
for current verification.

## Decision

Use the native mesh decoder from Google Draco 1.5.9 for
[`KHR_draco_mesh_compression`](https://github.com/KhronosGroup/glTF/tree/main/extensions/2.0/Khronos/KHR_draco_mesh_compression).
The [vendored dependency record](../../third_party/README.md) identifies the exact source and
license. The Khronos extension specifies Draco bitstream 2.2, which upstream's glTF configuration
supports for both sequential and standard Edgebreaker meshes. All 18 compressed files in the
pinned Khronos sample collection use that bitstream.

Compile the decoder into `gltfCore` as a static library with hidden symbols. This keeps the bundle
self-contained and lets hosts load their own Draco libraries. The standalone `gltfCore` package
exports the decoder archive as a link dependency and installs its license. The bundle carries the
Draco attribution in its combined notices.

The build uses the same vendored sources and the workspace's CMake 3.22+ and C++20 toolchain for
Windows x86-64 (MSVC), Linux x86-64, and macOS arm64 (Apple Clang). The decoder builds offline
without an additional SDK or system library. The [manual sample check](../DEVELOPMENT.md) uses
Node.js and the upstream Wasm module to verify the authored mesh values.

## Mapping and fallback

- `TRIANGLES` primitives use Draco whenever the primitive carries the extension. This applies
  whether the extension is required or optional and when uncompressed fallback bytes are present.
- The extension's bufferView may be in a GLB binary chunk, a data URI, or a restricted external
  buffer. The [external resource policy](../architecture/IMPORT_PIPELINE.md#external-resource-policy)
  applies to these buffers as it does to core glTF buffers.
- The mapping names a Draco unique attribute ID for each compressed semantic. Each decoded value
  is read through Draco's point-to-attribute mapping. The original glTF accessor supplies its
  component type, shape, count, and normalization. The decoded components must match the accessor
  type and count. The compressed path ignores the accessor's bufferView and byteOffset.
- The decoded face list supplies the triangle indices. A declared index accessor must be an
  unsigned scalar with matching count and room for the decoded values. Other attributes and morph
  target data use their original glTF accessors and must match the decoded point order.
- A required `TRIANGLE_STRIP` fails with a diagnostic. An optional `TRIANGLE_STRIP` uses its core
  glTF accessors and warns when that fallback succeeds. Other compressed primitive modes fail.
- Corrupt Draco data for a selected compressed triangle list, missing mapped IDs, and inconsistent
  accessor declarations fail the import. A failed reload keeps the existing layer.

The Wasm reference decodes each sample's compressed faces and attributes independently of the
plugin's native wrapper and USD authoring. The sample check compares positions, triangle winding,
normals, UVs, tangents, colors and skinned points where present. The integration fixture also
checks unsigned normalized color, quantized positions, an unmapped UV accessor, and the required
and optional paths.

## Package size

Compare v0.4.0 (`31585d8`) with the Draco build, using Release builds against the same OpenUSD 26.08
runtime and compiler on each host. The archive measurement uses `tar --zstd` on each CMake install
tree, including plugin registration and notices. Sizes are in bytes.

| Environment | Plugin library, v0.4.0 → Draco | Install archive, v0.4.0 → Draco | Archive increase |
| --- | ---: | ---: | ---: |
| Windows x86-64, MSVC 19.51 | 3,054,592 → 3,243,520 | 1,308,421 → 1,390,410 | 81,989 (6.3%) |
| Linux x86-64, GCC 15.2 in Ubuntu under WSL2 | 4,267,544 → 4,811,872 | 1,924,007 → 2,101,192 | 177,185 (9.2%) |

The [current roadmap](../roadmap/current.md) tracks the remaining macOS arm64 measurement and
release checks.
