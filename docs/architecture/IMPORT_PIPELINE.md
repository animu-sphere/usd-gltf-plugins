# Import pipeline

```text
.gltf / .glb
    -> usdGltfFileFormat (SdfFileFormat)
    -> gltfCore (fastgltf parsing and data access)
    -> gltfUsd (scene, mesh, material and binding translation)
       + gltfTexture (resolved and converted image assets)
    -> SdfLayer containing an ordinary USD scene
```

Conversion is eager on read, not `SdfAbstractData` lazy loading. Keep the plugin
thin and the translator reusable. The import-only plugin registration
explicitly declares writing unsupported, using the metadata supported by the
selected OpenUSD version.

Scenes are authored on a temporary `SdfLayer`, and its content is transferred
only after successful conversion. See the
[layer authoring decision](LAYER_AUTHORING.md). Import writes no image files:
see [TEXTURES](../design/TEXTURES.md) for their lifecycle and portability.

## Failure policy

Reject unsupported content rather than silently dropping it or presenting an
incorrect scene. Unsupported required extensions fail during parsing, and the diagnostic names each
of them.

An optional extension takes one of three outcomes, and none of them is silent:

- **Ignore, with a warning naming it,** when the file stays a valid glTF without it. glTF
  defines this as a fallback, so the result is what any client without it would show.
- **Approximate, with a warning stating what is lost,** when a path can express most of the meaning.
  The approximation is specified and tested, and is recorded as a limit in the capability matrix. For
  example, [a rotated normal map's frame](../adr/0011-texture-transform.md#normal-maps).
- **Reject,** when ignoring it would leave out scene content that a client would otherwise show, or
  would present malformed data as if it were valid.

Malformed or unsafe data must produce explicit diagnostics and failure rather
than a success result containing an unintentionally incomplete stage. Scene
selection is defined in
[SCENE_CONVENTIONS](../design/SCENE_CONVENTIONS.md#scene-selection).

## External resource policy

For buffers and images used by the selected scene, `.gltf` and `.glb` imports
accept relative, UTF-8 filesystem paths beneath the source file's directory. URI
percent escapes are decoded once and must be valid; decoded parent-directory
components, absolute paths, backslashes, colons, control characters, schemes,
authorities, queries and fragments are rejected. The source directory and
target are canonicalized, and links whose target escapes the source directory
are rejected. The target must be a regular file. Failed checks prevent layer
publication, and unused resources in other scenes are not read.

An external buffer must be at least as long as its declared `byteLength`. Only
that many bytes are read and cached for the duration of conversion. An external
image must have a PNG or JPEG signature and the matching file extension, because
the image loader chooses a decoder from the extension, and must be no larger than
2 GiB. Every image used by the selected scene has its structure and dimensions
checked without decoding, which rejects natural damage before the layer is
published; see [ADR 0006](../adr/0006-image-failures.md) for what this cannot find.

Resolution requires a local source and filesystem; it does not use USD's asset
resolver to find the resource. This deliberately excludes remote assets,
resolver-managed resources and shared files in parent directories.

A source inside a package, such as `model.glb` in a `.usdz`, reads only its own
bytes: buffers in its GLB binary chunk and data URIs. Import fails when the
selected scene uses any image or external buffer, before any file is resolved,
because OpenUSD does not route paths nested in the package to the package
resolver, and the directory beside the package is not the source's. See
[ADR 0025](../adr/0025-packaged-sources.md).
Canonicalization and file opening are separate operations: the check is not a
security sandbox against concurrent changes to the filesystem. Assets must not
be imported from directories writable by an untrusted concurrent actor.

## Derived USDZ

[The packaging command](../DEVELOPMENT.md#package-a-converted-asset) uses the normal
file format to read a local `.gltf` or `.glb`. It copies the imported layer specs,
including animation variants, to a native USD layer. It resolves each authored
image against the source layer, copies its PNG or JPEG bytes and rewrites the
asset path to point to the copy. This reads KTX2 and WebP through the same
[resolver cache scope](../design/TEXTURES.md#cache-and-asset-lifetime) for the entire
asset. Each resolved image path produces one package entry.

OpenUSD packages the staged `scene.usdc` and its images. Both staging and packaging
take place in a temporary directory beside the destination. A successful package
replaces the destination in one rename. If importing, reading an image or
packaging fails, the existing destination is preserved. The package contains the
selected glTF scene and opens with OpenUSD after the source and its resources are
removed. See [ADR 0032](../adr/0032-derived-usdz.md) for the packaging decision.
