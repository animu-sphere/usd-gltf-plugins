// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <cstddef>
#include <span>
#include <string>
#include <vector>

namespace gltfTexture
{

// Reads the RIFF chunks and the bitstream header without decoding. Accepts a still lossy or
// lossless image, with or without alpha, within the dimension limit.
[[nodiscard]] bool CheckWebp(std::span<const std::byte> bytes, std::string& error);

// Decodes to non-premultiplied RGBA, which is stored as a PNG.
[[nodiscard]] bool DecodeWebpToPng(std::span<const std::byte> bytes, std::vector<std::byte>& png,
                                   std::string& error);

} // namespace gltfTexture
