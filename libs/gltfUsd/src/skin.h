// SPDX-License-Identifier: Apache-2.0
#pragma once

#include "animation.h"
#include "sceneMap.h"

#include "gltfCore/accessor.h"

#include "pxr/usd/usd/common.h"

namespace gltfUsd
{

// Extends the scene map with the skeletons and skel roots, and clears the paths of joint-only nodes.
[[nodiscard]] bool MapSkins(const fastgltf::Asset& asset, const gltfCore::BufferLoader& loadBuffer,
                            SceneMap& map);

[[nodiscard]] bool AuthorSkeletons(const fastgltf::Asset& asset, const SceneMap& map,
                                   const pxr::UsdStagePtr& stage);

// Authors joint and morph values and their skeleton bindings in the current edit target.
[[nodiscard]] bool AuthorSkelAnimation(const SceneMap& map, const AnimationValues& values,
                                       const pxr::UsdStagePtr& stage, const pxr::SdfPath& root,
                                       const pxr::TfToken& name);

} // namespace gltfUsd
