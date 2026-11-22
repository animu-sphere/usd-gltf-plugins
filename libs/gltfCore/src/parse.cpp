// SPDX-License-Identifier: Apache-2.0
#include "gltfCore/parse.h"

#include <simdjson.h>

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <initializer_list>
#include <string>
#include <string_view>
#include <unordered_set>

namespace gltfCore
{
namespace
{

constexpr std::size_t kGlbHeaderSize = 12;
constexpr std::size_t kChunkHeaderSize = 8;
constexpr std::size_t kGlbJsonOffset = kGlbHeaderSize + kChunkHeaderSize;

std::uint32_t
readUint32LE(const std::byte* bytes)
{
    std::uint32_t value = 0;
    for (std::size_t i = 0; i < 4; ++i)
    {
        value |= std::uint32_t(std::to_integer<std::uint8_t>(bytes[i])) << (8 * i);
    }
    return value;
}

// fastgltf can accept chunks that extend past the declared GLB length or leave a partial chunk
// at the end. Check the framing against the actual size before handing it any chunk data.
bool
hasValidGlbLengths(const std::byte* bytes, std::size_t size)
{
    if (size < kGlbJsonOffset || readUint32LE(bytes + 8) != size)
    {
        return false;
    }
    for (std::size_t offset = kGlbHeaderSize; offset < size;)
    {
        if (size - offset < kChunkHeaderSize)
        {
            return false;
        }
        const auto length = readUint32LE(bytes + offset);
        offset += kChunkHeaderSize;
        if (length % 4 != 0 || length > size - offset)
        {
            return false;
        }
        offset += length;
    }
    return true;
}

std::string_view
jsonOf(const std::byte* bytes, std::size_t size)
{
    const std::string_view text(reinterpret_cast<const char*>(bytes), size);
    if (!text.starts_with("glTF"))
    {
        return text;
    }
    if (size < kGlbJsonOffset)
    {
        return {};
    }
    const auto length = readUint32LE(bytes + kGlbHeaderSize);
    return length <= size - kGlbJsonOffset ? text.substr(kGlbJsonOffset, length)
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

// fastgltf rejects malformed visibility values without naming the extension or node.
std::string
describeMalformedNodeVisibility(const std::byte* bytes, std::size_t size)
{
    const auto json = jsonOf(bytes, size);
    simdjson::dom::parser parser;
    simdjson::dom::element root;
    simdjson::dom::array nodes;
    if (parser.parse(json.data(), json.size()).get(root) != simdjson::SUCCESS ||
        root["nodes"].get_array().get(nodes) != simdjson::SUCCESS)
    {
        return {};
    }
    std::size_t index = 0;
    for (const auto node : nodes)
    {
        simdjson::dom::element extension;
        if (node["extensions"][fastgltf::extensions::KHR_node_visibility].get(extension) ==
            simdjson::SUCCESS)
        {
            const auto prefix = "glTF node " + std::to_string(index) + ": KHR_node_visibility";
            if (!extension.is_object())
            {
                return prefix + " must be an object";
            }
            simdjson::dom::element visible;
            if (extension["visible"].get(visible) == simdjson::SUCCESS && !visible.is_bool())
            {
                return prefix + " visible must be a boolean";
            }
        }
        ++index;
    }
    return {};
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
    const auto extensions = material["extensions"];
    constexpr auto emissiveStrengthExtension =
        fastgltf::extensions::KHR_materials_emissive_strength;
    simdjson::dom::element emissive;
    if (extensions[emissiveStrengthExtension].get(emissive) == simdjson::SUCCESS)
    {
        if (auto description = describeMalformedMaterialExtension(
                emissiveStrengthExtension, emissive, {"emissiveStrength"}, {});
            !description.empty())
        {
            return description;
        }
        // fastgltf can round tiny negative values to zero, so check the JSON number.
        double value = 1.0;
        if (emissive["emissiveStrength"].get_double().get(value) == simdjson::SUCCESS && value < 0)
        {
            return "KHR_materials_emissive_strength must be non-negative";
        }
        simdjson::dom::element unlit;
        if (extensions[fastgltf::extensions::KHR_materials_unlit].get(unlit) == simdjson::SUCCESS)
        {
            return "KHR_materials_emissive_strength cannot be combined with KHR_materials_unlit";
        }
    }
    simdjson::dom::element dispersion;
    if (extensions[fastgltf::extensions::KHR_materials_dispersion].get(dispersion) ==
        simdjson::SUCCESS)
    {
        if (auto description = describeMalformedMaterialExtension(
                fastgltf::extensions::KHR_materials_dispersion, dispersion, {"dispersion"}, {});
            !description.empty())
        {
            return description;
        }
        // fastgltf can round tiny negative values to zero, so check the JSON number.
        double value = 0.0;
        if (dispersion["dispersion"].get_double().get(value) == simdjson::SUCCESS && value < 0)
        {
            return "KHR_materials_dispersion must be non-negative";
        }
    }
    simdjson::dom::element anisotropy;
    if (material["extensions"][fastgltf::extensions::KHR_materials_anisotropy].get(anisotropy) ==
        simdjson::SUCCESS)
    {
        if (auto description = describeMalformedMaterialExtension(
                fastgltf::extensions::KHR_materials_anisotropy, anisotropy,
                {"anisotropyStrength", "anisotropyRotation"}, {"anisotropyTexture"});
            !description.empty())
        {
            return description;
        }
    }
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

// Fastgltf does not retain the fallback tag and can skip a non-object extension.
std::string
describeMalformedMeshopt(const simdjson::dom::element& root)
{
    using namespace simdjson;
    constexpr auto name = fastgltf::extensions::EXT_meshopt_compression;
    std::unordered_set<std::uint64_t> fallbackBuffers;
    dom::array buffers;
    if (root["buffers"].get_array().get(buffers) == SUCCESS)
    {
        std::uint64_t index = 0;
        for (const auto buffer : buffers)
        {
            dom::element extension;
            if (buffer["extensions"][name].get(extension) == SUCCESS)
            {
                bool fallback = false;
                const auto field = extension["fallback"].get_bool().get(fallback);
                if (!extension.is_object() || (field != SUCCESS && field != NO_SUCH_FIELD))
                {
                    return "EXT_meshopt_compression has an invalid buffer fallback tag";
                }
                if (fallback)
                {
                    fallbackBuffers.insert(index);
                }
            }
            ++index;
        }
    }

    bool declared = false;
    dom::array used;
    if (root["extensionsUsed"].get_array().get(used) == SUCCESS)
    {
        for (const auto extension : used)
        {
            std::string_view value;
            declared |= extension.get_string().get(value) == SUCCESS && value == name;
        }
    }
    dom::array views;
    if (root["bufferViews"].get_array().get(views) != SUCCESS)
    {
        return {};
    }
    std::size_t index = 0;
    for (const auto view : views)
    {
        const auto fail = [&](std::string_view reason)
        {
            return "EXT_meshopt_compression bufferView " + std::to_string(index) + ": " +
                   std::string(reason);
        };
        dom::element extension;
        if (view["extensions"][name].get(extension) == SUCCESS)
        {
            if (!extension.is_object() || !declared)
            {
                return fail("must be an object declared in extensionsUsed");
            }
            std::uint64_t compressedBuffer;
            std::string_view mode, filter;
            const auto filterResult = extension["filter"].get_string().get(filter);
            if (extension["buffer"].get_uint64().get(compressedBuffer) != SUCCESS ||
                !extension["byteLength"].is_uint64() || !extension["byteStride"].is_uint64() ||
                !extension["count"].is_uint64() ||
                (extension["byteOffset"].error() != NO_SUCH_FIELD &&
                 !extension["byteOffset"].is_uint64()) ||
                extension["mode"].get_string().get(mode) != SUCCESS ||
                (mode != "ATTRIBUTES" && mode != "TRIANGLES" && mode != "INDICES") ||
                (filterResult != NO_SUCH_FIELD &&
                 (filterResult != SUCCESS || (filter != "NONE" && filter != "OCTAHEDRAL" &&
                                              filter != "QUATERNION" && filter != "EXPONENTIAL"))))
            {
                return fail("invalid extension fields");
            }
            if (fallbackBuffers.contains(compressedBuffer))
            {
                return fail("compressed data cannot come from a tagged fallback buffer");
            }
        }
        else
        {
            std::uint64_t parentBuffer;
            if (view["buffer"].get_uint64().get(parentBuffer) == SUCCESS &&
                fallbackBuffers.contains(parentBuffer))
            {
                return fail("uncompressed view references a tagged fallback buffer");
            }
        }
        ++index;
    }
    return {};
}

// Fastgltf stores one material per variant, so repeated indices would silently overwrite one
// another. Check the source mappings, including those outside the selected scene.
std::string
describeMalformedMaterialVariants(const simdjson::dom::element& root)
{
    using namespace simdjson;
    constexpr auto name = fastgltf::extensions::KHR_materials_variants;
    const auto declaredIn = [&](std::string_view list)
    {
        dom::array extensions;
        if (root[list].get_array().get(extensions) == SUCCESS)
        {
            for (const auto extension : extensions)
            {
                std::string_view value;
                if (extension.get_string().get(value) == SUCCESS && value == name)
                {
                    return true;
                }
            }
        }
        return false;
    };
    const auto extension = root["extensions"][name];
    const bool declared = declaredIn("extensionsUsed");
    dom::array variants;
    std::size_t variantCount = 0;
    if (extension.error() == SUCCESS || declared || declaredIn("extensionsRequired"))
    {
        if (!declared)
        {
            return "KHR_materials_variants must be declared in extensionsUsed";
        }
        if (extension["variants"].get_array().get(variants) != SUCCESS || variants.size() == 0)
        {
            return "KHR_materials_variants requires a nonempty root variants array";
        }
        variantCount = variants.size();
        for (const auto variant : variants)
        {
            if (!variant["name"].is_string())
            {
                return "KHR_materials_variants variant name must be a string";
            }
        }
    }
    dom::array meshes, materials;
    if (root["meshes"].get_array().get(meshes) != SUCCESS)
    {
        return {};
    }
    const std::size_t materialCount =
        root["materials"].get_array().get(materials) == SUCCESS ? materials.size() : 0;
    std::size_t meshIndex = 0;
    for (const auto mesh : meshes)
    {
        dom::array primitives;
        if (mesh["primitives"].get_array().get(primitives) == SUCCESS)
        {
            std::size_t primitiveIndex = 0;
            for (const auto primitive : primitives)
            {
                const auto mappingExtension = primitive["extensions"][name];
                if (mappingExtension.error() == SUCCESS)
                {
                    const auto fail = [&](std::string_view reason)
                    {
                        return "KHR_materials_variants mesh " + std::to_string(meshIndex) +
                               " primitive " + std::to_string(primitiveIndex) + ": " +
                               std::string(reason);
                    };
                    if (variantCount == 0)
                    {
                        return fail("requires variants declared at the glTF root");
                    }
                    dom::array mappings;
                    if (mappingExtension["mappings"].get_array().get(mappings) != SUCCESS ||
                        mappings.size() == 0)
                    {
                        return fail("requires a nonempty mappings array");
                    }
                    std::unordered_set<std::uint64_t> seen;
                    for (const auto mapping : mappings)
                    {
                        std::uint64_t material;
                        dom::array indices;
                        if (mapping["material"].get_uint64().get(material) != SUCCESS ||
                            material >= materialCount)
                        {
                            return fail("mapping material index is invalid or out of range");
                        }
                        if (mapping["variants"].get_array().get(indices) != SUCCESS ||
                            indices.size() == 0)
                        {
                            return fail("mapping requires a nonempty variants array");
                        }
                        for (const auto index : indices)
                        {
                            std::uint64_t value;
                            if (index.get_uint64().get(value) != SUCCESS || value >= variantCount)
                            {
                                return fail("mapping variant index is invalid or out of range");
                            }
                            if (!seen.insert(value).second)
                            {
                                return fail("variant index occurs more than once");
                            }
                        }
                    }
                }
                ++primitiveIndex;
            }
        }
        ++meshIndex;
    }
    return {};
}

// KHR_animation_pointer requires the object that holds a targeted property, which fastgltf does not
// record for every material extension. Describes the first material pointer without one.
std::string
describeMissingPointerObject(const simdjson::dom::element& root)
{
    simdjson::dom::array animations;
    if (root["animations"].get_array().get(animations) != simdjson::SUCCESS)
    {
        return {};
    }
    std::size_t animationIndex = 0;
    for (const auto animation : animations)
    {
        simdjson::dom::array channels;
        std::size_t channelIndex = 0;
        if (animation["channels"].get_array().get(channels) == simdjson::SUCCESS)
        {
            for (const auto channel : channels)
            {
                // Conversion reports a pointer to a material that does not exist.
                constexpr std::string_view prefix = "/materials/";
                std::string_view pointer;
                simdjson::dom::object object;
                if (channel["target"]["extensions"][fastgltf::extensions::KHR_animation_pointer]
                           ["pointer"]
                               .get_string()
                               .get(pointer) == simdjson::SUCCESS &&
                    pointer.starts_with(prefix) &&
                    root.at_pointer(pointer.substr(0, pointer.find('/', prefix.size())))
                            .get_object()
                            .get(object) == simdjson::SUCCESS &&
                    root.at_pointer(pointer.substr(0, pointer.rfind('/'))).get_object().get(object) !=
                        simdjson::SUCCESS)
                {
                    return "glTF animation " + std::to_string(animationIndex) + " channel " +
                           std::to_string(channelIndex) + ": KHR_animation_pointer target " +
                           std::string(pointer) + " is in an object that the asset does not have";
                }
                ++channelIndex;
            }
        }
        ++animationIndex;
    }
    return {};
}

// Fastgltf keeps defaults for some missing material components and skips some invalid fields.
std::string
describeMalformedCheckedExtensions(const std::byte* bytes, std::size_t size)
{
    const auto json = jsonOf(bytes, size);
    simdjson::dom::parser parser;
    simdjson::dom::element root;
    simdjson::dom::array materials;
    if (parser.parse(json.data(), json.size()).get(root) != simdjson::SUCCESS)
    {
        return {};
    }
    if (auto description = describeMalformedMeshopt(root); !description.empty())
    {
        return description;
    }
    if (auto description = describeMalformedMaterialVariants(root); !description.empty())
    {
        return description;
    }
    if (auto description = describeMissingPointerObject(root); !description.empty())
    {
        return description;
    }
    if (root["materials"].get_array().get(materials) != simdjson::SUCCESS)
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
    return extension == fastgltf::extensions::EXT_mesh_gpu_instancing ||
           extension == fastgltf::extensions::EXT_meshopt_compression ||
           extension == fastgltf::extensions::EXT_texture_webp ||
           extension == fastgltf::extensions::KHR_animation_pointer ||
           extension == fastgltf::extensions::KHR_draco_mesh_compression ||
           extension == fastgltf::extensions::KHR_lights_punctual ||
           extension == fastgltf::extensions::KHR_materials_anisotropy ||
           extension == fastgltf::extensions::KHR_materials_clearcoat ||
           extension == fastgltf::extensions::KHR_materials_dispersion ||
           extension == fastgltf::extensions::KHR_materials_emissive_strength ||
           extension == fastgltf::extensions::KHR_materials_ior ||
           extension == fastgltf::extensions::KHR_materials_iridescence ||
           extension == fastgltf::extensions::KHR_materials_sheen ||
           extension == fastgltf::extensions::KHR_materials_specular ||
           extension == fastgltf::extensions::KHR_materials_transmission ||
           extension == fastgltf::extensions::KHR_materials_unlit ||
           extension == fastgltf::extensions::KHR_materials_variants ||
           extension == fastgltf::extensions::KHR_materials_volume ||
           extension == fastgltf::extensions::KHR_mesh_quantization ||
           extension == fastgltf::extensions::KHR_node_visibility ||
           extension == fastgltf::extensions::KHR_texture_basisu ||
           extension == fastgltf::extensions::KHR_texture_transform;
}

fastgltf::Expected<fastgltf::Asset>
Parse(const std::byte* bytes, std::size_t size, std::string& error)
{
    if (size >= 4 && std::memcmp(bytes, "glTF", 4) == 0 && !hasValidGlbLengths(bytes, size))
    {
        error = "invalid GLB file or chunk length";
        return fastgltf::Error::InvalidGLB;
    }
    auto data = fastgltf::GltfDataBuffer::FromBytes(bytes, size);
    if (data.error() != fastgltf::Error::None)
    {
        error = fastgltf::getErrorMessage(data.error());
        return data.error();
    }
    fastgltf::Parser parser(
        fastgltf::Extensions::EXT_mesh_gpu_instancing |
        fastgltf::Extensions::EXT_meshopt_compression | fastgltf::Extensions::EXT_texture_webp |
        fastgltf::Extensions::KHR_animation_pointer |
        fastgltf::Extensions::KHR_draco_mesh_compression |
        fastgltf::Extensions::KHR_lights_punctual | fastgltf::Extensions::KHR_materials_anisotropy |
        fastgltf::Extensions::KHR_materials_clearcoat |
        fastgltf::Extensions::KHR_materials_dispersion |
        fastgltf::Extensions::KHR_materials_emissive_strength |
        fastgltf::Extensions::KHR_materials_ior | fastgltf::Extensions::KHR_materials_iridescence |
        fastgltf::Extensions::KHR_materials_sheen | fastgltf::Extensions::KHR_materials_specular |
        fastgltf::Extensions::KHR_materials_transmission |
        fastgltf::Extensions::KHR_materials_unlit | fastgltf::Extensions::KHR_materials_variants |
        fastgltf::Extensions::KHR_materials_volume | fastgltf::Extensions::KHR_mesh_quantization |
        fastgltf::Extensions::KHR_node_visibility | fastgltf::Extensions::KHR_texture_basisu |
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
    if (result == fastgltf::Error::InvalidGltf || result == fastgltf::Error::InvalidJson ||
        result == fastgltf::Error::ValueOutOfRange)
    {
        error = describeMalformedNodeVisibility(bytes, size);
        if (error.empty())
        {
            error = describeMalformedCheckedExtensions(bytes, size);
        }
        if (error.empty())
        {
            error = describeMalformedMaterial(bytes, size);
        }
    }
    if (error.empty())
    {
        error = fastgltf::getErrorMessage(result);
    }
    return result;
}

} // namespace gltfCore
