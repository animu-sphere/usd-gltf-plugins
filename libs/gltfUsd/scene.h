// SPDX-License-Identifier: Apache-2.0
#pragma once

#include "accessor.h"

#include "pxr/pxr.h"
#include "pxr/usd/sdf/layer.h"

#include <fastgltf/types.hpp>

namespace gltfUsd
{

// loadBuffer is invoked synchronously on the conversion worker thread.
[[nodiscard]] pxr::SdfLayerRefPtr ConvertScene(const fastgltf::Asset& asset,
                                               const gltfCore::BufferLoader& loadBuffer);

} // namespace gltfUsd
