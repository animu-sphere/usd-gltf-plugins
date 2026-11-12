// SPDX-License-Identifier: Apache-2.0
#include "gltfTexture/image.h"
#include "gltfTexture/ktx2.h"
#include "gltfTexture/webp.h"

#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <iterator>
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

// tests/fixtures/textures/lossless_alpha.webp: 16 by 16 quadrants of red, green, blue and white
// from the upper left, the lower two with alpha 64. Its VP8L chunk has an odd size and a padding
// byte.
const std::vector<unsigned char> kWebp = {
    0x52, 0x49, 0x46, 0x46, 0x3C, 0x00, 0x00, 0x00, 0x57, 0x45, 0x42, 0x50, 0x56, 0x50,
    0x38, 0x4C, 0x2F, 0x00, 0x00, 0x00, 0x2F, 0x0F, 0xC0, 0x03, 0x10, 0x1F, 0x20, 0x10,
    0x48, 0xDE, 0x1F, 0x3A, 0x0D, 0x01, 0x41, 0xC1, 0xFF, 0xD1, 0xCD, 0xBF, 0x40, 0x20,
    0x49, 0x6D, 0x7F, 0xA8, 0x43, 0x1E, 0x3D, 0x00, 0x61, 0xB6, 0xD1, 0x24, 0x27, 0x39,
    0xC9, 0x7B, 0x9F, 0x41, 0x44, 0xFF, 0x43, 0xE1, 0xAB, 0xED, 0x03, 0x00};

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

bool
checkWebpRejected(const std::vector<unsigned char>& bytes, const char* label)
{
    std::string error;
    std::vector<std::byte> png;
    const auto span = std::as_bytes(std::span<const unsigned char>(bytes));
    if (gltfTexture::CheckWebp(span, error) || error.empty() ||
        gltfTexture::DecodeWebpToPng(span, png, error))
    {
        std::cerr << label << ": expected an error\n";
        return false;
    }
    return true;
}

// Decodes the WebP above and reads its pixels from the single stored block EncodePng writes for
// an image this small.
bool
checkWebp()
{
    const auto bytes = std::as_bytes(std::span<const unsigned char>(kWebp));
    bool ok = checkPrefixesRejected(bytes, ImageFormat::Webp, "WebP");

    std::vector<unsigned char> trailing = kWebp;
    trailing.insert(trailing.end(), {0, 1, 2});
    std::string error;
    if (!gltfTexture::CheckWebp(std::as_bytes(std::span<const unsigned char>(trailing)), error))
    {
        std::cerr << "WebP with trailing bytes: unexpected error: " << error << "\n";
        ok = false;
    }

    std::vector<std::byte> png;
    if (!gltfTexture::DecodeWebpToPng(bytes, png, error))
    {
        std::cerr << "WebP: unexpected error: " << error << "\n";
        return false;
    }
    ImageFormat format = ImageFormat::Jpeg;
    constexpr std::size_t kData = 8 + 25 + 8 + 2 + 5;
    constexpr std::size_t kRow = 16 * 4 + 1;
    if (!gltfTexture::DetectImageFormat(png, format) || format != ImageFormat::Png ||
        png.size() < kData + 16 * kRow || !gltfTexture::CheckImage(png, format, error))
    {
        std::cerr << "WebP: unexpected PNG\n";
        return false;
    }
    const std::array<std::pair<std::array<std::size_t, 2>, std::array<int, 4>>, 4> expected = {{
        {{4, 4}, {255, 0, 0, 255}},
        {{12, 4}, {0, 255, 0, 255}},
        {{4, 12}, {0, 0, 255, 64}},
        {{12, 12}, {255, 255, 255, 64}},
    }};
    for (const auto& [point, rgba] : expected)
    {
        const auto offset = kData + point[1] * kRow + 1 + point[0] * 4;
        for (std::size_t channel = 0; channel < 4; ++channel)
        {
            if (std::to_integer<int>(png[offset + channel]) != rgba[channel])
            {
                std::cerr << "WebP: pixel " << point[0] << ", " << point[1] << " differs\n";
                ok = false;
            }
        }
    }
    return ok;
}

bool
checkWebpStructure()
{
    bool ok = checkWebpRejected({}, "empty WebP");
    ok = checkWebpRejected({'R', 'I', 'F', 'F', 4, 0, 0, 0, 'W', 'E', 'B', 'P'},
                           "WebP header alone") &&
         ok;
    // A RIFF size that wraps when the header is added.
    ok = checkWebpRejected({'R', 'I', 'F', 'F', 0xFF, 0xFF, 0xFF, 0xFF, 'W', 'E',
                            'B', 'P', 'V', 'P', '8',  'L',  0,    0,    0,   0},
                           "WebP RIFF size") &&
         ok;
    // A chunk that claims more than the RIFF payload holds.
    auto chunk = kWebp;
    chunk[16] = 0xFF;
    chunk[17] = 0xFF;
    ok = checkWebpRejected(chunk, "WebP chunk size") && ok;
    // A chunk header cut by the end of the RIFF payload.
    auto cut = kWebp;
    cut.insert(cut.end(), {'E', 'X', 'I', 'F'});
    cut[4] = std::uint8_t(cut.size() - 8);
    ok = checkWebpRejected(cut, "WebP chunk header") && ok;
    // The VP8L signature byte.
    auto signature = kWebp;
    signature[20] = 0;
    ok = checkWebpRejected(signature, "WebP bitstream header") && ok;
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

bool
checkDecode(std::span<const std::byte> bytes, ImageFormat format, bool requireSuccess)
{
    std::vector<std::byte> png;
    std::string error;
    if (!gltfTexture::ConvertToPng(bytes, format, png, error))
    {
        return !requireSuccess && !error.empty() && png.empty();
    }
    return gltfTexture::DetectImageFormat(png, format) && format == ImageFormat::Png &&
           gltfTexture::CheckImage(png, format, error);
}

bool
checkMutations(const std::filesystem::path& fixture, ImageFormat format, std::size_t payload = 0)
{
    std::ifstream input(fixture, std::ios::binary);
    std::vector<char> data(std::istreambuf_iterator<char>{input}, {});
    const auto bytes = std::as_writable_bytes(std::span(data));
    ImageFormat detected;
    std::string error;
    if (!gltfTexture::DetectImageFormat(bytes, detected) || detected != format ||
        !gltfTexture::CheckImage(bytes, format, error) ||
        (payload && (payload >= bytes.size() || !checkDecode(bytes, format, true))))
    {
        std::cerr << fixture << ": invalid fuzz seed: " << error << '\n';
        return false;
    }

    // Flip each bit once. Only mutate the known payload for decode tests so that dimensions
    // and the amount of memory needed for a successful decode stay fixed.
    for (std::size_t offset = 0; offset < bytes.size(); ++offset)
    {
        for (unsigned bit = 0; bit < 8; ++bit)
        {
            const auto mask = std::byte(1u << bit);
            bytes[offset] ^= mask;
            if (gltfTexture::DetectImageFormat(bytes, detected))
            {
                error.clear();
                const bool accepted = gltfTexture::CheckImage(bytes, format, error);
                if (detected != format || (!accepted && error.empty()) ||
                    (accepted && format == ImageFormat::Png) ||
                    (payload && offset >= payload && !checkDecode(bytes, format, false)))
                {
                    std::cerr << fixture << ": unexpected result at byte " << offset << ", bit "
                              << bit << ": " << error << '\n';
                    return false;
                }
            }
            bytes[offset] ^= mask;
        }
    }
    return true;
}

} // namespace

int
main(int argc, char** argv)
{
    if (argc != 2)
    {
        std::cerr << "usage: image_test <texture fixture directory>\n";
        return 1;
    }
    const auto fixtures = std::filesystem::u8path(argv[1]);
    bool ok =
        checkDetect({0x89, 'P', 'N', 'G', '\r', '\n', 0x1A, '\n'}, true, ImageFormat::Png, "PNG");
    ok = checkDetect({0xFF, 0xD8, 0xFF}, true, ImageFormat::Jpeg, "JPEG") && ok;
    ok = checkDetect({0x89, 'P', 'N', 'G'}, false, ImageFormat::Png, "short PNG") && ok;
    ok = checkDetect({0xFF, 0xD8}, false, ImageFormat::Jpeg, "short JPEG") && ok;
    ok = checkDetect({}, false, ImageFormat::Png, "empty") && ok;
    ok = checkDetect({'R', 'I', 'F', 'F'}, false, ImageFormat::Png, "short WebP") && ok;
    ok = checkDetect({'R', 'I', 'F', 'F', 0, 0, 0, 0, 'W', 'E', 'B', 'P'}, true, ImageFormat::Webp,
                     "WebP") &&
         ok;
    ok = checkDetect({'R', 'I', 'F', 'F', 0, 0, 0, 0, 'W', 'A', 'V', 'E'}, false, ImageFormat::Png,
                     "RIFF of another form") &&
         ok;
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
    // WebP is served as PNG.
    ok = checkEntry(gltfTexture::MakePackageEntry(5, ImageFormat::Webp), true, 5) && ok;
    ok = checkEntry("images/5.webp", false) && ok;
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
    ok = checkWebp() && ok;
    ok = checkWebpStructure() && ok;
    ok = checkMutations(fixtures / "orientation.png", ImageFormat::Png) && ok;
    ok = checkMutations(fixtures / "flat.jpg", ImageFormat::Jpeg) && ok;
    // Payload offsets in these seeds follow their VP8L header or come from the KTX2 level index.
    ok = checkMutations(fixtures / "lossless_alpha.webp", ImageFormat::Webp, 25) && ok;
    ok = checkMutations(fixtures / "etc1s_alpha.ktx2", ImageFormat::Ktx2, 344) && ok;
    ok = checkMutations(fixtures / "uastc_zstd.ktx2", ImageFormat::Ktx2, 184) && ok;
    return ok ? 0 : 1;
}
