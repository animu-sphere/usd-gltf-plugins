# Current

Unfinished work only; see the [task lifecycle](README.md#task-lifecycle). Tasks are in
implementation order, which investigation may change. Each feature follows the
[contract for adding a feature](../design/IMPLEMENTATION_POLICY.md#adding-a-gltf-feature).

## v0.4.0: optimization and developer tools

- Establish a reproducible sample set and record import latency, peak RSS, repeated mesh arrays,
  and KTX2 decode counts and time. Extend the existing [import measurements](../reference/CAPABILITY_MATRIX.md#import-measurements).
- Use the measurements to choose improvement targets for mesh sharing and image caching. Resolve
  the [mesh sharing decision](backlog.md#decisions-for-later-versions) before introducing shared
  definitions or native instancing. Verify scene placement, rendering, and cache isolation across
  stages, resolvers and threads.
- Define and test a portable package for derived USD and converted images. Verify that it opens
  independently of the glTF source, and that repeated conversions can reuse cached results safely.
  Evaluate a CLI that calls the same conversion library if the packaging workflow needs one.
- Add fuzz and property coverage for malicious URIs, damaged accessors and images, and very large
  inputs. Address serious security findings as they are discovered.
- Verify ABI and package loading for additional OpenUSD versions and compilers before adding them
  to the supported targets.

### Release criteria

- Conversion and distribution procedures are reproducible, including opening a derived package
  independently of its source.
- Each shipped optimization has a recorded baseline and a measured result on the same sample set.
- The [release gate](README.md#release-gate) is satisfied.
