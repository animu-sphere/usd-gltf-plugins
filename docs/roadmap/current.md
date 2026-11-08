# Current

Unfinished work only; see the [task lifecycle](README.md#task-lifecycle). Tasks are in
implementation order, which investigation may change. Each feature follows the
[contract for adding a feature](../design/IMPLEMENTATION_POLICY.md#adding-a-gltf-feature).

## v0.2.0: wider static scene support

### Release criteria

Each adopted feature has fixtures, tests of USD structure and meaning, tests of failure, and renderer
tests where display compatibility is claimed. Sample assets are recorded as opened, verified in USD
meaning and rendered, each separately, so that opening does not imply support. The capability matrix
and release record agree on the scope and constraints. The [release gate](README.md#release-gate) is
satisfied.

Until then, use a pre-release such as `v0.2.0-alpha.1` rather than presenting the work as complete,
and narrow the scope or wait. Do not claim support for all of glTF.
