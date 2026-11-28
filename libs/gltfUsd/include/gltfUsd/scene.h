// SPDX-License-Identifier: Apache-2.0
#pragma once

#include "gltfCore/accessor.h"
#include "gltfCore/imageBasedLight.h"
#include "gltfCore/xmp.h"

#include "pxr/pxr.h"
#include "pxr/usd/sdf/layer.h"

#include <fastgltf/types.hpp>

#include <optional>
#include <string>

namespace gltfUsd
{

// loadBuffer is invoked synchronously on the conversion worker thread.
// sourcePath is the resolved source file, which image paths are relative to. xmp is the file's
// KHR_xmp_json_ld, and imageBasedLight the light of EXT_lights_image_based that the selected scene
// references, which fastgltf does not read.
[[nodiscard]] pxr::SdfLayerRefPtr
ConvertScene(const fastgltf::Asset& asset, const std::string& sourcePath,
             const gltfCore::BufferLoader& loadBuffer, const gltfCore::XmpMetadata& xmp = {},
             const std::optional<gltfCore::ImageBasedLight>& imageBasedLight = std::nullopt);

} // namespace gltfUsd
