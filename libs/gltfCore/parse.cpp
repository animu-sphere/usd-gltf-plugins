// SPDX-License-Identifier: Apache-2.0
#include "parse.h"

#include <simdjson.h>

#include <algorithm>
#include <cmath>
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

// Whether a number fastgltf reads as a float would overflow it.
bool
overflowsFloat(double number)
{
    return !std::isfinite(float(number));
}

// Describes a field of KHR_materials_clearcoat that fastgltf rejects, or returns an empty string.
std::string
describeMalformedClearcoat(const simdjson::dom::element& clearcoat)
{
    simdjson::dom::object object;
    if (clearcoat.get_object().get(object) != simdjson::SUCCESS)
    {
        return "KHR_materials_clearcoat is not an object";
    }
    for (const auto field : object)
    {
        double number = 0;
        if (field.key == "clearcoatFactor" || field.key == "clearcoatRoughnessFactor")
        {
            if (field.value.get_double().get(number) != simdjson::SUCCESS)
            {
                return "KHR_materials_clearcoat has a field of the wrong type";
            }
            if (overflowsFloat(number))
            {
                return "KHR_materials_clearcoat is out of range";
            }
        }
        else if (field.key == "clearcoatTexture" || field.key == "clearcoatRoughnessTexture" ||
                 field.key == "clearcoatNormalTexture")
        {
            simdjson::dom::element value;
            if (!field.value.is_object() || !field.value["index"].is_uint64() ||
                (field.value["texCoord"].get(value) == simdjson::SUCCESS && !value.is_uint64()) ||
                (field.value["scale"].get(value) == simdjson::SUCCESS && !value.is_number()))
            {
                return "KHR_materials_clearcoat has a field of the wrong type";
            }
            if (field.value["scale"].get_double().get(number) == simdjson::SUCCESS &&
                overflowsFloat(number))
            {
                return "KHR_materials_clearcoat is out of range";
            }
        }
    }
    return {};
}

// fastgltf rejects a material extension of the wrong type without saying why. Returns an empty
// string if no such extension is found.
std::string
describeMalformedMaterial(const std::byte* bytes, std::size_t size)
{
    const auto json = jsonOf(bytes, size);
    simdjson::dom::parser parser;
    simdjson::dom::element root;
    simdjson::dom::array materials;
    if (parser.parse(json.data(), json.size()).get(root) != simdjson::SUCCESS ||
        root["materials"].get_array().get(materials) != simdjson::SUCCESS)
    {
        return {};
    }
    for (const auto material : materials)
    {
        simdjson::dom::element unlit;
        if (material["extensions"][fastgltf::extensions::KHR_materials_unlit].get(unlit) ==
                simdjson::SUCCESS &&
            !unlit.is_object())
        {
            return "KHR_materials_unlit is not an object";
        }
        simdjson::dom::element ior;
        if (material["extensions"][fastgltf::extensions::KHR_materials_ior].get(ior) ==
            simdjson::SUCCESS)
        {
            simdjson::dom::element value;
            double number = 0;
            if (!ior.is_object())
            {
                return "KHR_materials_ior is not an object";
            }
            if (ior["ior"].get(value) == simdjson::SUCCESS)
            {
                if (value.get_double().get(number) != simdjson::SUCCESS)
                {
                    return "KHR_materials_ior has a field of the wrong type";
                }
                // fastgltf reads it as a float.
                if (overflowsFloat(number))
                {
                    return "KHR_materials_ior is out of range";
                }
            }
        }
        simdjson::dom::element clearcoat;
        if (material["extensions"][fastgltf::extensions::KHR_materials_clearcoat].get(clearcoat) ==
            simdjson::SUCCESS)
        {
            if (auto description = describeMalformedClearcoat(clearcoat); !description.empty())
            {
                return description;
            }
        }
    }
    return {};
}

} // namespace

bool
IsImplementedExtension(std::string_view extension)
{
    return extension == fastgltf::extensions::EXT_texture_webp ||
           extension == fastgltf::extensions::KHR_lights_punctual ||
           extension == fastgltf::extensions::KHR_materials_clearcoat ||
           extension == fastgltf::extensions::KHR_materials_ior ||
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
        fastgltf::Extensions::KHR_materials_clearcoat | fastgltf::Extensions::KHR_materials_ior |
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
    if (result == fastgltf::Error::InvalidGltf || result == fastgltf::Error::InvalidJson)
    {
        error = describeMalformedMaterial(bytes, size);
    }
    if (error.empty())
    {
        error = fastgltf::getErrorMessage(result);
    }
    return result;
}

} // namespace gltfCore
