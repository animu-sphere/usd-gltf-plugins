# Current

Unfinished work only; see the [task lifecycle](README.md#task-lifecycle). Tasks are in
implementation order, which investigation may change. Each feature follows the
[contract for adding a feature](../design/IMPLEMENTATION_POLICY.md#adding-a-gltf-feature).

## v0.5.0: Draco mesh compression

- Verify the Draco-enabled bundle on macOS arm64 in release CI and measure its package size against
  a v0.4.0 build with the same toolchain. Add the result to the
  [decoder decision](../adr/0033-draco-decoder.md#package-size).

### Release criteria

- Any shipped Draco support opens the supported compressed Khronos samples and verifies their
  authored mesh data. Unsupported inputs produce diagnostics and the sample baseline records the
  observed outcomes.
- The [release gate](README.md#release-gate) is satisfied.
