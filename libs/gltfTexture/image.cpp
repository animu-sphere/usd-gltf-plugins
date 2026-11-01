// SPDX-License-Identifier: Apache-2.0
#include "image.h"

#include "localFile.h"

#include <algorithm>
#include <array>
#include <cctype>
#include <charconv>
#include <filesystem>
#include <fstream>
#include <variant>

namespace gltfTexture
{
namespace
{

constexpr std::string_view kEntryPrefix = "images/";

const char*
extensionOf(ImageFormat format)
{
    return format == ImageFormat::Png ? ".png" : ".jpg";
}

bool
locateExternalImage(const fastgltf::URI& uri, const std::string& sourcePath,
                    ImageLocation& location, std::string& error)
{
    std::filesystem::path target;
    if (!gltfCore::ResolveLocalFile(uri, sourcePath, "external image", target, location.path,
                                    error))
    {
        return false;
    }
    std::array<char, 8> header{};
    std::ifstream input(target, std::ios::binary);
    input.read(header.data(), header.size());
    ImageFormat format;
    if (!DetectImageFormat(std::as_bytes(std::span(header).first(std::size_t(input.gcount()))),
                           format))
    {
        error = "external image is not PNG or JPEG";
        return false;
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
    location.embedded = false;
    return true;
}

} // namespace

bool
DetectImageFormat(std::span<const std::byte> bytes, ImageFormat& format)
{
    static constexpr std::array<unsigned char, 8> png = {0x89, 'P',  'N',  'G',
                                                         '\r', '\n', 0x1A, '\n'};
    static constexpr std::array<unsigned char, 3> jpeg = {0xFF, 0xD8, 0xFF};
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
GetEmbeddedImage(const fastgltf::Asset& asset, std::size_t imageIndex,
                 const gltfCore::BufferLoader& loadBuffer, std::span<const std::byte>& bytes,
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
    else
    {
        error = "image is not embedded";
        return false;
    }
    if (!DetectImageFormat(bytes, format))
    {
        error = "embedded image is not PNG or JPEG";
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
        return locateExternalImage(*uri->uri, sourcePath, location, error);
    }
    std::span<const std::byte> bytes;
    ImageFormat format;
    if (!GetEmbeddedImage(asset, imageIndex, loadBuffer, bytes, format, error))
    {
        return false;
    }
    location.path = MakePackageEntry(imageIndex, format);
    location.embedded = true;
    return true;
}

} // namespace gltfTexture
