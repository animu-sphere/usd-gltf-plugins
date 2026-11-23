// SPDX-License-Identifier: Apache-2.0
#include "externalBuffer.h"
#include "gltfTexture/image.h"
#include "gltfTexture/imageBasedLight.h"
#include "gltfCore/imageBasedLight.h"
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
    Package(const std::string& sourcePath, fastgltf::Asset&& parsed,
            std::optional<gltfCore::ImageBasedLight>&& imageBasedLight)
        : path(sourcePath), loader(sourcePath), asset(std::move(parsed)),
          light(std::move(imageBasedLight)),
          buffers(asset, [this](const fastgltf::Buffer& buffer, std::span<const std::byte>& data,
                                std::string& error) { return loader.Load(buffer, data, error); })
    {
    }

    std::string path;
    std::mutex buffersMutex;
    ExternalBufferLoader loader;
    fastgltf::Asset asset;
    // The selected scene's EXT_lights_image_based light, if any.
    std::optional<gltfCore::ImageBasedLight> light;
    gltfCore::BufferReader buffers;
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
    const auto* data = reinterpret_cast<const std::byte*>(bytes.get());
    auto parsed = gltfCore::Parse(data, input->GetSize(), error);
    std::optional<gltfCore::ImageBasedLight> light;
    if (parsed.error() != fastgltf::Error::None ||
        !gltfCore::ReadImageBasedLight(data, input->GetSize(), light, error))
    {
        return {};
    }
    return std::make_shared<Package>(resolvedPackagePath, std::move(parsed.get()),
                                     std::move(light));
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
    if (!package || !gltfTexture::ParsePackageEntry(entry, index, requested))
    {
        return false;
    }
    const std::lock_guard lock(package->buffersMutex);
    return gltfTexture::GetPackagedImage(package->asset, index, package->path, package->buffers,
                                         image.owned, image.bytes, image.format, error) &&
           requested == gltfTexture::ServedFormat(image.format);
}

// The package's light if `entry` names its latlong.
const gltfCore::ImageBasedLight*
findLight(const std::shared_ptr<Package>& package, const std::string& entry)
{
    std::size_t index = 0;
    if (!package || !gltfTexture::ParseLightEntry(entry, index) || !package->light ||
        package->light->index != index)
    {
        return nullptr;
    }
    return &*package->light;
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

// Serves embedded images from the source file, KTX2 and WebP images converted to PNG, and the
// latlong of an image-based light, so an imported stage needs no extracted files. See
// docs/adr/0001-image-assets.md and docs/adr/0046-image-based-lighting.md.
class UsdGltfPackageResolver final : public ArPackageResolver
{
  public:
    std::string
    Resolve(const std::string& resolvedPackagePath, const std::string& packagedPath) override
    {
        const auto package = findPackage(_caches.GetCurrentCache(), resolvedPackagePath);
        PackagedImage image;
        std::string error;
        if (const auto* light = findLight(package, packagedPath))
        {
            // Its faces are checked, not decoded.
            const std::lock_guard lock(package->buffersMutex);
            return gltfTexture::CheckImageBasedLight(package->asset, *light, package->path,
                                                     package->buffers, error)
                       ? packagedPath
                       : std::string();
        }
        if (!findImage(package, packagedPath, image) ||
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
        if (const auto* light = findLight(package, resolvedPackagedPath))
        {
            return openLight(*package, *light, resolvedPackagePath, resolvedPackagedPath);
        }
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
    // Decodes the faces, which can have changed since import, as a KTX2 image is. See
    // docs/adr/0006-image-failures.md.
    static std::shared_ptr<ArAsset>
    openLight(Package& package, const gltfCore::ImageBasedLight& light,
              const std::string& resolvedPackagePath, const std::string& resolvedPackagedPath)
    {
        TRACE_SCOPE("UsdGltfFileFormat image-based light to latlong");
        std::vector<std::byte> hdr;
        std::string error;
        bool converted = false;
        {
            const std::lock_guard lock(package.buffersMutex);
            converted = gltfTexture::ConvertImageBasedLight(package.asset, light, package.path,
                                                            package.buffers, hdr, error);
        }
        if (!converted)
        {
            TF_WARN("Cannot read '%s' in '%s': %s", resolvedPackagedPath.c_str(),
                    resolvedPackagePath.c_str(), error.c_str());
            return nullptr;
        }
        auto bytes = std::make_shared<const std::vector<std::byte>>(std::move(hdr));
        const auto* data = reinterpret_cast<const char*>(bytes->data());
        const auto size = bytes->size();
        return ArInMemoryAsset::FromBuffer(std::shared_ptr<const char>(std::move(bytes), data),
                                           size);
    }

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
