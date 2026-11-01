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
[layer authoring decision](LAYER_AUTHORING.md). Image assets may require
deliberate persistent storage; see [TEXTURES](../design/TEXTURES.md) for their
lifecycle and portability requirements.

## Failure policy

Reject unsupported content rather than silently dropping it or presenting an
incorrect scene. Unsupported required extensions fail; fastgltf rejects them
during parsing. Unsupported optional extensions are reported as warnings.
Malformed or unsafe data must produce explicit diagnostics and failure rather
than a success result containing an unintentionally incomplete stage. Scene
selection is defined in
[SCENE_CONVENTIONS](../design/SCENE_CONVENTIONS.md#scene-selection).

## External buffer policy

For buffers used by the selected scene, `.gltf` and `.glb` imports accept
relative, UTF-8 filesystem paths beneath the source file's directory. URI
percent escapes are decoded once and must be valid; decoded parent-directory
components, absolute paths, backslashes, colons, control characters, schemes,
authorities, queries and fragments are rejected. The source directory and
target are canonicalized, and links whose target escapes the source directory
are rejected. The target must be a regular file at least as long as its
declared `byteLength`. Only that many bytes are read and cached for the
duration of conversion; failed reads prevent layer publication. Unused
buffers in other scenes are not loaded.

External buffer resolution requires a local source and filesystem; it does not
use USD's asset resolver to open the buffer. This deliberately excludes remote
assets, resolver-managed resources and shared buffers in parent directories.
Canonicalization and file opening are separate operations: the check is not a
security sandbox against concurrent changes to the filesystem. Assets must not
be imported from directories writable by an untrusted concurrent actor. Image
URI policy is a [separate decision](../roadmap/README.md#decisions-to-verify).
