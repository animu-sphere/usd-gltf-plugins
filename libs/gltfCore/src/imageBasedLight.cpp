// SPDX-License-Identifier: Apache-2.0
#include "gltfCore/imageBasedLight.h"

#include "json.h"

#include <simdjson.h>

#include <cmath>
#include <cstdint>
#include <utility>

namespace gltfCore
{
namespace
{

using simdjson::SUCCESS;

constexpr std::uint64_t kMaxSpecularImageSize = 16384;

bool
isDeclared(const simdjson::dom::element& root)
{
    simdjson::dom::array used;
    if (root["extensionsUsed"].get_array().get(used) != SUCCESS)
    {
        return false;
    }
    for (const auto name : used)
    {
        std::string_view text;
        if (name.get_string().get(text) == SUCCESS && text == kImageBasedLightExtension)
        {
            return true;
        }
    }
    return false;
}

bool
readNumber(const simdjson::dom::element& element, double& value)
{
    return element.get_double().get(value) == SUCCESS && std::isfinite(value);
}

std::size_t
levelCount(std::uint64_t size)
{
    std::size_t count = 1;
    while (size > 1)
    {
        size /= 2;
        ++count;
    }
    return count;
}

// Reads the light into `result`. Each value is read into a local before it is iterated, as in
// xmp.cpp. Returns what is malformed, or an empty string.
std::string
readLight(const simdjson::dom::element& source, std::size_t index, std::size_t imageCount,
          ImageBasedLight& result)
{
    const auto label = std::string(kImageBasedLightExtension) + " light " + std::to_string(index);
    if (!source.is_object())
    {
        return label + " is not an object";
    }
    result.index = index;
    simdjson::dom::element field;
    if (source["name"].get(field) == SUCCESS)
    {
        std::string_view name;
        if (field.get_string().get(name) != SUCCESS)
        {
            return label + ": name must be a string";
        }
        result.name = std::string(name);
    }
    if (source["rotation"].get(field) == SUCCESS)
    {
        static const auto kRotation =
            ": rotation must be four numbers from -1 to 1 with a nonzero length";
        simdjson::dom::array rotation;
        if (field.get_array().get(rotation) != SUCCESS || rotation.size() != 4)
        {
            return label + kRotation;
        }
        double length = 0;
        std::size_t component = 0;
        for (const auto value : rotation)
        {
            double number = 0;
            if (!readNumber(value, number) || number < -1 || number > 1)
            {
                return label + kRotation;
            }
            result.rotation[component++] = number;
            length += number * number;
        }
        length = std::sqrt(length);
        if (!(length > 0))
        {
            return label + kRotation;
        }
        for (auto& value : result.rotation)
        {
            value /= length;
        }
    }
    if (source["intensity"].get(field) == SUCCESS &&
        !(readNumber(field, result.intensity) && result.intensity >= 0))
    {
        return label + ": intensity must be a finite number of at least 0";
    }
    {
        static const auto kCoefficients =
            ": irradianceCoefficients must be nine arrays of three finite numbers";
        simdjson::dom::array coefficients;
        if (source["irradianceCoefficients"].get_array().get(coefficients) != SUCCESS ||
            coefficients.size() != 9)
        {
            return label + kCoefficients;
        }
        std::size_t term = 0;
        for (const auto coefficient : coefficients)
        {
            simdjson::dom::array channels;
            if (coefficient.get_array().get(channels) != SUCCESS || channels.size() != 3)
            {
                return label + kCoefficients;
            }
            std::size_t channel = 0;
            for (const auto value : channels)
            {
                if (!readNumber(value, result.irradianceCoefficients[term][channel++]))
                {
                    return label + kCoefficients;
                }
            }
            ++term;
        }
    }
    std::uint64_t size = 0;
    if (source["specularImageSize"].get_uint64().get(size) != SUCCESS || size == 0 ||
        size > kMaxSpecularImageSize || (size & (size - 1)) != 0)
    {
        return label + ": specularImageSize must be a power of two from 1 to " +
               std::to_string(kMaxSpecularImageSize);
    }
    result.specularImageSize = std::uint32_t(size);
    const auto maxLevels = levelCount(size);
    simdjson::dom::array levels;
    if (source["specularImages"].get_array().get(levels) != SUCCESS || levels.size() == 0 ||
        levels.size() > maxLevels)
    {
        return label + ": specularImages must be a nonempty array of at most " +
               std::to_string(maxLevels) + " arrays of six image indices";
    }
    std::size_t levelIndex = 0;
    for (const auto level : levels)
    {
        simdjson::dom::array faces;
        if (level.get_array().get(faces) != SUCCESS || faces.size() != 6)
        {
            return label + ": specularImages must be a nonempty array of at most " +
                   std::to_string(maxLevels) + " arrays of six image indices";
        }
        auto& images = result.specularImages.emplace_back();
        std::size_t face = 0;
        for (const auto image : faces)
        {
            std::uint64_t imageIndex = 0;
            if (image.get_uint64().get(imageIndex) != SUCCESS)
            {
                return label + ": specularImages must be a nonempty array of at most " +
                       std::to_string(maxLevels) + " arrays of six image indices";
            }
            if (imageIndex >= imageCount)
            {
                return label + ": specularImages level " + std::to_string(levelIndex) + " face " +
                       std::to_string(face) + " image " + std::to_string(imageIndex) +
                       " is out of range";
            }
            images[face++] = std::size_t(imageIndex);
        }
        ++levelIndex;
    }
    return {};
}

// Reads the selected scene's light into `light` if it is not null. Returns what is malformed, or an
// empty string.
std::string
readImageBasedLight(const simdjson::dom::element& root, std::optional<ImageBasedLight>* light)
{
    simdjson::dom::element extension;
    const bool atRoot = root["extensions"][kImageBasedLightExtension].get(extension) == SUCCESS;
    bool present = atRoot;
    simdjson::dom::array lights;
    if (atRoot &&
        (!extension.is_object() || extension["lights"].get_array().get(lights) != SUCCESS))
    {
        return std::string(kImageBasedLightExtension) + " must be an object with a lights array";
    }
    std::uint64_t selected = 0;
    (void)root["scene"].get_uint64().get(selected);
    simdjson::dom::array scenes;
    std::optional<std::uint64_t> lightIndex;
    if (root["scenes"].get_array().get(scenes) == SUCCESS)
    {
        std::uint64_t sceneIndex = 0;
        for (const auto scene : scenes)
        {
            simdjson::dom::element reference;
            if (scene["extensions"][kImageBasedLightExtension].get(reference) == SUCCESS)
            {
                present = true;
                // Only the selected scene's light is read, as with punctual lights.
                if (sceneIndex == selected)
                {
                    const auto label = "glTF scene " + std::to_string(sceneIndex) + ": " +
                                       std::string(kImageBasedLightExtension);
                    std::uint64_t index = 0;
                    if (!reference.is_object() ||
                        reference["light"].get_uint64().get(index) != SUCCESS)
                    {
                        return label + " must be an object with a light index";
                    }
                    if (!atRoot || index >= lights.size())
                    {
                        return label + " light " + std::to_string(index) + " is out of range";
                    }
                    lightIndex = index;
                }
            }
            ++sceneIndex;
        }
    }
    if (present && !isDeclared(root))
    {
        return std::string(kImageBasedLightExtension) + " must be declared in extensionsUsed";
    }
    if (!lightIndex)
    {
        return {};
    }
    simdjson::dom::array images;
    const std::size_t imageCount =
        root["images"].get_array().get(images) == SUCCESS ? images.size() : 0;
    simdjson::dom::element source;
    (void)lights.at(*lightIndex).get(source);
    ImageBasedLight result;
    if (auto error = readLight(source, std::size_t(*lightIndex), imageCount, result);
        !error.empty())
    {
        return error;
    }
    if (light)
    {
        *light = std::move(result);
    }
    return {};
}

} // namespace

bool
ReadImageBasedLight(const std::byte* bytes, std::size_t size, std::optional<ImageBasedLight>& light,
                    std::string& error)
{
    light.reset();
    const auto json = detail::JsonOf(bytes, size);
    if (json.find(kImageBasedLightExtension) == std::string_view::npos)
    {
        return true;
    }
    simdjson::dom::parser parser;
    simdjson::dom::element root;
    if (parser.parse(json.data(), json.size()).get(root) != SUCCESS)
    {
        error = "invalid JSON";
        return false;
    }
    error = readImageBasedLight(root, &light);
    if (!error.empty())
    {
        light.reset();
        return false;
    }
    return true;
}

namespace detail
{

std::string
DescribeMalformedImageBasedLight(const simdjson::dom::element& root)
{
    return readImageBasedLight(root, nullptr);
}

} // namespace detail

} // namespace gltfCore
