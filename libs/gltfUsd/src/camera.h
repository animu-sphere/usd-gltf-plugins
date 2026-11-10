// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <fastgltf/types.hpp>
#include "pxr/base/gf/matrix4d.h"
#include "pxr/usd/usd/prim.h"

#include <cstddef>
#include <optional>

namespace gltfUsd
{

// Defines the camera below its node's prim, `parent`, without a transform, and returns its prim.
// Reports an error and returns an invalid prim for a camera that cannot be converted.
[[nodiscard]] pxr::UsdPrim AuthorCamera(const fastgltf::Asset& asset, std::size_t cameraIndex,
                                        const pxr::UsdPrim& parent);

// The transform of the camera's prim that cancels the scale and shear USD would apply to it from
// its node, whose transform relative to the asset is `parentWorld`. Reports an error and returns
// nullopt when that transform is degenerate, reflected or sheared.
[[nodiscard]] std::optional<pxr::GfMatrix4d> CameraCorrection(std::size_t nodeIndex,
                                                              std::size_t cameraIndex,
                                                              const pxr::GfMatrix4d& parentWorld);

} // namespace gltfUsd
