// SPDX-License-Identifier: Apache-2.0
#include "webp.h"

#include "imageStructure.h"
#include "ktx2.h"

#include <webp/decode.h>

#include <algorithm>
#include <cstdint>
#include <cstring>
#include <new>

namespace gltfTexture
{
namespace
{

constexpr std::size_t kTagSize = 4;
constexpr std::size_t kChunkHeaderSize = 8;
constexpr std::size_t kRiffHeaderSize = 12;

std::uint32_t
readLittleEndian(const std::byte* bytes)
{
    std::uint32_t value = 0;
    for (std::size_t i = 0; i < 4; ++i)
    {
        value |= std::uint32_t(std::to_integer<std::uint8_t>(bytes[i])) << (8 * i);
    }
    return value;
}

bool
hasTag(const std::byte* bytes, const char* tag)
{
    return std::memcmp(bytes, tag, kTagSize) == 0;
}

// Returns the bytes the RIFF header covers, after checking that each chunk lies within them.
// libwebp reads only the chunks it needs, so a file cut short is otherwise found only when its
// pixels are decoded. Bytes after the RIFF payload are accepted, as after a PNG's IEND.
bool
riffPayload(std::span<const std::byte> bytes, std::span<const std::byte>& riff, std::string& error)
{
    if (bytes.size() < kRiffHeaderSize || !hasTag(bytes.data(), "RIFF") ||
        !hasTag(bytes.data() + 8, "WEBP"))
    {
        error = "WebP image is damaged";
        return false;
    }
    const std::size_t riffSize = readLittleEndian(bytes.data() + kTagSize);
    if (riffSize < kTagSize + kChunkHeaderSize || riffSize > bytes.size() - kChunkHeaderSize)
    {
        error = "WebP image is damaged";
        return false;
    }
    riff = bytes.first(kChunkHeaderSize + riffSize);
    for (std::size_t offset = kRiffHeaderSize; offset < riff.size();)
    {
        if (riff.size() - offset < kChunkHeaderSize)
        {
            error = "WebP image is damaged";
            return false;
        }
        const std::size_t size = readLittleEndian(riff.data() + offset + kTagSize);
        offset += kChunkHeaderSize;
        if (size > riff.size() - offset)
        {
            error = "WebP image is damaged";
            return false;
        }
        // A chunk of odd size is followed by a padding byte, which some writers omit at the end.
        offset += size + std::min<std::size_t>(size & 1, riff.size() - offset - size);
    }
    return true;
}

bool
readFeatures(std::span<const std::byte> bytes, std::span<const std::byte>& riff,
             WebPBitstreamFeatures& features, std::string& error)
{
    if (!riffPayload(bytes, riff, error))
    {
        return false;
    }
    if (WebPGetFeatures(reinterpret_cast<const std::uint8_t*>(riff.data()), riff.size(),
                        &features) != VP8_STATUS_OK)
    {
        error = "WebP image is damaged";
        return false;
    }
    if (features.has_animation)
    {
        error = "animated WebP image is not supported";
        return false;
    }
    if (features.width <= 0 || features.height <= 0 ||
        std::uint32_t(features.width) > kMaxImageDimension ||
        std::uint32_t(features.height) > kMaxImageDimension)
    {
        error = "WebP image is larger than 16384 pixels per side";
        return false;
    }
    return true;
}

} // namespace

bool
CheckWebp(std::span<const std::byte> bytes, std::string& error)
{
    std::span<const std::byte> riff;
    WebPBitstreamFeatures features;
    return readFeatures(bytes, riff, features, error);
}

bool
DecodeWebpToPng(std::span<const std::byte> bytes, std::vector<std::byte>& png, std::string& error)
{
    std::span<const std::byte> riff;
    WebPBitstreamFeatures features;
    if (!readFeatures(bytes, riff, features, error))
    {
        return false;
    }
    try
    {
        const auto width = std::uint32_t(features.width);
        const auto height = std::uint32_t(features.height);
        const auto stride = std::size_t(width) * 4;
        std::vector<std::uint8_t> rgba(stride * height);
        if (!WebPDecodeRGBAInto(reinterpret_cast<const std::uint8_t*>(riff.data()), riff.size(),
                                rgba.data(), rgba.size(), int(stride)))
        {
            error = "WebP image is damaged";
            return false;
        }
        png = EncodePng(width, height, rgba);
    }
    catch (const std::bad_alloc&)
    {
        error = "not enough memory to decode WebP image";
        return false;
    }
    return true;
}

} // namespace gltfTexture
