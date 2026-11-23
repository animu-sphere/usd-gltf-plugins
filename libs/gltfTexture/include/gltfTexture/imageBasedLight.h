// SPDX-License-Identifier: Apache-2.0
#pragma once

#include "gltfCore/accessor.h"
#include "gltfCore/imageBasedLight.h"

#include <fastgltf/types.hpp>

#include <array>
#include <cstddef>
#include <cstdint>
#include <span>
#include <string>
#include <string_view>
#include <vector>

namespace gltfTexture
{

// The latlong of an EXT_lights_image_based light, as docs/adr/0046-image-based-lighting.md decides.
// Its faces are decoded only here, which the package resolver calls, and never at import.

// Names a light's latlong inside its .gltf or .glb package: lights/<index>.hdr.
[[nodiscard]] std::string MakeLightEntry(std::size_t lightIndex);
[[nodiscard]] bool ParseLightEntry(std::string_view entry, std::size_t& lightIndex);

// The latlong's size for faces of `faceSize`: 4N by 2N, but at most 8192 by 4096.
void LatlongSize(std::uint32_t faceSize, std::uint32_t& width, std::uint32_t& height);

// Checks each face of each level without decoding it: PNG or JPEG, its structure, and a square of
// the light's size halved once for each level before it.
[[nodiscard]] bool CheckImageBasedLight(const fastgltf::Asset& asset,
                                        const gltfCore::ImageBasedLight& light,
                                        const std::string& sourcePath,
                                        const gltfCore::BufferReader& buffers, std::string& error);

// Decodes the six faces of the first level, in the order +X, -X, +Y, -Y, +Z, -Z, and writes their
// latlong as a Radiance RGBE image. Each face must be a PNG or JPEG of `faceSize` square.
[[nodiscard]] bool ConvertCubeToRgbe(const std::array<std::span<const std::byte>, 6>& faces,
                                     std::uint32_t faceSize, std::vector<std::byte>& hdr,
                                     std::string& error);

// Reads the light's first level from the asset, checks it, and converts it as above.
[[nodiscard]] bool ConvertImageBasedLight(const fastgltf::Asset& asset,
                                          const gltfCore::ImageBasedLight& light,
                                          const std::string& sourcePath,
                                          const gltfCore::BufferReader& buffers,
                                          std::vector<std::byte>& hdr, std::string& error);

} // namespace gltfTexture
