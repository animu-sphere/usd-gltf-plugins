// SPDX-License-Identifier: Apache-2.0
#pragma once

#include "gltfCore/accessor.h"

#include <cstdint>
#include <map>
#include <string_view>

namespace gltfCore
{

struct DecodedDracoPrimitive
{
    // Accessors for mapped attributes, backed by the decoded bytes. Unmapped attributes and
    // morph targets are read from the source asset.
    fastgltf::Asset data{};
    std::map<std::string_view, std::size_t> attributes;
    std::vector<std::uint32_t> indices;
};

// Decodes a compressed triangle list. An uncompressed primitive or an optional Draco triangle
// strip leaves the result empty. Attribute names in the result borrow from the source primitive.
[[nodiscard]] bool DecodeDracoPrimitive(const fastgltf::Asset& asset,
                                        const fastgltf::Primitive& primitive,
                                        const BufferLoader& loadBuffer,
                                        DecodedDracoPrimitive& result, std::string& error);

} // namespace gltfCore
