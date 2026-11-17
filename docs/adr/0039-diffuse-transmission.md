# 0039: Diffuse transmission

Decision record, 2026-11-17. It evaluates an extension against the networks of
[ADR 0018](0018-transmission.md) and [ADR 0019](0019-volume.md), and keeps it unsupported.
[MATERIALS](../design/MATERIALS.md#diffuse-transmission) states the result, and the
[capability matrix](../reference/CAPABILITY_MATRIX.md) records current evidence.

## Context

`KHR_materials_diffuse_transmission` lets light pass diffusely through an infinitely thin dielectric,
as through a leaf or paper, lighting the side away from the light. This record reads the
[specification](https://github.com/KhronosGroup/glTF/tree/603619ef9d242d10d7f3c83d0b768e3487f04aa3/extensions/2.0/Khronos/KHR_materials_diffuse_transmission)
at KhronosGroup/glTF `603619e` (2025-10-13), whose status is Release Candidate, not ratified. Its
amount is `diffuseTransmissionFactor` times the A channel of `diffuseTransmissionTexture`, 0 by
default, and its color `diffuseTransmissionColorFactor` times the RGB of the sRGB-encoded
`diffuseTransmissionColorTexture`, white by default. It changes glTF's dielectric to

```
fresnel_mix(ior,
            base = mix(diffuse_brdf(baseColor), diffuse_btdf(diffuseTransmissionColor), diffuseTransmission),
            layer = specular_brdf(roughness^2))
```

so it takes energy from the diffuse reflection alone, under the same Fresnel reflection, and a metal
transmits nothing. `KHR_materials_transmission` overrides it: the diffuse part above is what that
extension mixes toward its specular transmission. With `KHR_materials_volume`, it is the boundary of a
scattering medium. It must not be used with `KHR_materials_unlit` or
`KHR_materials_pbrSpecularGlossiness`. A client without the extension draws the base alone, as an
opaque diffuse surface.

What the two shaders can express:

- MaterialX `gltf_pbr` (2.0.1 in 1.39.5, the version OpenUSD 26.08 builds, and on MaterialX's main
  branch on the day of this record) has no input for it. Its graph builds the diffuse term from an
  `oren_nayar_diffuse_bsdf` of `base_color` inside the node, where no input can replace it.
- MaterialX's `translucent_bsdf` is a diffuse transmission. Storm's GLSL for it flips the normal, so a
  light behind the surface lights the front, and it adds the irradiance from behind. Only a graph of
  its own could use it.
- MaterialX 1.39.5 can mix two surface shaders but not add them. A mix of `gltf_pbr` with a translucent
  surface scales the reflection, the metal and the clearcoat by the same weight, unlike glTF. Scaling
  `base_color` instead also scales the metal's F0 and the tint of `transmission`, which read the same
  input.
- UsdPreviewSurface has no transmission of any kind.

## Options

- **Keep the extension unsupported:** an optional diffuse transmission warns and is ignored, and a
  required one fails. Both paths draw the opaque base, as glTF's fallback does.
- **Copy `gltf_pbr`'s graph into an authored `NodeGraph`,** with its diffuse term mixed toward a
  `translucent_bsdf`. That has glTF's structure, but it forks MaterialX's implementation at one
  version, every other extension's inputs would have to be connected to the copy, and Storm would no
  longer recognize a `gltf_pbr` terminal, so its alpha and transmission material tags would come from a
  generic test.
- **Mix `gltf_pbr` with a translucent surface,** or scale its base color. Each changes reflections
  that glTF leaves alone, as above.
- **Author another surface shader for these materials,** such as `open_pbr_surface`, whose thin-walled
  subsurface is a `translucent_bsdf`. Its specular, coat and sheen are not glTF's, so the material would
  lose the mapping of every other extension.

## Decision

Keep `KHR_materials_diffuse_transmission` unsupported, through the importer's handling of every
unknown extension:

- Optional: the file imports with the warning `glTF optional extension
  'KHR_materials_diffuse_transmission' is not supported`, and each material is authored as without the
  extension, in both paths. Its textures are not resolved, so a missing or damaged image there does
  not fail the import. Its values are not checked.
- Required: the import fails with `unsupported required extensions:
  KHR_materials_diffuse_transmission`.

Revisit this when MaterialX `gltf_pbr` gains a diffuse transmission input, or when the specification
is ratified with a change that bears on these shaders.

## Evidence and limits

- `tests/test_file_format.py::test_diffuse_transmission_is_unsupported` asserts the warning, that a
  material with the extension authors the same networks as one without it, that its image is not
  resolved, and the failure when required, in glTF and GLB.
- The [sample check](../reference/CAPABILITY_MATRIX.md#sample-assets) records the extension as ignored
  in each sample that uses it.
- A leaf, paper or wax material draws opaque in both paths: nothing lights it from behind.
- `KHR_materials_volume` on such a material is not authored either, because
  [ADR 0019](0019-volume.md) applies the volume only behind `KHR_materials_transmission`.
