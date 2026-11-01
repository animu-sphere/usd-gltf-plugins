# Roadmap

The only version plan, without dates. What is implemented is in the
[capability matrix](../reference/CAPABILITY_MATRIX.md), and what shipped is in the
[changelog](../../CHANGELOG.md).

| Document | Contains |
| --- | --- |
| [current.md](current.md) | Unfinished work for the next release only. |
| [backlog.md](backlog.md) | Later versions, and decisions they depend on. |

## Task lifecycle

- **Done:** the pull request that finishes a task deletes it from `current.md`.
  Nothing is kept as a checked item. Behavior moves to the owning design or
  architecture document, evidence to the capability matrix, and delivered scope to
  the changelog.
- **Partly done:** reword the task so that it states only what remains.
- **Next version:** when a release ships, move the next version from `backlog.md` to
  `current.md`, replacing its scope with concrete tasks.
- **Dropped:** delete the task, and give the reason in the pull request.

The `docs_consistency` test checks that `current.md` plans one version, that a version
appears in only one document, and that no checked item remains.

## Release gate

Every release must satisfy these checks:

- [ ] Required conditions for the version were confirmed on the stated OS,
      OpenUSD and renderer.
- [ ] The capability matrix shows implementation, limitations and test evidence,
      and no other document repeats progress.
- [ ] `.gltf`, `.glb`, reload and failure, both shaders, bindings, PNG/JPEG and
      KTX2 were confirmed end to end, as applicable to the version.
- [ ] Saved images resolve after reopening, and the constraints on moving and
      packaging can be explained.
- [ ] Failure tests exist for invalid URIs, corrupt or huge buffers and images,
      and required extensions.
- [ ] Root CMake, standalone OpenStrata, CI, install and discovery, and inclusion
      of third-party licenses were verified.
- [ ] CI and renderer checks were confirmed at the release commit. Past successes
      are not treated as a current guarantee.
- [ ] The tag and release notes briefly state changes, the supported subset, known
      constraints and test environments, without copying the capability matrix.
