// SPDX-License-Identifier: Apache-2.0
#include "gltfCore/draco.h"

#include <draco/compression/decode.h>

#include <algorithm>
#include <bit>
#include <cstring>
#include <limits>

namespace gltfCore
{
namespace
{

constexpr auto Extension = "KHR_draco_mesh_compression";

draco::DataType
dracoType(fastgltf::ComponentType type)
{
    using fastgltf::ComponentType;
    switch (type)
    {
    case ComponentType::Byte:
        return draco::DT_INT8;
    case ComponentType::UnsignedByte:
        return draco::DT_UINT8;
    case ComponentType::Short:
        return draco::DT_INT16;
    case ComponentType::UnsignedShort:
        return draco::DT_UINT16;
    case ComponentType::UnsignedInt:
        return draco::DT_UINT32;
    case ComponentType::Float:
        return draco::DT_FLOAT32;
    default:
        return draco::DT_INVALID;
    }
}

bool
decodeAttribute(const fastgltf::Accessor& source, const draco::PointAttribute& attribute,
                std::size_t pointCount, fastgltf::Asset& data)
{
    const auto component = dracoType(source.componentType);
    const auto width = fastgltf::getNumComponents(source.type);
    if (source.count != pointCount || source.sparse || width < 1 || width > 4 ||
        width != attribute.num_components() || component == draco::DT_INVALID ||
        component != attribute.data_type() ||
        (!attribute.is_mapping_identity() && attribute.indices_map_size() != pointCount))
    {
        return false;
    }
    const auto elementSize = fastgltf::getComponentByteSize(source.componentType) * width;
    // A synthetic vertex bufferView follows the same four-byte alignment as core glTF.
    const std::size_t stride = (elementSize + 3) & ~std::size_t{3};
    fastgltf::sources::Vector storage;
    if (pointCount > storage.bytes.max_size() / stride)
    {
        return false;
    }
    storage.bytes.resize(pointCount * stride);
    static_assert(std::endian::native == std::endian::little);
    const auto* buffer = attribute.buffer();
    if (!buffer || attribute.byte_stride() < static_cast<std::int64_t>(elementSize))
    {
        return false;
    }
    for (std::size_t i = 0; i < pointCount; ++i)
    {
        const auto mapped = attribute.mapped_index(draco::PointIndex(i));
        if (mapped.value() >= attribute.size())
        {
            return false;
        }
        const auto offset = attribute.GetBytePos(mapped);
        if (offset < 0 || static_cast<std::size_t>(offset) > buffer->data_size() ||
            elementSize > buffer->data_size() - static_cast<std::size_t>(offset))
        {
            return false;
        }
        std::memcpy(storage.bytes.data() + i * stride, buffer->data() + offset, elementSize);
    }
    const auto length = storage.bytes.size();
    const auto index = data.accessors.size();
    data.accessors.push_back({.count = pointCount,
                              .normalized = source.normalized,
                              .type = source.type,
                              .componentType = source.componentType,
                              .bufferViewIndex = index});
    data.bufferViews.push_back({.bufferIndex = index, .byteLength = length, .byteStride = stride});
    data.buffers.push_back({.byteLength = length, .data = std::move(storage)});
    return true;
}

bool
decodeIndices(const fastgltf::Asset& asset, const fastgltf::Primitive& primitive,
              const draco::Mesh& mesh, std::vector<std::uint32_t>& indices)
{
    using fastgltf::ComponentType;
    const auto count = static_cast<std::size_t>(mesh.num_faces()) * 3;
    if (count == 0 || count > static_cast<std::size_t>(std::numeric_limits<int>::max()))
    {
        return false;
    }
    // glTF reserves the largest representable value of each index component type.
    auto limit = std::numeric_limits<std::uint32_t>::max();
    if (primitive.indicesAccessor)
    {
        const auto index = *primitive.indicesAccessor;
        if (index >= asset.accessors.size())
        {
            return false;
        }
        const auto& accessor = asset.accessors[index];
        if (accessor.type != fastgltf::AccessorType::Scalar || accessor.count != count ||
            accessor.normalized || accessor.sparse)
        {
            return false;
        }
        switch (accessor.componentType)
        {
        case ComponentType::UnsignedByte:
            limit = std::numeric_limits<std::uint8_t>::max();
            break;
        case ComponentType::UnsignedShort:
            limit = std::numeric_limits<std::uint16_t>::max();
            break;
        case ComponentType::UnsignedInt:
            break;
        default:
            return false;
        }
    }
    indices.reserve(count);
    for (draco::FaceIndex face(0); face < mesh.num_faces(); ++face)
    {
        for (const auto point : mesh.face(face))
        {
            if (point.value() >= mesh.num_points() || point.value() >= limit)
            {
                return false;
            }
            indices.push_back(point.value());
        }
    }
    return true;
}

} // namespace

bool
DecodeDracoPrimitive(const fastgltf::Asset& asset, const fastgltf::Primitive& primitive,
                     const BufferLoader& loadBuffer, DecodedDracoPrimitive& result,
                     std::string& error)
{
    if (!primitive.dracoCompression ||
        (primitive.type == fastgltf::PrimitiveType::TriangleStrip &&
         std::find(asset.extensionsRequired.begin(), asset.extensionsRequired.end(), Extension) ==
             asset.extensionsRequired.end()))
    {
        result = {};
        return true;
    }
    const auto& compression = *primitive.dracoCompression;
    if (primitive.type != fastgltf::PrimitiveType::Triangles)
    {
        error = "KHR_draco_mesh_compression supports only TRIANGLES";
        return false;
    }
    std::span<const std::byte> bytes;
    if (!GetBufferViewBytes(asset, compression.bufferView, bytes, error, loadBuffer))
    {
        error = "KHR_draco_mesh_compression: " + error;
        return false;
    }
    draco::DecoderBuffer buffer;
    buffer.Init(reinterpret_cast<const char*>(bytes.data()), bytes.size());
    draco::Decoder decoder;
    auto decoded = decoder.DecodeMeshFromBuffer(&buffer);
    if (!decoded.ok())
    {
        error = "KHR_draco_mesh_compression decode failed: " + decoded.status().error_msg_string();
        return false;
    }
    const auto& mesh = *decoded.value();
    const auto pointCount = mesh.num_points();
    if (pointCount == 0 ||
        pointCount > static_cast<std::uint32_t>(std::numeric_limits<int>::max()) ||
        compression.attributes.empty())
    {
        error = "KHR_draco_mesh_compression has an invalid point count or empty attribute map";
        return false;
    }
    const auto position = primitive.findAttribute("POSITION");
    if (position == primitive.attributes.end() ||
        position->accessorIndex >= asset.accessors.size() ||
        asset.accessors[position->accessorIndex].count != pointCount)
    {
        error = "KHR_draco_mesh_compression point count does not match the POSITION accessor";
        return false;
    }
    DecodedDracoPrimitive decodedPrimitive;
    // The mesh reader checks required extensions when interpreting quantized accessors.
    decodedPrimitive.data.extensionsRequired = asset.extensionsRequired;
    for (const auto& mapping : compression.attributes)
    {
        const auto source = primitive.findAttribute(mapping.name);
        const auto* attribute =
            mapping.accessorIndex <= std::numeric_limits<std::uint32_t>::max()
                ? mesh.GetAttributeByUniqueId(static_cast<std::uint32_t>(mapping.accessorIndex))
                : nullptr;
        if (source == primitive.attributes.end() ||
            source->accessorIndex >= asset.accessors.size() || !attribute ||
            !decodedPrimitive.attributes
                 .emplace(mapping.name, decodedPrimitive.data.accessors.size())
                 .second ||
            !decodeAttribute(asset.accessors[source->accessorIndex], *attribute, pointCount,
                             decodedPrimitive.data))
        {
            error = "KHR_draco_mesh_compression attribute " + mapping.name +
                    " does not match its accessor or has invalid decoded data";
            return false;
        }
    }
    if (!decodeIndices(asset, primitive, mesh, decodedPrimitive.indices))
    {
        error =
            "KHR_draco_mesh_compression faces have invalid indices or do not match the accessor";
        return false;
    }
    result = std::move(decodedPrimitive);
    return true;
}

} // namespace gltfCore
