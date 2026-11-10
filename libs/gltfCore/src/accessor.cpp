// SPDX-License-Identifier: Apache-2.0
#include "gltfCore/accessor.h"

#include <algorithm>
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

namespace
{

bool
readBase(const fastgltf::Asset& asset, const fastgltf::Accessor& accessor, std::size_t elementSize,
         bool vertex, AccessorData& result, std::string& error, const BufferLoader& loadBuffer)
{
    std::span<const std::byte> bytes;
    if (!GetBufferViewBytes(asset, *accessor.bufferViewIndex, bytes, error, loadBuffer))
    {
        return false;
    }
    const auto& view = asset.bufferViews[*accessor.bufferViewIndex];
    const auto componentSize = fastgltf::getComponentByteSize(accessor.componentType);
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
    result.bytes = bytes.subspan(accessor.byteOffset);
    result.stride = stride;
    return true;
}

bool
readSparseView(const fastgltf::Asset& asset, std::size_t viewIndex, std::size_t offset,
               std::size_t count, std::size_t elementSize, std::size_t alignment,
               std::span<const std::byte>& bytes, std::string& error,
               const BufferLoader& loadBuffer)
{
    if (!GetBufferViewBytes(asset, viewIndex, bytes, error, loadBuffer))
    {
        return false;
    }
    const auto& view = asset.bufferViews[viewIndex];
    if (view.byteStride || view.target || offset % alignment || view.byteOffset % alignment ||
        offset > bytes.size() || count > (bytes.size() - offset) / elementSize)
    {
        error = "sparse accessor has an invalid bufferView, offset, or count";
        return false;
    }
    bytes = bytes.subspan(offset, count * elementSize);
    return true;
}

} // namespace

const std::byte*
AccessorData::Element(std::size_t index) const
{
    const auto found = std::lower_bound(sparseIndices.begin(), sparseIndices.end(), index);
    if (found != sparseIndices.end() && *found == index)
    {
        return sparseValues.data() + (found - sparseIndices.begin()) * elementSize;
    }
    return bytes.empty() ? nullptr : bytes.data() + index * stride;
}

bool
GetAccessorData(const fastgltf::Asset& asset, std::size_t index, fastgltf::AccessorType type,
                fastgltf::ComponentType componentType, std::size_t elementSize, bool vertex,
                AccessorData& result, std::string& error, const BufferLoader& loadBuffer,
                bool normalized, bool allowSparse)
{
    if (index >= asset.accessors.size())
    {
        error = "accessor index is out of range";
        return false;
    }
    const auto& accessor = asset.accessors[index];
    const auto componentSize = fastgltf::getComponentByteSize(componentType);
    if (accessor.type != type || accessor.componentType != componentType ||
        accessor.normalized != normalized || accessor.count == 0 || !componentSize || !elementSize)
    {
        error = "accessor has an invalid type, component type, normalization, or count";
        return false;
    }
    if (!allowSparse && accessor.sparse)
    {
        error = "sparse accessors are not supported";
        return false;
    }
    result = {};
    result.count = accessor.count;
    result.elementSize = elementSize;
    if (accessor.bufferViewIndex)
    {
        if (!readBase(asset, accessor, elementSize, vertex, result, error, loadBuffer))
        {
            return false;
        }
    }
    else if (!allowSparse)
    {
        error = "accessors without a bufferView are not supported";
        return false;
    }
    else if (accessor.byteOffset != 0)
    {
        error = "accessor without a bufferView has a byte offset";
        return false;
    }
    if (!accessor.sparse)
    {
        return true;
    }

    const auto& sparse = *accessor.sparse;
    const auto indexType = sparse.indexComponentType;
    if (sparse.count == 0 || sparse.count > accessor.count ||
        (indexType != fastgltf::ComponentType::UnsignedByte &&
         indexType != fastgltf::ComponentType::UnsignedShort &&
         indexType != fastgltf::ComponentType::UnsignedInt))
    {
        error = "sparse accessor has an invalid count or index component type";
        return false;
    }
    const auto indexSize = fastgltf::getComponentByteSize(indexType);
    std::span<const std::byte> indices;
    if (!readSparseView(asset, sparse.indicesBufferView, sparse.indicesByteOffset, sparse.count,
                        indexSize, indexSize, indices, error, loadBuffer) ||
        !readSparseView(asset, sparse.valuesBufferView, sparse.valuesByteOffset, sparse.count,
                        elementSize, componentSize, result.sparseValues, error, loadBuffer))
    {
        return false;
    }
    result.sparseIndices.reserve(sparse.count);
    for (std::size_t i = 0; i < sparse.count; ++i)
    {
        std::size_t value = 0;
        for (std::size_t b = 0; b < indexSize; ++b)
        {
            value |= std::to_integer<std::size_t>(indices[i * indexSize + b]) << (8 * b);
        }
        if (value >= accessor.count || (i > 0 && value <= result.sparseIndices.back()))
        {
            error = "sparse accessor indices are out of range or not strictly increasing";
            return false;
        }
        result.sparseIndices.push_back(value);
    }
    return true;
}

} // namespace gltfCore
