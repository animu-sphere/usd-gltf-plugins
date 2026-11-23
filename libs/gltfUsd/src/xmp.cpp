// SPDX-License-Identifier: Apache-2.0
#include "xmp.h"

#include "pxr/base/tf/diagnostic.h"
#include "pxr/base/tf/token.h"
#include "pxr/base/vt/array.h"
#include "pxr/base/vt/types.h"
#include "pxr/base/vt/value.h"

#include <algorithm>
#include <cstdint>
#include <string>

namespace gltfUsd
{
namespace
{

using namespace pxr;
using Kind = gltfCore::JsonValue::Kind;

// JSON-LD's own form of a JSON literal, which keeps a value that USD cannot hold as its text.
VtDictionary
jsonLiteral(const std::string& text)
{
    VtDictionary literal;
    literal["@type"] = VtValue(std::string("@json"));
    literal["@value"] = VtValue(text);
    return literal;
}

template <class T, class Read>
VtValue
arrayOf(const std::vector<gltfCore::JsonValue>& elements, Read read)
{
    VtArray<T> result;
    result.reserve(elements.size());
    for (const auto& element : elements)
    {
        result.push_back(read(element));
    }
    return VtValue(std::move(result));
}

bool
allOf(const std::vector<gltfCore::JsonValue>& elements, std::initializer_list<Kind> kinds)
{
    return std::all_of(
        elements.begin(), elements.end(), [&](const gltfCore::JsonValue& element)
        { return std::find(kinds.begin(), kinds.end(), element.kind) != kinds.end(); });
}

// An array of strings, booleans, integers or numbers is a typed array. An empty array has strings,
// the type of most XMP arrays. Returns an empty value for any other array.
VtValue
typedArray(const std::vector<gltfCore::JsonValue>& elements)
{
    if (allOf(elements, {Kind::String}))
    {
        return arrayOf<std::string>(elements, [](const auto& e) { return e.string; });
    }
    if (allOf(elements, {Kind::Boolean}))
    {
        return arrayOf<bool>(elements, [](const auto& e) { return e.boolean; });
    }
    if (allOf(elements, {Kind::Integer}))
    {
        return arrayOf<std::int64_t>(elements, [](const auto& e) { return e.integer; });
    }
    if (allOf(elements, {Kind::Integer, Kind::Real}))
    {
        return arrayOf<double>(elements, [](const auto& e)
                               { return e.kind == Kind::Integer ? double(e.integer) : e.real; });
    }
    return {};
}

VtValue
convert(const gltfCore::JsonValue& value, const std::string& path, std::size_t packet)
{
    switch (value.kind)
    {
    case Kind::Boolean:
        return VtValue(value.boolean);
    case Kind::Integer:
        return VtValue(value.integer);
    case Kind::Real:
        return VtValue(value.real);
    case Kind::String:
        return VtValue(value.string);
    case Kind::Object:
    {
        VtDictionary dictionary;
        for (const auto& member : value.members)
        {
            dictionary[member.key] = convert(member.value, path + "/" + member.key, packet);
        }
        return VtValue(std::move(dictionary));
    }
    case Kind::Array:
        if (auto array = typedArray(value.elements); !array.IsEmpty())
        {
            return array;
        }
        break;
    case Kind::Null:
        break;
    }
    TF_WARN("glTF KHR_xmp_json_ld packet %zu: %s is %s, which USD cannot hold; authored as a JSON "
            "literal",
            packet, path.c_str(),
            value.kind == Kind::Null ? "null"
                                     : "an array of objects, arrays, nulls or mixed types");
    return VtValue(jsonLiteral(value.string));
}

} // namespace

XmpPackets::XmpPackets(const gltfCore::XmpMetadata& metadata) : _metadata(metadata)
{
}

const VtDictionary*
XmpPackets::Get(std::optional<std::size_t> packet) const
{
    if (!packet || *packet >= _metadata.packets.size())
    {
        return nullptr;
    }
    auto found = _converted.find(*packet);
    if (found == _converted.end())
    {
        const auto value = convert(_metadata.packets[*packet], "", *packet);
        found = _converted.emplace(*packet, value.Get<VtDictionary>()).first;
    }
    return &found->second;
}

bool
XmpPackets::Author(const UsdPrim& prim, std::optional<std::size_t> packet) const
{
    const auto* dictionary = Get(packet);
    if (!dictionary)
    {
        return true;
    }
    static const TfToken key("xmp");
    prim.SetAssetInfoByKey(key, VtValue(*dictionary));
    return prim.HasAuthoredAssetInfoKey(key);
}

} // namespace gltfUsd
