// SPDX-License-Identifier: Apache-2.0
#include "gltfUsd/scene.h"

#include "animation.h"
#include "camera.h"
#include "light.h"
#include "material.h"
#include "mesh.h"
#include "gltfCore/parse.h"
#include "sceneMap.h"
#include "gltfCore/sceneSelection.h"

#include "pxr/base/gf/matrix4d.h"
#include "pxr/base/tf/diagnostic.h"
#include "pxr/base/tf/errorMark.h"
#include "pxr/base/tf/errorTransport.h"
#include "pxr/base/vt/array.h"
#include "pxr/base/vt/value.h"
#include "pxr/usd/sdf/copyUtils.h"
#include "pxr/usd/usd/prim.h"
#include "pxr/usd/usd/stage.h"
#include "pxr/usd/usdGeom/metrics.h"
#include "pxr/usd/usdGeom/scope.h"
#include "pxr/usd/usdGeom/tokens.h"
#include "pxr/usd/usdGeom/xform.h"
#include "pxr/usd/usdGeom/xformOp.h"

#include <fastgltf/core.hpp>

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

// Converts a mesh at its first placement, and copies the result to the others, so that each mesh is
// read once and every node that uses it has the same prims.
bool
placeMesh(const fastgltf::Asset& asset, const SceneMap& map, std::size_t meshIndex,
          const SdfPath& path, const UsdStageRefPtr& stage,
          const gltfCore::BufferLoader& loadBuffer, MaterialAuthor& materials)
{
    const auto& first = map.meshes[meshIndex].front();
    if (path == first)
    {
        return ConvertMesh(asset, meshIndex, stage, path, loadBuffer, materials);
    }
    // Material bindings target /Asset/mtl, outside the copy, so they are copied unchanged.
    const auto layer = stage->GetRootLayer();
    if (!SdfCopySpec(layer, first, layer, path))
    {
        TF_RUNTIME_ERROR("Could not place glTF mesh %zu at %s", meshIndex, path.GetText());
        return false;
    }
    return true;
}

// The stated value as the operation's default, and the animation's values as its time samples,
// written to the layer directly because an animation can have very many.
template <class T>
bool
setValues(const UsdGeomXformOp& op, const T& stated, const TimeSamples<T>& samples,
          const SdfLayerHandle& layer)
{
    if (!op || !op.Set(stated))
    {
        return false;
    }
    const auto path = op.GetAttr().GetPath();
    for (const auto& [code, value] : samples)
    {
        layer->SetTimeSample(path, code, value);
    }
    return true;
}

// glTF composes T * R * S, which is the order of these operations.
bool
authorAnimatedTransform(const UsdGeomXform& xform, const NodeAnimation& animation,
                        const SdfLayerHandle& layer)
{
    return setValues(xform.AddTranslateOp(), animation.statedTranslation, animation.translation,
                     layer) &&
           setValues(xform.AddOrientOp(), animation.statedRotation, animation.rotation, layer) &&
           setValues(xform.AddScaleOp(), animation.statedScale, animation.scale, layer);
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
    const auto scenes = UsdGeomScope::Define(stage, root.GetPath().AppendChild(TfToken("scenes")));
    const auto mtl = UsdGeomScope::Define(stage, root.GetPath().AppendChild(TfToken("mtl")));
    if (!scenes || !mtl)
    {
        TF_RUNTIME_ERROR("Could not author glTF layer scopes");
        return {};
    }
    auto map = MapScene(asset, selectedIndex, scenes.GetPath());
    AnimationPlan animation;
    if (!map || !PlanAnimation(asset, loadBuffer, *map, animation))
    {
        return {};
    }
    // The scene's prim has no transform, so nodes keep the transforms they have in glTF.
    const auto& selected = asset.scenes[selectedIndex];
    const auto scene = UsdGeomXform::Define(stage, map->scene);
    if (!scene ||
        (!selected.name.empty() && !scene.GetPrim().SetDisplayName(std::string(selected.name))))
    {
        TF_RUNTIME_ERROR("Could not author glTF scene %zu", selectedIndex);
        return {};
    }
    stage->SetDefaultPrim(root.GetPrim());
    if (!UsdGeomSetStageUpAxis(stage, UsdGeomTokens->y) ||
        !UsdGeomSetStageMetersPerUnit(stage, 1.0))
    {
        TF_RUNTIME_ERROR("Could not author glTF stage metrics");
        return {};
    }
    if (animation.index)
    {
        // glTF's timeline starts at 0 seconds.
        stage->SetTimeCodesPerSecond(TimeCodesPerSecond);
        stage->SetFramesPerSecond(TimeCodesPerSecond);
        stage->SetStartTimeCode(0.0);
        stage->SetEndTimeCode(animation.endTimeCode);
    }

    MaterialAuthor materials(asset, sourcePath, loadBuffer, stage, mtl.GetPath());
    for (std::size_t position = 0; position < map->nodes.size(); ++position)
    {
        const auto& mapped = map->nodes[position];
        const auto index = mapped.index;
        const auto& node = asset.nodes[index];
        const auto xform = UsdGeomXform::Define(stage, mapped.path);
        if (!xform ||
            (!node.name.empty() && !xform.GetPrim().SetDisplayName(std::string(node.name))))
        {
            TF_RUNTIME_ERROR("Could not author glTF node %zu", index);
            return {};
        }
        const auto& moved = animation.nodes[position];
        if (moved ? !authorAnimatedTransform(xform, *moved, layer)
                  : !xform.AddTransformOp().Set(mapped.local))
        {
            TF_RUNTIME_ERROR("Could not author glTF node %zu transform", index);
            return {};
        }
        if (node.meshIndex &&
            !placeMesh(asset, *map, *node.meshIndex, mapped.mesh, stage, loadBuffer, materials))
        {
            return {};
        }
        if (node.cameraIndex &&
            !AuthorCamera(asset, index, *node.cameraIndex, xform.GetPrim(), mapped.world))
        {
            return {};
        }
        if (node.lightIndex &&
            !AuthorLight(asset, index, *node.lightIndex, xform.GetPrim(), mapped.world))
        {
            return {};
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
