# Texture and image design

- Treat PNG and JPEG as image assets accessible to USD consumers.
- For `KHR_texture_basisu`, provide a standard path from KTX2 through decoded
  RGBA to PNG, then to texture nodes. See [ADR 0005](../adr/0005-ktx2-decoder.md).
- For `EXT_texture_webp`, provide the same path from WebP to PNG. See
  [ADR 0014](../adr/0014-webp-decoder.md).
- Avoid unnecessary degradation and incorrect color-space interpretation.
  Color and non-color inputs must be handled distinctly as described in
  [MATERIALS](MATERIALS.md).
- Resolve embedded images and external URI images through a unified API,
  accommodating bufferViews, data URIs and external assets. `gltfTexture` owns
  this API and does not build UsdShade graphs.
- A future optional mode may retain KTX2, but the standard path must not
  require KTX2 support in the consuming imaging implementation.

## Cache and asset lifetime

Embedded images are served from the source file through a package resolver, and
external images use layer-relative paths: see
[ADR 0001](../adr/0001-image-assets.md). Import writes no image files, and converted
images use the same package paths, so results resolve after an asset moves with its
images.

Within an `ArResolverScopedCache`, successful KTX2 and WebP conversions can be reused
when the detected format and exact input bytes match. The hash only locates candidates.
Conversion settings and the decoder version are fixed by the loaded plugin, and the
cache lives only as long as its scope. An external image file is read again on each
open, so a changed or damaged file is still checked. Embedded images follow the
parsed package's lifetime described in [ADR 0001](../adr/0001-image-assets.md#lifetime-cache-and-concurrency).

The cache retains up to 64 MiB of source and PNG payloads per scope. A conversion
that exceeds the remaining budget is served directly. Returned assets own their
image bytes and can outlive the scope. Separate scopes keep separate caches, while
nested scopes or scopes explicitly sharing their OpenUSD cache data reuse the same
entries. The plugin synchronizes access when cache data is shared across threads.

Any persistent cache must also key on conversion settings and the decoder and
conversion implementation version.
A reference to a temporary file alone is not a portable persistent result, so
avoid an API that only returns a temporary path, and make storage duration and
ownership explicit. Failure handling for generated files must be designed
separately, because they are
[outside the layer's publication boundary](../architecture/LAYER_AUTHORING.md#decision).
