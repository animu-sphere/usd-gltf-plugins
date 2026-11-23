# 0045: XMP metadata

Decision record, 2026-11-23. It extends the [scene conventions](../design/SCENE_CONVENTIONS.md#xmp-metadata).
The [capability matrix](../reference/CAPABILITY_MATRIX.md) records current evidence.

## Context

[`KHR_xmp_json_ld`](https://github.com/KhronosGroup/glTF/tree/main/extensions/2.0/Khronos/KHR_xmp_json_ld)
is ratified and supersedes the archived `KHR_xmp`. The glTF root's extension holds `packets`, a nonempty
array of XMP packets in JSON-LD (ISO 16684-3), each with a `@context` that maps prefixes to namespace
URIs. An `asset`, `scene`, `node`, `mesh`, `material`, `image` or `animation` references one packet by
index, and several objects may reference the same one. The packet of an object has precedence over
the asset's packet. Metadata has no normative effect on appearance, and a viewer may choose what to
show. Arrays must be under `@list` (ordered) or `@set` (unordered), a language alternative is an
`rdf:Alt` object whose `rdf:_1`, `rdf:_2` and so on hold `@language` and `@value`, and booleans and
numbers use JSON's types. The specification restricts JSON-LD further, for example forbidding
expanded term definitions, and gives no guidance on converting to another format.

The importer ignored the extension with a warning, so XmpMetadataRoundedCube, which has packets on
its asset and its mesh, and ScatteringSkull, which has one on its asset, lost their titles, creators
and dates. ScatteringSkull's packet uses an `xmp:` prefix that its `@context` does not declare.

What USD offers:

- `assetInfo` is the metadata that USD defines for annotating the root prims of assets with data
  for asset management, such as an asset's name, identifier and version. It composes like
  `customData`: dictionaries merge key by key across opinions. `UsdMediaAssetPreviewsAPI`
  ([documentation](https://openusd.org/release/user_guides/schemas/usdMedia/AssetPreviewsAPI.html))
  keeps thumbnails in `assetInfo["previews"]` of any prim it is applied to, and reads a stage's own
  from the stage's `defaultPrim`. `customData` is general data for tools, which this importer already uses for
  `gltf:` bookkeeping such as an animation's `endTimeCode`.
- Layer metadata such as `customLayerData` does not compose through a reference, so a stage that
  references the asset would not see it on the referencing prim.
- A `VtDictionary` holds strings, booleans, 64-bit integers, doubles, typed arrays of those, and
  nested dictionaries, all of which `usda` and `usdc` write. It cannot hold JSON's `null` or an
  array whose elements are objects, arrays or mixed types: `VtArray<VtDictionary>` is not an Sdf value
  type, and `std::vector<VtValue>` is not written by either format.
- fastgltf 1.0.1 does not read the extension, and rejects a required extension that it does not know.

usd-vrm-plugins keeps VRM's `meta` object as JSON text in `customData`. That keeps every value, but a
consumer has to parse JSON to read a title.

## Options

- **Keep ignoring it,** with a warning, which loses each asset's rights and authorship.
- **JSON text,** each packet as one string, as usd-vrm-plugins keeps VRM metadata. Lossless, but
  unreadable with USD's own dictionary access.
- **`customData`** dictionaries: readable, but beside the importer's own bookkeeping and outside the
  metadata that USD reserves for asset identification.
- **`assetInfo`** dictionaries on each prim made from an object, and the asset's on the default prim,
  where `UsdMediaAssetPreviewsAPI` reads a stage's asset-wide previews.

## Decision

Each packet is authored as the dictionary `assetInfo["xmp"]` of every prim made from an object that
references it:

| glTF object | Prim |
| --- | --- |
| `asset` | `/Root`, the default prim |
| The selected `scene` | `Scene_{index}` |
| `node` | Its `Xform` or `SkelRoot` |
| `mesh` | Each placement's `Mesh_{index}`, including an instancer's prototype |
| `material` | Every `Material_{index}` and `Material_{index}_*` made from it |
| `image` | Each texture node that reads it, in both networks: `UsdUVTexture` and `ND_image_*` |
| `animation` | None: `customData["gltf"]["xmp"]` on `/Root` inside its `Animation_{index}` variant |

An animation has no prim of its own, and `/Root`'s `assetInfo` is the asset's, so its packet joins the
`customData` that each variant already holds. A packet that several objects reference is authored on
each of their prims. A packet is the asset's when it is on `/Root`, and describes only its own object
elsewhere: a node's packet does not apply to the mesh below it.

Values keep their JSON-LD form, so a key is the compact IRI, such as `"dc:title"`, a language
alternative stays an `rdf:Alt` dictionary, and `@list` and `@set` stay keys:

| JSON | USD |
| --- | --- |
| Object | `dictionary`, the same keys |
| String | `string` |
| `true`, `false` | `bool` |
| Integer within 64 bits | `int64` |
| Other number | `double` |
| Array of strings, of booleans or of integers | `string[]`, `bool[]` or `int64[]`; an empty array is `string[]` |
| Array of numbers, some not integers | `double[]` |
| `null`, or any other array | `{"@type": "@json", "@value": <minified JSON text>}`, with a warning naming the key's path |

The last row is JSON-LD's own form of a JSON literal, so a consumer can tell it from a string. A
compact IRI's key contains `:`, so USD's `GetAssetInfoByKey` key paths do not reach inside a packet;
the dictionary is read whole, as `GetAssetInfoByKey("xmp")`. Prefixes, including undeclared ones such
as ScatteringSkull's, are not expanded or checked, nor are the specification's other restrictions on
JSON-LD.

What is not authored:

- A packet that no object references, which describes nothing.
- Packets of objects that the importer does not author: scenes other than the selected one, nodes,
  meshes and materials that the selected scene does not use, and images that no authored texture
  node reads. They have no prims.
- The packet of a node of the selected scene that is only a skeleton joint, without a prim, with a
  warning.
- Metadata embedded in an image file, which the specification lets a client ignore.
- References from other objects, such as textures, which the specification does not define.

The archived `KHR_xmp` stays ignored with a warning, and fails when required, as before.

Compatibility and failure:

- Optional, the extension is read as above. Required, the import fails naming it, because fastgltf
  rejects required extensions that it does not know; metadata has no reason to be required.
- The parser reads the extension from the JSON, beside fastgltf, and checks it in every file that has
  it: it must be declared in `extensionsUsed`, the root's `packets` must be a nonempty array of objects
  each with a `@context` object of strings, and each reference must be an object with a `packet` index
  within the array, on any object of those seven kinds. Any of these failing fails the import without
  publishing a layer, as other malformed extensions do.

## Evidence and limits

- Tests assert each kind of object's prim, shared packets, both placements of a shared mesh, every
  value type and each literal's warning, export and reopening through `usda` and `usdc`, an animation's
  variant, a joint without a prim, each failure with the layer preserved on reload, the required case,
  and the archived extension. A gltfCore unit test checks the reader without USD.
- The [sample check](../reference/CAPABILITY_MATRIX.md#sample-assets) compares every packet with its
  prims in each sample file, and that no other prim has one.
- Metadata is not drawn, so there is no display check.
- A consumer that resolves precedence walks from a prim to `/Root`, and has to skip the packets of
  ancestors, which describe other objects. A tool that merges dictionaries, as composition does,
  merges two packets key by key, which XMP does not define.
- XMP's own types (dates, URIs and so on) stay strings, as JSON-LD has them.
