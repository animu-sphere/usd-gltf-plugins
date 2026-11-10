# 0028: Skin placement

Decision record, 2026-11-10. It makes the placement rules of the
[scene hierarchy decision](0026-scene-hierarchy.md) an algorithm, and replaces that record's inserted
`SkelRoot_{index}`. The [scene conventions](../design/SCENE_CONVENTIONS.md#skeletons-and-animation)
specify the result, the [capability matrix](../reference/CAPABILITY_MATRIX.md) records what is
implemented, and the [roadmap](../roadmap/current.md) holds the conversion of skins itself.

## Context

glTF and UsdSkel describe skinning with different structures:

- glTF designates nodes as a skin's joints. They need a common root, which need not be a joint, and
  other nodes may lie between them. A joint may have any other nodes attached, even a subtree with
  meshes. The transform of the node that holds a skinned mesh must be ignored: a vertex is
  `sum(weight * jointWorld * inverseBindMatrix * position)`. `skin.skeleton` is only a hint.
- UsdSkel has a `Skeleton` prim whose joints are tokens rather than prims, listed with parents before
  children. Bindings mean something only on a `SkelRoot` or its descendants. A skinned gprim's own
  transform has no effect: it is rendered in the skeleton's space. A `SkelAnimation` is valid only
  with translations, rotations and scales all authored, matches a skeleton's joints by token, may
  cover a subset of them, and may drive several skeletons. The documentation does not address nested
  skel roots.

Seven Khronos samples have skins. Fox and SimpleSkin have their skinned node and their joints below
different root nodes. BrainStem animates a node above its joints, and RiggedSimple's rest pose is not
its bind pose. RecursiveSkeletons has 84 skins, most of whose joints hang below other skins'
joints, and one mesh used by several skinned nodes. No sample has a node between joints,
joints listed before their parents, or skins that share joints with different inverse bind matrices,
so two synthetic fixtures add these.

## Experiment

[`tests/probe_skins.py`](../../tests/probe_skins.py) reads glTF itself, authors each candidate below
with a Python prototype, and checks it with UsdSkel's two validators, `UsdSkelTopology`, the skinned
meshes `UsdSkelCache` finds from each skel root, and `UsdSkelBakeSkinning` against glTF's equation,
at the first, the last and two other keyframes of the first animation. With `--render`, Storm renders
the skinned layer and the baked one at the same times, through `usdrecord`. It ran on Windows with
OpenUSD 26.08. Animation is authored only at the checked times, so it says nothing about
interpolation.

| Candidate | Validators | Skinned points against glTF | Storm against baked points |
| --- | --- | --- | --- |
| Adopted, below | Clean on all 9 | Within 1.2e-5 of each mesh's size, except RecursiveSkeletons (see the limits) | No differing pixel |
| A `SkelRoot_{index}` inserted below the scene | Clean, once skeletons whose space is the scene also move into it | The same | No differing pixel |
| A skel root per skin, nested where they overlap | Clean | The same | No differing pixel |
| Skel root around the skinned meshes only | Errors on 8 of 9: an animated skeleton outside the skel root has its `skel:animationSource` binding there | The same | No differing pixel |
| Rest pose as bind transforms | Clean | Off by up to 36 times a mesh's size on 5 of 9 | Up to 34% of covered pixels differ |

A pixel differs when a channel differs by more than 8 of 255. As a control, the baked images at two
of the checked times differ in 43% to 100% of covered pixels in each case. Every placement that keeps
the bind transforms found every skinned mesh and skinned it as glTF does, so the choice between them
is about paths and documented behavior, not about numbers.

## Decision

**Skeletons.** Each skin that a node of the selected scene uses is one `Skeleton_{skinIndex}`.

- Its *space* is the parent of the joints' common root when that root is a joint, otherwise the
  common root itself, or the scene when there is no such node. The skeleton prim is a child of the
  space's prim.
- Its joints are the skin's joints and every node between them and the space, so that no transform
  of the hierarchy has to be folded into another. They are ordered as the scene's nodes are, parents
  first and siblings in glTF order, and each primitive's joint indices are remapped to that order;
  `skel:joints` is not authored.
- A joint's token is its node's path of `Node_{index}` names from the scene, such as
  `Node_0/Node_2/Node_5`, whether or not the nodes above the space are joints. Tokens therefore name
  the same node in every skeleton, and one `SkelAnimation` per glTF animation drives all of them.
- Rest transforms are the nodes' local transforms. A joint's bind transform is the inverse of its
  inverse bind matrix, and a node added between joints has its rest transform in the skeleton's space.
  `geomBindTransform` is the identity. Skinning happens in the skeleton's space, so the skinned point
  is `space * skelJoint * inverse(bind) * position`, which is glTF's equation for any space.
- Skins that share joints are separate skeletons, because they may give a shared joint different
  inverse bind matrices. A skin that several nodes use is one skeleton with several bound meshes, and
  a mesh that nodes with different skins use is bound to each node's skeleton in each copy.

**Joint prims.** A node keeps its `Xform` unless it is one of a skeleton's joints and nothing has to be
below it. A joint keeps its prim when it has a mesh, camera or light, when a skeleton's space is that
node, or when any of its children keeps a prim, including a child that is not a joint at all. A kept
joint has the same local transform, and the same animation, as its joint, both converted from the
same glTF values. A joint is never a prim only because it is animated or named by `skin.skeleton`;
the hint is ignored.

**Skel roots.** For each skin, take the deepest common ancestor of its skeleton prim and of every
skinned mesh bound to it, and drop any such prim that lies below another. Each remaining prim becomes
a `SkelRoot` in place: a node's `Xform` or the scene's prim keeps its path, display name and transform
operations, and only its type changes. No skel root is inserted, so a node's path does not depend on
whether the asset has skins, and skel roots are never nested.

An inserted root worked as well, but moved root nodes, such as Fox's `Scene_0/Node_1` to
`Scene_0/SkelRoot_0/Node_1`, and the skeletons whose space is the scene with them. Nested roots also
worked, but UsdSkel's documentation does not describe them, and only its baking and Storm were tried.
A skeleton must be inside its skel root, because its animation binding means nothing outside one.

**Skinned nodes.** A skinned node keeps its own transform and animation, which still place its other
children. Neither glTF nor UsdSkel applies them to the skinned mesh.

**Rotations.** A rotation is normalized before it is authored, in a `SkelAnimation` as on an `Xform`.
USD makes a matrix of a quaternion without normalizing it, so a quaternion of length `l` also scales
by `l^2`. SimpleSkin's animated rotations are up to 2.3e-4 from unit length, which moved its skinned
points by 3e-4 of the mesh's size before the probe normalized them.

**Validation.** A skin's joints must be nodes of the selected scene and must not repeat, and its
inverse bind matrices must be finite, affine and invertible. Each is an error, not a fallback.

## Consequences

- The scene's prim or a node becomes a `SkelRoot`, a `Boundable` that keeps the transform operations
  of an `Xform`. In Fox, SimpleSkin and RecursiveSkeletons the scene's own prim is the skel root,
  because a root node holds the skinned mesh and another the joints.
- A dropped joint has no path: what referred to it as a prim refers to its skeleton and token.
  The conversion's map of source indices records both.
- `bindTransforms` are in the skeleton's space. UsdSkel's documentation calls them world space; they
  are the joints' world matrices only when the space is at the origin.
- Joint animation is authored twice for a kept joint, in the `SkelAnimation` and on the `Xform`.
- Skeletons are not merged, so assets with many skins over one hierarchy pose each skeleton
  separately. Merging skins that agree on every shared joint is an optimization for a later version.

## Limits

- UsdSkel stores animated scales as `half3[]`, and an animated joint takes its whole local transform
  from the animation, including a scale that never changes. A scale that half precision cannot
  represent moves skinned points: RecursiveSkeletons' static scales of 0.3 put points off by up to
  1.6e-4 of a mesh's size, and replacing them with 0.5, which half represents exactly, brings that to
  5e-7. The probe passes a placement against glTF with those scales rounded to half precision, within
  1e-4; every adopted case agreed within 3.2e-6.
  Translations are `float3[]` and rotations `quatf[]`.
- The probe checks poses at keyframes only. How samplers map to time samples, and the
  interpolation between them, are the roadmap's next decision.
- Only Storm, through `usdrecord` on one Windows machine, was compared, and against UsdSkel's own CPU
  skinning, not a glTF renderer.
- Morph targets, more than one animation and mesh sharing were not part of the experiment.
