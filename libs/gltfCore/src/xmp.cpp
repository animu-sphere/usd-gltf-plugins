// SPDX-License-Identifier: Apache-2.0
#include "gltfCore/xmp.h"

#include "json.h"

#include <simdjson.h>

#include <array>
#include <cstdint>
#include <string_view>
#include <utility>

namespace gltfCore
{
namespace
{

constexpr std::string_view kExtension = "KHR_xmp_json_ld";

using simdjson::SUCCESS;

JsonValue
toValue(const simdjson::dom::element& element)
{
    JsonValue value;
    switch (element.type())
    {
    case simdjson::dom::element_type::NULL_VALUE:
        value.string = "null";
        break;
    case simdjson::dom::element_type::BOOL:
        value.kind = JsonValue::Kind::Boolean;
        value.boolean = element.get_bool().value_unsafe();
        break;
    case simdjson::dom::element_type::INT64:
        value.kind = JsonValue::Kind::Integer;
        value.integer = element.get_int64().value_unsafe();
        break;
    case simdjson::dom::element_type::UINT64:
        value.kind = JsonValue::Kind::Real;
        value.real = double(element.get_uint64().value_unsafe());
        break;
    case simdjson::dom::element_type::DOUBLE:
        value.kind = JsonValue::Kind::Real;
        value.real = element.get_double().value_unsafe();
        break;
    case simdjson::dom::element_type::STRING:
        value.kind = JsonValue::Kind::String;
        value.string = std::string(element.get_string().value_unsafe());
        break;
    case simdjson::dom::element_type::ARRAY:
        value.kind = JsonValue::Kind::Array;
        value.string = simdjson::minify(element);
        for (const auto item : element.get_array().value_unsafe())
        {
            value.elements.push_back(toValue(item));
        }
        break;
    case simdjson::dom::element_type::OBJECT:
        value.kind = JsonValue::Kind::Object;
        for (const auto field : element.get_object().value_unsafe())
        {
            value.members.push_back({std::string(field.key), toValue(field.value)});
        }
        break;
    default:
        // The parser reports big integers as numbers unless asked not to.
        break;
    }
    return value;
}

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
        if (name.get_string().get(text) == SUCCESS && text == kExtension)
        {
            return true;
        }
    }
    return false;
}

// Reads the extension into `metadata` if it is not null. Returns what is malformed, or an empty
// string.
std::string
readXmp(const simdjson::dom::element& root, XmpMetadata* metadata)
{
    bool present = false;
    std::size_t packetCount = 0;
    simdjson::dom::element extension;
    if (root["extensions"][kExtension].get(extension) == SUCCESS)
    {
        present = true;
        simdjson::dom::array packets;
        if (!extension.is_object() || extension["packets"].get_array().get(packets) != SUCCESS ||
            packets.size() == 0)
        {
            return "KHR_xmp_json_ld must be an object with a nonempty packets array";
        }
        for (const auto packet : packets)
        {
            const auto label = "KHR_xmp_json_ld packet " + std::to_string(packetCount);
            simdjson::dom::object context;
            if (!packet.is_object())
            {
                return label + " is not an object";
            }
            if (packet["@context"].get_object().get(context) != SUCCESS)
            {
                return label + " has no @context object";
            }
            for (const auto prefix : context)
            {
                if (!prefix.value.is_string())
                {
                    return label + " @context maps " + std::string(prefix.key) +
                           " to a value that is not a string";
                }
            }
            if (metadata)
            {
                metadata->packets.push_back(toValue(packet));
            }
            ++packetCount;
        }
    }
    auto read = [&](const simdjson::dom::element& object, const std::string& label,
                    std::optional<std::size_t>& packet) -> std::string
    {
        simdjson::dom::element reference;
        if (object["extensions"][kExtension].get(reference) != SUCCESS)
        {
            return {};
        }
        present = true;
        std::uint64_t index = 0;
        if (!reference.is_object() || reference["packet"].get_uint64().get(index) != SUCCESS)
        {
            return label + ": KHR_xmp_json_ld must be an object with a packet index";
        }
        if (index >= packetCount)
        {
            return label + ": KHR_xmp_json_ld packet " + std::to_string(index) + " is out of range";
        }
        packet = std::size_t(index);
        return {};
    };
    XmpMetadata unused;
    auto& result = metadata ? *metadata : unused;
    simdjson::dom::element asset;
    if (root["asset"].get(asset) == SUCCESS)
    {
        if (auto error = read(asset, "glTF asset", result.asset); !error.empty())
        {
            return error;
        }
    }
    const std::array<std::pair<const char*, std::vector<std::optional<std::size_t>>*>, 6> kinds = {{
        {"scene", &result.scenes},
        {"node", &result.nodes},
        {"mesh", &result.meshes},
        {"material", &result.materials},
        {"image", &result.images},
        {"animation", &result.animations},
    }};
    constexpr std::array<const char*, 6> arrays = {"scenes",    "nodes",  "meshes",
                                                   "materials", "images", "animations"};
    for (std::size_t kind = 0; kind < kinds.size(); ++kind)
    {
        simdjson::dom::array objects;
        if (root[arrays[kind]].get_array().get(objects) != SUCCESS)
        {
            continue;
        }
        std::vector<std::optional<std::size_t>> packets(objects.size());
        bool any = false;
        std::size_t index = 0;
        for (const auto object : objects)
        {
            if (auto error = read(
                    object, std::string("glTF ") + kinds[kind].first + " " + std::to_string(index),
                    packets[index]);
                !error.empty())
            {
                return error;
            }
            any = any || packets[index].has_value();
            ++index;
        }
        if (any)
        {
            *kinds[kind].second = std::move(packets);
        }
    }
    if (present && !isDeclared(root))
    {
        return "KHR_xmp_json_ld must be declared in extensionsUsed";
    }
    return {};
}

} // namespace

std::optional<std::size_t>
XmpPacketOf(const std::vector<std::optional<std::size_t>>& references, std::size_t index)
{
    return index < references.size() ? references[index] : std::nullopt;
}

bool
ReadXmpMetadata(const std::byte* bytes, std::size_t size, XmpMetadata& metadata, std::string& error)
{
    metadata = {};
    const auto json = detail::JsonOf(bytes, size);
    if (json.find(kExtension) == std::string_view::npos)
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
    error = readXmp(root, &metadata);
    if (!error.empty())
    {
        metadata = {};
        return false;
    }
    return true;
}

namespace detail
{

std::string
DescribeMalformedXmp(const simdjson::dom::element& root)
{
    return readXmp(root, nullptr);
}

} // namespace detail

} // namespace gltfCore
