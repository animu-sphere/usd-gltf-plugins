// SPDX-License-Identifier: Apache-2.0
#pragma once

#include "gltfCore/accessor.h"

#include <fastgltf/types.hpp>
#include "pxr/usd/sdf/path.h"
#include "pxr/usd/usd/common.h"

#include <cstddef>

namespace gltfUsd
{

class MaterialAuthor;
struct AnimationPlan;
struct MappedNode;
struct MappedSkin;

// Authors the mesh as an Xform at `path`, with a Mesh child for each primitive, bound to its
// material. A node that uses the mesh places it by this or a copy of its result.
[[nodiscard]] bool ConvertMesh(const fastgltf::Asset& asset, std::size_t meshIndex,
                               const pxr::UsdStagePtr& stage, const pxr::SdfPath& path,
                               const gltfCore::BufferLoader& loadBuffer, MaterialAuthor& materials);

// Binds a placed copy to its node's skeleton after geometry has been copied to every placement.
[[nodiscard]] bool BindSkinnedMesh(const fastgltf::Asset& asset, std::size_t meshIndex,
                                   const pxr::UsdStagePtr& stage, const pxr::SdfPath& path,
                                   const MappedSkin& skin,
                                   const gltfCore::BufferLoader& loadBuffer);

[[nodiscard]] bool BindMorphMesh(const fastgltf::Mesh& source, const pxr::UsdStagePtr& stage,
                                 const MappedNode& node, const AnimationPlan& animations,
                                 std::size_t position);

} // namespace gltfUsd
