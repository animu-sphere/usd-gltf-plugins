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
| [Roadmap](roadmap/README.md), [current](roadmap/current.md), [backlog](roadmap/backlog.md) | **The only version plan:** unfinished scope, completion criteria, open decisions, task lifecycle | Finished work, test history, long design rationale |
| [Release records](releases/v0.2.0.md), `docs/releases/vX.Y.Z.md` | Each release's scope, constraints and evidence, written in its release pull request. | Current status, plans |

Decision records keep the options considered, the reasoning and the evidence
available when the decision was made; they are not progress reports:

- [Layer authoring](architecture/LAYER_AUTHORING.md)
- [Scene selection](architecture/SCENE_SELECTION.md)
- [Factor-only materials](architecture/MATERIAL_FACTORS.md)
- [0001: Image asset paths and lifetime](adr/0001-image-assets.md)
- [0002: Base color texture networks](adr/0002-base-color-texture-networks.md)
- [0003: Metallic-roughness, occlusion and emissive texture networks](adr/0003-pbr-texture-networks.md)
- [0004: Normal textures and tangents](adr/0004-normal-textures-and-tangents.md)
- [0005: KTX2 decoder](adr/0005-ktx2-decoder.md)
- [0006: Damaged and oversized images](adr/0006-image-failures.md)
- [0007: Display check](adr/0007-display-check.md)
- [0008: Release workflow](adr/0008-release-workflow.md)
- [0009: First release's targets and compatibility](adr/0009-release-targets.md)
- [0010: Sample asset baseline](adr/0010-sample-baseline.md)
- [0011: Texture transforms](adr/0011-texture-transform.md)
- [0012: Cameras](adr/0012-cameras.md)
- [0013: Lights](adr/0013-lights.md)
- [0014: WebP decoder](adr/0014-webp-decoder.md)
- [0015: Unlit materials](adr/0015-unlit.md)
- [0016: Index of refraction](adr/0016-ior.md)
- [0017: Clearcoat](adr/0017-clearcoat.md)
- [0018: Transmission](adr/0018-transmission.md)
- [0019: Volume](adr/0019-volume.md)
- [0020: Specular](adr/0020-specular.md)
- [0021: Sheen](adr/0021-sheen.md)
- [0022: Iridescence](adr/0022-iridescence.md)
- [0023: Anisotropy](adr/0023-anisotropy.md)
- [0024: Import measurements and mesh sharing](adr/0024-import-measurements.md)
- [0025: Sources inside packages](adr/0025-packaged-sources.md)
- [0026: Scene hierarchy](adr/0026-scene-hierarchy.md)
- [0027: Plugin workspace members](adr/0027-plugin-workspace.md)

New decisions go in `docs/adr/NNNN-title.md`.

## Change rules

- A pull request that changes the implementation also updates the capability
  matrix.
- A specification change updates only the owning design or architecture
  document.
- A plan change updates only the roadmap.
- A pull request that finishes a task deletes it from the
  [current plan](roadmap/current.md).

The `docs_consistency` test ([check_docs.py](../tests/check_docs.py)) checks
relative links, matrix states and evidence paths, and misplaced status lines,
CI run links and release headings. It does not determine support from the code:
that requires tests and, for display claims, renderer checks.
