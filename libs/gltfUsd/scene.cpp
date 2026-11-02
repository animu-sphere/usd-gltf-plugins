// SPDX-License-Identifier: Apache-2.0
#include "scene.h"

#include "material.h"
#include "mesh.h"
#include "parse.h"
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

#include <fastgltf/core.hpp>

#include <cmath>
#include <condition_variable>
#include <deque>
#include <exception>
#include <future>
#include <mutex>
#include <string>
#include <string_view>
#include <thread>
#include <utility>
#include <variant>
#include <vector>

namespace gltfUsd
{
namespace
{

using namespace pxr;

// Returns what a node uses that cannot be converted, or nullptr.
const char*
unsupportedNodeContent(const fastgltf::Node& node)
{
    if (node.skinIndex)
    {
        return "skins are";
    }
    if (node.cameraIndex)
    {
        return "cameras are";
    }
    if (node.lightIndex)
    {
        return "KHR_lights_punctual lights are";
    }
    return node.weights.empty() ? nullptr : "morph weights are";
}

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
        // The tolerance accepts rotations that would otherwise scale and shear the node. This does
        // not use fastgltf::math::normalize, whose quaternion division multiplies in fastgltf 1.0.0.
        const double length = std::sqrt(lengthSquared);
        const fastgltf::math::nquat rotation(
            fastgltf::num(q.x() / length), fastgltf::num(q.y() / length),
            fastgltf::num(q.z() / length), fastgltf::num(q.w() / length));
        matrix = fastgltf::math::scale(
            fastgltf::math::rotate(
                fastgltf::math::translate(fastgltf::math::nmat4x4(), trs->translation), rotation),
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
        if (const char* unsupported = unsupportedNodeContent(node))
        {
            TF_RUNTIME_ERROR("glTF node %zu: %s not supported yet", index, unsupported);
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

// Runs authoring on one thread that lives as long as the process, one task at a time. A thread per
// read crashed intermittently in TBB on macOS, without this plugin too: see docs/architecture/
// LAYER_AUTHORING.md. Never destroyed, because the thread can outlive static destruction.
class AuthoringThread
{
  public:
    static AuthoringThread&
    Get()
    {
        static AuthoringThread* const instance = new AuthoringThread;
        return *instance;
    }

    template <class Function>
    SdfLayerRefPtr
    Run(Function function)
    {
        // A resolver or buffer loader could read another glTF layer while authoring.
        if (std::this_thread::get_id() == _thread.get_id())
        {
            return function();
        }
        std::packaged_task<SdfLayerRefPtr()> task(std::move(function));
        auto result = task.get_future();
        {
            const std::lock_guard<std::mutex> lock(_mutex);
            _tasks.push_back(std::move(task));
        }
        _wake.notify_one();
        return result.get();
    }

  private:
    AuthoringThread() : _thread([this] { _Loop(); })
    {
    }

    void
    _Loop()
    {
        for (;;)
        {
            std::packaged_task<SdfLayerRefPtr()> task;
            {
                std::unique_lock<std::mutex> lock(_mutex);
                _wake.wait(lock, [this] { return !_tasks.empty(); });
                task = std::move(_tasks.front());
                _tasks.pop_front();
            }
            task();
        }
    }

    std::mutex _mutex;
    std::condition_variable _wake;
    std::deque<std::packaged_task<SdfLayerRefPtr()>> _tasks;
    std::thread _thread; // Last, so that the members above exist when it starts.
};

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
        if (!gltfCore::IsImplementedExtension(extension))
        {
            TF_WARN("glTF optional extension '%s' is not supported", extension.c_str());
        }
    }

    // Reload can hold a change block on this thread; author the stage on another thread.
    TfErrorTransport errors;
    SdfLayerRefPtr result;
    try
    {
        result = AuthoringThread::Get().Run(
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
