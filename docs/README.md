# Documentation

This documentation records the adopted initial design, not an implementation
claim. Design decisions are distinguished from questions that require
experiments or an architecture decision record (ADR).

| Category | Purpose | Start here |
| --- | --- | --- |
| Architecture | Component responsibilities and import flow | [Workspace](architecture/WORKSPACE.md) · [Import pipeline](architecture/IMPORT_PIPELINE.md) |
| Design | Scope and intended conversion contracts | [Implementation policy](design/IMPLEMENTATION_POLICY.md) · [Scene conventions](design/SCENE_CONVENTIONS.md) · [Materials](design/MATERIALS.md) · [Textures](design/TEXTURES.md) |
| Reference | What is actually implemented and supported | [Capability matrix](reference/CAPABILITY_MATRIX.md) |
| Roadmap | Phases, acceptance criteria, and unresolved decisions | [Roadmap](roadmap/README.md) |

Each subject has one primary owner: workspace structure belongs to
WORKSPACE, import mechanics to IMPORT_PIPELINE, and conversion conventions to
the corresponding design document. The policy sets scope and principles;
the roadmap records outstanding work. When implementation diverges from an
adopted design, resolve the discrepancy explicitly rather than presenting
planned behavior as implemented.

Installation, build, and usage guides will be added when there are verified
procedures to document. ADRs will record decisions reached through the
investigations listed in the [roadmap](roadmap/README.md#decisions-to-verify).
