// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <fastgltf/types.hpp>
#include "pxr/base/gf/matrix4d.h"
#include "pxr/usd/usd/prim.h"

#include <cstddef>

namespace gltfUsd
{

// parent is the node's prim, and parentWorld its transform relative to the asset.
[[nodiscard]] bool AuthorLight(const fastgltf::Asset& asset, std::size_t nodeIndex,
                               std::size_t lightIndex, const pxr::UsdPrim& parent,
                               const pxr::GfMatrix4d& parentWorld);

} // namespace gltfUsd
