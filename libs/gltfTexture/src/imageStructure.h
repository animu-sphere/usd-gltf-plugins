// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <cstddef>
#include <cstdint>
#include <span>
#include <string>

namespace gltfTexture
{

// The largest width or height of an image in any format. A small file can declare a
// very large image, and the image loader allocates for it before decoding.
inline constexpr std::uint32_t kMaxImageDimension = 16384;

// These check the structure of a PNG or JPEG that has the right signature, without
// decoding it, so that damage is found before the image loader reads the file. See
// docs/adr/0006-image-failures.md for what they cannot find.

// Requires a valid IHDR, at least one IDAT and an IEND, and every chunk checksum.
[[nodiscard]] bool CheckPng(std::span<const std::byte> bytes, std::string& error);

// Requires 8-bit Huffman baseline or progressive coding, the tables and frame header
// before the first scan, and an end marker after it.
[[nodiscard]] bool CheckJpeg(std::span<const std::byte> bytes, std::string& error);

} // namespace gltfTexture
