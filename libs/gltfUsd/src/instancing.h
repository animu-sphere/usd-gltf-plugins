// SPDX-License-Identifier: Apache-2.0
#pragma once

#include "gltfCore/accessor.h"

#include <fastgltf/types.hpp>
#include "pxr/usd/sdf/path.h"
#include "pxr/usd/usd/common.h"

#include <cstddef>

namespace gltfUsd
{

struct MappedNode;

// Defines the node's EXT_mesh_gpu_instancing instances as the UsdGeomPointInstancer
// `node.instancer`, with one instance of the prototype `node.mesh` for each element of its
// attributes. The prototype is placed afterwards, and AuthorInstancerExtent then bounds it.
[[nodiscard]] bool DefineInstancer(const fastgltf::Asset& asset, const MappedNode& node,
                                   const pxr::UsdStagePtr& stage,
                                   const gltfCore::BufferReader& buffers);

[[nodiscard]] bool AuthorInstancerExtent(const MappedNode& node, const pxr::UsdStagePtr& stage);

} // namespace gltfUsd
