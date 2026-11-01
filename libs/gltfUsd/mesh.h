// SPDX-License-Identifier: Apache-2.0
#pragma once

#include "accessor.h"

#include <fastgltf/types.hpp>
#include "pxr/usd/usd/prim.h"

#include <cstddef>

namespace gltfUsd
{

class MaterialAuthor;

[[nodiscard]] bool AuthorMesh(const fastgltf::Asset& asset, std::size_t meshIndex,
                              const pxr::UsdPrim& parent, const gltfCore::BufferLoader& loadBuffer,
                              MaterialAuthor& materials);

} // namespace gltfUsd
