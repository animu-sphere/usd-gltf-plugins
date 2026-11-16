# 0037 — Animated node visibility

[`KHR_animation_pointer`](https://github.com/KhronosGroup/glTF/tree/main/extensions/2.0/Khronos/KHR_animation_pointer)
can target any mutable property defined in the glTF asset. We support the node visibility property
used by CubeVisibility and LightVisibility. The vendored fastgltf parser retains the pointer and
whether the node declares its visibility extension; conversion decides what a pointer can animate.

## Decision

Read `/nodes/{index}/extensions/KHR_node_visibility/visible`, where the node index is a canonical,
in-range decimal index and the node has a `KHR_node_visibility` object. Its `visible` field can be
omitted because it defaults to true. A channel must declare `path: "pointer"`, omit `target.node`,
and provide a JSON property pointer. A supported visibility target can occur only once per clip.
Report an unsupported pointer with its clip and channel: skip it when the extension is optional,
or reject the file when it is required. Reject a malformed channel or invalid supported target in
either case. Valid targets outside the selected scene or on omitted, unattached joints are not
sampled.

Read dense float scalar input times and dense unsigned byte scalar outputs, with one output per
input. Times must be finite, nonnegative and strictly increasing. A zero byte is false; any other
byte is true, including when the accessor is marked normalized. Require `STEP` interpolation.
The existing buffer reader handles GLB, data URIs and restricted external buffers and checks bounds.

Author the stated value and the held token samples in the existing `animation` variants at 24 time
codes per second. A stated value outside the variant would override the samples. Instead, every
variant has the stated value for each node whose visibility is animated in any clip. Where a clip
has no channel for that node, it has no samples. If a consumer selects no valid variant, the
visibility of these nodes falls back to USD's `inherited` token. With numeric time, an animated
clip holds its first and last keyframes beyond the sampled interval. With default time it uses the
stated value. Visibility keyframes contribute to the clip and stage end times.

True maps to `inherited`, so it cannot reveal a descendant of an invisible node. False maps to
`invisible` on the same Xform or SkelRoot as [static visibility](0036-node-visibility.md). Cameras
remain active. Visibility does not alter transforms or skeletal animation.

## Evidence

The [file format tests](../../tests/test_file_format.py) check mixed clips, held values, inheritance,
variant switching and failed reloads for glTF and GLB. The [sample check](../../tests/check_samples.py)
compares all visibility keys, values around transitions and affected mesh and light visibility with
the four Khronos files. The [display check](../../tests/check_display.py) compares animated frames
with static controls through a camera under the animated node in Storm, with both material paths.
