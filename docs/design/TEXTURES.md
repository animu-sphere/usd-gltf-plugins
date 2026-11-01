# Texture and image design

Status: **adopted direction**, with storage and implementation choices open.

- Treat PNG and JPEG as image assets accessible to USD consumers.
- For `KHR_texture_basisu`, provide a standard path from KTX2 through decoded
  RGBA to a common image format such as PNG, then to texture nodes.
  KTX-Software is a candidate decoder/transcoder; its selection and version
  require validation.
- Avoid unnecessary degradation and incorrect color-space interpretation.
  Color and non-color inputs must be handled distinctly as described in
  [MATERIALS](MATERIALS.md).
- Resolve embedded images and external URI images through a unified API,
  accommodating both bufferViews and external assets.
- A future optional mode may retain KTX2, but the standard path must not
  require KTX2 support in the consuming imaging implementation.

## Cache and asset lifetime

Cache keys must include image content, conversion settings, and the
decoder/conversion implementation version, avoiding collisions and stale
results. The cache's lifetime and location, concurrent behavior, and asset
path strategy remain to be designed and tested. In particular, converted
images must remain resolvable when a persistent stage is moved; a reference
to a temporary file alone is not a portable persistent result.

The design must specify how source-relative, embedded and converted images
are represented and retained across stage and cache lifetimes. Avoid
claiming portability until a strategy and its tests exist.
