// SPDX-License-Identifier: Apache-2.0
#pragma once

#include "pxr/pxr.h"
#include "pxr/base/gf/matrix4d.h"
#include "pxr/usd/sdf/path.h"

#include <fastgltf/types.hpp>

#include <cstddef>
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
    pxr::SdfPath path;
    // The path of the node's mesh, or empty for a node without one.
    pxr::SdfPath mesh;
    // At time 0 when an animation moves the node or its ancestors, and otherwise as the node states.
    pxr::GfMatrix4d local{1.0};
    // Relative to the asset, and so to the scene, which has no transform.
    pxr::GfMatrix4d world{1.0};
};

// Where conversion puts the selected scene's source objects. Paths are kept rather than derived from
// glTF parents, because skins will place joints and skel roots where glTF has no prim.
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
};

// Checks the scene's nodes and maps them below `scenes`, without authoring anything. Reports an
// error and returns nullopt for an invalid, repeated or cyclic node, an invalid transform, a mesh
// index out of range or content that cannot be converted yet.
[[nodiscard]] std::optional<SceneMap> MapScene(const fastgltf::Asset& asset, std::size_t sceneIndex,
                                               const pxr::SdfPath& scenes);

} // namespace gltfUsd
