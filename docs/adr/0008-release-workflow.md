# 0008: Release workflow

Decision record, 2026-11-02. The
[release process](../roadmap/README.md#release-process) says how to use it, and the
[capability matrix](../reference/CAPABILITY_MATRIX.md) records what has been run.

## Context

CI builds and tests, but its cells stop at `verify: test`, so packaging had never run on any
platform. `usd-vrm-plugins` has a mature release workflow, and was the reference. Much of it
serves things this repository does not have: several interdependent bundles, command line tools,
libraries for C++ consumers, and registry packages that other projects pin.

## Decision

Keep the parts that apply to one bundle, and leave out the rest until there is a need:

| Kept | Left out, and why |
| --- | --- |
| Tag check, and a dry run through `workflow_dispatch` | Registry publication: nothing pins this package yet |
| Build, test and package on each CI platform, with a reproducible digest | The aggregate product: with one bundle it only wraps the same archive |
| Proof that the package works outside the repository | Consumer lanes: the package has no CMake interface |
| A **draft** release, published by a person after the release gate | Closure checklists: the workflow fails on the first failed step instead |

**Pins are read, not copied.** The workflow takes its cells from `ost ci matrix --lane main`.
The reference's `release.yml` has its own copy of these pins, and its comments record a tag build
that failed because that copy went stale while pull request lanes stayed green.

**Pre-releases keep the package's version.** The
[roadmap](../roadmap/current.md#release-criteria) asks for a tag such as `v0.1.0-alpha.1`. The
project and bundle stay at `0.1.0`, so package files are named `0.1.0` whatever the suffix, and two
pre-releases can have files of one name with different content. Each release's `SHA256SUMS` tells
them apart. A pre-release takes its notes from `[Unreleased]`, and a release needs a dated section.

**The bundle now contains `LICENSE`.** A package holds only the plugin's own tree, not the
`share/licenses` directory of a CMake install, so it carried third-party notices and not the
project's license. `tests/check_package.py` requires both.

**The display check stays manual.** It needs a GPU, which hosted runners do not offer in a way
this project has tested, so it is a [release gate](../roadmap/README.md#release-gate) step at the
tagged commit rather than a job. See [ADR 0007](0007-display-check.md).

## Limits

- `ost plugin test --from-package` reaches only discovery, because the bundle declares no smoke
  fixture for its L3 and L4 levels. `tests/check_package.py` covers import and images instead.
- The package requires the exact OpenUSD runtime it was built for, as its manifest states.
