# 0036 — Static node visibility

## Context

[`KHR_node_visibility`](https://github.com/KhronosGroup/glTF/tree/main/extensions/2.0/Khronos/KHR_node_visibility)
defaults to visible. A false value hides the node's drawing and light contribution and those of its
descendants, including descendants with an explicit true value. Cameras remain usable.

[`UsdGeomImageable::ComputeVisibility`](https://openusd.org/release/api/class_usd_geom_imageable.html)
propagates an `invisible` opinion down the USD hierarchy. `UsdGeomVisibilityAPI` adds purpose
visibility and allows descendants to override an ancestor's purpose opinion. Authoring visibility
per mesh or light could leave camera prims with computed `inherited`, but would require editing each
attachment to show the node again.

## Decision

Use fastgltf's boolean parser for the extension. On each retained node with `visible: false`,
author `visibility = "invisible"` on the node's Xform or SkelRoot. The USD hierarchy applies the
same opinion to meshes and lights, including each placement of a shared mesh, and lets a consumer
edit the node as a unit. Keep camera prims active at their existing paths so they can still be used
as render cameras. Their computed imageable visibility still reflects the ancestor's opinion.

## Evidence and limits

The [display check](../../tests/check_display.py) renders through an imported camera below a hidden
node. It checks geometry in that subtree, including a child with `visible: true`, against a
visible control. It also compares the illumination of a visible surface with a directional, point
or spot light inside the subtree, with the parent visible and hidden. Renderer verification covers
Storm with OpenUSD 26.08 on Windows and both material paths.

The [sample check](../../tests/check_samples.py) compares each mesh and light's computed visibility
with the selected glTF scene's hierarchy. Animated visibility for CubeVisibility and LightVisibility
uses the same hierarchy; see [ADR 0037](0037-animated-node-visibility.md).
