# 0010: Sample asset baseline

Decision record, 2026-11-02. The
[capability matrix](../reference/CAPABILITY_MATRIX.md#sample-assets) records current results, and
the [README](../../README.md#development) has the command.

## Context

For v0.1.0, 87 files in the variants of 34 chosen models were opened once by hand. The choice of
models was not recorded, 7 of 24 rejections were read, and 9 assets were judged by eye. None of it
could be repeated, and an opened file said nothing about whether its content was right.

## Options

- **Keep a chosen list of models.** Faster, but the choice favors what is known to work, and has to
  be argued each time a model is added.
- **Reference USD files or images for each asset.** Finds any change, but accepting a new reference
  easily accepts a defect, and images [depend on the GPU and driver](0007-display-check.md#options).
- **Every file, compared with the glTF itself.** Slower, and it finds only what is compared, but the
  expected values do not come from the plugin.

## Decision

Check every `.gltf` and `.glb` on disk, in `tests/check_samples.py`, against values it decodes itself
from the file. The repository's `model-index.json` omits files, so it is not used. The check commits
one line per file to `tests/sample_baseline.json`, and fails if a result differs from it. Accepting
a change is an edit that a pull request shows.

- **Three separate results.** Opened: the stage opens, or the diagnostic. Meaning: USD compared with
  the file. Rendered: Storm draws through each path.
- **A process per file,** because [loader crashes are real](0006-image-failures.md#context), and so a
  crash or a hang is a result for one file.
- **Decoding does not use fastgltf,** so a shared misreading cannot pass. It reads the JSON, GLB
  chunks, buffers and accessors in Python.
- **Meaning is `partial` when the file uses an extension the plugin ignores,** even if everything
  compared agrees, because what the extension changes is not compared. The check also fails a
  file whose ignored extension had no warning. `IMPLEMENTED_EXTENSIONS` in the script must grow
  with each extension.
- **A rejection is examined from the file:** the check lists every unsupported thing in it, and
  whether the diagnostic names one of them. This shows a file's whole list of causes, not only the
  first one the plugin met, so it also shows what a feature would unlock.
- **Rendering only asks whether anything was drawn.** It authors a camera at an angle, framing the
  bounds, in a session layer. The default camera saw a flat plane edge on and clipped a model a few
  millimeters across, which looked like blank renders. It uses `usdrecord`, as
  [the display check does](0007-display-check.md#decision).
- **It is not part of CTest.** The assets are not in this repository, and rendering needs a GPU and
  the lookdev runtime. Without `--render`, a run needs no GPU, and rendered results are not compared.

## Compared for meaning

For the selected scene: stage metrics; prim paths and the mesh count; world transforms at the
default time; each animation's translation, rotation and scale values, chosen through its variant,
at each keyframe, 1e-6 seconds before it and a quarter, half and three quarters into each segment,
against glTF's interpolation, within 1e-5 of their size or 2e-4 for `CUBICSPLINE`, the absence of
time samples on the properties it does not animate, its end in `customData`, and the timeline they
give ([ADR 0029](0029-node-animation.md), [ADR 0030](0030-animation-choice.md)); skeleton paths,
joint order, mesh bindings and UsdSkel's skinned world points at rest and at the start and end of
each animation, compared with glTF's skinning equation within 1e-4 of the mesh's size after rounding
animated joint scales to half precision ([ADR 0028](0028-skin-placement.md)); each camera's
view and projection, as a renderer combines them; each light's position, direction, color,
intensity and cone, through the UsdLux definitions in [ADR 0013](0013-lights.md); points,
triangles (in any rotation that keeps winding), normals including generated ones, UV sets with V
flipped, tangents and vertex colors; bindings and `doubleSided`. For each bound material: which
shader each path resolves to; for each texture in both networks, that it resolves, UV set, its
[transform](0011-texture-transform.md), wrap modes and image bytes (PNG and JPEG sources must be
identical, and KTX2 must be served as an image);
Preview's color space, factors, scales, normal scale and occlusion strength; and alpha mode and
cutoff in both paths. For an [unlit](0015-unlit.md) material, its base color in Preview's emission,
the inputs that zero both shaders' lighting, and that no fallback texture is authored. The
[ior](0016-ior.md) of each material in both paths, or that none is authored at the default. For an
enabled [clearcoat](0017-clearcoat.md), its weight and roughness in Preview, its texture slots in both
networks, and its normal in MaterialX alone; otherwise, that no clearcoat is authored. For a
[transmission](0018-transmission.md) above 0, its value and texture slot in MaterialX, and that Preview
has neither; otherwise, that no transmission is authored. For a [volume](0019-volume.md) of a thickness
above 0 on a transmissive material, its thickness, texture slot and any attenuation in MaterialX, and
that Preview has none; otherwise, that no volume is authored. For a [specular](0020-specular.md) weight
or color other than 1 or with a texture, its value and texture slot in MaterialX, and that Preview has neither;
otherwise, that neither is authored. For a [sheen](0021-sheen.md) color other than black, its color, any
roughness other than 0 and their texture slots in MaterialX, and that Preview has none; otherwise, that
no sheen is authored. For an [iridescent film](0022-iridescence.md) of strength above 0, its
strength and raw R texture, uniform film ior, and thickness from its maximum or a range interpolated
by raw G in MaterialX, and that Preview has none; otherwise, that no film is authored.

## Evidence that it can fail

Each change below was authored in a session layer over a real asset, not built into the plugin, and
each produced a mismatch: a moved point; a reversed triangle; V not flipped; a reversed normal or
tangent; `doubleSided` changed; a moved node; another binding; a missing mesh and an extra one; wrap
modes in each network; base color read as raw and a normal map as sRGB; another image in one or both
networks; a missing image; another UV set; metallic-roughness and normal scales changed; no MaterialX
output; other stage metrics; and MaterialX alpha mode and cutoff and Preview's threshold changed.

For an iridescent film, separate session edits to its strength, film ior, thickness maximum,
thickness minimum and thickness channel each produced a mismatch on a generated textured quad.

On real assets it found a defect that the fixtures had not: a rotation slightly off unit length is
applied without normalizing it.

## Limits

- It finds only what is compared. Not compared: MaterialX factors combined with a texture, Preview
  alpha connections, the vertex color products for Preview, the content of decoded KTX2 images, and
  sampler filters, which are not applied.
- Values agree within 1e-5 of their size. A camera's view is compared within 1e-5 of the camera's
  distance from the origin instead, because the parser reads the node's rotation as floats, which the
  view multiplies by its translation: NodePerformanceTest's camera, about 680 units away, differs by
  1.3e-4 in a value of 8.6.
- `verified` is not a claim of correct appearance, and `drawn` does not judge a pixel. A file can
  be drawn wrongly and still pass.
- The results are from one machine. Another platform may need its own diagnostics or renders
  reviewed before they are compared.
- It depends on one commit of the samples. Moving to another commit is a reviewed baseline change.
