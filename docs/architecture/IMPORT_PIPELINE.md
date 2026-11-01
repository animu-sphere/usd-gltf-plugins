# Import pipeline

Status: **early implementation**. A file-format registration scaffold exists;
fastgltf is linked as a build dependency and tested separately, but the
importer is not implemented and reads fail explicitly.

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

Investigate direct `SdfLayer` authoring versus authoring through a temporary
`UsdStage` before selecting an approach. Verify layer consistency, error
recovery and that conversion does not create unrelated files. Image assets
may require deliberate persistent storage; their lifecycle and portability
are separate unresolved requirements in [TEXTURES](../design/TEXTURES.md).

Resolve relative external buffers and image URIs against the source asset;
also handle GLB embedded data. Define a safe URI policy. Reject unsupported
required extensions rather than silently presenting an incorrect scene;
define the diagnostic policy for unsupported optional extensions. Malformed
or unsafe data must produce explicit diagnostics and failure rather than a
success result containing an unintentionally incomplete stage.

Scene selection is defined in [SCENE_CONVENTIONS](../design/SCENE_CONVENTIONS.md);
the helper is not yet connected to the file-format importer. See also the
[open decisions](../roadmap/README.md#decisions-to-verify).
