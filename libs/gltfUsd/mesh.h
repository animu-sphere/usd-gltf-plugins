#pragma once

#include <fastgltf/types.hpp>
#include "pxr/usd/sdf/primSpec.h"

#include <cstddef>

namespace gltfUsd {

[[nodiscard]] bool AuthorMesh(const fastgltf::Asset& asset, std::size_t meshIndex,
                              const pxr::SdfPrimSpecHandle& parent);

} // namespace gltfUsd
