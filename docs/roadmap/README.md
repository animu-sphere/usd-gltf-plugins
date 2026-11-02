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
- **Next version:** the release pull request moves the next version from `backlog.md` to
  `current.md`, replacing its scope with concrete tasks.
- **Deferred:** move the task to a version in `backlog.md`, and give the reason in the pull request.
- **Dropped:** delete the task, and give the reason in the pull request.

The `docs_consistency` test checks that `current.md` plans one version, that a version
appears in only one document, and that no checked item remains.

## Release gate

CI and the [release workflow](../../.github/workflows/release.yml) fail on their own for tests,
packaging and packages that do not work outside the repository. A person confirms the rest, at the
commit to be tagged:

- The version's release criteria are met.
- The [display check](../reference/CAPABILITY_MATRIX.md#display-checks) passes, and the
  [sample check](../reference/CAPABILITY_MATRIX.md#sample-assets) matches its baseline.
- The release record states changes, the supported subset, known constraints and test environments,
  without copying the capability matrix.

## Release process

1. Open one release pull request. It sets the version in `openstrata.toml` and the bundle manifest,
   gives the changelog a dated section, adds `docs/releases/vX.Y.Z.md`, and moves the next version
   out of the backlog. A pre-release such as `v0.1.0-alpha.1` needs none of this.
2. After merging, run the release workflow by hand on `main`, and the display and sample checks on
   that commit.
3. Push the tag. The workflow also pushes the packages to GHCR, and creates a draft release with
   their pins.
4. Edit the draft's notes if needed, and publish it.

A release needs no pull request after the tag. Commits, run links and checksums are in the GitHub
release, not the record. A problem found by steps 2 to 4 is fixed on `main` before tagging.

The [decision record](../adr/0008-release-workflow.md) explains what the workflow does not do.
