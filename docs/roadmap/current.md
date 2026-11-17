# Current

Unfinished work only; see the [task lifecycle](README.md#task-lifecycle). Tasks are in
implementation order, which investigation may change. Each feature follows the
[contract for adding a feature](../design/IMPLEMENTATION_POLICY.md#adding-a-gltf-feature).

## v0.12.0 — Dispersion

- Evaluate `KHR_materials_dispersion` against a pinned Khronos specification and the MaterialX and
  UsdPreviewSurface shader paths. MaterialX `gltf_pbr` 1.39.5 declares `dispersion`, but its graph
  does not read it. Record the representation and fallback in an ADR.
- If the representation passes the evaluation, import the factor in glTF and GLB, with its
  interaction with `KHR_materials_transmission`, `KHR_materials_volume` and `KHR_materials_ior`.
  Verify the authored data against the Khronos samples, and record what Storm draws.

### Release criteria

- The supported dispersion behavior and shader limits are documented and verified.
- The [release gate](README.md#release-gate) is satisfied.
