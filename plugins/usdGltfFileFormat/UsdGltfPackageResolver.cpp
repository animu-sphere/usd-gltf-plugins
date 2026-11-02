// SPDX-License-Identifier: Apache-2.0
#include "externalBuffer.h"
#include "image.h"
#include "ktx2.h"
#include "parse.h"

#include "pxr/pxr.h"
#include "pxr/base/tf/diagnostic.h"
#include "pxr/base/tf/registryManager.h"
#include "pxr/base/tf/type.h"
#include "pxr/usd/ar/asset.h"
#include "pxr/usd/ar/definePackageResolver.h"
#include "pxr/usd/ar/inMemoryAsset.h"
#include "pxr/usd/ar/packageResolver.h"
#include "pxr/usd/ar/resolvedPath.h"
#include "pxr/usd/ar/resolver.h"
#include "pxr/usd/ar/threadLocalScopedCache.h"

#include <cstddef>
#include <memory>
#include <optional>
#include <span>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

PXR_NAMESPACE_OPEN_SCOPE

namespace
{

// Keeps the asset and its loaded buffers alive for the image bytes they own.
struct Package
{
    explicit Package(const std::string& sourcePath) : path(sourcePath), loader(sourcePath)
    {
    }

    std::string path;
    ExternalBufferLoader loader;
    std::optional<fastgltf::Asset> asset;
};

std::shared_ptr<Package>
openPackage(const std::string& resolvedPackagePath)
{
    const auto input = ArGetResolver().OpenAsset(ArResolvedPath(resolvedPackagePath));
    const auto bytes = input ? input->GetBuffer() : nullptr;
    if (!bytes)
    {
        return {};
    }
    // The file format has already reported why the source cannot be read.
    std::string error;
    auto parsed =
        gltfCore::Parse(reinterpret_cast<const std::byte*>(bytes.get()), input->GetSize(), error);
    if (parsed.error() != fastgltf::Error::None)
    {
        return {};
    }
    auto package = std::make_shared<Package>(resolvedPackagePath);
    package->asset = std::move(parsed.get());
    return package;
}

// `bytes` refers to the package, or to `owned` for a KTX2 file.
struct PackagedImage
{
    std::vector<std::byte> owned;
    std::span<const std::byte> bytes;
    gltfTexture::ImageFormat format;
};

bool
findImage(const std::shared_ptr<Package>& package, const std::string& entry, PackagedImage& image)
{
    std::size_t index;
    gltfTexture::ImageFormat requested;
    std::string error;
    auto* loader = package ? &package->loader : nullptr;
    return package && gltfTexture::ParsePackageEntry(entry, index, requested) &&
           gltfTexture::GetPackagedImage(
               *package->asset, index, package->path,
               [loader](const fastgltf::Buffer& buffer, std::span<const std::byte>& data,
                        std::string& loadError) { return loader->Load(buffer, data, loadError); },
               image.owned, image.bytes, image.format, error) &&
           requested == gltfTexture::ServedFormat(image.format);
}

} // namespace

// Serves embedded images from the source file, and KTX2 images converted to PNG, so
// an imported stage needs no extracted files. See docs/adr/0001-image-assets.md.
class UsdGltfPackageResolver final : public ArPackageResolver
{
  public:
    std::string
    Resolve(const std::string& resolvedPackagePath, const std::string& packagedPath) override
    {
        PackagedImage image;
        std::string error;
        if (!findImage(findPackage(resolvedPackagePath), packagedPath, image) ||
            !gltfTexture::CheckImage(image.bytes, image.format, error))
        {
            return {};
        }
        return packagedPath;
    }

    std::shared_ptr<ArAsset>
    OpenAsset(const std::string& resolvedPackagePath,
              const std::string& resolvedPackagedPath) override
    {
        const auto package = findPackage(resolvedPackagePath);
        PackagedImage image;
        if (!findImage(package, resolvedPackagedPath, image))
        {
            return nullptr;
        }
        auto png = std::make_shared<std::vector<std::byte>>();
        std::string error;
        // The source can have changed since import, and the image loader must not see
        // a damaged image. See docs/adr/0006-image-failures.md.
        if (!gltfTexture::CheckImage(image.bytes, image.format, error) ||
            (image.format == gltfTexture::ImageFormat::Ktx2 &&
             !gltfTexture::DecodeKtx2ToPng(image.bytes, *png, error)))
        {
            TF_WARN("Cannot read '%s' in '%s': %s", resolvedPackagedPath.c_str(),
                    resolvedPackagePath.c_str(), error.c_str());
            return nullptr;
        }
        if (image.format != gltfTexture::ImageFormat::Ktx2)
        {
            return ArInMemoryAsset::FromBuffer(
                std::shared_ptr<const char>(package,
                                            reinterpret_cast<const char*>(image.bytes.data())),
                image.bytes.size());
        }
        const auto* data = reinterpret_cast<const char*>(png->data());
        const auto size = png->size();
        return ArInMemoryAsset::FromBuffer(std::shared_ptr<const char>(std::move(png), data), size);
    }

    void
    BeginCacheScope(VtValue* cacheScopeData) override
    {
        _caches.BeginCacheScope(cacheScopeData);
    }

    void
    EndCacheScope(VtValue* cacheScopeData) override
    {
        _caches.EndCacheScope(cacheScopeData);
    }

  private:
    using PackageCache = std::unordered_map<std::string, std::shared_ptr<Package>>;

    std::shared_ptr<Package>
    findPackage(const std::string& resolvedPackagePath)
    {
        const auto cache = _caches.GetCurrentCache();
        if (!cache)
        {
            return openPackage(resolvedPackagePath);
        }
        const auto found = cache->find(resolvedPackagePath);
        if (found != cache->end())
        {
            return found->second;
        }
        return (*cache)[resolvedPackagePath] = openPackage(resolvedPackagePath);
    }

    ArThreadLocalScopedCache<PackageCache> _caches;
};

AR_DEFINE_PACKAGE_RESOLVER(UsdGltfPackageResolver, ArPackageResolver);

PXR_NAMESPACE_CLOSE_SCOPE
