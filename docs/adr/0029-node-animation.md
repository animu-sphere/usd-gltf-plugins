# 0029: Node animation

Decision record, 2026-11-10. It decides how glTF's animation samplers become USD time samples, and how
an animated node is authored. The [scene conventions](../design/SCENE_CONVENTIONS.md#skeletons-and-animation)
specify the result, the [capability matrix](../reference/CAPABILITY_MATRIX.md) records what is
implemented, and the [roadmap](../roadmap/current.md) holds skins, morph targets and the choice
between animations.

## Context

A glTF animation is a set of channels, each targeting one property of one node, and samplers of
keyframes in seconds. Its timeline starts at 0 and ends at the last keyframe, and a property is
clamped to its first and last keyframes outside them. A sampler interpolates in one of three ways:

- `STEP` holds a keyframe's value until the next.
- `LINEAR` interpolates a translation or scale linearly and a rotation by spherical linear
  interpolation along the shorter path.
- `CUBICSPLINE` evaluates a Hermite spline from each keyframe's value and in and out tangents, scaled
  by the segment's duration. A rotation is normalized after it.

A USD attribute's time samples are interpolated by the stage, linearly unless an application asks
for held interpolation of the whole stage, which an asset cannot request. `GfQuatf` and `GfQuatd`
are interpolated by `GfSlerp`, which also takes the shorter path. USD's splines animate scalar
attributes only, not a `translate`, `orient` or `scale` operation. A layer's time codes are scaled to
the stage's seconds through `timeCodesPerSecond` when a stage references it.

[usd-vrm-plugins](https://github.com/animu-sphere/usd-vrm-plugins) converts a VRM animation by
evaluating every channel at the union of its keyframes at 30 time codes per second. That makes a
`STEP` channel ramp between keyframes, and it approximates `CUBICSPLINE` linearly through its values,
with a warning. The roadmap asks this importer to verify each interpolation.

26 Khronos sample models have animations, with 1206 translation, rotation and scale samplers. Only
InterpolationTest uses `STEP` and `CUBICSPLINE`, so synthetic samplers add uneven keyframe spacing, steep
tangents, rotations of up to 179 degrees in opposite hemispheres, a single keyframe and a timeline
whose first keyframe is not at 0.

## Experiment

[`tests/probe_animation.py`](../../tests/probe_animation.py) authors each candidate below as time
samples of a `translate`, `orient` or `scale` operation, asks USD for the value inside every segment,
at and just before every keyframe and outside the range, and compares it with glTF's equations
evaluated in double precision. Translation and scale errors are relative to the sampler's largest
value; rotation errors are degrees. It ran on Windows with OpenUSD 26.08.

| Candidate | Samples | Synthetic | Time samples per keyframe |
| --- | --- | --- | --- |
| `STEP`, keyframes only, as usd-vrm-plugins | Rotation 45, translation 0.37, scale 1 | Rotation 170 | 1 |
| `STEP`, a hold 0.001 or 1e-5 time codes before each keyframe | The same, just before the keyframes | The same | 1.8 |
| `STEP`, a hold 9e-7 or 1e-7 time codes before each keyframe | Rotation 3e-6, others 0 within double precision | Rotation 8e-6, scale 6e-8 | 1.8 |
| `LINEAR`, keyframes | Rotation 1.3e-5, scale 1e-7, translation 9e-16 | Rotation 6e-6 | 1 |
| `CUBICSPLINE`, values at keyframes only, as usd-vrm-plugins | Rotation 10, scale 0.096, translation 0.036 | Rotation 36, translation 0.36 | 1 |
| `CUBICSPLINE`, 16 equal pieces per segment | Rotation 0.28, scale 0.0028, translation 0.001 | Rotation 0.32, translation 0.0039 | 13 |
| `CUBICSPLINE`, pieces to stay within 1e-4, below | Rotation 0.011, scale 5.4e-5, translation 8.9e-5 | Rotation 0.0091, scale and translation 8.1e-5 | 43 to 91 |

The adopted samples, written as usda and as usdc and referenced from a stage at 30 time codes per
second with a layer offset of 12.5 and a scale of 0.5, gave the same errors. The three operations
composed by USD matched glTF's `T * R * S` of the same values for every animated node, within 9.1e-4
of the largest matrix element, for the reason in the limits.

The hold works because `UsdStage` does not interpolate between two samples less than 1e-6 time codes
apart: it holds the earlier, read from
[OpenUSD's source](https://github.com/PixarAnimationStudios/OpenUSD/blob/v26.05/pxr/usd/usd/interpolators.cpp).
The source read is 26.05's, and 26.08 behaved as it says. That threshold is in the layer's time
codes, after any offset, so the experiment's composed stage saw it too. It is not documented; were it removed, the hold would leave a ramp of 1e-7 time codes.

## Decision

**Timeline.** The layer authors `timeCodesPerSecond` and `framesPerSecond` of 24, USD's own fallback,
a `startTimeCode` of 0 and an `endTimeCode` at the last keyframe of the authored channels. A time
code is a glTF second times 24, so keyframes are generally between whole time codes. A stage that
references the asset at another rate maps its seconds itself. Without an authored channel, none of
this metadata is authored.

**Interpolation.**

- `STEP`: each keyframe, and the previous keyframe's value 1e-7 time codes before it. A keyframe that
  close to the previous one gets no hold.
- `LINEAR`: each keyframe. USD's interpolation is glTF's, including slerp along the shorter path.
- `CUBICSPLINE`: each keyframe, and each segment cut into equal pieces, evaluated by glTF's equation,
  enough that the lines between them stay within 1e-4 of the curve, in a quaternion's components or
  times the sampler's largest value. The spline's second derivative is linear in the segment, so
  its largest norm `m` is at an end, and `ceil(sqrt(m / (8 * 1e-4 * scale)))` pieces bound the error.
  A rotation is normalized after it, as glTF does. A segment is cut into at most 256 pieces, with a
  warning when it needs more; InterpolationTest's need at most 114.

Values before the first keyframe and after the last are USD's, which holds them, as glTF clamps.

**Animated nodes.** A node that a channel animates has `translate`, `orient` and `scale` operations in
that order, which USD composes as glTF's `T * R * S`. Each has the node's own value as its default,
which holds when no animation plays and for queries at the default time, and the channel's values as
time samples. `translate` is a `double3`, `orient` a `quatf` and `scale` a `float3`; glTF's values are
floats, and `quatd` changed no result above by more than float precision. Rotations are normalized.
A node that no channel animates keeps its matrix operation.

**Which channels.** Only the first animation is authored until the choice between animations is
decided, with a warning naming how many there are. Within it, a channel without a node is ignored,
as glTF says, and so is a channel whose node is outside the selected scene, which is not authored.
Channels are not merged across animations.

**Cameras and lights.** A camera or light below an animated node keeps one correction for its node's
scale, computed from the transforms at time 0. It stays right while no scale on the node's path
changes and each rotation that changes is below a similarity, because the node's world transform is
then a rotation of the one at time 0. Otherwise the asset is rejected. A scale or rotation changes
when any of its values, or the node's own, differs from the first by more than the cameras' tolerance
of 0.001, so a constant scale that an exporter writes on every node is accepted.

**Validation.** Each is an error rather than a fallback, for the authored animation's channels:

- a sampler or node index out of range, a path other than translation, rotation, scale or weights, or
  a node and path that two channels target;
- an animated node with a matrix, which glTF forbids;
- input times that are not float scalars, or not finite, nonnegative and strictly increasing;
- an output whose type, component type or count does not match its path and interpolation, values
  that are not finite, or rotations whose squared length is not within 0.001 of 1, or 0.02 for
  bytes, whose quantization alone moves it by up to 0.016;
- a `CUBICSPLINE` sampler with fewer than two keyframes, or one whose rotation passes through zero.

Weights channels on the selected scene are rejected as unsupported until morph targets are converted.

## Consequences

- 31 sample files that were rejected only for having animations can open, with the first animation.
- Every animated node changes from one matrix operation to three, and gets time samples.
- A `CUBICSPLINE` channel has up to about 90 time samples per keyframe where its tangents are
  steep, and few where they are not.
- The default value of an animated operation is the node's own, so a renderer at the default time
  shows the node unanimated, not at time 0.
- A `UsdSkelAnimation` will take its joints' values from the same samples, so skins and morph targets
  follow the same timeline and interpolation.

## Limits

- `GfSlerp` interpolates two quaternions less than about 0.5 degrees apart linearly, without the
  normalization its comment describes, and `GfRotation`, through which an `orient` operation becomes a
  matrix, takes the angle from `acos` of the real part. Together they rotate a node near the identity
  by up to about 0.26 degrees more or less than its value says, read from
  [OpenUSD's source](https://github.com/PixarAnimationStudios/OpenUSD/blob/v26.05/pxr/base/gf/quatd.cpp).
  The samples were off by up to 0.072 degrees through the matrix. `quatd` does not avoid it.
- The hold relies on an undocumented threshold of UsdStage, as above.
- Values were compared through USD's interpolation only, not rendered or compared with a glTF
  renderer, and on one Windows machine.
- The timeline ends at the last keyframe of the authored channels, not of channels outside the
  selected scene, which glTF's timeline also counts.
