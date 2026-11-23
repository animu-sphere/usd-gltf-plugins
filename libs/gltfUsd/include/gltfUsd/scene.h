// SPDX-License-Identifier: Apache-2.0
#pragma once

#include "gltfCore/accessor.h"
#include "gltfCore/xmp.h"

#include "pxr/pxr.h"
#include "pxr/usd/sdf/layer.h"

#include <fastgltf/types.hpp>

#include <string>

namespace gltfUsd
{

// loadBuffer is invoked synchronously on the conversion worker thread.
// sourcePath is the resolved source file, which image paths are relative to. xmp is the file's
// KHR_xmp_json_ld, which fastgltf does not read.
[[nodiscard]] pxr::SdfLayerRefPtr ConvertScene(const fastgltf::Asset& asset,
                                               const std::string& sourcePath,
                                               const gltfCore::BufferLoader& loadBuffer,
                                               const gltfCore::XmpMetadata& xmp = {});

} // namespace gltfUsd
