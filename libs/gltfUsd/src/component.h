// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <cstddef>
#include <cstdint>

namespace gltfUsd
{

// glTF stores all accessor components in little-endian order.
inline std::uint32_t
ReadUnsigned(const std::byte* data, std::size_t size)
{
    std::uint32_t value = 0;
    for (std::size_t i = 0; i < size; ++i)
    {
        value |= std::uint32_t(std::to_integer<unsigned char>(data[i])) << (8 * i);
    }
    return value;
}

} // namespace gltfUsd
