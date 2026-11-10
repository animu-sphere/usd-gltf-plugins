// SPDX-License-Identifier: Apache-2.0
#include "sceneMap.h"

#include "pxr/base/tf/diagnostic.h"
#include "pxr/base/tf/token.h"

#include <fastgltf/core.hpp>

#include <cmath>
#include <limits>
#include <string>
#include <utility>
#include <variant>

namespace gltfUsd
{
namespace
{

using namespace pxr;

bool
nodeMatrix(const fastgltf::Node& node, GfMatrix4d& result)
{
    fastgltf::math::nmat4x4 matrix;
    if (const auto* trs = std::get_if<fastgltf::TRS>(&node.transform))
    {
        const auto& q = trs->rotation;
        const double lengthSquared = double(q.x()) * q.x() + double(q.y()) * q.y() +
                                     double(q.z()) * q.z() + double(q.w()) * q.w();
        if (!std::isfinite(lengthSquared) || std::abs(lengthSquared - 1.0) > 0.001)
        {
            return false;
        }
        // The tolerance accepts rotations that would otherwise scale and shear the node.
        matrix = fastgltf::math::scale(
            fastgltf::math::rotate(
                fastgltf::math::translate(fastgltf::math::nmat4x4(), trs->translation),
                fastgltf::math::normalize(q)),
            trs->scale);
    }
    else
    {
        matrix = std::get<fastgltf::math::nmat4x4>(node.transform);
    }

    if (matrix[0][3] != 0 || matrix[1][3] != 0 || matrix[2][3] != 0 || matrix[3][3] != 1)
    {
        return false;
    }

    // glTF matrices use column vectors; Gf matrices use row vectors.
    for (std::size_t column = 0; column < 4; ++column)
    {
        for (std::size_t row = 0; row < 4; ++row)
        {
            if (!std::isfinite(matrix[column][row]))
            {
                return false;
            }
            result[column][row] = matrix[column][row];
        }
    }
    return true;
}

} // namespace

std::optional<SceneMap>
MapScene(const fastgltf::Asset& asset, std::size_t sceneIndex, const pxr::SdfPath& scenes)
{
    SceneMap map;
    map.sceneIndex = sceneIndex;
    map.scene = scenes.AppendChild(TfToken("Scene_" + std::to_string(sceneIndex)));
    map.positions.resize(asset.nodes.size());
    map.meshes.resize(asset.meshes.size());

    // Each node and its parent's position in map.nodes, if any. Depth first, so that parents come
    // before their children and the prims keep glTF's order of siblings.
    std::vector<std::pair<std::size_t, std::optional<std::size_t>>> pending;
    const auto& roots = asset.scenes[sceneIndex].nodeIndices;
    for (auto it = roots.rbegin(); it != roots.rend(); ++it)
    {
        pending.emplace_back(*it, std::nullopt);
    }
    while (!pending.empty())
    {
        const auto [index, parent] = pending.back();
        pending.pop_back();
        if (index >= asset.nodes.size() || map.positions[index])
        {
            TF_RUNTIME_ERROR("glTF scene contains an invalid, repeated, or cyclic node index %zu",
                             index);
            return std::nullopt;
        }
        const auto& node = asset.nodes[index];
        if (!node.weights.empty() && !node.meshIndex)
        {
            TF_RUNTIME_ERROR("glTF node %zu: morph weights require a mesh", index);
            return std::nullopt;
        }
        MappedNode mapped;
        mapped.index = index;
        mapped.parent = parent;
        if (!nodeMatrix(node, mapped.local))
        {
            TF_RUNTIME_ERROR("glTF node %zu has an invalid transform", index);
            return std::nullopt;
        }
        const auto& parentPath = parent ? map.nodes[*parent].path : map.scene;
        mapped.path = parentPath.AppendChild(TfToken("Node_" + std::to_string(index)));
        // Gf matrices use row vectors, so the parent's transform applies last.
        mapped.world = parent ? mapped.local * map.nodes[*parent].world : mapped.local;
        if (node.meshIndex)
        {
            if (*node.meshIndex >= asset.meshes.size())
            {
                TF_RUNTIME_ERROR("glTF mesh index %zu is out of range", *node.meshIndex);
                return std::nullopt;
            }
            mapped.mesh =
                mapped.path.AppendChild(TfToken("Mesh_" + std::to_string(*node.meshIndex)));
            map.meshes[*node.meshIndex].push_back(mapped.mesh);
            const auto& mesh = asset.meshes[*node.meshIndex];
            const auto count = mesh.primitives.empty() ? 0 : mesh.primitives.front().targets.size();
            const auto& weights = node.weights.empty() ? mesh.weights : node.weights;
            if (!weights.empty() && weights.size() != count)
            {
                TF_RUNTIME_ERROR("glTF node %zu: morph weight count does not match target count",
                                 index);
                return std::nullopt;
            }
            mapped.morphWeights.resize(count, 0.0f);
            for (std::size_t i = 0; i < count; ++i)
            {
                if (!weights.empty())
                {
                    const double weight = weights[i];
                    if (!std::isfinite(weight) ||
                        std::abs(weight) > std::numeric_limits<float>::max())
                    {
                        TF_RUNTIME_ERROR("glTF node %zu: morph weights must be finite floats",
                                         index);
                        return std::nullopt;
                    }
                    mapped.morphWeights[i] = static_cast<float>(weight);
                }
                mapped.blendShapes.emplace_back("Node_" + std::to_string(index) + "_Target_" +
                                                std::to_string(i));
            }
        }
        const auto position = map.nodes.size();
        map.positions[index] = position;
        map.nodes.push_back(std::move(mapped));
        for (auto it = node.children.rbegin(); it != node.children.rend(); ++it)
        {
            pending.emplace_back(*it, position);
        }
    }
    return map;
}

} // namespace gltfUsd
