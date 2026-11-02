// SPDX-License-Identifier: Apache-2.0
#include "parse.h"

#include <simdjson.h>

#include <algorithm>
#include <cstdint>
#include <string_view>

namespace gltfCore
{
namespace
{

bool
isSupportedRequired(std::string_view extension)
{
    return extension == fastgltf::extensions::KHR_texture_basisu;
}

std::string_view
jsonOf(const std::byte* bytes, std::size_t size)
{
    constexpr std::size_t kHeaderAndChunk = 20;
    const std::string_view text(reinterpret_cast<const char*>(bytes), size);
    if (!text.starts_with("glTF"))
    {
        return text;
    }
    if (size < kHeaderAndChunk)
    {
        return {};
    }
    std::uint32_t length = 0;
    for (std::size_t i = 0; i < 4; ++i)
    {
        length |= std::uint32_t(std::to_integer<std::uint8_t>(bytes[12 + i])) << (8 * i);
    }
    return length <= size - kHeaderAndChunk ? text.substr(kHeaderAndChunk, length)
                                            : std::string_view{};
}

// fastgltf stops at the first such extension without naming it, so this reads them from the JSON.
// Returns an empty string if they cannot be read.
std::string
describeRequiredExtensions(const std::byte* bytes, std::size_t size)
{
    const auto json = jsonOf(bytes, size);
    simdjson::dom::parser parser;
    simdjson::dom::element root;
    simdjson::dom::array required;
    if (parser.parse(json.data(), json.size()).get(root) != simdjson::SUCCESS ||
        root["extensionsRequired"].get_array().get(required) != simdjson::SUCCESS)
    {
        return {};
    }
    std::string names;
    for (const auto extension : required)
    {
        std::string_view name;
        if (extension.get_string().get(name) == simdjson::SUCCESS && !isSupportedRequired(name))
        {
            names += (names.empty() ? "" : ", ") + std::string(name);
        }
    }
    return names.empty() ? names : "unsupported required extensions: " + names;
}

} // namespace

fastgltf::Expected<fastgltf::Asset>
Parse(const std::byte* bytes, std::size_t size, std::string& error)
{
    auto data = fastgltf::GltfDataBuffer::FromBytes(bytes, size);
    if (data.error() != fastgltf::Error::None)
    {
        error = fastgltf::getErrorMessage(data.error());
        return data.error();
    }
    // Parse light references so the scene converter can reject selected lights.
    // Enabling parsing does not mean the extension is supported.
    fastgltf::Parser parser(fastgltf::Extensions::KHR_lights_punctual |
                            fastgltf::Extensions::KHR_texture_basisu);
    auto asset = parser.loadGltf(data.get(), {}, fastgltf::Options::None);
    auto result = asset.error();
    if (result == fastgltf::Error::None &&
        !std::all_of(asset->extensionsRequired.begin(), asset->extensionsRequired.end(),
                     [](const std::string& extension) { return isSupportedRequired(extension); }))
    {
        result = fastgltf::Error::MissingExtensions;
    }
    if (result == fastgltf::Error::None)
    {
        return asset;
    }
    if (result == fastgltf::Error::MissingExtensions ||
        result == fastgltf::Error::UnknownRequiredExtension)
    {
        error = describeRequiredExtensions(bytes, size);
    }
    if (error.empty())
    {
        error = fastgltf::getErrorMessage(result);
    }
    return result;
}

} // namespace gltfCore
