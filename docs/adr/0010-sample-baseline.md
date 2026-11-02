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

For the selected scene: stage metrics; prim paths and the mesh count; world transforms; points,
triangles (in any rotation that keeps winding), normals including generated ones, UV sets with V
flipped, tangents and vertex colors; bindings and `doubleSided`. For each bound material: which
shader each path resolves to; for each texture in both networks, that it resolves, UV set, its
[transform](0011-texture-transform.md), wrap modes and image bytes (PNG and JPEG sources must be
identical, and KTX2 must be served as an image);
Preview's color space, factors, scales, normal scale and occlusion strength; and alpha mode and
cutoff in both paths.

## Evidence that it can fail

Each change below was authored in a session layer over a real asset, not built into the plugin, and
each produced a mismatch: a moved point; a reversed triangle; V not flipped; a reversed normal or
tangent; `doubleSided` changed; a moved node; another binding; a missing mesh and an extra one; wrap
modes in each network; base color read as raw and a normal map as sRGB; another image in one or both
networks; a missing image; another UV set; metallic-roughness and normal scales changed; no MaterialX
output; other stage metrics; and MaterialX alpha mode and cutoff and Preview's threshold changed.

On real assets it found a defect that the fixtures had not: a rotation slightly off unit length is
applied without normalizing it.

## Limits

- It finds only what is compared. Not compared: MaterialX factors combined with a texture, Preview
  alpha connections, the vertex color products for Preview, the content of decoded KTX2 images, and
  sampler filters, which are not applied.
- `verified` is not a claim of correct appearance, and `drawn` does not judge a pixel. A file can
  be drawn wrongly and still pass.
- The results are from one machine. Another platform may need its own diagnostics or renders
  reviewed before they are compared.
- It depends on one commit of the samples. Moving to another commit is a reviewed baseline change.
