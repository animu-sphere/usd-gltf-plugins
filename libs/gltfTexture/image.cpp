// SPDX-License-Identifier: Apache-2.0
#include "image.h"

#include "imageStructure.h"
#include "ktx2.h"
#include "localFile.h"

#include <algorithm>
#include <array>
#include <cctype>
#include <charconv>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <new>
#include <system_error>
#include <variant>

namespace gltfTexture
{
namespace
{

constexpr std::string_view kEntryPrefix = "images/";
// The longest signature, which is KTX2's.
constexpr std::size_t kSignatureSize = 12;
// External images are read whole, and an image within the dimension limit fits this
// unless it is padded or stored with very little compression.
constexpr std::uintmax_t kMaxImageFileSize = std::uintmax_t(2) << 30;

const char*
extensionOf(ImageFormat format)
{
    return ServedFormat(format) == ImageFormat::Png ? ".png" : ".jpg";
}

bool
readFile(const std::filesystem::path& target, std::vector<std::byte>& bytes, std::string& error)
{
    std::error_code ec;
    const auto size = std::filesystem::file_size(target, ec);
    std::ifstream input(target, std::ios::binary);
    if (ec || !input)
    {
        error = "cannot read external image";
        return false;
    }
    if (size > kMaxImageFileSize)
    {
        error = "external image is larger than 2 GiB";
        return false;
    }
    try
    {
        bytes.resize(std::size_t(size));
    }
    catch (const std::bad_alloc&)
    {
        error = "not enough memory to read external image";
        return false;
    }
    input.read(reinterpret_cast<char*>(bytes.data()), std::streamsize(bytes.size()));
    if (std::size_t(input.gcount()) != bytes.size())
    {
        error = "cannot read external image";
        return false;
    }
    return true;
}

bool
locateExternalImage(const fastgltf::URI& uri, std::size_t imageIndex, const std::string& sourcePath,
                    ImageLocation& location, std::string& error)
{
    std::filesystem::path target;
    if (!gltfCore::ResolveLocalFile(uri, sourcePath, "external image", target, location.path,
                                    error))
    {
        return false;
    }
    std::vector<std::byte> bytes;
    if (!readFile(target, bytes, error))
    {
        return false;
    }
    ImageFormat format;
    if (!DetectImageFormat(bytes, format))
    {
        error = "external image is not PNG, JPEG or KTX2";
        return false;
    }
    location.format = format;
    if (format == ImageFormat::Ktx2)
    {
        if (!CheckKtx2(bytes, error))
        {
            return false;
        }
        location.path = MakePackageEntry(imageIndex, format);
        location.packaged = true;
        return true;
    }
    auto extension = target.extension().string();
    std::transform(extension.begin(), extension.end(), extension.begin(),
                   [](unsigned char c) { return char(std::tolower(c)); });
    // Hio chooses a decoder from the file extension.
    if (extension != extensionOf(format) && !(format == ImageFormat::Jpeg && extension == ".jpeg"))
    {
        error = "external image file extension does not match its PNG or JPEG content";
        return false;
    }
    if (!CheckImage(bytes, format, error))
    {
        return false;
    }
    location.packaged = false;
    return true;
}

} // namespace

ImageFormat
ServedFormat(ImageFormat format)
{
    return format == ImageFormat::Ktx2 ? ImageFormat::Png : format;
}

bool
DetectImageFormat(std::span<const std::byte> bytes, ImageFormat& format)
{
    static constexpr std::array<unsigned char, 8> png = {0x89, 'P',  'N',  'G',
                                                         '\r', '\n', 0x1A, '\n'};
    static constexpr std::array<unsigned char, 3> jpeg = {0xFF, 0xD8, 0xFF};
    static constexpr std::array<unsigned char, kSignatureSize> ktx2 = {
        0xAB, 'K', 'T', 'X', ' ', '2', '0', 0xBB, '\r', '\n', 0x1A, '\n'};
    const auto startsWith = [bytes](const auto& signature)
    {
        return bytes.size() >= signature.size() &&
               std::equal(signature.begin(), signature.end(), bytes.begin(),
                          [](unsigned char a, std::byte b)
                          { return a == std::to_integer<unsigned char>(b); });
    };
    if (startsWith(png))
    {
        format = ImageFormat::Png;
        return true;
    }
    if (startsWith(jpeg))
    {
        format = ImageFormat::Jpeg;
        return true;
    }
    if (startsWith(ktx2))
    {
        format = ImageFormat::Ktx2;
        return true;
    }
    return false;
}

bool
CheckImage(std::span<const std::byte> bytes, ImageFormat format, std::string& error)
{
    switch (format)
    {
    case ImageFormat::Png:
        return CheckPng(bytes, error);
    case ImageFormat::Jpeg:
        return CheckJpeg(bytes, error);
    case ImageFormat::Ktx2:
        return CheckKtx2(bytes, error);
    }
    return false;
}

std::string
MakePackageEntry(std::size_t imageIndex, ImageFormat format)
{
    return std::string(kEntryPrefix) + std::to_string(imageIndex) + extensionOf(format);
}

bool
ParsePackageEntry(std::string_view entry, std::size_t& imageIndex, ImageFormat& format)
{
    if (!entry.starts_with(kEntryPrefix))
    {
        return false;
    }
    entry.remove_prefix(kEntryPrefix.size());
    const auto parsed = std::from_chars(entry.data(), entry.data() + entry.size(), imageIndex);
    const auto digits = std::size_t(parsed.ptr - entry.data());
    if (parsed.ec != std::errc{} || (digits > 1 && entry[0] == '0'))
    {
        return false;
    }
    const auto extension = entry.substr(digits);
    if (extension == extensionOf(ImageFormat::Png))
    {
        format = ImageFormat::Png;
        return true;
    }
    if (extension == extensionOf(ImageFormat::Jpeg))
    {
        format = ImageFormat::Jpeg;
        return true;
    }
    return false;
}

bool
GetPackagedImage(const fastgltf::Asset& asset, std::size_t imageIndex,
                 const std::string& sourcePath, const gltfCore::BufferLoader& loadBuffer,
                 std::vector<std::byte>& owned, std::span<const std::byte>& bytes,
                 ImageFormat& format, std::string& error)
{
    if (imageIndex >= asset.images.size())
    {
        error = "image index is out of range";
        return false;
    }
    const auto& data = asset.images[imageIndex].data;
    if (const auto* view = std::get_if<fastgltf::sources::BufferView>(&data))
    {
        if (!gltfCore::GetBufferViewBytes(asset, view->bufferViewIndex, bytes, error, loadBuffer))
        {
            return false;
        }
    }
    else if (const auto* array = std::get_if<fastgltf::sources::Array>(&data))
    {
        bytes = {array->bytes.data(), array->bytes.size()};
    }
    else if (const auto* vector = std::get_if<fastgltf::sources::Vector>(&data))
    {
        bytes = {vector->bytes.data(), vector->bytes.size()};
    }
    else if (const auto* uri = std::get_if<fastgltf::sources::URI>(&data))
    {
        std::filesystem::path target;
        std::string relative;
        if (!gltfCore::ResolveLocalFile(*uri->uri, sourcePath, "external image", target, relative,
                                        error) ||
            !readFile(target, owned, error))
        {
            return false;
        }
        bytes = owned;
        if (!DetectImageFormat(bytes, format) || format != ImageFormat::Ktx2)
        {
            error = "external image is read directly, not through the package";
            return false;
        }
        return true;
    }
    else
    {
        error = "image has no data";
        return false;
    }
    if (!DetectImageFormat(bytes, format))
    {
        error = "embedded image is not PNG, JPEG or KTX2";
        return false;
    }
    return true;
}

bool
LocateImage(const fastgltf::Asset& asset, std::size_t imageIndex, const std::string& sourcePath,
            const gltfCore::BufferLoader& loadBuffer, ImageLocation& location, std::string& error)
{
    if (imageIndex >= asset.images.size())
    {
        error = "image index is out of range";
        return false;
    }
    if (const auto* uri = std::get_if<fastgltf::sources::URI>(&asset.images[imageIndex].data))
    {
        return locateExternalImage(*uri->uri, imageIndex, sourcePath, location, error);
    }
    std::vector<std::byte> owned;
    std::span<const std::byte> bytes;
    ImageFormat format;
    if (!GetPackagedImage(asset, imageIndex, sourcePath, loadBuffer, owned, bytes, format, error) ||
        !CheckImage(bytes, format, error))
    {
        return false;
    }
    location.path = MakePackageEntry(imageIndex, format);
    location.packaged = true;
    location.format = format;
    return true;
}

} // namespace gltfTexture
