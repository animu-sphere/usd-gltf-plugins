// SPDX-License-Identifier: Apache-2.0
#pragma once

#include "pxr/pxr.h"
#include "pxr/base/gf/matrix4d.h"
#include "pxr/base/tf/token.h"
#include "pxr/base/vt/array.h"
#include "pxr/usd/sdf/path.h"

#include <fastgltf/types.hpp>

#include <cstddef>
#include <map>
#include <optional>
#include <vector>

namespace gltfUsd
{

// A node of the selected scene, where conversion puts it.
struct MappedNode
{
    std::size_t index = 0;
    // The parent's position in SceneMap::nodes, or nullopt for a root node of the scene.
    std::optional<std::size_t> parent;
    // Empty after skin placement for a joint without a prim.
    pxr::SdfPath path;
    // The path of the node's mesh, or empty for a node without one.
    pxr::SdfPath mesh;
    // Per-placement morph bindings, in source target order.
    pxr::VtTokenArray blendShapes;
    pxr::VtFloatArray morphWeights;
    pxr::SdfPath morphSkeleton;
    // As the node states it, without animation.
    pxr::GfMatrix4d local{1.0};
    // Relative to the asset, and so to the scene, which has no transform, without animation.
    pxr::GfMatrix4d world{1.0};
};

struct MappedJoint
{
    std::size_t position; // In SceneMap::nodes
    pxr::TfToken token;
    pxr::GfMatrix4d bind;
};

struct MappedSkin
{
    pxr::SdfPath path;
    std::vector<MappedJoint> joints; // In scene order, including nodes between source joints
    std::vector<int> remap;          // From the glTF skin's joint order to `joints`
};

// Where conversion puts the selected scene's source objects.
struct SceneMap
{
    std::size_t sceneIndex = 0;
    pxr::SdfPath scene;
    // Parents before their children, and siblings in their glTF order.
    std::vector<MappedNode> nodes;
    // By node index, the node's position in `nodes`, or nullopt for a node outside the scene.
    std::vector<std::optional<std::size_t>> positions;
    // By mesh index, the mesh's path below each node that uses it, in the order of `nodes`.
    std::vector<std::vector<pxr::SdfPath>> meshes;
    // By skin index. Only skins used in the selected scene have skeletons.
    std::map<std::size_t, MappedSkin> skins;
    // Existing scene or node paths whose types become SkelRoot.
    pxr::SdfPathVector skelRoots;
};

// Checks the scene's nodes and maps them below `scenes`, without authoring anything. Reports an
// error and returns nullopt for an invalid, repeated or cyclic node, an invalid transform, a mesh
// index out of range or content that cannot be converted yet.
[[nodiscard]] std::optional<SceneMap> MapScene(const fastgltf::Asset& asset, std::size_t sceneIndex,
                                               const pxr::SdfPath& scenes);

} // namespace gltfUsd
