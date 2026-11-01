#pragma once

#include "pxr/pxr.h"
#include "pxr/usd/sdf/layer.h"

#include <fastgltf/types.hpp>

namespace gltfUsd {

[[nodiscard]] pxr::SdfLayerRefPtr ConvertScene(const fastgltf::Asset& asset);

} // namespace gltfUsd
