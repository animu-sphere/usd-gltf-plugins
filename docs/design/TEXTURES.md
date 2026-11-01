# Texture and image design

- Treat PNG and JPEG as image assets accessible to USD consumers.
- For `KHR_texture_basisu`, provide a standard path from KTX2 through decoded
  RGBA to a common image format such as PNG, then to texture nodes.
  KTX-Software is a candidate decoder/transcoder; its selection and version
  require validation.
- Avoid unnecessary degradation and incorrect color-space interpretation.
  Color and non-color inputs must be handled distinctly as described in
  [MATERIALS](MATERIALS.md).
- Resolve embedded images and external URI images through a unified API,
  accommodating bufferViews, data URIs and external assets. `gltfTexture` owns
  this API and does not build UsdShade graphs.
- A future optional mode may retain KTX2, but the standard path must not
  require KTX2 support in the consuming imaging implementation.

## Cache and asset lifetime

Cache keys must include image content, conversion settings, and the
decoder/conversion implementation version, avoiding collisions and stale
results. The cache's lifetime and location, concurrent behavior, and asset
path strategy must be decided in an ADR before a decoder is implemented. In
particular, converted images must remain resolvable when a persistent stage is
moved; a reference to a temporary file alone is not a portable persistent
result, so avoid an API that only returns a temporary path, and make storage
duration and ownership explicit.

The design must specify how source-relative, embedded and converted images
are represented and retained across stage and cache lifetimes. Avoid
claiming portability until a strategy and its tests exist. Failure handling for
generated files must be designed separately, because they are
[outside the layer's publication boundary](../architecture/LAYER_AUTHORING.md#decision).
