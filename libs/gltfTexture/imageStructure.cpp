// SPDX-License-Identifier: Apache-2.0
#include "imageStructure.h"

#include "ktx2.h"

#include <algorithm>
#include <array>
#include <cstring>

namespace gltfTexture
{
namespace
{

std::uint32_t
readByte(std::span<const std::byte> bytes, std::size_t offset)
{
    return std::to_integer<std::uint32_t>(bytes[offset]);
}

std::uint32_t
readBigEndian16(std::span<const std::byte> bytes, std::size_t offset)
{
    return (readByte(bytes, offset) << 8) | readByte(bytes, offset + 1);
}

std::uint32_t
readBigEndian32(std::span<const std::byte> bytes, std::size_t offset)
{
    return (readBigEndian16(bytes, offset) << 16) | readBigEndian16(bytes, offset + 2);
}

bool
isChunk(std::span<const std::byte> type, const char (&name)[5])
{
    return std::memcmp(type.data(), name, 4) == 0;
}

bool
damaged(const char* format, const char* reason, std::string& error)
{
    error = std::string(format) + " image is damaged: " + reason;
    return false;
}

bool
tooLarge(const char* format, std::string& error)
{
    error = std::string(format) + " image is wider or taller than " +
            std::to_string(kMaxImageDimension) + " pixels";
    return false;
}

bool
validPngFormat(std::uint32_t depth, std::uint32_t color)
{
    switch (color)
    {
    case 0: // Grayscale.
        return depth == 1 || depth == 2 || depth == 4 || depth == 8 || depth == 16;
    case 3: // Palette.
        return depth == 1 || depth == 2 || depth == 4 || depth == 8;
    case 2: // RGB.
    case 4: // Grayscale and alpha.
    case 6: // RGBA.
        return depth == 8 || depth == 16;
    default:
        return false;
    }
}

} // namespace

bool
CheckPng(std::span<const std::byte> bytes, std::string& error)
{
    static constexpr const char* kPng = "PNG";
    static constexpr std::size_t kSignature = 8;
    // The length, type and checksum around each chunk's data.
    static constexpr std::size_t kChunkFrame = 12;

    bool header = false;
    bool needsPalette = false;
    bool palette = false;
    bool data = false;
    auto offset = kSignature;
    for (;;)
    {
        // Comparing against what remains cannot overflow, unlike adding to the offset.
        if (bytes.size() < offset || bytes.size() - offset < kChunkFrame ||
            readBigEndian32(bytes, offset) > bytes.size() - offset - kChunkFrame)
        {
            return damaged(kPng, "it ends before its last chunk", error);
        }
        const std::size_t length = readBigEndian32(bytes, offset);
        const auto type = bytes.subspan(offset + 4, 4);
        if (Crc32(bytes.subspan(offset + 4, length + 4)) !=
            readBigEndian32(bytes, offset + 8 + length))
        {
            return damaged(kPng, "a chunk checksum does not match", error);
        }
        const auto body = bytes.subspan(offset + 8, length);
        if (!header)
        {
            if (!isChunk(type, "IHDR") || length != 13)
            {
                return damaged(kPng, "it does not start with a valid IHDR", error);
            }
            const auto width = readBigEndian32(body, 0);
            const auto height = readBigEndian32(body, 4);
            if (width == 0 || height == 0)
            {
                return damaged(kPng, "it declares no pixels", error);
            }
            if (width > kMaxImageDimension || height > kMaxImageDimension)
            {
                return tooLarge(kPng, error);
            }
            const auto color = readByte(body, 9);
            if (!validPngFormat(readByte(body, 8), color) || readByte(body, 10) != 0 ||
                readByte(body, 11) != 0 || readByte(body, 12) > 1)
            {
                return damaged(kPng, "its IHDR declares an invalid format", error);
            }
            needsPalette = color == 3;
            header = true;
        }
        else if (isChunk(type, "PLTE"))
        {
            palette = true;
        }
        else if (isChunk(type, "IDAT"))
        {
            if (needsPalette && !palette)
            {
                return damaged(kPng, "its palette is missing", error);
            }
            data = data || length > 0;
        }
        else if (isChunk(type, "IEND"))
        {
            break;
        }
        offset += kChunkFrame + length;
    }
    if (!data)
    {
        return damaged(kPng, "it has no image data", error);
    }
    return true;
}

bool
CheckJpeg(std::span<const std::byte> bytes, std::string& error)
{
    static constexpr const char* kJpeg = "JPEG";
    static constexpr const char* kBeforeScan = "it ends or breaks before its first scan";
    bool frame = false;
    bool quantization = false;
    bool huffman = false;
    std::size_t offset = 2;
    for (;;)
    {
        if (offset >= bytes.size() || readByte(bytes, offset) != 0xFF)
        {
            return damaged(kJpeg, kBeforeScan, error);
        }
        // A marker may be preceded by any number of fill bytes.
        while (offset < bytes.size() && readByte(bytes, offset) == 0xFF)
        {
            ++offset;
        }
        if (offset == bytes.size())
        {
            return damaged(kJpeg, kBeforeScan, error);
        }
        const auto marker = readByte(bytes, offset++);
        // Markers without a length have no place before the first scan.
        if (marker == 0x00 || marker == 0x01 || (marker >= 0xD0 && marker <= 0xD9))
        {
            return damaged(kJpeg, kBeforeScan, error);
        }
        if (bytes.size() - offset < 2 || readBigEndian16(bytes, offset) < 2 ||
            readBigEndian16(bytes, offset) > bytes.size() - offset)
        {
            return damaged(kJpeg, kBeforeScan, error);
        }
        const std::size_t length = readBigEndian16(bytes, offset);
        const auto body = bytes.subspan(offset + 2, length - 2);
        offset += length;
        if (marker == 0xDB)
        {
            quantization = true;
        }
        else if (marker == 0xC4)
        {
            huffman = true;
        }
        else if (marker == 0xC0 || marker == 0xC1 || marker == 0xC2)
        {
            if (frame || body.size() < 6 || body.size() != 6 + 3 * std::size_t(readByte(body, 5)))
            {
                return damaged(kJpeg, "its frame header is invalid", error);
            }
            const auto components = readByte(body, 5);
            if (readBigEndian16(body, 1) == 0 || readBigEndian16(body, 3) == 0 ||
                (components != 1 && components != 3 && components != 4))
            {
                return damaged(kJpeg, "its frame header is invalid", error);
            }
            if (readByte(body, 0) != 8)
            {
                error = "JPEG image is not 8-bit";
                return false;
            }
            if (readBigEndian16(body, 1) > kMaxImageDimension ||
                readBigEndian16(body, 3) > kMaxImageDimension)
            {
                return tooLarge(kJpeg, error);
            }
            frame = true;
        }
        else if (marker >= 0xC3 && marker <= 0xCF && marker != 0xC8 && marker != 0xCC)
        {
            error = "JPEG image is not baseline or progressive Huffman coding";
            return false;
        }
        else if (marker == 0xDA)
        {
            break;
        }
    }
    if (!frame || !quantization || !huffman)
    {
        return damaged(kJpeg, "its first scan has no frame header or tables", error);
    }
    // Entropy-coded data escapes 0xFF, so this pair can only be the marker itself.
    static constexpr std::array<std::byte, 2> kEnd = {std::byte(0xFF), std::byte(0xD9)};
    const auto scan = bytes.subspan(offset);
    if (std::search(scan.begin(), scan.end(), kEnd.begin(), kEnd.end()) == scan.end())
    {
        return damaged(kJpeg, "it ends before its end marker", error);
    }
    return true;
}

} // namespace gltfTexture
