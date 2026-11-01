# Documentation

## Document ownership

Each fact has one owning document. Other documents link to the owner instead
of copying progress, support tables or CI results. When implementation
diverges from an adopted design, resolve the discrepancy explicitly rather
than presenting planned behavior as implemented.

| Document | Owns | Does not contain |
| --- | --- | --- |
| [Root README](../README.md) | Product description, quick start, build and test commands, links, licenses | Feature lists, test results, detailed design, progress |
| This index | Table of contents and ownership rules | Implementation status |
| [Contributing](../CONTRIBUTING.md), [Security](../SECURITY.md), [Code of Conduct](../CODE_OF_CONDUCT.md) | How to contribute, report vulnerabilities and behave in project spaces | Build commands, specifications |
| [Changelog](../CHANGELOG.md) | Notable changes per release | Support tables, plans |
| [Implementation policy](design/IMPLEMENTATION_POLICY.md) | Stable principles, non-goals, design constraints | Version plans, achievement status |
| [Workspace](architecture/WORKSPACE.md) | Component responsibilities, dependency direction, workspace structure | Build procedures, progress |
| [Import pipeline](architecture/IMPORT_PIPELINE.md) | Parse, resolve, convert and publish flow; failure and URI policy | Material graphs, support tables |
| [Scene conventions](design/SCENE_CONVENTIONS.md) | Meaning of prim hierarchy, names, coordinates, transforms and primvars | What is or is not implemented |
| [Materials](design/MATERIALS.md) | PBR, shader graph, channel and approximation specification | Material progress |
| [Textures](design/TEXTURES.md) | URI, image conversion, cache and path lifetime specification | Texture support status |
| [Capability matrix](reference/CAPABILITY_MATRIX.md) | **The only record of what is implemented and verified:** scope, limitations, evidence, environments | Future commitments, release plans |
| [Roadmap](roadmap/README.md) | **The only version plan:** scope, completion criteria, open decisions | Test history, long design rationale |

Decision records keep the options considered, the reasoning and the evidence
available when the decision was made; they are not progress reports:

- [Layer authoring](architecture/LAYER_AUTHORING.md)
- [Scene selection](architecture/SCENE_SELECTION.md)

New decisions go in `docs/adr/NNNN-title.md`.

## Change rules

- A pull request that changes the implementation also updates the capability
  matrix.
- A specification change updates only the owning design or architecture
  document.
- A plan change updates only the roadmap.

The `docs_consistency` test ([check_docs.py](../tests/check_docs.py)) checks
relative links, matrix states and evidence paths, and misplaced status lines,
CI run links and release headings. It does not determine support from the code:
that requires tests and, for display claims, renderer checks.
