#pragma once

#include <fastgltf/types.hpp>

#include <cstddef>
#include <span>
#include <string>

namespace gltfCore {

struct AccessorData {
    std::span<const std::byte> bytes;
    std::size_t count;
    std::size_t stride;
};

[[nodiscard]] bool GetAccessorData(const fastgltf::Asset& asset, std::size_t index,
                                   fastgltf::AccessorType type,
                                   fastgltf::ComponentType componentType,
                                   std::size_t elementSize, bool vertex,
                                   AccessorData& result, std::string& error);

} // namespace gltfCore
