# Current

Unfinished work only; see the [task lifecycle](README.md#task-lifecycle). Tasks are in
implementation order, which investigation may change. Each feature follows the
[contract for adding a feature](../design/IMPLEMENTATION_POLICY.md#adding-a-gltf-feature).

## v0.17.0 — XMP metadata

- Decide in an ADR how `KHR_xmp_json_ld` packets map to USD: the packet that the asset references
  and the packets of nodes, meshes, materials and other objects the importer authors, for example as
  `customData` or `assetInfo` on the layer and on each object's prim. Cover packets that several
  objects share, objects that become several prims or none, JSON-LD values that USD dictionaries
  cannot hold, packets on objects outside the selected scene, and the archived `KHR_xmp`, which
  stays ignored.
- Import XMP metadata by that decision. Verify against XmpMetadataRoundedCube and ScatteringSkull.

### Release criteria

- No Khronos sample file ignores `KHR_xmp_json_ld`, and the behavior and its limits are documented
  and verified.
- The [release gate](README.md#release-gate) is satisfied.
