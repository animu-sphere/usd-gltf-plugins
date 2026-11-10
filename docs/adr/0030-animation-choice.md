# 0030: Choosing an animation

Decision record, 2026-11-10. It decides how a stage chooses one of an asset's glTF animations, and
where each animation's values and `UsdSkelAnimation` live. It replaces the
[node animation decision](0029-node-animation.md)'s authoring of the first animation only, and keeps
its timeline and interpolation. The [scene conventions](../design/SCENE_CONVENTIONS.md#skeletons-and-animation)
specify the result, the [capability matrix](../reference/CAPABILITY_MATRIX.md) records what is
implemented, and the [roadmap](../roadmap/current.md) holds the conversion.

## Context

A glTF file may have any number of animations, none of them a default. Each may move any nodes,
including the same nodes as another in other properties, and has its own timeline from 0 to its last
keyframe. glTF does not say how a client chooses one, and playing two at once is the client's
blend, not the file's. The importer authors only the first today, with a warning.

A choice must switch node transforms, joints through `skel:animationSource` and, once converted,
morph target weights together, have one deterministic default, and never mix two animations. Two
ways were in the roadmap:

- A variant set `animation` on `/Asset`, with a variant per animation, chosen by a variant
  selection wherever the asset is referenced.
- A layer per animation, which the file format plugin produces from a file format argument such as
  `Fox.glb:SDF_FORMAT_ARGS:animation=1`, chosen by the asset path.

Three Khronos samples have more than one animation: Fox has three over the same joints,
InterpolationTest nine on different nodes, and MorphStressTest three of weights only.

## Experiment

[`tests/probe_animation_choice.py`](../../tests/probe_animation_choice.py) reads glTF itself and
authors each candidate below with a Python prototype: nodes and skeletons as the
[skin placement decision](0028-skin-placement.md) places them, every animation's channels at their
keyframes, and a `UsdSkelAnimation` per animation. A host stage at 30 time codes per second references
the asset with a layer offset of 12.5 and a scale of 0.5, and chooses each animation in the
candidate's way. Every node's local transform, and every joint's through `UsdSkelCache`, is compared
with glTF's pose of that animation alone at its keyframes, before its first and after its last. It
also chooses nothing, switches the choice on the open stage in its session layer, and names a variant
that does not exist. It ran on Fox, InterpolationTest, CesiumMan and a synthetic fixture whose three
animations move one node in different properties, the same joints, and have timelines of 1, 2.5 and
3 seconds, one starting at 0.5. It ran on Windows with OpenUSD 26.08. Morph targets are not converted
yet, so MorphStressTest was not part of it.

| Candidate | Poses against glTF | Stage's end | A choice that does not exist | Fox, bytes of usdc |
| --- | --- | --- | --- | --- |
| Variant set, values inside each variant | Within 1.1e-6 of the largest element, chosen, by default and after switching | The longest animation's | Nodes that any animation moves have no value, so an identity transform, without a diagnostic | 111,660, with every animation |
| Variant set, nodes' own values outside the variants | Off by up to 1.5 on the fixture and 1 on InterpolationTest | The longest animation's | The nodes' own values | 111,660 |
| Layer per animation | Within 1.1e-6, chosen, by default and after switching | Each animation's own | The argument is the plugin's to reject | 85,729 for one, 182,404 for all three |

Values outside the variants fail because an opinion outside a variant is stronger than the variant's,
and USD takes the strongest opinion that has any value: a default there hides the variant's time
samples. On Fox the joints are skeleton joints without prims, and its `UsdSkelAnimation` is inside the
variant, so it passed there too. UsdSkel's validators reported nothing on any candidate, and every
skeleton resolved its `skel:animationSource` to the chosen animation's prim, or to none where the
animation moves none of its joints.

A layer's metadata cannot vary by variant, so the variant set's stage has the longest animation's end
for every choice. A dictionary in the variant's `customData` on `/Asset` held each animation's own
end, and composed through the reference.

Through the current plugin, which does not read the argument yet, `animation=1` reached the layer when
opened, after `Reload`, through a stage, through a reference from a usda layer, through a reference
inside a usdz package, and through `usdcat`. A layer with the argument was a different layer from
the one without. Switching a reference's asset path in a session layer needs that layer to have the
host's `timeCodesPerSecond`: the probe's first run, without it, scaled the offset by the fallback 24.

## Decision

**A variant set.** `/Asset` has a variant set `animation` with a variant `Animation_{index}` for every
glTF animation, in glTF order, including one that moves nothing in the selected scene. The asset
selects `Animation_0`. A referencing layer chooses another by its own selection. An asset without
animations has no variant set.

It was chosen over a layer per animation because a choice is then ordinary USD: usdview and other
tools list and switch the variants of an open stage, one converted layer serves every choice, and
several references to one asset can play different animations without converting it again. A layer per
animation kept each timeline in the layer's metadata, and an unknown choice could be rejected, but a
tool can neither list nor switch the choices, and each one converts the whole file.

**What is inside a variant.**

- A node that any animation moves has its `translate`, `orient` and `scale` operations and their
  order outside the variants, without values. Each variant authors all three: the node's own value as
  the default, and the animation's channels as time samples as the
  [node animation decision](0029-node-animation.md) specifies. A node that no animation moves keeps
  its matrix outside the variants.
- A joint that an animation moves is in that variant's `UsdSkelAnimation` `Animation_{index}` below
  `/Asset/animations`, defined inside the variant, with the joints it moves. Each skeleton with such
  a joint has its `skel:animationSource` inside the variant. A skeleton whose joints the animation
  does not move has none, and is at its rest pose. Morph target weights will go in the same prim.
- A camera or light below a node that any animation moves has its correction for scale inside each
  variant, computed and checked for that animation alone as the node animation decision specifies.
- `customData` on `/Asset` inside the variant has a dictionary `gltf` with `endTimeCode`, the
  animation's last keyframe in time codes, and `name`, the animation's name when it has one.

**Timeline.** The layer's `startTimeCode` is 0 and its `endTimeCode` the last keyframe of any
animation. Each animation's timeline still starts at 0, and after its own end its values hold, as
glTF clamps them.

**Which channels.** Every animation is authored, and validated as the node animation decision
specifies. A channel without a node, or whose node is outside the selected scene, is ignored. No
channel is merged into another animation's variant.

## Consequences

- An invalid animation rejects the file even when it is not the first, which no longer opens with
  only the first.
- Converting an asset converts all its animations: Fox's layer is 30% larger than with one.
- The nodes that animations move get time samples in every variant, and an operation's value exists
  only inside them.
- A player that uses the stage's time range plays a shorter animation for the longest one's length,
  holding its last pose.
- `UsdSkelAnimation` prims and `skel:animationSource` bindings exist only inside variants, so skins
  are converted after this decision.

## Limits

- A selection that names no variant leaves the nodes that any animation moves at an identity
  transform, without a diagnostic, as USD treats any unknown variant.
- An animation's own end is only in `customData`, which players do not read.
- Only variant selections were compared, through UsdSkelCache and transforms, not rendered; skel
  animation was checked at keyframes only, and on one Windows machine.
- Morph target weights are not converted yet, so placing them in the same `UsdSkelAnimation` was not
  tried.
