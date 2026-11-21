# 0041: Morph target tangents

Decision record, 2026-11-19. It extends the [morph target conventions](../design/SCENE_CONVENTIONS.md#morph-targets)
and the [tangent frame decision](0004-normal-textures-and-tangents.md#tangents). The
[capability matrix](../reference/CAPABILITY_MATRIX.md) records current evidence.

## Context

A glTF [morph target](https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html#morph-targets) can
displace TANGENT as well as POSITION and NORMAL. The displacement is VEC3 float, or with
`KHR_mesh_quantization` normalized signed byte or short: it moves the tangent's XYZ, and W, the
bitangent sign, stays as the base attribute gives it. The specification says a client should support
morphing all three. The importer rejected TANGENT deltas, which was the only reason both
AnimatedMorphCube sample files failed.

What USD offers and what the importer already does with a frame:

- `UsdSkelBlendShape` has `offsets` and `normalOffsets`, and its in-betweens have the same. It has
  no tangent offsets, and UsdSkel deforms points and normals only, not primvars.
- The supplied frame is retained as `gltf_tangent`, but neither shading network reads it: both use
  the frame the renderer derives ([ADR 0004](0004-normal-textures-and-tangents.md#tangents)). A
  morphed tangent would change nothing either network draws.
- The generated `gltf_generatedTangent` already describes the base mesh, and following a
  deformation is the consumer's responsibility.

## Options

- **Keep rejecting:** two sample files fail for data that no network reads.
- **Ignore the deltas with a warning:** the files open, but a consumer of `gltf_tangent` loses
  what it would need to follow the morph.
- **Bake morphed tangents into time samples of `gltf_tangent`:** this evaluates the morph outside
  UsdSkel, for one animation choice, and goes stale when anything downstream changes the weights.
- **Keep the deltas on each blend shape,** beside the offsets that UsdSkel applies.

## Decision

Keep the deltas. A target's TANGENT is authored on its `BlendShape_{targetIndex}` as the custom
`vector3f[]` attribute `gltf:tangentOffsets`, one value per point in glTF order, like `offsets`. It
is read like the other deltas, including sparse accessors, in the formats above. A TANGENT delta on a
primitive without TANGENT fails with a diagnostic, as a NORMAL delta does without NORMAL. A target
with only a TANGENT delta still gets a blend shape, with zero `offsets`, so the targets keep their
indices.

UsdSkel does not apply these offsets, so `gltf_tangent` stays the base frame, and no warning is given
because no network reads it. A consumer that needs the morphed frame adds the weighted offsets to the
base XYZ, keeps W, and normalizes the result. UsdSkel skinning does not transform `gltf_tangent`
either; that was already so.

## Evidence and limits

- Tests author float, byte and short deltas, including a target with only TANGENT, and check that
  `gltf_tangent` is unchanged. A delta without a base TANGENT, an unsigned or unnormalized integer, a
  VEC4 delta and a wrong count fail and keep the existing layer. Sparse forms read like dense data.
- The [sample check](../reference/CAPABILITY_MATRIX.md#sample-assets) opens both AnimatedMorphCube
  files, compares every tangent delta with the file, and draws them through both shader paths. Their
  material has no normal texture, so no frame is drawn, and no display comparison is claimed.
