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
        if (extension.get_string().get(name) == simdjson::SUCCESS && !IsImplementedExtension(name))
        {
            names += (names.empty() ? "" : ", ") + std::string(name);
        }
    }
    return names.empty() ? names : "unsupported required extensions: " + names;
}

bool
isNumberPair(const simdjson::dom::element& value)
{
    simdjson::dom::array pair;
    return value.get_array().get(pair) == simdjson::SUCCESS && pair.size() == 2 &&
           pair.at(0).is_number() && pair.at(1).is_number();
}

bool
isWellFormedTextureTransform(const simdjson::dom::object& transform)
{
    for (const auto field : transform)
    {
        const auto& value = field.value;
        if (((field.key == "offset" || field.key == "scale") && !isNumberPair(value)) ||
            (field.key == "rotation" && !value.is_number()) ||
            (field.key == "texCoord" && !value.is_uint64()))
        {
            return false;
        }
    }
    return true;
}

bool
textureTransformsAreWellFormed(const simdjson::dom::element& element)
{
    simdjson::dom::object object;
    if (element.get_object().get(object) == simdjson::SUCCESS)
    {
        for (const auto field : object)
        {
            simdjson::dom::object transform;
            if ((field.key == fastgltf::extensions::KHR_texture_transform &&
                 field.value.get_object().get(transform) == simdjson::SUCCESS &&
                 !isWellFormedTextureTransform(transform)) ||
                !textureTransformsAreWellFormed(field.value))
            {
                return false;
            }
        }
        return true;
    }
    simdjson::dom::array array;
    if (element.get_array().get(array) == simdjson::SUCCESS)
    {
        for (const auto item : array)
        {
            if (!textureTransformsAreWellFormed(item))
            {
                return false;
            }
        }
    }
    return true;
}

// fastgltf reads a field of this extension with the wrong type as if it were absent, which would
// show the texture untransformed.
bool
hasMalformedTextureTransform(const std::byte* bytes, std::size_t size)
{
    const auto json = jsonOf(bytes, size);
    simdjson::dom::parser parser;
    simdjson::dom::element root;
    simdjson::dom::element materials;
    return parser.parse(json.data(), json.size()).get(root) == simdjson::SUCCESS &&
           root["materials"].get(materials) == simdjson::SUCCESS &&
           !textureTransformsAreWellFormed(materials);
}

// fastgltf reads an intensity or range of the wrong type as if it were absent, which would
// show the light at intensity 1 or without its range.
bool
hasMalformedLight(const std::byte* bytes, std::size_t size)
{
    const auto json = jsonOf(bytes, size);
    simdjson::dom::parser parser;
    simdjson::dom::element root;
    simdjson::dom::array lights;
    if (parser.parse(json.data(), json.size()).get(root) != simdjson::SUCCESS ||
        root["extensions"][fastgltf::extensions::KHR_lights_punctual]["lights"].get_array().get(
            lights) != simdjson::SUCCESS)
    {
        return false;
    }
    for (const auto light : lights)
    {
        for (const char* field : {"intensity", "range"})
        {
            simdjson::dom::element value;
            if (light[field].get(value) == simdjson::SUCCESS && !value.is_number())
            {
                return true;
            }
        }
    }
    return false;
}

// fastgltf rejects such a material without saying why.
bool
hasMalformedUnlit(const std::byte* bytes, std::size_t size)
{
    const auto json = jsonOf(bytes, size);
    simdjson::dom::parser parser;
    simdjson::dom::element root;
    simdjson::dom::array materials;
    if (parser.parse(json.data(), json.size()).get(root) != simdjson::SUCCESS ||
        root["materials"].get_array().get(materials) != simdjson::SUCCESS)
    {
        return false;
    }
    for (const auto material : materials)
    {
        simdjson::dom::element unlit;
        if (material["extensions"][fastgltf::extensions::KHR_materials_unlit].get(unlit) ==
                simdjson::SUCCESS &&
            !unlit.is_object())
        {
            return true;
        }
    }
    return false;
}

} // namespace

bool
IsImplementedExtension(std::string_view extension)
{
    return extension == fastgltf::extensions::EXT_texture_webp ||
           extension == fastgltf::extensions::KHR_lights_punctual ||
           extension == fastgltf::extensions::KHR_materials_unlit ||
           extension == fastgltf::extensions::KHR_mesh_quantization ||
           extension == fastgltf::extensions::KHR_texture_basisu ||
           extension == fastgltf::extensions::KHR_texture_transform;
}

fastgltf::Expected<fastgltf::Asset>
Parse(const std::byte* bytes, std::size_t size, std::string& error)
{
    auto data = fastgltf::GltfDataBuffer::FromBytes(bytes, size);
    if (data.error() != fastgltf::Error::None)
    {
        error = fastgltf::getErrorMessage(data.error());
        return data.error();
    }
    fastgltf::Parser parser(
        fastgltf::Extensions::EXT_texture_webp | fastgltf::Extensions::KHR_lights_punctual |
        fastgltf::Extensions::KHR_materials_unlit | fastgltf::Extensions::KHR_mesh_quantization |
        fastgltf::Extensions::KHR_texture_basisu | fastgltf::Extensions::KHR_texture_transform);
    auto asset = parser.loadGltf(data.get(), {}, fastgltf::Options::None);
    auto result = asset.error();
    if (result == fastgltf::Error::None &&
        !std::all_of(asset->extensionsRequired.begin(), asset->extensionsRequired.end(),
                     [](const std::string& extension)
                     { return IsImplementedExtension(extension); }))
    {
        result = fastgltf::Error::MissingExtensions;
    }
    if (result == fastgltf::Error::None)
    {
        if (hasMalformedTextureTransform(bytes, size))
        {
            error = "KHR_texture_transform has a field of the wrong type";
            return fastgltf::Error::InvalidGltf;
        }
        if (hasMalformedLight(bytes, size))
        {
            error = "KHR_lights_punctual has a field of the wrong type";
            return fastgltf::Error::InvalidGltf;
        }
        return asset;
    }
    if (result == fastgltf::Error::MissingExtensions ||
        result == fastgltf::Error::UnknownRequiredExtension)
    {
        error = describeRequiredExtensions(bytes, size);
    }
    if (result == fastgltf::Error::InvalidGltf && hasMalformedUnlit(bytes, size))
    {
        error = "KHR_materials_unlit is not an object";
    }
    if (error.empty())
    {
        error = fastgltf::getErrorMessage(result);
    }
    return result;
}

} // namespace gltfCore
