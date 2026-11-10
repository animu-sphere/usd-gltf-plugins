# 0027: Plugin workspace members

Decision record, 2026-11-09. The [workspace architecture](../architecture/WORKSPACE.md) describes
the resulting layout, and the [README](../../README.md#development) has the build commands.

## Context

The repository had one OpenStrata member, the plugin bundle, and two CMake entry points that did the
same work twice. The root added the vendored dependencies, the three libraries and the bundle; the
bundle, when it was the top-level project, added the same things from the repository root itself.
The two wrote their outputs to different places: the root to its build directory, the bundle to
its own source directory. The libraries kept headers and sources side by side, with `gltfUsd`'s
internal headers indistinguishable from its interface, and had no OpenStrata identity, so
`ost plugin test --workspace --graph-only` saw one bundle and nothing it is built from.

OpenStrata's [plugin-workspace guide](https://github.com/animu-sphere/open-strata/blob/main/docs/guides/adopt-a-plugin-workspace.md)
and usd-vrm-plugins describe a different shape: each library and bundle is a CMake project of its
own with a descriptor, the root only orchestrates, and the same tree builds with `ost` and with
plain CMake.

## Options

- **Keep one member.** The smallest change, but the graph would keep omitting the libraries, and
  `ost library build` could not build or test any of them alone.
- **Libraries as members, and the bundle requires them.** The graph would have every edge.
  `requires.libraries` on a bundle is a runtime requirement, however: `ost plugin package` stages
  each required library's whole install tree under `runtime/libraries/` in the bundle's package.
  These libraries are static, so that would ship their archives, headers and CMake packages, and the
  vendored dependencies' archives, in a plugin package that needs none of them. OpenStrata 0.23.19
  has build-only scopes (`build_libraries`) for library descriptors, not for bundles.
- **Libraries as members, and the bundle builds them into itself.** The libraries' edges between
  each other are declared and checked, and each library builds, installs and tests alone. The bundle
  links them statically from source, as before, and declares no library requirement.

## Decision

The third option.

- `libs/gltfCore`, `libs/gltfTexture` and `libs/gltfUsd` each have an `openstrata.library.yaml` and
  are listed in `openstrata.toml`. `gltfTexture` requires `gltfCore`, and `gltfUsd` requires both.
  Their runtime directories are empty, because they are static.
- Each library is a CMake project with `include/<library>/` for its interface, `src/` for its
  implementation and private headers, and `tests/` for its unit tests. When it is the top-level
  project, as under `ost library build`, it installs a CMake package `<library>::<library>` that also
  exports the vendored archives it links, so that a consumer of the installed package can link it.
  Added from another project, it installs nothing.
- The bundle keeps its sources in `src/` and its `plugInfo.json` template in
  `plugin/resources/usdGltfFileFormat/`. As the top-level project it adds the libraries from source
  and writes its outputs to its own directory, as before; as a subdirectory it uses the root's
  libraries and writes to the root's build directory. `ost plugin build` installs it to its stage,
  which `ost plugin test` and `ost plugin package` read. In OpenStrata 0.23.19 `ost plugin view` and
  `ost plugin test-view` do not read that stage but the bundle's directory
  ([open-strata#296](https://github.com/animu-sphere/open-strata/issues/296)), so the standalone build
  cannot stop writing there; it did briefly while this was implemented, and `ost plugin view`
  could not open a `.glb`. OpenStrata 0.23.20 fixed it, and the standalone build has written to its
  build directory since.
- The root finds OpenUSD once, adds the libraries in dependency order and every bundle under
  `plugins/`, and registers the tests that need the whole workspace.
- `VERSION` is the one version. CMake reads it, the descriptors mirror it, and
  `scripts/make_release_notes.py` checks them, including that each library requirement is the
  current minor version, in CTest and in the release workflow.
- A plain-CMake CI lane builds and tests the workspace against the OpenUSD install that the Linux
  workspace cell pins, with no `ost` on the runner.

## Consequences

The bundle's package is unchanged: its library, `plugInfo.json`, notices and license. The workspace
graph has three libraries and their three edges, but no edge from the bundle, so a library change
is not attributed to the bundle by the graph; the bundle's build still rebuilds it. If OpenStrata
gains a build-only library scope for bundles, the bundle should declare its libraries with it.

`ost plugin test` on the bundle still declares no smoke fixture, so it confirms discovery and not an
import, as before.
