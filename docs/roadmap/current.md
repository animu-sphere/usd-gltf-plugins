# Current

Unfinished work only; see the [task lifecycle](README.md#task-lifecycle). Tasks are in
implementation order, which investigation may change. Each feature follows the
[contract for adding a feature](../design/IMPLEMENTATION_POLICY.md#adding-a-gltf-feature).

## v0.14.0 — Points and lines

- Decide in an ADR how POINTS, LINES, LINE_LOOP and LINE_STRIP map to USD, for example
  `UsdGeomPoints` and linear `UsdGeomBasisCurves`. Cover widths, which glTF does not define, normals,
  vertex colors, texture coordinates, materials, and lighting when NORMAL is absent.
- Import them by that decision. Verify against MeshPrimitiveModes and PrimitiveModeNormalsTest,
  with a display check where display is claimed.

### Release criteria

- No Khronos sample file is rejected for points or lines, and the behavior and its limits are
  documented and verified.
- The [release gate](README.md#release-gate) is satisfied.
