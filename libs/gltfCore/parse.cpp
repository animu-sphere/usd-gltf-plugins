// SPDX-License-Identifier: Apache-2.0
#include "parse.h"

#include <simdjson.h>

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <initializer_list>
#include <string>
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

// Describes a malformed field of a material extension, or returns an empty string. `numbers` are
// its number fields, `textures` its texture info fields and `colors` its fields of three numbers.
std::string
describeMalformedMaterialExtension(std::string_view name, const simdjson::dom::element& extension,
                                   std::initializer_list<std::string_view> numbers,
                                   std::initializer_list<std::string_view> textures,
                                   std::initializer_list<std::string_view> colors = {})
{
    const auto describe = [name](const char* problem) { return std::string(name) + problem; };
    simdjson::dom::object object;
    if (extension.get_object().get(object) != simdjson::SUCCESS)
    {
        return describe(" is not an object");
    }
    const auto contains = [](std::initializer_list<std::string_view> keys, std::string_view key)
    { return std::find(keys.begin(), keys.end(), key) != keys.end(); };
    for (const auto field : object)
    {
        double number = 0;
        if (contains(numbers, field.key))
        {
            if (field.value.get_double().get(number) != simdjson::SUCCESS)
            {
                return describe(" has a field of the wrong type");
            }
            if (overflowsFloat(number))
            {
                return describe(" is out of range");
            }
        }
        else if (contains(textures, field.key))
        {
            simdjson::dom::element value;
            if (!field.value.is_object() || !field.value["index"].is_uint64() ||
                (field.value["texCoord"].get(value) == simdjson::SUCCESS && !value.is_uint64()) ||
                (field.value["scale"].get(value) == simdjson::SUCCESS && !value.is_number()))
            {
                return describe(" has a field of the wrong type");
            }
            if (field.value["scale"].get_double().get(number) == simdjson::SUCCESS &&
                overflowsFloat(number))
            {
                return describe(" is out of range");
            }
        }
        else if (contains(colors, field.key))
        {
            simdjson::dom::array color;
            if (field.value.get_array().get(color) != simdjson::SUCCESS || color.size() != 3)
            {
                return describe(" has a field of the wrong type");
            }
            for (const auto component : color)
            {
                if (component.get_double().get(number) != simdjson::SUCCESS)
                {
                    return describe(" has a field of the wrong type");
                }
                if (overflowsFloat(number))
                {
                    return describe(" is out of range");
                }
            }
        }
    }
    return {};
}

// Describes extensions checked even when fastgltf accepts them, or returns an empty string.
std::string
describeMalformedCheckedExtension(const simdjson::dom::element& material)
{
    simdjson::dom::element iridescence;
    if (material["extensions"][fastgltf::extensions::KHR_materials_iridescence].get(iridescence) ==
        simdjson::SUCCESS)
    {
        if (auto description = describeMalformedMaterialExtension(
                fastgltf::extensions::KHR_materials_iridescence, iridescence,
                {"iridescenceFactor", "iridescenceIor", "iridescenceThicknessMinimum",
                 "iridescenceThicknessMaximum"},
                {"iridescenceTexture", "iridescenceThicknessTexture"});
            !description.empty())
        {
            return description;
        }
    }
    simdjson::dom::element sheen;
    if (material["extensions"][fastgltf::extensions::KHR_materials_sheen].get(sheen) ==
        simdjson::SUCCESS)
    {
        if (auto description = describeMalformedMaterialExtension(
                fastgltf::extensions::KHR_materials_sheen, sheen, {"sheenRoughnessFactor"},
                {"sheenColorTexture", "sheenRoughnessTexture"}, {"sheenColorFactor"});
            !description.empty())
        {
            return description;
        }
    }
    simdjson::dom::element specular;
    if (material["extensions"][fastgltf::extensions::KHR_materials_specular].get(specular) ==
        simdjson::SUCCESS)
    {
        if (auto description = describeMalformedMaterialExtension(
                fastgltf::extensions::KHR_materials_specular, specular, {"specularFactor"},
                {"specularTexture", "specularColorTexture"}, {"specularColorFactor"});
            !description.empty())
        {
            return description;
        }
    }
    simdjson::dom::element volume;
    if (material["extensions"][fastgltf::extensions::KHR_materials_volume].get(volume) !=
        simdjson::SUCCESS)
    {
        return {};
    }
    return describeMalformedMaterialExtension(fastgltf::extensions::KHR_materials_volume, volume,
                                              {"thicknessFactor", "attenuationDistance"},
                                              {"thicknessTexture"}, {"attenuationColor"});
}

// fastgltf reads a sheenColorFactor, specularColorFactor or attenuationColor of fewer than three
// numbers, keeping the default for the rest. Also check the iridescence fields. Returns an
// empty string if every such extension is well formed.
std::string
describeMalformedCheckedExtensions(const std::byte* bytes, std::size_t size)
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
        if (auto description = describeMalformedCheckedExtension(material); !description.empty())
        {
            return description;
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
            if (auto description = describeMalformedMaterialExtension(
                    fastgltf::extensions::KHR_materials_clearcoat, clearcoat,
                    {"clearcoatFactor", "clearcoatRoughnessFactor"},
                    {"clearcoatTexture", "clearcoatRoughnessTexture", "clearcoatNormalTexture"});
                !description.empty())
            {
                return description;
            }
        }
        simdjson::dom::element transmission;
        if (material["extensions"][fastgltf::extensions::KHR_materials_transmission].get(
                transmission) == simdjson::SUCCESS)
        {
            if (auto description = describeMalformedMaterialExtension(
                    fastgltf::extensions::KHR_materials_transmission, transmission,
                    {"transmissionFactor"}, {"transmissionTexture"});
                !description.empty())
            {
                return description;
            }
        }
        if (auto description = describeMalformedCheckedExtension(material); !description.empty())
        {
            return description;
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
           extension == fastgltf::extensions::KHR_materials_iridescence ||
           extension == fastgltf::extensions::KHR_materials_sheen ||
           extension == fastgltf::extensions::KHR_materials_specular ||
           extension == fastgltf::extensions::KHR_materials_transmission ||
           extension == fastgltf::extensions::KHR_materials_unlit ||
           extension == fastgltf::extensions::KHR_materials_volume ||
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
        fastgltf::Extensions::KHR_materials_iridescence |
        fastgltf::Extensions::KHR_materials_sheen | fastgltf::Extensions::KHR_materials_specular |
        fastgltf::Extensions::KHR_materials_transmission |
        fastgltf::Extensions::KHR_materials_unlit | fastgltf::Extensions::KHR_materials_volume |
        fastgltf::Extensions::KHR_mesh_quantization | fastgltf::Extensions::KHR_texture_basisu |
        fastgltf::Extensions::KHR_texture_transform);
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
        if (auto description = describeMalformedCheckedExtensions(bytes, size);
            !description.empty())
        {
            error = description;
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
