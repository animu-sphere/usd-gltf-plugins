// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <cstddef>
#include <cstdint>
#include <span>
#include <string>
#include <vector>

namespace gltfTexture
{

// Reads the header, level index and metadata without transcoding. Accepts a single
// 2D image in ETC1S or UASTC LDR 4x4, within the transcoder's dimension limit.
[[nodiscard]] bool CheckKtx2(std::span<const std::byte> bytes, std::string& error);

// Transcodes the first mip level to RGBA, which is stored as a PNG.
[[nodiscard]] bool DecodeKtx2ToPng(std::span<const std::byte> bytes, std::vector<std::byte>& png,
                                   std::string& error);

// Writes 8-bit RGBA without compression, because the result is only held in memory
// until the image loader has read it. `rgba` must hold width * height * 4 bytes.
[[nodiscard]] std::vector<std::byte> EncodePng(std::uint32_t width, std::uint32_t height,
                                               std::span<const std::uint8_t> rgba);

// The checksum of a PNG chunk.
[[nodiscard]] std::uint32_t Crc32(std::span<const std::byte> bytes);

} // namespace gltfTexture
