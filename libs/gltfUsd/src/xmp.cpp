// SPDX-License-Identifier: Apache-2.0
#include "xmp.h"

#include "pxr/base/tf/diagnostic.h"
#include "pxr/base/tf/token.h"
#include "pxr/base/vt/array.h"
#include "pxr/base/vt/types.h"
#include "pxr/base/vt/value.h"
#include "pxr/usd/sdf/schema.h"

#include <algorithm>
#include <cstdint>
#include <string>
#include <vector>

namespace gltfUsd
{
namespace
{

using namespace pxr;
using Kind = gltfCore::JsonValue::Kind;

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

// An empty dictionary that libsdf made. On macOS, a VtValue that this library makes of a VtDictionary
// does not compare as one in OpenUSD's libraries, which write it without a type: VtDictionary is not
// one of Vt's known value types, so its type_info is compared by address. Every dictionary is
// therefore made by VtDictionary::SetValueAtPath, in libvt, or copied from this one.
const VtValue&
emptyDictionary()
{
    static const VtValue empty = SdfSchema::GetInstance().GetFallback(SdfFieldKeys->AssetInfo);
    return empty;
}

// Sets the value at `path` in `root`, whose keys are not split at ':'.
void
convert(const gltfCore::JsonValue& value, std::vector<std::string>& path, std::size_t packet,
        VtDictionary& root)
{
    switch (value.kind)
    {
    case Kind::Boolean:
        root.SetValueAtPath(path, VtValue(value.boolean));
        return;
    case Kind::Integer:
        root.SetValueAtPath(path, VtValue(value.integer));
        return;
    case Kind::Real:
        root.SetValueAtPath(path, VtValue(value.real));
        return;
    case Kind::String:
        root.SetValueAtPath(path, VtValue(value.string));
        return;
    case Kind::Object:
        root.SetValueAtPath(path, emptyDictionary());
        for (const auto& member : value.members)
        {
            path.push_back(member.key);
            convert(member.value, path, packet, root);
            path.pop_back();
        }
        return;
    case Kind::Array:
        if (auto array = typedArray(value.elements); !array.IsEmpty())
        {
            root.SetValueAtPath(path, array);
            return;
        }
        break;
    case Kind::Null:
        break;
    }
    std::string location;
    for (auto key = path.begin() + 1; key != path.end(); ++key)
    {
        location += "/" + *key;
    }
    TF_WARN("glTF KHR_xmp_json_ld packet %zu: %s is %s, which USD cannot hold; authored as a JSON "
            "literal",
            packet, location.c_str(),
            value.kind == Kind::Null ? "null"
                                     : "an array of objects, arrays, nulls or mixed types");
    // JSON-LD's own form of a JSON literal, which keeps the value as its text.
    root.SetValueAtPath(path, emptyDictionary());
    path.push_back("@type");
    root.SetValueAtPath(path, VtValue(std::string("@json")));
    path.back() = "@value";
    root.SetValueAtPath(path, VtValue(value.string));
    path.pop_back();
}

} // namespace

XmpPackets::XmpPackets(const gltfCore::XmpMetadata& metadata) : _metadata(metadata)
{
}

const VtValue*
XmpPackets::Get(std::optional<std::size_t> packet) const
{
    if (!packet || *packet >= _metadata.packets.size())
    {
        return nullptr;
    }
    auto found = _converted.find(*packet);
    if (found == _converted.end())
    {
        static const std::string top = "packet";
        VtDictionary root;
        std::vector<std::string> path{top};
        convert(_metadata.packets[*packet], path, *packet, root);
        found = _converted.emplace(*packet, root[top]).first;
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
    prim.SetAssetInfoByKey(key, *dictionary);
    return prim.HasAuthoredAssetInfoKey(key);
}

} // namespace gltfUsd
