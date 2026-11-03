# 0012: Cameras

Decision record, 2026-11-03. [Scene conventions](../design/SCENE_CONVENTIONS.md#cameras) specify the
result, and the [capability matrix](../reference/CAPABILITY_MATRIX.md) records current evidence.

## Context

glTF and `UsdGeomCamera` both look along local -Z with +Y up, but differ in what they leave open:

- glTF derives a camera's view from the rotation and translation of its node's world transform. A
  node whose columns are zero, or whose transform without scale is not a rotation, such as a
  reflection, has no view. USD renderers view through the inverse of the prim's whole transform:
  `HdCamera` takes the transform as it is, and so a scaled camera scales its clipping planes and an
  orthographic camera's width. `GfFrustum` removes scale, so it does not show what Hydra draws.
- A perspective camera without `aspectRatio` uses the viewport's, and one without `zfar` has an
  infinite projection. USD authors an aperture of fixed shape, and a finite clipping range: an
  infinite far plane gives a projection of NaN.
- USD expresses a field of view as a focal length and apertures in tenths of a unit, and only their
  ratio sets the projection when no f-stop is authored.

## Decision

### Placement

A camera is a `Camera_<index>` prim below each node that uses it, beside `Mesh_<index>`, as meshes
are duplicated. A camera on its own `Xform` child, or at the node itself, was not considered: a node
prim is an `Xform` and holds a mesh and children, which a `Camera` prim cannot replace.

### Scale

The options were:

- Author the camera with no transform, and accept that a scaled node scales the view. Simple, but
  the Duck sample, whose root is scaled by 0.01, would see its clipping planes at 0.01 to 100 rather
  than 1 to 10000.
- Reject a scaled camera node. That loses every such sample, for a scale glTF defines as ignored.
- Give the camera prim a transform that cancels the node's scale and shear, relative to the asset.

Choose the last. The camera prim's transform is the node's rotation and translation times the
inverse of its world transform, and is not authored when it is identity within 1e-6. A scale applied
above the asset, such as the assembler's [change of units](../design/SCENE_CONVENTIONS.md#coordinate-system-and-units),
still scales the view, which is what a change of units should do.

A node whose transform has no view is rejected rather than given a guessed rotation. glTF's test is
that the determinant of the columns, each divided by its length, is close to 1; the importer requires
it to be within 0.001, the tolerance of a node's rotation. `GfMatrix4d::RemoveScaleShear` then removes
what remains within it.

### Projection

A perspective camera uses `GfCamera`'s own convention, its default horizontal aperture of 20.955 and
the focal length that gives `yfov` vertically, so that `GfCamera` reports the same values. An
orthographic camera's apertures are twice its magnifications, in tenths of a unit.

Without `aspectRatio`, the options were a square aperture, `GfCamera`'s default shape of about 1.37,
or rejecting the camera. A square is chosen: usdview conforms a scene camera to its window by matching
its height, fitting it inside the window or cropping to the camera, and switches to matching width
only in a window narrower than the camera. A square aperture therefore keeps `yfov` in usdview
whenever the window is at least as wide as tall. Any fixed shape loses the glTF meaning in some
window, and a file without `aspectRatio` is valid.

Without `zfar`, the far plane is 1,000,000, the fallback of `clippingRange` itself, rather than a
multiple of `znear`, which would clip a scene that glTF shows. A `znear` at or beyond it is rejected.

A `yfov` of pi or more, which glTF only discourages, is rejected, because no focal length holds it.
A negative magnification, also only discouraged, would need a negative aperture, whose effect no
renderer was seen to define, and is rejected.

## Evidence and limits

`test_cameras` compares, for each camera prim, the inverse of its whole world transform times the
projection of its `GfCamera` with the glTF matrices written from the specification's definitions,
in code that does not use the plugin's formulas. The [sample check](0010-sample-baseline.md) does the
same for every camera of the selected scene of each sample, with the view taken from the file.

Each change below was built into the plugin, and both run against it. The sample column counts the
15 files of the six models whose selected scene has a camera and that open: Cameras, Duck, ToyCar,
MultiUVTest, EnvironmentTest and TransmissionTest.

| Change to the plugin | Failing tests | Sample files that differ |
| --- | --- | --- |
| Scale not cancelled | `test_cameras`, `test_cameras_reference_into_a_scaled_stage` | 4 |
| Focal length from the horizontal aperture | `test_cameras` | 11 |
| Orthographic apertures of one magnification | `test_cameras` | 2 |
| An undefined aspect ratio of 1.5 | `test_cameras`, `test_cameras_reference_into_a_scaled_stage` | 2 |
| A reflected node accepted | `test_camera_failures_preserve_layer` | 0 |

No sample has a camera under a reflected node, so only the fixture finds the last.

Not established:

- No camera was rendered through as a check. Display depends on how a renderer conforms the aperture
  to its viewport, which this importer cannot author.
- Other renderers than Storm were not read for how they treat a scaled camera.
- Animated cameras are not supported, like all animation.
