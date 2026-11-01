// SPDX-License-Identifier: Apache-2.0
#include "image.h"
#include "ktx2.h"

#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <iostream>
#include <limits>
#include <span>
#include <string>
#include <utility>
#include <vector>

namespace
{

using gltfTexture::ImageFormat;

const std::vector<unsigned char> kKtx2Signature = {0xAB, 'K',  'T',  'X',  ' ',  '2',
                                                   '0',  0xBB, '\r', '\n', 0x1A, '\n'};

bool
checkDetect(std::vector<unsigned char> bytes, bool expected, ImageFormat expectedFormat,
            const char* label)
{
    ImageFormat format = ImageFormat::Png;
    const bool success = gltfTexture::DetectImageFormat(
        std::as_bytes(std::span<const unsigned char>(bytes)), format);
    if (success != expected || (success && format != expectedFormat))
    {
        std::cerr << label << ": unexpected image format result\n";
        return false;
    }
    return true;
}

bool
checkEntry(const std::string& entry, bool expected, std::size_t expectedIndex = 0,
           ImageFormat expectedFormat = ImageFormat::Png)
{
    std::size_t index = 0;
    ImageFormat format = ImageFormat::Png;
    const bool success = gltfTexture::ParsePackageEntry(entry, index, format);
    if (success != expected || (success && (index != expectedIndex || format != expectedFormat)))
    {
        std::cerr << "'" << entry << "': unexpected package entry result\n";
        return false;
    }
    return true;
}

bool
checkKtx2Rejected(const std::vector<unsigned char>& bytes, const char* label)
{
    std::string error;
    std::vector<std::byte> png;
    const auto span = std::as_bytes(std::span<const unsigned char>(bytes));
    if (gltfTexture::CheckKtx2(span, error) || error.empty() ||
        gltfTexture::DecodeKtx2ToPng(span, png, error))
    {
        std::cerr << label << ": expected an error\n";
        return false;
    }
    return true;
}

// Every shorter prefix of a valid image is a truncated file, and must not pass or read
// out of bounds.
bool
checkPrefixesRejected(std::span<const std::byte> bytes, ImageFormat format, const char* label)
{
    std::string error;
    if (!gltfTexture::CheckImage(bytes, format, error))
    {
        std::cerr << label << ": unexpected error: " << error << "\n";
        return false;
    }
    for (std::size_t size = 0; size < bytes.size(); ++size)
    {
        if (gltfTexture::CheckImage(bytes.first(size), format, error))
        {
            std::cerr << label << ": a prefix of " << size << " bytes passed\n";
            return false;
        }
    }
    return true;
}

bool
checkRejected(const std::vector<unsigned char>& bytes, ImageFormat format, const char* label)
{
    std::string error;
    if (gltfTexture::CheckImage(std::as_bytes(std::span<const unsigned char>(bytes)), format,
                                error) ||
        error.empty())
    {
        std::cerr << label << ": expected an error\n";
        return false;
    }
    return true;
}

bool
checkStructure()
{
    const std::vector<std::uint8_t> rgba = {1, 2, 3, 4};
    const auto png = gltfTexture::EncodePng(1, 1, rgba);
    bool ok = checkPrefixesRejected(png, ImageFormat::Png, "PNG");

    // A chunk that claims more than the file holds, where adding to the offset would wrap.
    std::vector<unsigned char> huge = {0x89, 'P',  'N', 'G', '\r', '\n', 0x1A, '\n', 0xFF, 0xFF,
                                       0xFF, 0xFF, 'I', 'H', 'D',  'R',  0,    0,    0,    0};
    ok = checkRejected(huge, ImageFormat::Png, "PNG chunk length") && ok;

    const std::vector<unsigned char> jpeg = {
        0xFF, 0xD8,                                            // Start of image.
        0xFF, 0xDB, 0,    3,    0,                             // Quantization table.
        0xFF, 0xC0, 0,    11,   8, 0, 1, 0, 1,  1, 1, 0x11, 0, // Frame of 1 by 1.
        0xFF, 0xC4, 0,    3,    0,                             // Huffman table.
        0xFF, 0xDA, 0,    8,    1, 1, 0, 0, 63, 0,             // Scan header.
        0x12, 0xFF, 0x00, 0x34,                                // Scan data.
        0xFF, 0xD9};                                           // End of image.
    ok = checkPrefixesRejected(std::as_bytes(std::span<const unsigned char>(jpeg)),
                               ImageFormat::Jpeg, "JPEG") &&
         ok;
    for (const unsigned char length : {0, 1})
    {
        ok = checkRejected({0xFF, 0xD8, 0xFF, 0xE0, 0, length}, ImageFormat::Jpeg,
                           "JPEG segment length") &&
             ok;
    }
    ok = checkRejected({0xFF, 0xD8, 0xFF, 0xE0, 0xFF, 0xFF, 0}, ImageFormat::Jpeg,
                       "JPEG segment past the end") &&
         ok;
    ok = checkRejected({0xFF, 0xD8, 0xFF, 0xC0, 0, 2}, ImageFormat::Jpeg, "JPEG empty frame") && ok;
    ok = checkRejected({0xFF, 0xD8, 0xFF, 0xD9}, ImageFormat::Jpeg, "JPEG with no scan") && ok;
    return ok;
}

// Two rows of two pixels fit a known layout: one stored block after the zlib header.
bool
checkPng()
{
    const std::vector<std::uint8_t> rgba = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16};
    const auto png = gltfTexture::EncodePng(2, 2, rgba);
    ImageFormat format = ImageFormat::Jpeg;
    constexpr std::size_t kData = 8 + 25 + 8 + 2 + 5;
    bool ok = gltfTexture::DetectImageFormat(png, format) && format == ImageFormat::Png &&
              png.size() == kData + 18 + 4 + 4 + 12;
    for (std::size_t i = 0; ok && i < 2; ++i)
    {
        ok = std::to_integer<int>(png[kData + i * 9]) == 0 &&
             std::memcmp(&png[kData + i * 9 + 1], &rgba[i * 8], 8) == 0;
    }
    if (!ok)
    {
        std::cerr << "unexpected PNG layout\n";
    }
    return ok;
}

} // namespace

int
main()
{
    bool ok =
        checkDetect({0x89, 'P', 'N', 'G', '\r', '\n', 0x1A, '\n'}, true, ImageFormat::Png, "PNG");
    ok = checkDetect({0xFF, 0xD8, 0xFF}, true, ImageFormat::Jpeg, "JPEG") && ok;
    ok = checkDetect({0x89, 'P', 'N', 'G'}, false, ImageFormat::Png, "short PNG") && ok;
    ok = checkDetect({0xFF, 0xD8}, false, ImageFormat::Jpeg, "short JPEG") && ok;
    ok = checkDetect({}, false, ImageFormat::Png, "empty") && ok;
    ok = checkDetect({'R', 'I', 'F', 'F'}, false, ImageFormat::Png, "WebP") && ok;
    ok = checkDetect(kKtx2Signature, true, ImageFormat::Ktx2, "KTX2") && ok;
    ok = checkDetect({0xAB, 'K', 'T', 'X', ' ', '2', '0', 0xBB}, false, ImageFormat::Png,
                     "short KTX2") &&
         ok;
    ok = checkDetect({0xAB, 'K', 'T', 'X', ' ', '1', '1', 0xBB, '\r', '\n', 0x1A, '\n'}, false,
                     ImageFormat::Png, "KTX1") &&
         ok;

    ok = checkEntry(gltfTexture::MakePackageEntry(0, ImageFormat::Png), true, 0) && ok;
    // KTX2 is served as PNG.
    ok = checkEntry(gltfTexture::MakePackageEntry(3, ImageFormat::Ktx2), true, 3) && ok;
    ok = checkEntry("images/3.ktx2", false) && ok;
    ok = checkEntry(gltfTexture::MakePackageEntry(12, ImageFormat::Jpeg), true, 12,
                    ImageFormat::Jpeg) &&
         ok;
    for (const char* entry :
         {"", "0.png", "images/", "images/.png", "images/00.png", "images/-1.png", "images/1",
          "images/1.jpeg", "images/1.png.png", "images/1.PNG", "images/1/../2.png", "Images/1.png",
          "images/99999999999999999999999.png"})
    {
        ok = checkEntry(entry, false) && ok;
    }

    fastgltf::Asset asset;
    std::vector<std::byte> owned;
    std::span<const std::byte> bytes;
    ImageFormat format;
    std::string error;
    if (gltfTexture::GetPackagedImage(asset, 0, "", {}, owned, bytes, format, error) ||
        error.empty())
    {
        std::cerr << "out-of-range image: expected an error\n";
        ok = false;
    }

    ok = checkKtx2Rejected({}, "empty KTX2") && ok;
    ok = checkKtx2Rejected(kKtx2Signature, "KTX2 signature alone") && ok;
    ok = checkPng() && ok;
    ok = checkStructure() && ok;
    return ok ? 0 : 1;
}
