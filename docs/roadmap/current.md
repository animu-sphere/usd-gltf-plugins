# Current

Unfinished work only; see the [task lifecycle](README.md#task-lifecycle). Tasks are in
implementation order, which investigation may change. Each feature follows the
[contract for adding a feature](../design/IMPLEMENTATION_POLICY.md#adding-a-gltf-feature).

## v0.7.0: Meshopt compression

- Define how `EXT_meshopt_compression` bufferViews are decoded and shared across accessor reads.
  Record the decoder, input bounds, fallback behavior and supported modes and filters in an ADR.
- Import the supported compressed bufferViews through the existing buffer and accessor paths.
  Verify mesh and animation data against Khronos samples and cover valid optional fallback and
  corrupt payloads with fixtures.

### Release criteria

- The two Khronos files that require `EXT_meshopt_compression` open, their USD data matches the
  decoded glTF data, and the sample baseline records the results.
- The [release gate](README.md#release-gate) is satisfied.
