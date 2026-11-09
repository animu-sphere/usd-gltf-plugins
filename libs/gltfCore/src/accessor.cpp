// SPDX-License-Identifier: Apache-2.0
#include "gltfCore/accessor.h"

#include <variant>

namespace gltfCore
{

bool
GetBufferViewBytes(const fastgltf::Asset& asset, std::size_t index,
                   std::span<const std::byte>& result, std::string& error,
                   const BufferLoader& loadBuffer)
{
    if (index >= asset.bufferViews.size())
    {
        error = "bufferView index is out of range";
        return false;
    }
    const auto& view = asset.bufferViews[index];
    if (view.bufferIndex >= asset.buffers.size())
    {
        error = "buffer index is out of range";
        return false;
    }
    const auto& buffer = asset.buffers[view.bufferIndex];
    std::span<const std::byte> bytes;
    if (const auto* array = std::get_if<fastgltf::sources::Array>(&buffer.data))
    {
        bytes = {array->bytes.data(), array->bytes.size()};
    }
    else if (const auto* vector = std::get_if<fastgltf::sources::Vector>(&buffer.data))
    {
        bytes = {vector->bytes.data(), vector->bytes.size()};
    }
    else if (const auto* byteView = std::get_if<fastgltf::sources::ByteView>(&buffer.data))
    {
        bytes = byteView->bytes;
    }
    else if (std::holds_alternative<fastgltf::sources::URI>(buffer.data) && loadBuffer)
    {
        if (!loadBuffer(buffer, bytes, error))
        {
            return false;
        }
    }
    else
    {
        error = "external or unsupported buffer data is not supported";
        return false;
    }
    if (buffer.byteLength > bytes.size() || view.byteOffset > buffer.byteLength ||
        view.byteLength > buffer.byteLength - view.byteOffset)
    {
        error = "bufferView exceeds its buffer";
        return false;
    }
    result = bytes.subspan(view.byteOffset, view.byteLength);
    return true;
}

bool
GetAccessorData(const fastgltf::Asset& asset, std::size_t index, fastgltf::AccessorType type,
                fastgltf::ComponentType componentType, std::size_t elementSize, bool vertex,
                AccessorData& result, std::string& error, const BufferLoader& loadBuffer,
                bool normalized)
{
    if (index >= asset.accessors.size())
    {
        error = "accessor index is out of range";
        return false;
    }
    const auto& accessor = asset.accessors[index];
    if (accessor.type != type || accessor.componentType != componentType ||
        accessor.normalized != normalized || accessor.count == 0)
    {
        error = "accessor has an invalid type, component type, normalization, or count";
        return false;
    }
    if (accessor.sparse)
    {
        error = "sparse accessors are not supported";
        return false;
    }
    if (!accessor.bufferViewIndex)
    {
        error = "accessors without a bufferView are not supported";
        return false;
    }
    std::span<const std::byte> bytes;
    if (!GetBufferViewBytes(asset, *accessor.bufferViewIndex, bytes, error, loadBuffer))
    {
        return false;
    }
    const auto& view = asset.bufferViews[*accessor.bufferViewIndex];
    const auto componentSize = fastgltf::getComponentByteSize(componentType);
    if (componentSize == 0 || accessor.byteOffset % componentSize != 0 ||
        (view.byteOffset % componentSize + accessor.byteOffset % componentSize) % componentSize !=
            0 ||
        (vertex && accessor.byteOffset % 4 != 0))
    {
        error = "accessor has invalid byte alignment";
        return false;
    }
    const std::size_t stride = view.byteStride.value_or(elementSize);
    if (elementSize == 0 || (!vertex && view.byteStride) || stride < elementSize ||
        (vertex && view.byteStride && (stride > 252 || stride % 4 != 0)) ||
        (vertex && accessor.count > 1 && stride % 4 != 0) ||
        accessor.byteOffset > view.byteLength ||
        elementSize > view.byteLength - accessor.byteOffset ||
        accessor.count - 1 > (view.byteLength - accessor.byteOffset - elementSize) / stride)
    {
        error = "accessor exceeds its bufferView or has an invalid stride";
        return false;
    }
    result = {bytes.subspan(accessor.byteOffset), accessor.count, stride};
    return true;
}

} // namespace gltfCore
