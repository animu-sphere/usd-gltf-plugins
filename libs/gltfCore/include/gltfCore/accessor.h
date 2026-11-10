// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <fastgltf/types.hpp>

#include <cstddef>
#include <functional>
#include <span>
#include <string>
#include <vector>

namespace gltfCore
{

struct AccessorData
{
    std::span<const std::byte> bytes;
    std::size_t count;
    std::size_t stride;
    std::vector<std::size_t> sparseIndices;
    std::span<const std::byte> sparseValues;
    std::size_t elementSize = 0;

    // For callers that opt into sparse and implicit-zero data. A null pointer denotes zero.
    [[nodiscard]] const std::byte* Element(std::size_t index) const;
};

using BufferLoader =
    std::function<bool(const fastgltf::Buffer&, std::span<const std::byte>&, std::string&)>;

[[nodiscard]] bool GetBufferViewBytes(const fastgltf::Asset& asset, std::size_t index,
                                      std::span<const std::byte>& result, std::string& error,
                                      const BufferLoader& loadBuffer = {});

[[nodiscard]] bool GetAccessorData(const fastgltf::Asset& asset, std::size_t index,
                                   fastgltf::AccessorType type,
                                   fastgltf::ComponentType componentType, std::size_t elementSize,
                                   bool vertex, AccessorData& result, std::string& error,
                                   const BufferLoader& loadBuffer = {}, bool normalized = false,
                                   bool allowSparse = false);

} // namespace gltfCore
