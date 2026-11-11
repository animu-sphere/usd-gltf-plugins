# Current

Unfinished work only; see the [task lifecycle](README.md#task-lifecycle). Tasks are in
implementation order, which investigation may change. Each feature follows the
[contract for adding a feature](../design/IMPLEMENTATION_POLICY.md#adding-a-gltf-feature).

## v0.4.0: optimization and developer tools

- Add fuzz and property coverage for malicious URIs, damaged accessors and images, and very large
  inputs. Address serious security findings as they are discovered.
- Verify ABI and package loading for additional OpenUSD versions and compilers before adding them
  to the supported targets.

### Release criteria

- Conversion and distribution procedures are reproducible, including opening a derived package
  independently of its source.
- Each shipped optimization has a recorded baseline and a measured result on the same sample set.
- The [release gate](README.md#release-gate) is satisfied.
