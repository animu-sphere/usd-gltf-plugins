# Current

Unfinished work only; see the [task lifecycle](README.md#task-lifecycle). Tasks are in
implementation order, which investigation may change. Each feature follows the
[contract for adding a feature](../design/IMPLEMENTATION_POLICY.md#adding-a-gltf-feature).

## v0.3.0: scene graph, animation and deformation

Scene graph correctness comes first. The [scene hierarchy](../adr/0026-scene-hierarchy.md),
[skin placement](../adr/0028-skin-placement.md) and [node animation](../adr/0029-node-animation.md)
decisions set the structure; each task below builds on the last.

1. Choose between glTF animations, rather than authoring the first with a warning, with a variant set
   or a layer per animation decided in an ADR after a proof of concept. Node, joint and morph
   animation switch together, one default is deterministic, each animation's timeline and playback is
   documented, and animations never mix. This comes before skins, because where a
   `UsdSkelAnimation` and its `skel:animationSource` binding live depends on it.
2. Skins through `UsdSkel`, placed as the skin placement decision specifies: skeletons, joints,
   bindings, skel roots and skeletal animation through `skel:animationSource`, with skins and joints
   added to the conversion's map of source indices to USD paths. Record the half-precision scales of
   animated joints as a limit.
3. Morph targets and weights, static and animated, including skinned meshes and meshes that several
   nodes use.
4. Representative fixtures and samples for transform equivalence over time, and consumer checks
   where feasible, with the release record calling out the path change and its migration.

### Release criteria

- Imported assets have stable paths below `/Asset/scenes/Scene_{index}` and `/Asset/mtl`, and nested
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
- The release record and changelog call out the breaking change of prim paths.
- The [release gate](README.md#release-gate) is satisfied.

If the scope grows too large, keep scene graph and correctness work in this version and defer
optimization rather than weaken correctness. Until the criteria are met, use a pre-release such as
`v0.3.0-alpha.1` rather than presenting the work as complete.
