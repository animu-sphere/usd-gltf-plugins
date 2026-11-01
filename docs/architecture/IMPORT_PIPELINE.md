# Import pipeline

Status: **early implementation**. The importer opens selected nodes, transforms
and a limited triangle-mesh subset including UVs with embedded or local external buffers;
other asset content is not implemented.

```text
.gltf / .glb
    -> usdGltfFileFormat (SdfFileFormat)
    -> gltfCore (fastgltf parsing and data access)
    -> gltfUsd (scene, mesh, material and binding translation)
       + gltfTexture (resolved and converted image assets)
    -> SdfLayer containing an ordinary USD scene
```

The initial approach is eager conversion on read, not `SdfAbstractData` lazy
loading. Keep the plugin thin and the translator reusable. The import-only
plugin registration must explicitly declare writing unsupported, using the
metadata supported by the selected OpenUSD version.

The selected approach is to author on a temporary `SdfLayer` and transfer
its content only after successful conversion. See the
[layer authoring decision](LAYER_AUTHORING.md) for experiments and remaining
integration tests. Image assets may require deliberate persistent storage;
their lifecycle and portability are separate unresolved requirements in
[TEXTURES](../design/TEXTURES.md).

Resolve external buffer URIs as described below; image URI handling remains
open. Reject unsupported
required extensions rather than silently presenting an incorrect scene;
define the diagnostic policy for unsupported optional extensions. Malformed
or unsafe data must produce explicit diagnostics and failure rather than a
success result containing an unintentionally incomplete stage.

Scene selection is defined in [SCENE_CONVENTIONS](../design/SCENE_CONVENTIONS.md).
The importer currently supports triangle meshes as described in
[scene conventions](../design/SCENE_CONVENTIONS.md). It rejects unsupported
mesh content, skins, cameras, lights, morph data and animations rather than
silently dropping them. Unsupported optional extensions are reported as
warnings; required extensions are rejected by fastgltf. Images are not yet
loaded or resolved. See also the
[open decisions](../roadmap/README.md#decisions-to-verify).

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

External buffer resolution currently requires a local source and filesystem;
it does not use USD's asset resolver to open the buffer. This deliberately
excludes remote assets, resolver-managed resources and shared buffers in
parent directories. Canonicalization and file opening are separate operations:
the check is not a security sandbox against concurrent changes to the
filesystem. Assets must not be imported from directories writable by an
untrusted concurrent actor. Image URI policy remains a separate decision.
