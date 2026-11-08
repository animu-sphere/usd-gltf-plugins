# Current

Unfinished work only; see the [task lifecycle](README.md#task-lifecycle). Tasks are in
implementation order, which investigation may change. Each feature follows the
[contract for adding a feature](../design/IMPLEMENTATION_POLICY.md#adding-a-gltf-feature).

## v0.3.0: animation and deformation

Each task builds on the last.

1. Map glTF time, interpolation and samplers to USD time samples, with STEP, LINEAR and CUBICSPLINE
   each verified, and diagnostics for invalid samplers.
2. TRS animation of nodes, multiple clips, and a policy for animations outside the selected scene,
   which are rejected today.
3. Joints, inverse bind matrices and skins through `UsdSkel`, alongside node transforms.
4. Morph targets and animated weights, with tests of skins combined with morphs.
5. Decide placement such as `/Asset/rig` and `/Asset/anim` in an ADR, after composition and viewer
   experiments. It is not a required structure today.

### Release criteria

Representative animated models are consistent in values and poses at multiple times, across multiple
clips and reload. Invalid samplers have diagnostics, and unsupported interpolation and deformation are
explicit. Display tests state their environment, and behavior that differs from glTF is recorded as a
limit in the capability matrix and the release record. The [release gate](README.md#release-gate) is
satisfied.

Until then, use a pre-release such as `v0.3.0-alpha.1` rather than presenting the work as complete.
