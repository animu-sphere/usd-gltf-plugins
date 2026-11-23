// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

namespace gltfCore
{

inline constexpr std::string_view kImageBasedLightExtension = "EXT_lights_image_based";

// The light of EXT_lights_image_based that the selected scene references, as the file states it
// after the checks of docs/adr/0046-image-based-lighting.md.
struct ImageBasedLight
{
    // Into the root's lights.
    std::size_t index = 0;
    std::string name;
    // In glTF's order, x, y, z, w, and normalized.
    std::array<double, 4> rotation = {0, 0, 0, 1};
    double intensity = 1;
    // A power of two, the size of the first level's faces.
    std::uint32_t specularImageSize = 0;
    // By level, each face's image index in the order +X, -X, +Y, -Y, +Z, -Z.
    std::vector<std::array<std::size_t, 6>> specularImages;
    std::array<std::array<double, 3>, 9> irradianceCoefficients = {};
};

// Reads the light that the file's default scene, or scene 0 without one, references. Leaves
// `light` empty if it references none. On failure, `error` says what is malformed; Parse fails for
// the same files.
[[nodiscard]] bool ReadImageBasedLight(const std::byte* bytes, std::size_t size,
                                       std::optional<ImageBasedLight>& light, std::string& error);

} // namespace gltfCore
