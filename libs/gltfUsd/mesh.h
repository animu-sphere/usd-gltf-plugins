// SPDX-License-Identifier: Apache-2.0
#pragma once

#include "accessor.h"

#include <fastgltf/types.hpp>
#include "pxr/usd/sdf/primSpec.h"

#include <cstddef>

namespace gltfUsd
{

[[nodiscard]] bool AuthorMesh(const fastgltf::Asset& asset, std::size_t meshIndex,
                              const pxr::SdfPrimSpecHandle& parent,
                              const gltfCore::BufferLoader& loadBuffer);

} // namespace gltfUsd
