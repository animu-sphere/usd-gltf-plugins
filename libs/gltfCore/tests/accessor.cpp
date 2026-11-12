// SPDX-License-Identifier: Apache-2.0
#include "gltfCore/accessor.h"

#include <algorithm>
#include <array>
#include <cstddef>
#include <iostream>
#include <limits>
#include <string>
#include <utility>
#include <vector>

namespace
{

bool
check(const fastgltf::Asset& asset, bool expected, const char* label, bool normalized = false)
{
    gltfCore::AccessorData result;
    std::string error;
    const bool success = gltfCore::GetAccessorData(asset, 0, fastgltf::AccessorType::Vec3,
                                                   fastgltf::ComponentType::Float, 12, true, result,
                                                   error, {}, normalized);
    if (success != expected || (!success && error.empty()))
    {
        std::cerr << label << ": unexpected accessor result: " << error << '\n';
        return false;
    }
    return true;
}

bool
checkInterleavedBounds(fastgltf::Asset& asset)
{
    auto& buffer = asset.buffers[0];
    auto& view = asset.bufferViews[0];
    auto& accessor = asset.accessors[0];
    auto& bytes = std::get<fastgltf::sources::Vector>(buffer.data).bytes;
    constexpr std::size_t elementSize = 12;
    constexpr std::array<std::size_t, 4> strides = {12, 16, 32, 252};
    constexpr std::array<std::size_t, 3> counts = {1, 2, 5};
    constexpr std::array<std::size_t, 3> offsets = {0, 4, 16};
    for (const auto stride : strides)
    {
        for (const auto count : counts)
        {
            for (const auto offset : offsets)
            {
                view.byteOffset = 4;
                view.byteStride = stride;
                accessor.byteOffset = offset;
                accessor.count = count;
                // Lay out the records byte by byte, omitting any padding after the last one.
                bytes.assign(view.byteOffset + offset, std::byte{0});
                for (std::size_t index = 0; index < count; ++index)
                {
                    if (index != 0)
                    {
                        bytes.resize(bytes.size() + stride - elementSize);
                    }
                    bytes.insert(bytes.end(), elementSize, std::byte(index + 1));
                }
                buffer.byteLength = bytes.size();
                view.byteLength = bytes.size() - view.byteOffset;

                gltfCore::AccessorData result;
                std::string error;
                bool valid = gltfCore::GetAccessorData(asset, 0, fastgltf::AccessorType::Vec3,
                                                       fastgltf::ComponentType::Float, elementSize,
                                                       true, result, error) &&
                             result.count == count && result.stride == stride &&
                             result.bytes.data() == bytes.data() + view.byteOffset + offset &&
                             result.bytes.size() == view.byteLength - offset;
                for (std::size_t index = 0; valid && index < count; ++index)
                {
                    const auto* element = result.Element(index);
                    valid = element == result.bytes.data() + index * stride &&
                            std::all_of(element, element + elementSize, [index](std::byte byte)
                                        { return byte == std::byte(index + 1); });
                }
                if (!valid)
                {
                    std::cerr << "interleaved accessor with stride " << stride << ", count "
                              << count << ", offset " << offset << " differs: " << error << '\n';
                    return false;
                }

                --view.byteLength;
                if (!check(asset, false, "one byte missing from the view"))
                {
                    return false;
                }
                ++view.byteLength;
                bytes.pop_back();
                if (!check(asset, false, "one byte missing from the backing buffer"))
                {
                    return false;
                }
            }
        }
    }
    return true;
}

} // namespace

int
main()
{
    fastgltf::Asset asset;
    fastgltf::Buffer buffer{};
    buffer.byteLength = 36;
    buffer.data = fastgltf::sources::Vector{std::vector<std::byte>(36)};
    asset.buffers.push_back(std::move(buffer));
    fastgltf::BufferView view{};
    view.bufferIndex = 0;
    view.byteLength = 36;
    asset.bufferViews.push_back(std::move(view));
    fastgltf::Accessor accessor{};
    accessor.bufferViewIndex = 0;
    accessor.count = 3;
    accessor.type = fastgltf::AccessorType::Vec3;
    accessor.componentType = fastgltf::ComponentType::Float;
    asset.accessors.push_back(std::move(accessor));

    bool ok = check(asset, true, "valid packed accessor");
    asset.accessors[0].count = std::numeric_limits<std::size_t>::max();
    ok = check(asset, false, "overflowing count") && ok;
    asset.accessors[0].count = 3;
    asset.accessors[0].byteOffset = std::numeric_limits<std::size_t>::max();
    ok = check(asset, false, "overflowing accessor offset") && ok;
    asset.accessors[0].byteOffset = 0;
    asset.bufferViews[0].byteOffset = std::numeric_limits<std::size_t>::max();
    ok = check(asset, false, "overflowing bufferView offset") && ok;
    asset.bufferViews[0].byteOffset = 0;
    asset.bufferViews[0].byteLength = std::numeric_limits<std::size_t>::max();
    ok = check(asset, false, "overflowing bufferView length") && ok;
    asset.bufferViews[0].byteLength = 36;
    asset.bufferViews[0].byteStride = 0;
    ok = check(asset, false, "zero stride") && ok;
    asset.bufferViews[0].byteStride = 8;
    ok = check(asset, false, "undersized stride") && ok;
    asset.bufferViews[0].byteStride = 12;
    ok = check(asset, true, "valid explicit stride") && ok;
    asset.accessors[0].normalized = true;
    ok = check(asset, false, "unexpected normalization") && ok;
    ok = check(asset, true, "expected normalization", true) && ok;
    asset.accessors[0].normalized = false;
    ok = check(asset, false, "missing normalization", true) && ok;
    asset.buffers[0].byteLength = 37;
    ok = check(asset, false, "short backing data") && ok;
    asset.buffers[0].byteLength = 36;
    asset.accessors[0].type = fastgltf::AccessorType::Vec2;
    asset.accessors[0].componentType = fastgltf::ComponentType::UnsignedByte;
    asset.accessors[0].normalized = true;
    asset.bufferViews[0].byteStride.reset();
    gltfCore::AccessorData result;
    std::string error;
    if (gltfCore::GetAccessorData(asset, 0, fastgltf::AccessorType::Vec2,
                                  fastgltf::ComponentType::UnsignedByte, 2, true, result, error, {},
                                  true) ||
        error.empty())
    {
        std::cerr << "misaligned packed byte vertices were accepted\n";
        ok = false;
    }
    asset.bufferViews[0].byteStride = 4;
    error.clear();
    if (!gltfCore::GetAccessorData(asset, 0, fastgltf::AccessorType::Vec2,
                                   fastgltf::ComponentType::UnsignedByte, 2, true, result, error,
                                   {}, true))
    {
        std::cerr << "aligned byte vertices rejected: " << error << '\n';
        ok = false;
    }
    asset.accessors[0].type = fastgltf::AccessorType::Vec3;
    asset.accessors[0].componentType = fastgltf::ComponentType::Float;
    asset.accessors[0].normalized = false;
    ok = checkInterleavedBounds(asset) && ok;
    return ok ? 0 : 1;
}
