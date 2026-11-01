// SPDX-License-Identifier: Apache-2.0
#include "image.h"

#include <array>
#include <cstddef>
#include <iostream>
#include <limits>
#include <span>
#include <string>
#include <utility>
#include <vector>

namespace
{

using gltfTexture::ImageFormat;

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

    ok = checkEntry(gltfTexture::MakePackageEntry(0, ImageFormat::Png), true, 0) && ok;
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
    std::span<const std::byte> bytes;
    ImageFormat format;
    std::string error;
    if (gltfTexture::GetEmbeddedImage(asset, 0, {}, bytes, format, error) || error.empty())
    {
        std::cerr << "out-of-range image: expected an error\n";
        ok = false;
    }
    return ok ? 0 : 1;
}
