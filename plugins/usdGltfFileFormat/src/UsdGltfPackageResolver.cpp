// SPDX-License-Identifier: Apache-2.0
#include "externalBuffer.h"
#include "gltfTexture/image.h"
#include "gltfCore/parse.h"

#include "pxr/pxr.h"
#include "pxr/base/arch/hash.h"
#include "pxr/base/tf/diagnostic.h"
#include "pxr/base/tf/registryManager.h"
#include "pxr/base/tf/type.h"
#include "pxr/base/trace/trace.h"
#include "pxr/usd/ar/asset.h"
#include "pxr/usd/ar/definePackageResolver.h"
#include "pxr/usd/ar/inMemoryAsset.h"
#include "pxr/usd/ar/packageResolver.h"
#include "pxr/usd/ar/resolvedPath.h"
#include "pxr/usd/ar/resolver.h"
#include "pxr/usd/ar/threadLocalScopedCache.h"

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <memory>
#include <mutex>
#include <new>
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
    std::mutex loaderMutex;
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

// `bytes` refers to the package, or to `owned` for a KTX2 or WebP file.
struct PackagedImage
{
    std::vector<std::byte> owned;
    std::span<const std::byte> bytes;
    gltfTexture::ImageFormat format;
};

struct ConvertedImage
{
    gltfTexture::ImageFormat format;
    std::vector<std::byte> source;
    std::shared_ptr<ArAsset> asset;
};

constexpr std::size_t maxCachedImageBytes = 64 * 1024 * 1024;

struct ResolverCache
{
    // Cache data can be explicitly shared between threads through a parent scope.
    std::mutex mutex;
    std::unordered_map<std::string, std::shared_ptr<Package>> packages;
    std::unordered_multimap<std::uint64_t, ConvertedImage> images;
    std::size_t imageBytes = 0;

    // The caller holds mutex for both operations.
    std::shared_ptr<ArAsset>
    FindImage(std::uint64_t hash, const PackagedImage& image) const
    {
        const auto [first, last] = images.equal_range(hash);
        for (auto it = first; it != last; ++it)
        {
            const auto& cached = it->second;
            if (cached.format == image.format && std::ranges::equal(cached.source, image.bytes))
            {
                return cached.asset;
            }
        }
        return {};
    }

    void
    RememberImage(std::uint64_t hash, PackagedImage& image, const std::shared_ptr<ArAsset>& asset)
    {
        // Bound the retained source and PNG payloads. Larger images still open normally.
        const auto sourceSize = image.bytes.size();
        const auto pngSize = asset->GetSize();
        const auto remaining = maxCachedImageBytes - imageBytes;
        if (sourceSize > remaining || pngSize > remaining - sourceSize || FindImage(hash, image))
        {
            return;
        }
        try
        {
            auto source = image.owned.empty()
                              ? std::vector<std::byte>(image.bytes.begin(), image.bytes.end())
                              : std::move(image.owned);
            images.emplace(hash, ConvertedImage{image.format, std::move(source), asset});
            imageBytes += sourceSize + pngSize;
        }
        catch (const std::bad_alloc&)
        {
            // The converted asset is already usable without caching it.
        }
    }
};

bool
findImage(const std::shared_ptr<Package>& package, const std::string& entry, PackagedImage& image)
{
    std::size_t index;
    gltfTexture::ImageFormat requested;
    std::string error;
    return package && gltfTexture::ParsePackageEntry(entry, index, requested) &&
           gltfTexture::GetPackagedImage(
               *package->asset, index, package->path,
               [&package](const fastgltf::Buffer& buffer, std::span<const std::byte>& data,
                          std::string& loadError)
               {
                   const std::lock_guard lock(package->loaderMutex);
                   return package->loader.Load(buffer, data, loadError);
               },
               image.owned, image.bytes, image.format, error) &&
           requested == gltfTexture::ServedFormat(image.format);
}

bool
convertToPng(const PackagedImage& image, std::vector<std::byte>& png, std::string& error)
{
    if (image.format == gltfTexture::ImageFormat::Ktx2)
    {
        TRACE_SCOPE("UsdGltfFileFormat KTX2 to PNG");
        return gltfTexture::ConvertToPng(image.bytes, image.format, png, error);
    }
    return gltfTexture::ConvertToPng(image.bytes, image.format, png, error);
}

} // namespace

// Serves embedded images from the source file, and KTX2 and WebP images converted to PNG, so
// an imported stage needs no extracted files. See docs/adr/0001-image-assets.md.
class UsdGltfPackageResolver final : public ArPackageResolver
{
  public:
    std::string
    Resolve(const std::string& resolvedPackagePath, const std::string& packagedPath) override
    {
        PackagedImage image;
        std::string error;
        if (!findImage(findPackage(_caches.GetCurrentCache(), resolvedPackagePath), packagedPath,
                       image) ||
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
        TRACE_SCOPE("UsdGltfFileFormat image open");
        const auto cache = _caches.GetCurrentCache();
        const auto package = findPackage(cache, resolvedPackagePath);
        PackagedImage image;
        if (!findImage(package, resolvedPackagedPath, image))
        {
            return nullptr;
        }
        std::string error;
        const bool converted = gltfTexture::ServedFormat(image.format) != image.format;
        const bool cacheable = cache && converted && image.bytes.size() < maxCachedImageBytes;
        std::uint64_t hash = 0;
        if (cacheable)
        {
            hash =
                ArchHash64(reinterpret_cast<const char*>(image.bytes.data()), image.bytes.size());
            const std::lock_guard lock(cache->mutex);
            if (auto asset = cache->FindImage(hash, image))
            {
                return asset;
            }
        }
        // The source can have changed since import, and the image loader must not see
        // a damaged image. See docs/adr/0006-image-failures.md.
        std::vector<std::byte> png;
        if (!gltfTexture::CheckImage(image.bytes, image.format, error) ||
            (converted && !convertToPng(image, png, error)))
        {
            TF_WARN("Cannot read '%s' in '%s': %s", resolvedPackagedPath.c_str(),
                    resolvedPackagePath.c_str(), error.c_str());
            return nullptr;
        }
        if (!converted)
        {
            return ArInMemoryAsset::FromBuffer(
                std::shared_ptr<const char>(package,
                                            reinterpret_cast<const char*>(image.bytes.data())),
                image.bytes.size());
        }
        auto bytes = std::make_shared<const std::vector<std::byte>>(std::move(png));
        const auto* data = reinterpret_cast<const char*>(bytes->data());
        const auto size = bytes->size();
        auto asset =
            ArInMemoryAsset::FromBuffer(std::shared_ptr<const char>(std::move(bytes), data), size);
        if (cacheable)
        {
            const std::lock_guard lock(cache->mutex);
            cache->RememberImage(hash, image, asset);
        }
        return asset;
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
    static std::shared_ptr<Package>
    findPackage(const std::shared_ptr<ResolverCache>& cache, const std::string& resolvedPackagePath)
    {
        if (!cache)
        {
            return openPackage(resolvedPackagePath);
        }
        {
            const std::lock_guard lock(cache->mutex);
            const auto found = cache->packages.find(resolvedPackagePath);
            if (found != cache->packages.end())
            {
                return found->second;
            }
        }
        // Opening the source may call this resolver again through a containing package.
        auto package = openPackage(resolvedPackagePath);
        const std::lock_guard lock(cache->mutex);
        return cache->packages.try_emplace(resolvedPackagePath, std::move(package)).first->second;
    }

    ArThreadLocalScopedCache<ResolverCache> _caches;
};

AR_DEFINE_PACKAGE_RESOLVER(UsdGltfPackageResolver, ArPackageResolver);

PXR_NAMESPACE_CLOSE_SCOPE
