// SPDX-License-Identifier: Apache-2.0
#include "gltfUsd/scene.h"

#include "animation.h"
#include "camera.h"
#include "imageBasedLight.h"
#include "instancing.h"
#include "light.h"
#include "material.h"
#include "mesh.h"
#include "gltfCore/parse.h"
#include "sceneMap.h"
#include "skin.h"
#include "xmp.h"
#include "gltfCore/sceneSelection.h"

#include "pxr/base/gf/matrix4d.h"
#include "pxr/base/tf/diagnostic.h"
#include "pxr/base/tf/errorMark.h"
#include "pxr/base/tf/errorTransport.h"
#include "pxr/base/tf/stringUtils.h"
#include "pxr/base/vt/array.h"
#include "pxr/base/vt/value.h"
#include "pxr/usd/sdf/copyUtils.h"
#include "pxr/usd/usd/editContext.h"
#include "pxr/usd/usd/prim.h"
#include "pxr/usd/usd/stage.h"
#include "pxr/usd/usd/variantSets.h"
#include "pxr/usd/usdGeom/metrics.h"
#include "pxr/usd/usdGeom/scope.h"
#include "pxr/usd/usdGeom/tokens.h"
#include "pxr/usd/usdGeom/xform.h"
#include "pxr/usd/usdGeom/xformOp.h"
#include "pxr/usd/usdSkel/root.h"

#include <fastgltf/core.hpp>

#include <algorithm>
#include <condition_variable>
#include <deque>
#include <exception>
#include <future>
#include <mutex>
#include <optional>
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

UsdGeomXformable
defineNode(const UsdStageRefPtr& stage, const SceneMap& map, const SdfPath& path)
{
    if (std::binary_search(map.skelRoots.begin(), map.skelRoots.end(), path))
    {
        return UsdSkelRoot::Define(stage, path);
    }
    return UsdGeomXform::Define(stage, path);
}

// Converts a mesh at its first placement, and copies the result to the others, so that each mesh is
// read once and every node that uses it has the same prims.
bool
placeMesh(const fastgltf::Asset& asset, const SceneMap& map, std::size_t meshIndex,
          const SdfPath& path, const UsdStageRefPtr& stage, const gltfCore::BufferReader& buffers,
          MaterialAuthor& materials, DecodedMeshes& decodedMeshes, VariantBindings& variantBindings,
          const XmpPackets& xmp)
{
    const auto& first = map.meshes[meshIndex].front();
    if (path == first)
    {
        if (!ConvertMesh(asset, meshIndex, stage, path, buffers, materials, decodedMeshes,
                         variantBindings))
        {
            return false;
        }
        if (!xmp.Author(stage->GetPrimAtPath(path),
                        gltfCore::XmpPacketOf(xmp.Metadata().meshes, meshIndex)))
        {
            TF_RUNTIME_ERROR("Could not author the XMP metadata of glTF mesh %zu", meshIndex);
            return false;
        }
        return true;
    }
    // Material bindings target a scope outside the copied subtree, so they are copied unchanged,
    // and the mesh's metadata with them.
    const auto layer = stage->GetRootLayer();
    if (!SdfCopySpec(layer, first, layer, path))
    {
        TF_RUNTIME_ERROR("Could not place glTF mesh %zu at %s", meshIndex, path.GetText());
        return false;
    }
    return true;
}

// The stated value as the operation's default, and the animation's values as its time samples.
// Through the attribute rather than SdfLayer::SetTimeSample, whose typed overload rejected values of
// the right type on macOS.
template <class T>
bool
setValues(const UsdGeomXformOp& op, const T& stated, const TimeSamples<T>& samples)
{
    if (!op || !op.Set(stated))
    {
        return false;
    }
    for (const auto& [code, value] : samples)
    {
        if (!op.Set(value, UsdTimeCode(code)))
        {
            return false;
        }
    }
    return true;
}

const TfToken&
visibilityToken(bool visible)
{
    return visible ? UsdGeomTokens->inherited : UsdGeomTokens->invisible;
}

// A camera or light below a node that an animation moves, and its correction for each animation.
struct AnimatedCorrection
{
    SdfPath prim;
    std::vector<GfMatrix4d> corrections; // By animation
};

using Correction = std::optional<GfMatrix4d> (*)(std::size_t, std::size_t, const GfMatrix4d&);

bool
isIdentity(const GfMatrix4d& matrix)
{
    return GfIsClose(matrix, GfMatrix4d(1.0), 1e-6);
}

// The transform of a camera's or light's prim, `correct`ed for its node's scale. Without an
// animation that moves the node or its ancestors, it is a transform operation, unless it is the
// identity. Otherwise the operation has no value outside the variants, and each animation's
// correction is added to `animated` for its variant, unless every one is the identity.
bool
authorCorrection(const UsdPrim& prim, std::size_t position, std::size_t index, Correction correct,
                 const SceneMap& map, const AnimationPlan& plan,
                 std::vector<AnimatedCorrection>& animated)
{
    if (!prim)
    {
        return false;
    }
    const auto nodeIndex = map.nodes[position].index;
    const bool moves = plan.animated[position];
    std::vector<GfMatrix4d> corrections;
    for (std::size_t a = 0; a < (moves ? plan.animations.size() : 1); ++a)
    {
        const auto correction =
            correct(nodeIndex, index,
                    moves ? plan.animations[a].world[position] : map.nodes[position].world);
        if (!correction)
        {
            return false;
        }
        corrections.push_back(*correction);
    }
    if (std::all_of(corrections.begin(), corrections.end(), isIdentity))
    {
        return true;
    }
    const auto op = UsdGeomXformable(prim).AddTransformOp();
    if (!op || (!moves && !op.Set(corrections.front())))
    {
        TF_RUNTIME_ERROR("Could not author the transform of %s", prim.GetPath().GetText());
        return false;
    }
    if (moves)
    {
        animated.push_back({prim.GetPath(), std::move(corrections)});
    }
    return true;
}

// The ordered operations of the prim at `path`, or none unless there are `count`.
std::vector<UsdGeomXformOp>
orderedOps(const UsdStageRefPtr& stage, const SdfPath& path, std::size_t count)
{
    bool resetsXformStack = false;
    auto ops = UsdGeomXformable(stage->GetPrimAtPath(path)).GetOrderedXformOps(&resetsXformStack);
    if (ops.size() != count)
    {
        return {};
    }
    return ops;
}

// Direct bindings would be stronger than the variants. Author every choice for mapped primitives,
// including the base material wherever a choice has no mapping.
bool
authorMaterialVariants(const UsdStageRefPtr& stage, const SdfPath& root, const SceneMap& map,
                       const fastgltf::Asset& asset, const VariantBindings& bindings)
{
    if (asset.materialVariants.empty())
    {
        return true;
    }
    static const TfToken setName("materialVariant");
    static const std::string original("Original");
    auto variantSet = stage->GetPrimAtPath(root).GetVariantSets().AddVariantSet(setName);
    const auto authorVariant = [&](const std::string& name, std::optional<std::size_t> index)
    {
        const auto fail = [&](const std::string& reason)
        {
            TF_RUNTIME_ERROR("Could not author glTF material variant %s: %s", name.c_str(),
                             reason.c_str());
            return false;
        };
        if (!variantSet.AddVariant(name) || !variantSet.SetVariantSelection(name))
        {
            return fail("could not add or select the USD variant");
        }
        const auto target = variantSet.GetVariantEditTarget();
        if (!target.IsValid())
        {
            return fail("could not edit the USD variant");
        }
        const UsdEditContext context(stage, target);
        if (index)
        {
            stage->GetPrimAtPath(root).SetCustomDataByKey(TfToken("gltf:materialVariantName"),
                                                          VtValue(asset.materialVariants[*index]));
        }
        for (const auto& [key, materials] : bindings)
        {
            const auto [meshIndex, primitiveIndex] = key;
            const auto& primitive = asset.meshes[meshIndex].primitives[primitiveIndex];
            auto material = primitive.materialIndex;
            if (index && primitive.mappings[*index])
            {
                material = primitive.mappings[*index];
            }
            const auto& binding = materials.at(material ? std::optional(*material) : std::nullopt);
            const TfToken child("Primitive_" + std::to_string(primitiveIndex));
            for (const auto& placement : map.meshes[meshIndex])
            {
                const UsdGeomGprim mesh(stage->GetPrimAtPath(placement.AppendChild(child)));
                std::string error;
                if (!mesh || !binding.Author(mesh, error))
                {
                    return fail(mesh ? error : "could not find placed mesh primitive");
                }
            }
        }
        return true;
    };
    if (!variantSet || !authorVariant(original, std::nullopt))
    {
        TF_RUNTIME_ERROR("Could not author the glTF material variant set");
        return false;
    }
    for (std::size_t index = 0; index < asset.materialVariants.size(); ++index)
    {
        const auto& name = asset.materialVariants[index];
        const auto suffix = name.empty() ? std::string{} : "_" + TfMakeValidIdentifier(name);
        if (!authorVariant("Variant_" + std::to_string(index) + suffix, index))
        {
            return false;
        }
    }
    if (!variantSet.SetVariantSelection(original))
    {
        TF_RUNTIME_ERROR("Could not select the original glTF materials");
        return false;
    }
    return true;
}

// The variant set `animation` on the asset's root, with each animation's values in its variant:
// the stated values and time samples of each node that any animation moves, cameras' and lights'
// corrections, and the animation's end and name in the root's customData. Selects the first.
bool
authorAnimations(const UsdStageRefPtr& stage, const SdfPath& root, const SceneMap& map,
                 const AnimationPlan& plan, const std::vector<AnimatedCorrection>& corrections,
                 const MaterialAuthor& materials, const XmpPackets& xmp)
{
    if (plan.animations.empty())
    {
        if (!AuthorSkelAnimation(map, {}, stage, root, TfToken("Rest")))
        {
            TF_RUNTIME_ERROR("Could not author static glTF morph weights");
            return false;
        }
        return true;
    }
    static const TfToken setName("animation");
    if (!stage->GetPrimAtPath(root).GetVariantSets().AddVariantSet(setName))
    {
        TF_RUNTIME_ERROR("Could not author the glTF animation variant set");
        return false;
    }
    auto variantName = [](std::size_t a) { return "Animation_" + std::to_string(a); };
    for (std::size_t a = 0; a < plan.animations.size(); ++a)
    {
        const auto& values = plan.animations[a];
        auto fail = [&]()
        {
            TF_RUNTIME_ERROR("Could not author glTF animation %zu", a);
            return false;
        };
        // Selecting the variant recomposes the stage, so prims are looked up by path below.
        auto variantSet = stage->GetPrimAtPath(root).GetVariantSet(setName);
        if (!variantSet.AddVariant(variantName(a)) ||
            !variantSet.SetVariantSelection(variantName(a)))
        {
            return fail();
        }
        const UsdEditContext context(variantSet.GetVariantEditContext());
        for (const auto& [position, stated] : plan.statedVisibility)
        {
            const auto node = UsdGeomImageable(stage->GetPrimAtPath(map.nodes[position].path));
            const auto attr = node.CreateVisibilityAttr();
            if (!attr || !attr.Set(visibilityToken(stated)))
            {
                return fail();
            }
            if (const auto samples = values.visibility.find(position);
                samples != values.visibility.end())
            {
                for (const auto& [code, visible] : samples->second)
                {
                    if (!attr.Set(visibilityToken(visible), UsdTimeCode(code)))
                    {
                        return fail();
                    }
                }
            }
        }
        for (std::size_t position = 0; position < map.nodes.size(); ++position)
        {
            if (!plan.moved[position] || map.nodes[position].path.IsEmpty())
            {
                continue;
            }
            const auto& node = *values.nodes[position];
            // glTF composes T * R * S, which is the order of these operations.
            const auto ops = orderedOps(stage, map.nodes[position].path, 3);
            if (ops.empty() || !setValues(ops[0], node.statedTranslation, node.translation) ||
                !setValues(ops[1], node.statedRotation, node.rotation) ||
                !setValues(ops[2], node.statedScale, node.scale))
            {
                return fail();
            }
        }
        for (const auto& correction : corrections)
        {
            const auto ops = orderedOps(stage, correction.prim, 1);
            if (ops.empty() || !ops[0].Set(correction.corrections[a]))
            {
                return fail();
            }
        }
        if (!AuthorSkelAnimation(map, values, stage, root, TfToken(variantName(a))))
        {
            return fail();
        }
        if (std::string error; !materials.AuthorAnimation(a, error))
        {
            TF_RUNTIME_ERROR("Could not author glTF animation %zu: %s", a, error.c_str());
            return false;
        }
        // A failure here is an error, which authorScene's mark catches.
        const auto asset = stage->GetPrimAtPath(root);
        if (!asset)
        {
            return fail();
        }
        asset.SetCustomDataByKey(TfToken("gltf:endTimeCode"), VtValue(values.endTimeCode));
        if (!values.name.empty())
        {
            asset.SetCustomDataByKey(TfToken("gltf:name"), VtValue(values.name));
        }
        // An animation has no prim of its own, and /Root's assetInfo is the asset's.
        if (const auto* packet = xmp.Get(gltfCore::XmpPacketOf(xmp.Metadata().animations, a)))
        {
            asset.SetCustomDataByKey(TfToken("gltf:xmp"), *packet);
        }
    }
    if (!stage->GetPrimAtPath(root).GetVariantSet(setName).SetVariantSelection(variantName(0)))
    {
        TF_RUNTIME_ERROR("Could not select glTF animation 0");
        return false;
    }
    return true;
}

SdfLayerRefPtr
authorScene(const fastgltf::Asset& asset, const std::string& sourcePath,
            const gltfCore::BufferReader& buffers, std::size_t selectedIndex, const XmpPackets& xmp,
            const std::optional<gltfCore::ImageBasedLight>& imageBasedLight)
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
    const auto root = UsdGeomXform::Define(stage, SdfPath("/Root"));
    if (!root)
    {
        TF_RUNTIME_ERROR("Could not author glTF layer root");
        return {};
    }
    const auto scenes = UsdGeomScope::Define(stage, root.GetPath().AppendChild(TfToken("Scenes")));
    const auto materialScope =
        UsdGeomScope::Define(stage, root.GetPath().AppendChild(TfToken("Materials")));
    if (!scenes || !materialScope)
    {
        TF_RUNTIME_ERROR("Could not author glTF layer scopes");
        return {};
    }
    auto map = MapScene(asset, selectedIndex, scenes.GetPath());
    AnimationPlan animation;
    if (!map || !MapSkins(asset, buffers, *map) || !PlanAnimation(asset, buffers, *map, animation))
    {
        return {};
    }
    // The scene's prim has no transform, so nodes keep the transforms they have in glTF.
    const auto& selected = asset.scenes[selectedIndex];
    const auto scene = defineNode(stage, *map, map->scene);
    if (!scene ||
        (!selected.name.empty() && !scene.GetPrim().SetDisplayName(std::string(selected.name))) ||
        !xmp.Author(scene.GetPrim(), gltfCore::XmpPacketOf(xmp.Metadata().scenes, selectedIndex)))
    {
        TF_RUNTIME_ERROR("Could not author glTF scene %zu", selectedIndex);
        return {};
    }
    if (imageBasedLight &&
        !AuthorImageBasedLight(asset, *imageBasedLight, sourcePath, buffers, scene.GetPrim()))
    {
        return {};
    }
    stage->SetDefaultPrim(root.GetPrim());
    // The asset's packet is on the default prim, where a stage's own asset metadata is read.
    if (!xmp.Author(root.GetPrim(), xmp.Metadata().asset))
    {
        TF_RUNTIME_ERROR("Could not author the glTF asset's XMP metadata");
        return {};
    }
    if (!UsdGeomSetStageUpAxis(stage, UsdGeomTokens->y) ||
        !UsdGeomSetStageMetersPerUnit(stage, 1.0))
    {
        TF_RUNTIME_ERROR("Could not author glTF stage metrics");
        return {};
    }
    if (animation.hasChannels)
    {
        // glTF's timeline starts at 0 seconds.
        stage->SetTimeCodesPerSecond(TimeCodesPerSecond);
        stage->SetFramesPerSecond(TimeCodesPerSecond);
        stage->SetStartTimeCode(0.0);
        stage->SetEndTimeCode(animation.endTimeCode);
    }

    MaterialAuthor materials(asset, sourcePath, buffers, stage, materialScope.GetPath(), animation,
                             xmp);
    DecodedMeshes decodedMeshes;
    VariantBindings variantBindings;
    std::vector<AnimatedCorrection> corrections;
    for (std::size_t position = 0; position < map->nodes.size(); ++position)
    {
        const auto& mapped = map->nodes[position];
        const auto nodePacket = gltfCore::XmpPacketOf(xmp.Metadata().nodes, mapped.index);
        if (mapped.path.IsEmpty())
        {
            if (nodePacket)
            {
                TF_WARN("glTF node %zu: a skeleton joint without a prim; its KHR_xmp_json_ld "
                        "packet is not authored",
                        mapped.index);
            }
            continue;
        }
        const auto index = mapped.index;
        const auto& node = asset.nodes[index];
        const auto xform = defineNode(stage, *map, mapped.path);
        if (!xform ||
            (!node.name.empty() && !xform.GetPrim().SetDisplayName(std::string(node.name))) ||
            !xmp.Author(xform.GetPrim(), nodePacket))
        {
            TF_RUNTIME_ERROR("Could not author glTF node %zu", index);
            return {};
        }
        if (!node.visible && !animation.statedVisibility.contains(position) &&
            !xform.CreateVisibilityAttr().Set(UsdGeomTokens->invisible))
        {
            TF_RUNTIME_ERROR("Could not author glTF node %zu visibility", index);
            return {};
        }
        // A moved node's values are in each animation's variant.
        if (animation.moved[position]
                ? !xform.AddTranslateOp() || !xform.AddOrientOp() || !xform.AddScaleOp()
                : !xform.AddTransformOp().Set(mapped.local))
        {
            TF_RUNTIME_ERROR("Could not author glTF node %zu transform", index);
            return {};
        }
        // An instanced mesh is the prototype of its node's point instancer, and drawn only by it.
        const bool instanced = !mapped.instancer.IsEmpty();
        if (node.meshIndex && ((instanced && !DefineInstancer(asset, mapped, stage, buffers)) ||
                               !placeMesh(asset, *map, *node.meshIndex, mapped.mesh, stage, buffers,
                                          materials, decodedMeshes, variantBindings, xmp) ||
                               (instanced && !AuthorInstancerExtent(mapped, stage))))
        {
            return {};
        }
        if (node.cameraIndex &&
            !authorCorrection(AuthorCamera(asset, *node.cameraIndex, xform.GetPrim()), position,
                              *node.cameraIndex, CameraCorrection, *map, animation, corrections))
        {
            return {};
        }
        if (node.lightIndex &&
            !authorCorrection(AuthorLight(asset, index, *node.lightIndex, xform.GetPrim()),
                              position, *node.lightIndex, LightCorrection, *map, animation,
                              corrections))
        {
            return {};
        }
    }
    if (!AuthorSkeletons(asset, *map, stage))
    {
        return {};
    }
    // Bind each copy independently, since they may use different skins or morph weights.
    for (std::size_t position = 0; position < map->nodes.size(); ++position)
    {
        const auto& mapped = map->nodes[position];
        const auto& node = asset.nodes[mapped.index];
        if (node.skinIndex &&
            !BindSkinnedMesh(asset, *node.meshIndex, stage, mapped.mesh,
                             map->skins.at(*node.skinIndex), buffers, decodedMeshes))
        {
            return {};
        }
        if (!mapped.blendShapes.empty() &&
            !BindMorphMesh(asset.meshes[*node.meshIndex], stage, mapped, animation, position))
        {
            return {};
        }
    }
    if (std::string error; !materials.SampleAnimations(error))
    {
        TF_RUNTIME_ERROR("Could not sample glTF material animations: %s", error.c_str());
        return {};
    }
    if (!authorAnimations(stage, root.GetPath(), *map, animation, corrections, materials, xmp) ||
        !authorMaterialVariants(stage, root.GetPath(), *map, asset, variantBindings))
    {
        return {};
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
             const gltfCore::BufferLoader& loadBuffer, const gltfCore::XmpMetadata& xmp,
             const std::optional<gltfCore::ImageBasedLight>& imageBasedLight)
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
                    const gltfCore::BufferReader buffers(asset, loadBuffer);
                    const XmpPackets packets(xmp);
                    staged = authorScene(asset, sourcePath, buffers, selected->index, packets,
                                         imageBasedLight);
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
