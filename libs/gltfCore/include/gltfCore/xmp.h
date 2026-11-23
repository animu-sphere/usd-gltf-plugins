// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <cstddef>
#include <cstdint>
#include <optional>
#include <string>
#include <vector>

namespace gltfCore
{

struct JsonMember;

// A value of a KHR_xmp_json_ld packet, as the file states it.
struct JsonValue
{
    enum class Kind
    {
        Null,
        Boolean,
        Integer,
        Real,
        String,
        Array,
        Object,
    };

    Kind kind = Kind::Null;
    bool boolean = false;
    std::int64_t integer = 0;
    // A Real, and an integer beyond std::int64_t.
    double real = 0;
    // A String's value; for Null and Array, the value as minified JSON text.
    std::string string;
    std::vector<JsonValue> elements;
    // In the file's order.
    std::vector<JsonMember> members;
};

struct JsonMember
{
    std::string key;
    JsonValue value;
};

// KHR_xmp_json_ld: its packets, and the packet that each object which may reference one does.
// Each vector has an element for every object of its kind, and is empty if the file has no
// reference of that kind.
struct XmpMetadata
{
    std::vector<JsonValue> packets; // Each an Object.
    std::optional<std::size_t> asset;
    std::vector<std::optional<std::size_t>> scenes;
    std::vector<std::optional<std::size_t>> nodes;
    std::vector<std::optional<std::size_t>> meshes;
    std::vector<std::optional<std::size_t>> materials;
    std::vector<std::optional<std::size_t>> images;
    std::vector<std::optional<std::size_t>> animations;
};

// The packet that `references` gives an object, if any.
[[nodiscard]] std::optional<std::size_t>
XmpPacketOf(const std::vector<std::optional<std::size_t>>& references, std::size_t index);

// Reads KHR_xmp_json_ld from glTF or GLB bytes. Leaves `metadata` empty for a file without it. On
// failure, `error` says what is malformed; Parse fails for the same files.
[[nodiscard]] bool ReadXmpMetadata(const std::byte* bytes, std::size_t size, XmpMetadata& metadata,
                                   std::string& error);

} // namespace gltfCore
