# 0009: First release's targets and compatibility

Decision record, 2026-11-02. The [capability matrix](../reference/CAPABILITY_MATRIX.md) records
what has been verified, and [ADR 0008](0008-release-workflow.md) how packages are made.

## Context

The roadmap left three questions for investigation before any support claim: which OpenUSD builds
and compilers to target and how fastgltf affects their ABI, what renderers do with the authored
data, and which platforms are supported.

## Decision

**Targets are the three OpenStrata `cy2026` / `usd` runtimes that CI pins:** Windows x86-64
(MSVC toolset 143), Linux x86-64 and macOS arm64, each with OpenUSD 26.08 and Python 3.13. A package
works only with the exact runtime it was built for, and its manifest requires `==26.08`. Building
from source against another OpenUSD is possible through the root CMake project, but is not tested
and not claimed.

**fastgltf and the other vendored libraries are private to the plugin.** They are compiled from
pinned sources in [`third_party`](../../third_party/README.md) with the runtime's C++20 toolchain and
linked statically, and no type of theirs crosses the plugin's boundary, which is `SdfFileFormat` and
`ArPackageResolver` as `plugInfo.json` registers them. The Linux package at first exported about 500
of their symbols, so another copy in the process could have bound to them. Everything is now
compiled with hidden visibility. The parser enables `KHR_texture_basisu`, which is implemented, and
`KHR_lights_punctual`, parsed only so that selected lights are rejected.

**Display is claimed only for Storm on Windows,** with the scope of the
[display check](../reference/CAPABILITY_MATRIX.md#display-checks), through both shader paths. It
covers UV orientation and winding. The asset authors `upAxis = "Y"` and `metersPerUnit = 1`, which a
renderer needs to honor, and that was not tested beyond Storm. Linux and macOS have tests of USD
data and no display check, and no other renderer has been tried.

## Limits

- One OpenUSD version on three platforms is not a compatibility range. Wider verification is in
  the [backlog](../roadmap/backlog.md).
- Nothing was run on hosts other than the CI runners and one Windows machine.
