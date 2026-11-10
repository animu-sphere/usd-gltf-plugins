// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <fastgltf/types.hpp>
#include "pxr/base/gf/matrix4d.h"
#include "pxr/usd/usd/prim.h"

#include <cstddef>
#include <optional>

namespace gltfUsd
{

// Defines the light below its node's prim, `parent`, without a transform, and returns its prim.
// Reports an error and returns an invalid prim for a light that cannot be converted.
[[nodiscard]] pxr::UsdPrim AuthorLight(const fastgltf::Asset& asset, std::size_t nodeIndex,
                                       std::size_t lightIndex, const pxr::UsdPrim& parent);

// The transform of the light's prim that cancels the scale USD would apply to it from
// its node, whose transform relative to the asset is `parentWorld`. Reports an error and returns
// nullopt when that transform is degenerate.
[[nodiscard]] std::optional<pxr::GfMatrix4d>
LightCorrection(std::size_t nodeIndex, std::size_t lightIndex, const pxr::GfMatrix4d& parentWorld);

} // namespace gltfUsd
