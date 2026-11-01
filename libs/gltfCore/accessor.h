// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <fastgltf/types.hpp>

#include <cstddef>
#include <functional>
#include <span>
#include <string>

namespace gltfCore {

struct AccessorData {
    std::span<const std::byte> bytes;
    std::size_t count;
    std::size_t stride;
};

using BufferLoader = std::function<bool(const fastgltf::Buffer&,
                                        std::span<const std::byte>&, std::string&)>;

[[nodiscard]] bool GetAccessorData(const fastgltf::Asset& asset, std::size_t index,
                                   fastgltf::AccessorType type,
                                   fastgltf::ComponentType componentType,
                                   std::size_t elementSize, bool vertex,
                                   AccessorData& result, std::string& error,
                                   const BufferLoader& loadBuffer = {},
                                   bool normalized = false);

} // namespace gltfCore
