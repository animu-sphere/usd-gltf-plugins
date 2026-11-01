// SPDX-License-Identifier: Apache-2.0
#include "externalBuffer.h"
#include "image.h"
#include "parse.h"

#include "pxr/pxr.h"
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

PXR_NAMESPACE_OPEN_SCOPE

namespace
{

// Keeps the asset and its loaded buffers alive for the image bytes they own.
struct Package
{
    explicit Package(const std::string& path) : loader(path)
    {
    }

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
    auto parsed =
        gltfCore::Parse(reinterpret_cast<const std::byte*>(bytes.get()), input->GetSize());
    if (parsed.error() != fastgltf::Error::None)
    {
        return {};
    }
    auto package = std::make_shared<Package>(resolvedPackagePath);
    package->asset = std::move(parsed.get());
    return package;
}

std::span<const std::byte>
findImage(const std::shared_ptr<Package>& package, const std::string& entry)
{
    std::size_t index;
    gltfTexture::ImageFormat requested;
    gltfTexture::ImageFormat actual;
    std::span<const std::byte> bytes;
    std::string error;
    auto* loader = package ? &package->loader : nullptr;
    if (!package || !gltfTexture::ParsePackageEntry(entry, index, requested) ||
        !gltfTexture::GetEmbeddedImage(
            *package->asset, index,
            [loader](const fastgltf::Buffer& buffer, std::span<const std::byte>& data,
                     std::string& loadError) { return loader->Load(buffer, data, loadError); },
            bytes, actual, error) ||
        requested != actual)
    {
        return {};
    }
    return bytes;
}

} // namespace

// Serves embedded images from the source file, so an imported stage needs no
// extracted files. See docs/adr/0001-image-assets.md.
class UsdGltfPackageResolver final : public ArPackageResolver
{
  public:
    std::string
    Resolve(const std::string& resolvedPackagePath, const std::string& packagedPath) override
    {
        return findImage(findPackage(resolvedPackagePath), packagedPath).empty() ? std::string()
                                                                                 : packagedPath;
    }

    std::shared_ptr<ArAsset>
    OpenAsset(const std::string& resolvedPackagePath,
              const std::string& resolvedPackagedPath) override
    {
        const auto package = findPackage(resolvedPackagePath);
        const auto image = findImage(package, resolvedPackagedPath);
        if (image.empty())
        {
            return nullptr;
        }
        return ArInMemoryAsset::FromBuffer(
            std::shared_ptr<const char>(package, reinterpret_cast<const char*>(image.data())),
            image.size());
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
