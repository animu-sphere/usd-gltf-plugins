# Current

Unfinished work only; see the [task lifecycle](README.md#task-lifecycle). Tasks are in
implementation order, which investigation may change. Each feature follows the
[contract for adding a feature](../design/IMPLEMENTATION_POLICY.md#adding-a-gltf-feature).

## v0.3.0: scene graph, animation and deformation

Scene graph correctness comes first. The [scene hierarchy decision](../adr/0026-scene-hierarchy.md)
sets the structure; each task below builds on the last.

1. Keep an explicit map in the conversion from source scene, node, mesh and skin indices to USD
   paths, which later tasks extend with joints and inserted skel roots. Separate converting a mesh
   from placing it below a node, keyed by mesh index. Test parentage and world transforms with
   nested-node fixtures.
2. Decide in an ADR, after experiments with UsdSkel validation and a renderer: where a skel root goes
   when joints and skinned meshes have different ancestors; joints with children other than joints,
   joints animated or referenced elsewhere; shared skins and several skinned meshes; bind-space
   consistency, joint order and animated parents.
3. Map glTF time, interpolation and samplers to USD time samples, with STEP, LINEAR and CUBICSPLINE
   each verified, a documented sampling fallback where USD cannot interpolate the same way, and
   diagnostics for invalid samplers. Animate node translation, rotation and scale, and decide a policy
   for animations of nodes outside the selected scene, which are rejected today.
4. Skins through `UsdSkel`: joints, inverse bind matrices, bindings and skeletal animation through
   `skel:animationSource`.
5. Morph targets and weights, static and animated, including skinned meshes and meshes that several
   nodes use.
6. Choose between glTF animations, with a variant set or a layer per animation decided in an ADR
   after a proof of concept. Node, joint and morph animation switch together, one default is
   deterministic, the mapping of time codes and playback is documented, and animations never mix.
7. Representative fixtures and samples for transform equivalence over time, and consumer checks
   where feasible, with the release record calling out the path change and its migration.

### Release criteria

- Imported assets have stable paths below `/Asset/scenes/Scene_{index}` and `/Asset/mtl`, and nested
  nodes, with their meshes, cameras and lights, match the source's world transforms.
- Skins work with several skinned meshes and shared skeletons, and generated skel roots have neutral,
  deterministic names.
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
