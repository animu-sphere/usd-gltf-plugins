# 0026: Scene hierarchy

Decision record, 2026-11-09. The [scene conventions](../design/SCENE_CONVENTIONS.md#structure-and-names)
specify the result, the [capability matrix](../reference/CAPABILITY_MATRIX.md) records what is
implemented, and the [roadmap](../roadmap/current.md) holds the decisions this record leaves open.

## Context

The selected scene's root nodes were children of `/Asset/geo`, a `Scope` beside `/Asset/mtl`. Skins,
animation and mesh sharing each add prims that are not a node's geometry: skeletons, skel roots,
animation prims and perhaps shared mesh definitions. The scene conventions left `/Asset/rig` and
`/Asset/anim` undecided until UsdSkel and animation were designed. The name `geo` also says that
everything below it is geometry, while nodes already carry cameras and lights.

UsdSkel skins geometry only below a `SkelRoot`, so where a skel root goes depends on where the
skinned meshes and their skeleton are in the node hierarchy. A glTF skin names its joints and its
meshes' nodes independently, and several meshes and skins can share joints.

## Options

- Keep `/Asset/geo`, with `/Asset/rig` and `/Asset/anim` beside it. A skinned mesh must be below its
  skel root, so either skinned meshes leave their nodes, losing the nodes' identity and transforms, or
  one skel root wraps all of `geo`.
- Put root nodes directly below `/Asset`. Nodes would mix with `mtl` and generated scopes, and the
  scene itself, its name and a later choice between scenes would have no prim.
- Put the scene below `/Asset/scenes`, with skel roots inside the node hierarchy. This is adopted.

## Decision

The asset root `/Asset` stays the default prim. The selected scene is an `Xform` named
`Scene_{sceneIndex}` below the `Scope` `/Asset/scenes`, with the scene's name as its display name and
no transform, so world transforms are those of glTF. Root nodes are its children. Only the selected
scene is authored, chosen as the [scene selection decision](../architecture/SCENE_SELECTION.md)
describes; importing several scenes, and how a user would choose between them, is a separate decision,
and scenes are never shown together. `/Asset/mtl` is unchanged.

Prims are named after source indices with one prefix per kind, as the scene conventions list. Each
prefix names one kind and each index occurs once below a parent, so names cannot collide and need no
suffixes. A generated prim with no source index, such as a skel root, is numbered in the order that
conversion meets it. Once joints are converted, a source node can have more than one representation,
or a joint instead of a prim, and an inserted skel root moves the nodes below it. The conversion
therefore keeps an explicit map from source scene, node, mesh and skin indices to USD paths and joints,
rather than deriving a node's path from its glTF parents.

For skins and animation:

- A skin becomes a `UsdSkelSkeleton` named `Skeleton_{skinIndex}`, through the standard UsdSkel
  schemas. Joint nodes are normally represented by the skeleton's joints rather than also as `Xform`
  prims. A joint that has children other than joints, is animated on its own or is referenced by
  something else keeps its observable transform and relationships: these cases are not dropped to
  avoid duplication.
- A `UsdSkelRoot` is inside the scene's hierarchy, not in a collection such as `/Asset/rig`. An
  existing node becomes the skel root only if that keeps its transform, its identity and the ancestry
  and bindings that UsdSkel needs. Otherwise a `SkelRoot_{index}` is inserted at a common ancestor of
  the skeleton and its skinned meshes, without changing any evaluated transform. A skin and a skel root
  are not one to one: shared skeletons and several skinned meshes may need one root. Names are neutral;
  a category such as `Character` is never assumed.
- Node translation, rotation and scale are animated by time samples on the node's own transform
  operations. Joints are animated by a `UsdSkelAnimation` bound through `skel:animationSource`, and
  morph targets by blend shapes. A `Scope` `/Asset/animations` exists only when needed, holding
  `Animation_{animationIndex}` where a `UsdSkelAnimation` applies. A `UsdSkelAnimation` is not a glTF
  animation: a glTF animation can also move nodes and morph weights, so selecting one must switch all
  three consistently.

Meshes stay expanded below each node that uses them. Converting a mesh is kept separate from placing
it below a node, so that a shared definition can later replace the copies. There is no visible
`/Asset/meshes` library: its prims would render a second time, and `visibility`, `active` or `purpose`
opinions set on them to hide them would be inherited through references. References and native
instancing are different features, and instancing waits for a review of per-instance material
bindings, skins, blend shapes, editing restrictions and renderer support.

## Consequences

This is a breaking change of prim paths. A layer that references the asset's default prim still
composes, but a path into the asset changes: `/Asset/geo/Node_3` becomes
`/Asset/scenes/Scene_0/Node_3` when scene 0 is selected. The path includes the selected scene's
index, which is that of the declared default scene, or 0 as a fallback. Overrides, collections and
relationships that target the old paths must be updated.

The scene prim and `scenes` add no transform, so the world transforms that cameras and lights cancel
are unchanged. The placement rules for skins are constraints, not an algorithm: the roadmap holds the
placement algorithm, joints with other children, interpolation fidelity, the selection between
animations, multiple scenes and mesh sharing, each to be decided after experiments with UsdSkel
validation and renderers.
