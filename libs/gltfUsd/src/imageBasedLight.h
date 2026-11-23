// SPDX-License-Identifier: Apache-2.0
#pragma once

#include "gltfCore/accessor.h"
#include "gltfCore/imageBasedLight.h"

#include <fastgltf/types.hpp>
#include "pxr/usd/usd/prim.h"

#include <string>

namespace gltfUsd
{

// Defines the selected scene's EXT_lights_image_based light as a dome light below the scene's
// prim, after checking its faces without decoding them. See
// docs/adr/0046-image-based-lighting.md. Reports an error and returns false if it cannot.
[[nodiscard]] bool AuthorImageBasedLight(const fastgltf::Asset& asset,
                                         const gltfCore::ImageBasedLight& light,
                                         const std::string& sourcePath,
                                         const gltfCore::BufferReader& buffers,
                                         const pxr::UsdPrim& scene);

} // namespace gltfUsd
