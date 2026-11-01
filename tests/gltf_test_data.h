#pragma once

#include <cstddef>
#include <cstdint>
#include <string>
#include <string_view>
#include <vector>

namespace gltfTestData {

inline std::vector<std::byte> bytesOf(std::string_view text)
{
    const auto* begin = reinterpret_cast<const std::byte*>(text.data());
    return {begin, begin + text.size()};
}

inline void appendUint32(std::vector<std::byte>& bytes, std::uint32_t value)
{
    for (unsigned int shift = 0; shift < 32; shift += 8) {
        bytes.push_back(static_cast<std::byte>((value >> shift) & 0xff));
    }
}

inline std::vector<std::byte> glbOf(std::string text)
{
    while (text.size() % 4 != 0) {
        text.push_back(' ');
    }
    std::vector<std::byte> bytes;
    appendUint32(bytes, 0x46546c67);
    appendUint32(bytes, 2);
    appendUint32(bytes, static_cast<std::uint32_t>(20 + text.size()));
    appendUint32(bytes, static_cast<std::uint32_t>(text.size()));
    appendUint32(bytes, 0x4e4f534a);
    auto json = bytesOf(text);
    bytes.insert(bytes.end(), json.begin(), json.end());
    return bytes;
}

} // namespace gltfTestData
