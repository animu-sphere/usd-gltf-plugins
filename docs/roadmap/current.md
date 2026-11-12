# Current

Unfinished work only; see the [task lifecycle](README.md#task-lifecycle). Tasks are in
implementation order, which investigation may change. Each feature follows the
[contract for adding a feature](../design/IMPLEMENTATION_POLICY.md#adding-a-gltf-feature).

## v0.6.0: Tangent frames

- Evaluate supplied and generated tangent frames for normal maps and anisotropy in MaterialX and
  UsdPreviewSurface. Revise [ADR 0004](../adr/0004-normal-textures-and-tangents.md#tangents) with the
  supported shading paths and the frame contract before changing authoring.
- Implement the adopted frame contract and cover handedness, mirrored UVs and nonuniform transforms.

### Release criteria

- Compare the shipped frame behavior with a glTF reference renderer for supplied and generated
  tangents. Record agreement and renderer limitations in the capability matrix.
- The [release gate](README.md#release-gate) is satisfied.
