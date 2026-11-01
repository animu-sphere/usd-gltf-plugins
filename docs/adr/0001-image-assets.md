# 0001: Image asset paths and lifetime

Decision record, 2026-11-01. The [capability matrix](../reference/CAPABILITY_MATRIX.md)
records current implementation and evidence.

## Context

A file-format plugin produces a layer when a file is opened, but USD consumers
read images through asset paths. glTF images are external files, bufferViews or
data URIs. The last two have no path of their own, and KTX2 images must reach
consumers as standard images. [TEXTURES](../design/TEXTURES.md) requires that
results remain resolvable when an asset moves and that nothing depends on a
temporary file alone.

## Options

- **Extract to a temporary or cache directory.** Authored paths would be
  machine-local, and every open would need a policy for collisions, concurrency,
  staleness and disposal. A saved layer would break on another machine.
- **Extract beside the source.** This writes during `Usd.Stage.Open`, fails in
  read-only locations and leaves files behind as a side effect of reading.
- **Serve images from the source through `ArPackageResolver`.** The source file
  remains the only storage. `usd-vrm-plugins` uses this approach on the same
  OpenUSD runtime. Consumers need the plugin, which they already need to open
  the file.

## Decision

Use the package resolver. Images are never written to disk during import.

| Image | Authored asset path | Served by |
| --- | --- | --- |
| bufferView or data URI | `./<source name>[images/<image index>.<png or jpg>]` | `UsdGltfPackageResolver`, from the source file |
| Relative local URI | `./<decoded URI path>` | The active asset resolver |

Paths are relative, so they are anchored to the layer and resolve after the asset
moves with its images. The entry name uses the image index, like other stable
index-based names. Its extension comes from the image's signature, not its
`mimeType`, because the decoder is chosen from the extension. The resolver is in the
file-format library, since each is useless without the other.

External URIs follow the
[external resource policy](../architecture/IMPORT_PIPELINE.md#external-resource-policy).
Only images used by the selected scene are checked, and a failed check prevents
layer publication. Remote and resolver-managed images are excluded, as for buffers.

### Lifetime, cache and concurrency

- **Storage and disposal:** there is no storage of our own, so there is nothing to
  dispose of or to go stale. The source file owns the bytes.
- **Cache:** the resolver parses the source for each request, unless a caller holds
  an `ArResolverScopedCache`. It then shares the parsed package in that scope, keyed
  by resolved path. This is unmeasured for large assets; a longer-lived cache needs
  measurements and an invalidation rule first.
- **Concurrency:** requests share no mutable state outside that thread-local scope,
  and each `ArAsset` keeps its own package alive.
- **Reopening and reload:** the same request is answered from the file's current
  content. An entry whose index or format no longer matches fails to resolve.

### KTX2

KTX2 follows this contract: `KHR_texture_basisu` images get a `.png` entry, and the
resolver decodes them to PNG on request. This means no decoded image cache is
needed for correctness. Decoder selection and its build check still need their own
ADR, and this path is not implemented.

## Limits

- Only the signature is checked at import. Decoding every image during open would
  be slow, so a damaged image body is reported by the renderer's image loader when
  it is read, and the layer has already been published.
- There is no limit on image dimensions or size of our own.
- A layer exported from an imported stage still points into the source for embedded
  images, and needs the plugin and source. Explicit packaging is planned in the
  [backlog](../roadmap/backlog.md).
- A source inside another package, such as a `.usdz`, is untested.
