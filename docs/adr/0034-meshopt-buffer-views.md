# 0034: Meshopt buffer views

Decision record, 2026-11-15. See the [capability matrix](../reference/CAPABILITY_MATRIX.md)
for current verification.

Use meshoptimizer 0.25 for the ratified
[`EXT_meshopt_compression`](https://github.com/KhronosGroup/glTF/tree/main/extensions/2.0/Vendor/EXT_meshopt_compression)
bitstreams. Build the vendored codec sources into a hidden static dependency of
`gltfCore`, export the archive with the standalone library, and ship the MIT
license with the library and the bundle. `KHR_meshopt_compression` remains out of
scope.

- The first read of a compressed buffer view used by the selected scene decodes
  it. Later reads in the same conversion reuse the bytes by view index. Readers
  keep the bytes alive while any accessor borrows them. USD authoring sees the
  same buffer view layout and accessors as for uncompressed data.
- Compressed bytes can come from the GLB binary chunk, a data URI, or an
  [allowed external buffer](../architecture/IMPORT_PIPELINE.md#external-resource-policy).
  Validate both declared ranges, the decoded size, and the mode, stride, count
  and filter before decoding. Reject decoded data over 512 MiB in one reader.
  Validate the EXT bitstream header and let the codec check the encoded payload.
- Read `ATTRIBUTES` with `NONE`, `OCTAHEDRAL`, `QUATERNION` or `EXPONENTIAL`,
  and `TRIANGLES` and `INDICES` without a filter. Apply the filter before
  passing the bytes to the existing accessor readers.
- Prefer the compressed bytes for both required and optional uses. A required
  extension may reference a URI-less placeholder for the uncompressed layout.
  An optional extension retains its normal glTF fallback for clients without
  the extension. When the importer reads a compressed view, an invalid layout,
  missing compressed source, or corrupt payload fails the import even if
  fallback data is present. A failed reload preserves the old layer.

The sample check uses meshoptimizer's JavaScript reference decoder, independent
of the native codec and wrapper. It feeds the decoded bytes to the same tests of
mesh and animation values as for the other [Khronos samples](../adr/0010-sample-baseline.md).
