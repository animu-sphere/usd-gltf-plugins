// SPDX-License-Identifier: Apache-2.0
#pragma once

#include "gltfCore/draco.h"
#include "material.h"

#include <fastgltf/types.hpp>
#include "pxr/usd/sdf/path.h"
#include "pxr/usd/usd/common.h"

#include <cstddef>
#include <map>
#include <optional>
#include <utility>

namespace gltfUsd
{

struct AnimationPlan;
struct MappedNode;
struct MappedSkin;

// Decoded primitives shared by geometry authoring and later skin bindings in one conversion.
using DecodedMeshes =
    std::map<std::pair<std::size_t, std::size_t>, gltfCore::DecodedDracoPrimitive>;

// Only primitives with mappings need deferred binding; their geometry is still copied normally.
// Keyed by mesh and primitive index, then by the glTF material index (absent for glTF's default).
using VariantBindings = std::map<std::pair<std::size_t, std::size_t>,
                                 std::map<std::optional<std::size_t>, MaterialBinding>>;

// Authors the mesh as an Xform at `path`, with a Mesh child for each primitive, bound to its
// material. A node that uses the mesh places it by this or a copy of its result.
[[nodiscard]] bool ConvertMesh(const fastgltf::Asset& asset, std::size_t meshIndex,
                               const pxr::UsdStagePtr& stage, const pxr::SdfPath& path,
                               const gltfCore::BufferReader& buffers, MaterialAuthor& materials,
                               DecodedMeshes& decodedMeshes, VariantBindings& variantBindings);

// Binds a placed copy to its node's skeleton after geometry has been copied to every placement.
[[nodiscard]] bool BindSkinnedMesh(const fastgltf::Asset& asset, std::size_t meshIndex,
                                   const pxr::UsdStagePtr& stage, const pxr::SdfPath& path,
                                   const MappedSkin& skin, const gltfCore::BufferReader& buffers,
                                   const DecodedMeshes& decodedMeshes);

[[nodiscard]] bool BindMorphMesh(const fastgltf::Mesh& source, const pxr::UsdStagePtr& stage,
                                 const MappedNode& node, const AnimationPlan& animations,
                                 std::size_t position);

} // namespace gltfUsd
