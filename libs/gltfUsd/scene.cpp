// SPDX-License-Identifier: Apache-2.0
#include "scene.h"

#include "material.h"
#include "mesh.h"
#include "sceneSelection.h"

#include "pxr/base/gf/matrix4d.h"
#include "pxr/base/tf/diagnostic.h"
#include "pxr/base/tf/errorMark.h"
#include "pxr/base/tf/errorTransport.h"
#include "pxr/base/vt/array.h"
#include "pxr/base/vt/value.h"
#include "pxr/usd/usd/prim.h"
#include "pxr/usd/usd/stage.h"
#include "pxr/usd/usdGeom/metrics.h"
#include "pxr/usd/usdGeom/scope.h"
#include "pxr/usd/usdGeom/tokens.h"
#include "pxr/usd/usdGeom/xform.h"

#include <cmath>
#include <exception>
#include <future>
#include <string>
#include <utility>
#include <variant>
#include <vector>

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
        matrix = fastgltf::math::scale(
            fastgltf::math::rotate(
                fastgltf::math::translate(fastgltf::math::nmat4x4(), trs->translation),
                trs->rotation),
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

SdfLayerRefPtr
authorScene(const fastgltf::Asset& asset, const std::string& sourcePath,
            const gltfCore::BufferLoader& loadBuffer, std::size_t selectedIndex)
{
    TfErrorMark mark;
    auto layer = SdfLayer::CreateAnonymous("gltf-conversion.usda");
    if (!layer)
    {
        TF_RUNTIME_ERROR("Could not create temporary glTF layer");
        return {};
    }
    const auto stage = UsdStage::Open(layer);
    if (!stage)
    {
        TF_RUNTIME_ERROR("Could not open temporary glTF authoring stage");
        return {};
    }
    const auto root = UsdGeomXform::Define(stage, SdfPath("/Asset"));
    if (!root)
    {
        TF_RUNTIME_ERROR("Could not author glTF layer root");
        return {};
    }
    const auto geo = UsdGeomScope::Define(stage, root.GetPath().AppendChild(TfToken("geo")));
    const auto mtl = UsdGeomScope::Define(stage, root.GetPath().AppendChild(TfToken("mtl")));
    if (!geo || !mtl)
    {
        TF_RUNTIME_ERROR("Could not author glTF layer scopes");
        return {};
    }
    stage->SetDefaultPrim(root.GetPrim());
    if (!UsdGeomSetStageUpAxis(stage, UsdGeomTokens->y) ||
        !UsdGeomSetStageMetersPerUnit(stage, 1.0))
    {
        TF_RUNTIME_ERROR("Could not author glTF stage metrics");
        return {};
    }

    MaterialAuthor materials(asset, sourcePath, loadBuffer, stage, mtl.GetPath());
    std::vector<bool> visited(asset.nodes.size(), false);
    std::vector<std::pair<std::size_t, UsdPrim>> pending;
    const auto& roots = asset.scenes[selectedIndex].nodeIndices;
    for (auto it = roots.rbegin(); it != roots.rend(); ++it)
    {
        pending.emplace_back(*it, geo.GetPrim());
    }
    while (!pending.empty())
    {
        auto [index, parent] = std::move(pending.back());
        pending.pop_back();
        if (index >= asset.nodes.size() || visited[index])
        {
            TF_RUNTIME_ERROR("glTF scene contains an invalid, repeated, or cyclic node index %zu",
                             index);
            return {};
        }
        visited[index] = true;
        const auto& node = asset.nodes[index];
        if (node.skinIndex || node.cameraIndex || node.lightIndex || !node.weights.empty())
        {
            TF_RUNTIME_ERROR(
                "glTF node %zu uses skin, camera, light, or morph data that is not supported yet",
                index);
            return {};
        }
        GfMatrix4d matrix;
        if (!nodeMatrix(node, matrix))
        {
            TF_RUNTIME_ERROR("glTF node %zu has an invalid transform", index);
            return {};
        }
        const auto xform = UsdGeomXform::Define(
            stage, parent.GetPath().AppendChild(TfToken("Node_" + std::to_string(index))));
        if (!xform ||
            (!node.name.empty() && !xform.GetPrim().SetDisplayName(std::string(node.name))))
        {
            TF_RUNTIME_ERROR("Could not author glTF node %zu", index);
            return {};
        }
        if (!xform.AddTransformOp().Set(matrix))
        {
            TF_RUNTIME_ERROR("Could not author glTF node %zu transform", index);
            return {};
        }
        if (node.meshIndex &&
            !AuthorMesh(asset, *node.meshIndex, xform.GetPrim(), loadBuffer, materials))
        {
            return {};
        }
        for (auto it = node.children.rbegin(); it != node.children.rend(); ++it)
        {
            pending.emplace_back(*it, xform.GetPrim());
        }
    }
    if (!mark.IsClean())
    {
        TF_RUNTIME_ERROR("Errors occurred while authoring the glTF layer");
        return {};
    }
    return layer;
}

} // namespace

pxr::SdfLayerRefPtr
ConvertScene(const fastgltf::Asset& asset, const std::string& sourcePath,
             const gltfCore::BufferLoader& loadBuffer)
{
    using namespace pxr;
    const auto selection = gltfCore::SelectScene(asset);
    const auto* selected = std::get_if<gltfCore::SelectedScene>(&selection);
    if (!selected)
    {
        const auto error = std::get<gltfCore::SceneSelectionError>(selection);
        TF_RUNTIME_ERROR("glTF scene selection failed: %s",
                         error == gltfCore::SceneSelectionError::NoScenes
                             ? "no scenes"
                             : "default scene index is out of range");
        return {};
    }
    if (selected->usedFallback)
    {
        TF_WARN("glTF has no default scene; selecting scene 0");
    }
    if (!asset.animations.empty())
    {
        TF_RUNTIME_ERROR("glTF animations are not supported yet");
        return {};
    }
    for (const auto& extension : asset.extensionsUsed)
    {
        TF_WARN("glTF optional extension '%s' is not supported", extension.c_str());
    }

    // Reload can hold a change block on this thread; author the stage on another thread.
    TfErrorTransport errors;
    SdfLayerRefPtr result;
    try
    {
        auto task =
            std::async(std::launch::async,
                       [&]()
                       {
                           TfErrorMark mark;
                           SdfLayerRefPtr staged;
                           try
                           {
                               staged = authorScene(asset, sourcePath, loadBuffer, selected->index);
                           }
                           catch (const std::exception& e)
                           {
                               TF_RUNTIME_ERROR("glTF authoring failed: %s", e.what());
                           }
                           mark.TransportTo(errors);
                           return staged;
                       });
        result = task.get();
    }
    catch (const std::exception& e)
    {
        TF_RUNTIME_ERROR("Could not run glTF authoring: %s", e.what());
        return {};
    }
    const bool failed = !errors.IsEmpty();
    errors.Post();
    if (failed)
    {
        return {};
    }
    if (!result)
    {
        TF_RUNTIME_ERROR("glTF authoring did not produce a layer");
    }
    return result;
}

} // namespace gltfUsd
