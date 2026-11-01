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
incorrect scene. unsupported required extensions fail during parsing. Unsupported optional
extensions are reported as warnings. `KHR_lights_punctual` is parsed to detect
light references on selected nodes, which are rejected; parsing it does not
enable support for the extension.
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
Canonicalization and file opening are separate operations: the check is not a
security sandbox against concurrent changes to the filesystem. Assets must not
be imported from directories writable by an untrusted concurrent actor.
