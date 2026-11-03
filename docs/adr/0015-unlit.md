# 0015: Unlit materials

Decision record, 2026-11-03. It extends the networks of the
[factor-only decision](../architecture/MATERIAL_FACTORS.md) and
[ADR 0002](0002-base-color-texture-networks.md). [MATERIALS](../design/MATERIALS.md#unlit-materials)
specifies the result, and the [capability matrix](../reference/CAPABILITY_MATRIX.md) records current
evidence.

## Context

`KHR_materials_unlit` adds no properties. It marks a material as constantly shaded: its color is the
product of `baseColorFactor`, `baseColorTexture` and the vertex color, independent of lighting.
Alpha coverage and `doubleSided` still apply. Every other core property is a fallback for clients
without the extension, and glTF asks clients that can render PBR not to upgrade an unlit material to
it. A file that does not require the extension is still a valid lit material without it, so ignoring
it would be a [defined fallback](../architecture/IMPORT_PIPELINE.md#failure-policy), but not what a
client with the extension shows.

Neither shader has an unlit mode:

- MaterialX `gltf_pbr` scales both its dielectric F0 and F90 by `specular`, so with `specular`,
  `metallic` and `base_color` at 0 it reflects nothing and shows only `emissive`.
- UsdPreviewSurface, read in Storm's `previewSurface.glslfx` for OpenUSD 26.08, computes a metal's
  F0 and F90 from `diffuseColor` and scales its diffuse term by `1 - metallic`. A black metal therefore
  reflects nothing from a light, at any angle. In the specular workflow F90 is always 1, so a
  `specularColor` of 0 still reflects at grazing angles. A dome light's reflection adds a term that
  does not depend on F0 or F90, so no input removes it.

## Options

- **Ignore the extension, with a warning,** which shows the fallback: lit, and often rough white.
- **MaterialX `surface_unlit`,** which states the meaning, but has no alpha mode or cutoff, so MASK
  needs its own nodes. A material's MaterialX surface would then be one of two shaders.
- **Emit the base color from the existing shaders,** with their lit parts at zero. Alpha, its modes
  and the base color graph stay as they are, and only the input they connect to changes.

## Decision

Emit the base color from the existing shaders:

| Input | `preview` (UsdPreviewSurface) | `mtlx` (`gltf_pbr`) |
| --- | --- | --- |
| Base color term, as [ADR 0002](0002-base-color-texture-networks.md) builds it | `emissiveColor` | `emissive` |
| Lit color | `diffuseColor` 0 | `base_color` 0 |
| Reflection | `metallic` 1, `roughness` 1 | `metallic` 0, `specular` 0 |
| Alpha, its mode and cutoff | Unchanged | Unchanged |

The fallback metallic-roughness, normal, occlusion and emissive properties author nothing, and their
textures are not resolved, so a damaged fallback image does not fail the import. Their values are still
checked, and a texture's UV set must still exist on the primitive, because both are rules of glTF
itself rather than of the extension. A value of the extension that is not an object fails, with a
diagnostic naming it. glTF leaves an unlit material that also uses another shading extension
undefined; those extensions are ignored with a warning, as before.

## Evidence and limits

- Tests assert both networks for each alpha mode, textures, vertex colors, the fallback properties and
  failures. The sample check compares the unlit inputs of each unlit sample material.
- The [display check](../reference/CAPABILITY_MATRIX.md#display-checks) renders an unlit quad in
  darkness, under a light, with fallback properties that would change a lit quad, and with an image.
- Through Storm's UsdPreviewSurface, a dome light adds a reflection to an unlit material, read from its
  source and not displayed. Other renderers may give a black metal a white F90, and so a reflection at
  grazing angles, through UsdPreviewSurface.
- The result is emission, so a renderer's exposure and tone mapping apply to it as to any other
  emission. A consumer that reads `diffuseColor` or `base_color` alone sees black.
