// SPDX-License-Identifier: Apache-2.0
#include "gltfCore/accessor.h"

#include <meshoptimizer.h>

#include <algorithm>
#include <bit>
#include <new>
#include <string_view>
#include <utility>
#include <variant>

namespace gltfCore
{
namespace
{

using Mode = fastgltf::MeshoptCompressionMode;
using Filter = fastgltf::MeshoptCompressionFilter;

constexpr std::size_t maxDecodedBytes = 512 * 1024 * 1024;
static_assert(std::endian::native == std::endian::little);

bool
validRange(const fastgltf::Asset& asset, std::size_t buffer, std::size_t offset, std::size_t length)
{
    return buffer < asset.buffers.size() && offset <= asset.buffers[buffer].byteLength &&
           length <= asset.buffers[buffer].byteLength - offset;
}

bool
readRange(const fastgltf::Asset& asset, std::size_t index, std::size_t offset, std::size_t length,
          const BufferLoader& loadBuffer, std::span<const std::byte>& result, std::string& error)
{
    if (!validRange(asset, index, offset, length))
    {
        error = index >= asset.buffers.size() ? "buffer index is out of range"
                                              : "bufferView exceeds its buffer";
        return false;
    }
    const auto& buffer = asset.buffers[index];
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
    if (bytes.size() < buffer.byteLength)
    {
        error = "bufferView's buffer is shorter than its declared byteLength";
        return false;
    }
    result = bytes.subspan(offset, length);
    return true;
}

bool
validLayout(const fastgltf::BufferView& view)
{
    const auto& c = *view.meshoptCompression;
    const auto stride = c.byteStride;
    if (stride == 0 || c.count == 0 || view.byteLength % stride != 0 ||
        view.byteLength / stride != c.count || (view.byteStride && *view.byteStride != stride))
    {
        return false;
    }
    switch (c.mode)
    {
    case Mode::Attributes:
        return stride <= 256 && stride % 4 == 0 &&
               (c.filter == Filter::None || c.filter == Filter::Exponential ||
                (c.filter == Filter::Octahedral && (stride == 4 || stride == 8)) ||
                (c.filter == Filter::Quaternion && stride == 8));
    case Mode::Triangles:
    case Mode::Indices:
        return (stride == 2 || stride == 4) && c.filter == Filter::None &&
               (c.mode != Mode::Triangles || c.count % 3 == 0);
    }
    return false;
}

std::size_t
minimumEncodedSize(const fastgltf::BufferView& view)
{
    const auto& c = *view.meshoptCompression;
    switch (c.mode)
    {
    case Mode::Attributes:
        // For every 64 decoded bytes there is at least one byte of group headers.
        return 1 + (view.byteLength + 63) / 64 + std::max<std::size_t>(32, c.byteStride);
    case Mode::Triangles:
        return 1 + c.count / 3 + 16;
    case Mode::Indices:
        return 1 + c.count + 4;
    }
    return 0;
}

int
signedComponent(const std::byte* bytes, std::size_t size)
{
    int value = std::to_integer<int>(bytes[0]);
    if (size == 2)
    {
        value |= std::to_integer<int>(bytes[1]) << 8;
    }
    const int sign = 1 << (size * 8 - 1);
    return (value ^ sign) - sign;
}

bool
applyFilter(const fastgltf::CompressedBufferView& c, std::vector<std::byte>& bytes)
{
    // Reject values outside the filters' defined input domains before calling the codec.
    const auto stride = c.byteStride;
    switch (c.filter)
    {
    case Filter::None:
        return true;
    case Filter::Octahedral:
    case Filter::Quaternion:
    {
        const bool quat = c.filter == Filter::Quaternion;
        const auto componentSize = stride / 4;
        for (std::size_t offset = 0; offset < bytes.size(); offset += stride)
        {
            const auto* element = bytes.data() + offset;
            const int scale = quat ? (signedComponent(element + 6, 2) | 3)
                                   : signedComponent(element + 2 * componentSize, componentSize);
            if (scale < (quat ? 7 : 1) || (scale & (scale + 1)) != 0)
            {
                return false;
            }
            for (std::size_t j = 0; j < (quat ? 3u : 2u); ++j)
            {
                const auto value = signedComponent(element + j * componentSize, componentSize);
                if (value < -scale || value > scale)
                {
                    return false;
                }
            }
        }
        if (quat)
        {
            meshopt_decodeFilterQuat(bytes.data(), c.count, stride);
        }
        else
        {
            meshopt_decodeFilterOct(bytes.data(), c.count, stride);
        }
        return true;
    }
    case Filter::Exponential:
        for (std::size_t offset = 3; offset < bytes.size(); offset += 4)
        {
            const int exponent = signedComponent(bytes.data() + offset, 1);
            if (exponent < -100 || exponent > 100)
            {
                return false;
            }
        }
        meshopt_decodeFilterExp(bytes.data(), c.count, stride);
        return true;
    default:
        return false;
    }
}

bool
decode(const fastgltf::CompressedBufferView& c, std::span<const std::byte> source,
       std::vector<std::byte>& result)
{
    const auto* data = reinterpret_cast<const unsigned char*>(source.data());
    // The native library can also read bitstream versions outside the EXT specification.
    switch (c.mode)
    {
    case Mode::Attributes:
        return data[0] == 0xa0 && meshopt_decodeVertexBuffer(result.data(), c.count, c.byteStride,
                                                             data, source.size()) == 0;
    case Mode::Triangles:
        return data[0] == 0xe1 && meshopt_decodeIndexBuffer(result.data(), c.count, c.byteStride,
                                                            data, source.size()) == 0;
    case Mode::Indices:
        return data[0] == 0xd1 && meshopt_decodeIndexSequence(result.data(), c.count, c.byteStride,
                                                              data, source.size()) == 0;
    }
    return false;
}

} // namespace

BufferReader::BufferReader(const fastgltf::Asset& asset, BufferLoader loadBuffer)
    : _asset(&asset), _loadBuffer(std::move(loadBuffer))
{
}

bool
BufferReader::GetView(const fastgltf::Asset& asset, std::size_t index,
                      std::span<const std::byte>& result, std::string& error) const
{
    if (index >= asset.bufferViews.size())
    {
        error = "bufferView index is out of range";
        return false;
    }
    const auto& view = asset.bufferViews[index];
    if (!view.meshoptCompression)
    {
        return readRange(asset, view.bufferIndex, view.byteOffset, view.byteLength, _loadBuffer,
                         result, error);
    }

    const auto fail = [&](std::string_view reason)
    {
        error = "EXT_meshopt_compression bufferView " + std::to_string(index) + ": " +
                std::string(reason);
        return false;
    };
    if (_asset != &asset)
    {
        return fail("requires a buffer reader for this asset");
    }
    if (const auto cached = _decoded.find(index); cached != _decoded.end())
    {
        result = cached->second;
        return true;
    }
    if (!validLayout(view) ||
        !validRange(asset, view.bufferIndex, view.byteOffset, view.byteLength))
    {
        return fail("invalid decoded layout or fallback buffer range");
    }
    if (view.byteLength > maxDecodedBytes - _decodedBytes)
    {
        return fail("decoded buffer views exceed 512 MiB");
    }
    const auto& c = *view.meshoptCompression;
    if (c.byteLength < minimumEncodedSize(view))
    {
        return fail("compressed payload is shorter than its layout permits");
    }
    std::span<const std::byte> source;
    std::string sourceError;
    if (!readRange(asset, c.bufferIndex, c.byteOffset, c.byteLength, _loadBuffer, source,
                   sourceError))
    {
        return fail(sourceError);
    }
    try
    {
        std::vector<std::byte> decoded(view.byteLength);
        if (!decode(c, source, decoded) || !applyFilter(c, decoded))
        {
            return fail("invalid bitstream or filter data");
        }
        result = _decoded.emplace(index, std::move(decoded)).first->second;
        _decodedBytes += result.size();
    }
    catch (const std::bad_alloc&)
    {
        return fail("cannot allocate memory for decoded buffer view");
    }
    return true;
}

} // namespace gltfCore
