# Contributing

Thanks for helping improve `usd-gltf-plugins`. Small fixes, documentation
updates, tests, and questions are all welcome.

## Before you start

- Search existing issues and pull requests first.
- For a larger change, open an issue before doing substantial work. This helps
  avoid solving the wrong problem. The [roadmap](docs/roadmap/README.md) shows
  the planned scope of each version.
- Do not include private models, textures, credentials, or other material you
  are not allowed to redistribute. Check the license of test assets before
  adding them.

## Build and test

Follow the commands in the [README](README.md#development). A change to the
build must keep both
[build entry points](docs/architecture/WORKSPACE.md#build-entry-points) working.

For a documentation-only change, the full build is usually unnecessary. Run the
documentation checks, and mention in the pull request that the build was not run:

```sh
python tests/check_docs.py
```

## Formatting

C and C++ files use the repository's `.clang-format` configuration. Run
clang-format on files you changed, for example:

```sh
clang-format -i path/to/file.cpp path/to/file.h
```

Please do not format vendored code under `third_party/`.

## Documentation

Each fact has one owning document. See the
[ownership table and change rules](docs/README.md#document-ownership), and
update the [capability matrix](docs/reference/CAPABILITY_MATRIX.md) with evidence
when the implementation changes.

## Pull requests

Keep a pull request focused and explain:

1. what changed and why;
2. how it was tested; and
3. any compatibility, documentation, or follow-up work that matters.

A small pull request is easier to review than a broad cleanup. Reviewers may
ask for tests or documentation when a change affects supported behavior.

By contributing, you agree that your work is provided under the repository's
[Apache-2.0 license](LICENSE).

Please also follow the [Code of Conduct](CODE_OF_CONDUCT.md).
