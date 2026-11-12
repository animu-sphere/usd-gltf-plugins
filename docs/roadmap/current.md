# Current

Unfinished work only; see the [task lifecycle](README.md#task-lifecycle). Tasks are in
implementation order, which investigation may change. Each feature follows the
[contract for adding a feature](../design/IMPLEMENTATION_POLICY.md#adding-a-gltf-feature).

## v0.5.0: Draco mesh compression

- Evaluate a pinnable Draco decoder for `KHR_draco_mesh_compression`. Measure the package size and
  document the license, build requirements and decision for the release platforms.
- If the decoder is adopted, define how Draco attributes and indices map to the existing mesh
  conversion. Verify required and optional extension handling, including uncompressed fallback and
  preservation of the existing layer when a compressed payload fails to decode.

### Release criteria

- Any shipped Draco support opens the supported compressed Khronos samples and verifies their
  authored mesh data. Unsupported inputs produce diagnostics and the sample baseline records the
  observed outcomes.
- The [release gate](README.md#release-gate) is satisfied.
