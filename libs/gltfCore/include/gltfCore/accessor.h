// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <fastgltf/types.hpp>

#include <cstddef>
#include <functional>
#include <span>
#include <string>
#include <unordered_map>
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

// Keep this reader alive for the lifetime of the spans returned from compressed views.
// A reader is used serially with one immutable asset. Uncompressed reads also accept the
// temporary assets that hold decoded Draco attributes.
class BufferReader
{
  public:
    BufferReader() = default; // For uncompressed assets with no external buffers.
    explicit BufferReader(const fastgltf::Asset& asset, BufferLoader loadBuffer = {});
    BufferReader(const BufferReader&) = delete;
    BufferReader& operator=(const BufferReader&) = delete;

    [[nodiscard]] bool GetView(const fastgltf::Asset& asset, std::size_t index,
                               std::span<const std::byte>& result, std::string& error) const;

  private:
    const fastgltf::Asset* _asset = nullptr;
    BufferLoader _loadBuffer;
    mutable std::unordered_map<std::size_t, std::vector<std::byte>> _decoded;
    mutable std::size_t _decodedBytes = 0;
};

[[nodiscard]] bool GetAccessorData(const fastgltf::Asset& asset, std::size_t index,
                                   fastgltf::AccessorType type,
                                   fastgltf::ComponentType componentType, std::size_t elementSize,
                                   bool vertex, AccessorData& result, std::string& error,
                                   const BufferReader& buffers = {}, bool normalized = false,
                                   bool allowSparse = false);

} // namespace gltfCore
