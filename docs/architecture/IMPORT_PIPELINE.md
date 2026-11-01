# Import pipeline

Status: **early implementation**. The importer opens selected nodes, transforms
and a limited embedded triangle-mesh subset; other asset content is not implemented.

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

Resolve relative external buffers and image URIs against the source asset;
also handle GLB embedded data. Define a safe URI policy. Reject unsupported
required extensions rather than silently presenting an incorrect scene;
define the diagnostic policy for unsupported optional extensions. Malformed
or unsafe data must produce explicit diagnostics and failure rather than a
success result containing an unintentionally incomplete stage.

Scene selection is defined in [SCENE_CONVENTIONS](../design/SCENE_CONVENTIONS.md).
The importer currently supports embedded triangle meshes as described in
[scene conventions](../design/SCENE_CONVENTIONS.md). It rejects unsupported
mesh content, skins, cameras, lights, morph data and animations rather than
silently dropping them. Unsupported optional extensions are reported as
warnings; required extensions are rejected by fastgltf. External buffers and
images are not yet loaded or resolved. See also the
[open decisions](../roadmap/README.md#decisions-to-verify).
