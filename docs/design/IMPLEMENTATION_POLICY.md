# Implementation policy

## Purpose and principles

Import glTF 2.0 and GLB as ordinary OpenUSD scenes so standard USD tools and
Hydra applications can consume the result. The primary target is opening a
file through `Usd.Stage.Open`, without a dedicated renderer.

1. Develop as an independent OpenStrata Project repository.
2. Use fastgltf for parsing and C++20 as the default implementation language,
   subject to the OpenUSD toolchain and ABI investigation.
3. Use existing USD schemas such as UsdGeom, UsdShade, UsdSkel, and UsdLux;
   do not introduce a glTF-specific typed or API schema.
4. Do not introduce a dedicated Hydra or imaging plugin.
5. Use usd-vrm-plugins as a structural and material implementation reference,
   without a code, library, or build dependency or forced common library.
6. Prioritize import; USD-to-glTF export is a later phase.
7. Use `/Root`, `/Root/Scenes`, and `/Root/Materials` as the basic scene layout.
8. Provide both MaterialX and UsdPreviewSurface shading paths in UsdShade.
9. Provide a standard-image conversion path for KTX2 and WebP rather than
   requiring imaging that reads them from consumers.

## Quality principles

- Do not silently drop important information to make a load succeed. Reject
  unsupported content with a diagnostic, or use an explicit fallback that is
  specified and tested.
- A support claim requires implementation, tests and a stated environment.
- Opening a stage is not evidence of correctness. A rejection with a fitting
  diagnostic is a valid test result.
- Do not promise releases by date.

## Non-goals

Full support for all glTF extensions and lossless round-tripping are not goals
of the initial versions.

The component boundaries are defined in [WORKSPACE](../architecture/WORKSPACE.md);
the [import pipeline](../architecture/IMPORT_PIPELINE.md) owns the entry point
and failure policy. Scene, material, and texture details belong to
[SCENE_CONVENTIONS](SCENE_CONVENTIONS.md), [MATERIALS](MATERIALS.md), and
[TEXTURES](TEXTURES.md), respectively.

## Adding a glTF feature

One pull request covers one contract or feature, and closes it with its tests and
documents. It states:

- **Input:** the extension, component types, URI, bufferView and data URI sources,
  and boundary values.
- **Representation:** the USD prims, properties, primvars or node graphs, and what
  meaning they cannot keep.
- **Compatibility:** behavior when required, when optional and when unsupported,
  with its diagnostics.
- **Failure:** no partial layer is published, and a failed reload preserves the
  existing stage.
- **Verification:** valid fixtures, failures, USD structure and meaning, and a display
  comparison where display compatibility is claimed.
- **Documents:** the capability matrix row and evidence, the
  [sample baseline](../adr/0010-sample-baseline.md) if its results change, an ADR or design
  change only if the design changed, and removal of the task from the roadmap.

Each material extension is its own unit: evaluate what MaterialX and UsdPreviewSurface
can express first. A result that looks close is not a claim of equivalence with glTF.

## Quality and dependencies

- Use CMake, integrated with the current OpenStrata workspace conventions.
  Verify the current schema before creating configuration. CMake presets do not
  replace OpenStrata and must not pin environment-specific absolute paths.
- Make versions of fastgltf, the KTX decoder/transcoder, and test dependencies
  pinnable using a method consistent with OpenStrata, with their licenses and
  notices stated. A default configure does not use the network.
- Target Linux, Windows, and macOS; determine the CI matrix, supported OpenUSD
  versions, compilers, runtime and ABI compatibility through validation.
- Cover accessor and buffer bounds, naming, UVs, color spaces and texture
  conversion with unit tests; cover stage opening, prim paths, bindings and
  KTX2 with integration tests; verify rendering in Hydra/viewers separately.
- Check redistribution licenses before using official glTF samples or other
  test assets. Malformed GLB, out-of-range accessors, oversized inputs,
  unsafe external URIs and corrupt KTX2 must fail safely with diagnostics.
- Separate tests of structure (prims, attributes, bindings), meaning (values,
  interpolation, UVs, color spaces) and display (a renderer and GPU).
- Path and image checks are not a sandbox, and
  [an image can still crash the loader](../adr/0006-image-failures.md#limits).
  Where assets come from unknown sources, isolate import in a separate process.
- Keep conversion serial until reproducible evidence shows that it can be parallel,
  separating CPU parsing from USD authoring. Add lazy loading or a cache only for a
  measured problem.

## External references

- [glTF 2.0 specification](https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html)
- [fastgltf](https://github.com/spnda/fastgltf)
- [OpenUSD file format plugins](https://openusd.org/release/api/_sdf__page__file_format_plugin.html)
- [Basis Universal](https://github.com/BinomialLLC/basis_universal)
- [KHR_texture_basisu](https://github.com/KhronosGroup/glTF/tree/main/extensions/2.0/Khronos/KHR_texture_basisu)
- [EXT_texture_webp](https://github.com/KhronosGroup/glTF/tree/main/extensions/2.0/Vendor/EXT_texture_webp)
- [libwebp](https://github.com/webmproject/libwebp)
- [usd-vrm-plugins](https://github.com/animu-sphere/usd-vrm-plugins)
