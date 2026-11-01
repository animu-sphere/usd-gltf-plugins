#pragma once

#include <fastgltf/core.hpp>

#include <cstddef>

namespace gltfCore {

[[nodiscard]] fastgltf::Expected<fastgltf::Asset> Parse(const std::byte* bytes,
                                                       std::size_t size);

} // namespace gltfCore
