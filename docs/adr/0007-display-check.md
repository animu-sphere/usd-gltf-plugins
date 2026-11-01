# 0007: Display check

Decision record, 2026-11-02. The
[capability matrix](../reference/CAPABILITY_MATRIX.md#display-checks) records current
results, and the [README](../../README.md#development) has the command.

## Context

Display had been confirmed by hand: rendering each fixture, editing a copy to reach the
second shader path, and comparing by eye or with one-off measurements on assets that were
then discarded. None of this could be repeated at a release, which the
[release gate](../roadmap/README.md#release-gate) requires.

## Options

- **Reference images, compared within a tolerance.** Simple to write, but images depend on
  GPU and driver, a difference says nothing about what broke, and accepting a new image
  easily accepts a defect.
- **Stated expectations at computed points.** Each comparison states a property the
  documents already claim, such as a quadrant's color or a map equaling its reference. It
  needs more code, and it finds only what is stated.

## Decision

Use stated expectations, in `tests/check_display.py`.

- **Colors are named, not exact:** a channel is on above 170 and off below 70. Lighting can
  then vary without hiding a wrong image.
- **Two routes to the same value are compared:** an image against a factor, a map against
  supplied normals, or KTX2 against PNG. Each comparison that could pass because a result is
  flat has a matching comparison that requires a difference.
- **Points are computed** from a mesh's bounds and the camera, so a change of view or
  layout does not invalidate them. A fixture without a camera gets one in a session layer.
- **The second path is reached with a session layer** that authors empty connections on
  `outputs:mtlx:surface`. The check confirms through `ComputeSurfaceSource` which shader each
  material resolves to, and renders must also show the documented differences between paths.
- **Assets that were discarded are now generated** by the check, each rendered alone.
- **Rendering uses `usdrecord`**, in a separate process because
  [loader crashes are real](0006-image-failures.md#context). It cannot cull backfaces, so the
  reflection fixture calls `UsdImagingGL.Engine` as usdview does, in an offscreen Qt context.
  That context must be Qt's default: a 4.5 core profile raised OpenGL errors from Storm.
- **It is not part of CTest.** It needs a GPU and the lookdev runtime, while CI uses the `usd`
  profile, which has no PySide6. Running it is a release step.

The FlightHelmet sample stays outside the check. It is not in this repository, and it was
only ever judged by eye.

## Evidence that it can fail

Each change below was built into the plugin, and the check run against it:

| Change to the plugin | Failed comparisons |
| --- | --- |
| UV V not flipped | 122 |
| Triangle winding reversed | 21 |
| Preview base color read as raw | 6 |
| MaterialX base color not marked sRGB | 7 |
| Preview roughness read from B | 4 |
| Preview normal Y flipped | 8 |
| Preview normal scale doubled | 14 |
| MaterialX normal scale doubled | 2 |

The last was not found at first. In `normal_textures.gltf`, that view moves the scale 0.5
quad by only 2 between scales 0.5 and 1, which is within its tolerance. A generated quad now
tilts 45 degrees toward the light at the camera, where the same change is 22. The fixture's
row is still compared, but is not evidence for scale.

## Limits

- It finds only what is stated. A defect away from a sample point passes.
- All results are from one machine. The named thresholds and tolerances were chosen there,
  and other GPUs may need different ones. The Engine path needs OpenGL, so the reflection
  check is not expected to run on the macOS Metal runtime.
- It says nothing about renderers other than Storm, or about general shading correctness.
