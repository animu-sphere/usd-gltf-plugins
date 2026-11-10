# Current

Unfinished work only; see the [task lifecycle](README.md#task-lifecycle). Tasks are in
implementation order, which investigation may change. Each feature follows the
[contract for adding a feature](../design/IMPLEMENTATION_POLICY.md#adding-a-gltf-feature).

## v0.3.0: scene graph, animation and deformation

The [scene hierarchy](../adr/0026-scene-hierarchy.md),
[skin placement](../adr/0028-skin-placement.md), [node animation](../adr/0029-node-animation.md) and
[animation choice](../adr/0030-animation-choice.md) decisions set the structure.

### Release criteria

- Imported assets have stable paths below `/Root/Scenes/Scene_{index}` and `/Root/Materials`, and nested
  nodes, with their meshes, cameras and lights, match the source's world transforms.
- Skins work with several skinned meshes and shared joints, and skel roots are existing prims, so a
  node's path does not depend on whether the asset has skins.
- Joint and node animation reproduce the expected poses at multiple times, across reload. Morph
  targets work at rest and with animated weights, including skinned examples.
- Several animations have a documented, reproducible way to choose one, and are never mixed.
- No geometry is drawn twice because of resource definitions.
- Invalid samplers have diagnostics, and unsupported interpolation and deformation are explicit.
  Behavior that differs from glTF is recorded as a limit in the capability matrix and the release
  record, and display tests state their environment.
- The release record and changelog call out the breaking change of prim paths and explain how to
  update references and overrides.
- The [release gate](README.md#release-gate) is satisfied.

If the scope grows too large, keep scene graph and correctness work in this version and defer
optimization rather than weaken correctness. Until the criteria are met, use a pre-release such as
`v0.3.0-alpha.1` rather than presenting the work as complete.
