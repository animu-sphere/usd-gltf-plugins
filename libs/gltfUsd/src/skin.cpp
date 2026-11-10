// SPDX-License-Identifier: Apache-2.0
#include "skin.h"

#include "component.h"

#include "pxr/base/gf/math.h"
#include "pxr/base/gf/vec3h.h"
#include "pxr/base/tf/diagnostic.h"
#include "pxr/base/vt/array.h"
#include "pxr/usd/usdGeom/scope.h"
#include "pxr/usd/usdSkel/animation.h"
#include "pxr/usd/usdSkel/bindingAPI.h"
#include "pxr/usd/usdSkel/skeleton.h"

#include <algorithm>
#include <bit>
#include <cmath>
#include <iterator>
#include <limits>
#include <set>
#include <string>
#include <type_traits>
#include <utility>

namespace gltfUsd
{
namespace
{

using namespace pxr;

bool
readBindTransforms(const fastgltf::Asset& asset, const fastgltf::Skin& skin,
                   const gltfCore::BufferLoader& loadBuffer, VtMatrix4dArray& binds,
                   std::string& error)
{
    binds.assign(skin.joints.size(), GfMatrix4d(1.0));
    if (!skin.inverseBindMatrices)
    {
        return true;
    }
    gltfCore::AccessorData data;
    if (!gltfCore::GetAccessorData(asset, *skin.inverseBindMatrices, fastgltf::AccessorType::Mat4,
                                   fastgltf::ComponentType::Float, 64, false, data, error,
                                   loadBuffer))
    {
        return false;
    }
    if (data.count < skin.joints.size())
    {
        error = "fewer inverse bind matrices than joints";
        return false;
    }
    for (std::size_t i = 0; i < binds.size(); ++i)
    {
        GfMatrix4d inverseBind;
        // glTF uses column vectors, so its columns become Gf's rows.
        for (std::size_t row = 0; row < 4; ++row)
        {
            for (std::size_t column = 0; column < 4; ++column)
            {
                const auto offset = i * data.stride + 4 * (4 * row + column);
                const auto value =
                    std::bit_cast<float>(ReadUnsigned(data.bytes.data() + offset, 4));
                if (!std::isfinite(value))
                {
                    error = "inverse bind matrix is not finite";
                    return false;
                }
                inverseBind[row][column] = value;
            }
        }
        if (inverseBind.GetColumn(3) != GfVec4d(0, 0, 0, 1))
        {
            error = "inverse bind matrix is not affine";
            return false;
        }
        double determinant;
        binds[i] = inverseBind.GetInverse(&determinant);
        if (determinant == 0 || !std::isfinite(determinant))
        {
            error = "inverse bind matrix is singular";
            return false;
        }
    }
    return true;
}

bool
mapSkin(const fastgltf::Asset& asset, std::size_t skinIndex, const SceneMap& map,
        const gltfCore::BufferLoader& loadBuffer, MappedSkin& mapped, std::vector<char>& isJoint,
        std::vector<char>& keep, std::string& error)
{
    const auto& skin = asset.skins[skinIndex];
    if (skin.joints.empty())
    {
        error = "skin has no joints";
        return false;
    }
    std::map<std::size_t, std::size_t> sourceJoints;
    SdfPath common;
    for (std::size_t i = 0; i < skin.joints.size(); ++i)
    {
        const auto joint = skin.joints[i];
        if (joint >= map.positions.size() || !map.positions[joint])
        {
            error = "joint node " + std::to_string(joint) + " is outside the selected scene";
            return false;
        }
        const auto position = *map.positions[joint];
        if (!sourceJoints.emplace(position, i).second)
        {
            error = "joint node " + std::to_string(joint) + " is repeated";
            return false;
        }
        const auto& path = map.nodes[position].path;
        common = common.IsEmpty() ? path : common.GetCommonPrefix(path);
    }
    // The first source joint in scene order is the only one that can be the common root.
    const auto& first = map.nodes[sourceJoints.begin()->first].path;
    const auto space = common == first ? common.GetParentPath() : common;
    mapped.path = space.AppendChild(TfToken("Skeleton_" + std::to_string(skinIndex)));

    // Cache each joint's rest transform in skeleton space while walking in scene order.
    std::map<std::size_t, GfMatrix4d> closure;
    for (const auto& entry : sourceJoints)
    {
        for (auto position = std::optional(entry.first); position;
             position = map.nodes[*position].parent)
        {
            if (map.nodes[*position].path == space)
            {
                keep[*position] = 1;
                break;
            }
            if (!closure.try_emplace(*position, 1.0).second)
            {
                break;
            }
        }
    }
    if (closure.size() > std::numeric_limits<int>::max())
    {
        error = "too many joints for USD joint indices";
        return false;
    }
    VtMatrix4dArray binds;
    if (!readBindTransforms(asset, skin, loadBuffer, binds, error))
    {
        return false;
    }
    mapped.remap.resize(skin.joints.size());
    for (auto& [position, rest] : closure)
    {
        const auto& node = map.nodes[position];
        rest = node.local;
        if (node.parent)
        {
            const auto parent = closure.find(*node.parent);
            if (parent != closure.end())
            {
                rest *= parent->second;
            }
        }
        const auto source = sourceJoints.find(position);
        auto bind = rest;
        if (source != sourceJoints.end())
        {
            bind = binds[source->second];
            mapped.remap[source->second] = static_cast<int>(mapped.joints.size());
        }
        mapped.joints.push_back({position, node.path.MakeRelativePath(map.scene).GetToken(), bind});
        isJoint[position] = 1;
    }
    return true;
}

template <class T>
T
sampleAt(const TimeSamples<T>& samples, const T& stated, double code)
{
    if (samples.empty())
    {
        return stated;
    }
    const auto next =
        std::lower_bound(samples.begin(), samples.end(), code,
                         [](const auto& sample, double t) { return sample.first < t; });
    if (next == samples.end())
    {
        return samples.back().second;
    }
    if (next == samples.begin() || next->first == code)
    {
        return next->second;
    }
    const auto& [before, from] = *std::prev(next);
    const auto& [after, to] = *next;
    // Match USD's hold across nearly coincident samples, including the hold for a STEP channel.
    if (after - before < 1e-6)
    {
        return from;
    }
    const double fraction = (code - before) / (after - before);
    if constexpr (std::is_same_v<T, GfQuatf>)
    {
        return GfSlerp(fraction, from, to);
    }
    else if constexpr (std::is_same_v<T, VtFloatArray>)
    {
        VtFloatArray weights(from.size());
        for (std::size_t i = 0; i < weights.size(); ++i)
        {
            weights[i] = float(std::lerp(double(from[i]), double(to[i]), fraction));
        }
        return weights;
    }
    else
    {
        return GfLerp(fraction, from, to);
    }
}

template <class T>
void
addSampleTimes(const TimeSamples<T>& samples, std::set<double>& times)
{
    for (const auto& sample : samples)
    {
        times.insert(sample.first);
    }
}

} // namespace

bool
MapSkins(const fastgltf::Asset& asset, const gltfCore::BufferLoader& loadBuffer, SceneMap& map)
{
    std::map<std::size_t, SdfPath> users;
    for (const auto& mapped : map.nodes)
    {
        const auto& node = asset.nodes[mapped.index];
        if (!node.skinIndex)
        {
            continue;
        }
        if (*node.skinIndex >= asset.skins.size())
        {
            TF_RUNTIME_ERROR("glTF node %zu: skin index %zu is out of range", mapped.index,
                             *node.skinIndex);
            return false;
        }
        if (mapped.mesh.IsEmpty())
        {
            TF_RUNTIME_ERROR("glTF node %zu: a skin requires a mesh", mapped.index);
            return false;
        }
        auto [entry, inserted] = users.try_emplace(*node.skinIndex, mapped.mesh);
        if (!inserted)
        {
            entry->second = entry->second.GetCommonPrefix(mapped.mesh);
        }
    }
    std::vector<char> isJoint(map.nodes.size(), 0);
    std::vector<char> keep(map.nodes.size(), 0);
    for (const auto& [index, usersRoot] : users)
    {
        auto& skin = map.skins[index];
        std::string error;
        if (!mapSkin(asset, index, map, loadBuffer, skin, isJoint, keep, error))
        {
            TF_RUNTIME_ERROR("glTF skin %zu: %s", index, error.c_str());
            return false;
        }
        map.skelRoots.push_back(skin.path.GetCommonPrefix(usersRoot));
    }
    for (auto& mapped : map.nodes)
    {
        if (mapped.blendShapes.empty())
        {
            continue;
        }
        const auto skin = asset.nodes[mapped.index].skinIndex;
        if (skin)
        {
            mapped.morphSkeleton = map.skins.at(*skin).path;
        }
        else
        {
            mapped.morphSkeleton = mapped.path.AppendChild(TfToken("MorphSkeleton"));
            map.skelRoots.push_back(mapped.path);
        }
    }
    SdfPath::RemoveDescendentPaths(&map.skelRoots);

    for (std::size_t p = map.nodes.size(); p-- > 0;)
    {
        auto& mapped = map.nodes[p];
        const auto& node = asset.nodes[mapped.index];
        if (!isJoint[p] || keep[p] || node.meshIndex || node.cameraIndex || node.lightIndex)
        {
            if (mapped.parent)
            {
                keep[*mapped.parent] = 1;
            }
        }
        else
        {
            mapped.path = {};
        }
    }
    return true;
}

bool
AuthorSkeletons(const fastgltf::Asset& asset, const SceneMap& map, const UsdStagePtr& stage)
{
    for (const auto& node : map.nodes)
    {
        if (!node.morphSkeleton.IsEmpty() && !asset.nodes[node.index].skinIndex)
        {
            // UsdSkelCache resolves blend shape weights through a skeleton and SkelRoot uses
            // its joints for bounds. Without influences the mesh still uses its own transform.
            const auto skeleton = UsdSkelSkeleton::Define(stage, node.morphSkeleton);
            const VtMatrix4dArray identity(1, GfMatrix4d(1.0));
            if (!skeleton || !skeleton.CreateJointsAttr().Set(VtTokenArray{TfToken("Root")}) ||
                !skeleton.CreateBindTransformsAttr().Set(identity) ||
                !skeleton.CreateRestTransformsAttr().Set(identity))
            {
                TF_RUNTIME_ERROR("Could not author glTF morph skeleton at %s",
                                 node.morphSkeleton.GetText());
                return false;
            }
        }
    }
    for (const auto& [index, skin] : map.skins)
    {
        const auto skeleton = UsdSkelSkeleton::Define(stage, skin.path);
        const auto& name = asset.skins[index].name;
        VtTokenArray joints, jointNames;
        VtMatrix4dArray rests, binds;
        for (const auto& joint : skin.joints)
        {
            joints.push_back(joint.token);
            const auto& node = map.nodes[joint.position];
            const auto& jointName = asset.nodes[node.index].name;
            jointNames.emplace_back(jointName.empty() ? "Node_" + std::to_string(node.index)
                                                      : std::string(jointName));
            rests.push_back(node.local);
            binds.push_back(joint.bind);
        }
        if (!skeleton || (!name.empty() && !skeleton.GetPrim().SetDisplayName(name)) ||
            !skeleton.CreateJointsAttr().Set(joints) ||
            !skeleton.CreateJointNamesAttr().Set(jointNames) ||
            !skeleton.CreateRestTransformsAttr().Set(rests) ||
            !skeleton.CreateBindTransformsAttr().Set(binds))
        {
            TF_RUNTIME_ERROR("Could not author glTF skin %zu", index);
            return false;
        }
    }
    return true;
}

namespace
{

bool
authorJointValues(const SceneMap& map, const AnimationValues& values,
                  const UsdSkelAnimation& animation, const std::vector<TfToken>& moved)
{
    VtTokenArray joints;
    std::vector<std::size_t> positions;
    std::set<double> times;
    for (std::size_t p = 0; p < moved.size(); ++p)
    {
        if (!moved[p].IsEmpty())
        {
            joints.push_back(moved[p]);
            positions.push_back(p);
            const auto& node = *values.nodes[p];
            addSampleTimes(node.translation, times);
            addSampleTimes(node.rotation, times);
            addSampleTimes(node.scale, times);
        }
    }
    if (joints.empty())
    {
        return true;
    }
    if (!animation.CreateJointsAttr().Set(joints))
    {
        return false;
    }
    const auto translations = animation.CreateTranslationsAttr();
    const auto rotations = animation.CreateRotationsAttr();
    const auto scales = animation.CreateScalesAttr();
    // Each property is an array in USD, so the animation samples the union of the joint channels'
    // times. At default time no arrays have values, and USD uses the skeleton's rest transforms.
    for (const auto code : times)
    {
        VtVec3fArray t(positions.size());
        VtQuatfArray r(positions.size());
        VtVec3hArray s(positions.size());
        for (std::size_t i = 0; i < positions.size(); ++i)
        {
            const auto position = positions[i];
            const auto& node = *values.nodes[position];
            const auto translation = sampleAt(node.translation, node.statedTranslation, code);
            const auto scale = sampleAt(node.scale, node.statedScale, code);
            for (std::size_t axis = 0; axis < 3; ++axis)
            {
                if (!std::isfinite(scale[axis]) ||
                    std::abs(scale[axis]) > float(std::numeric_limits<GfHalf>::max()) ||
                    std::abs(translation[axis]) > std::numeric_limits<float>::max())
                {
                    TF_RUNTIME_ERROR("glTF joint node %zu: animated transform exceeds the range "
                                     "of UsdSkel translations or scales",
                                     map.nodes[position].index);
                    return false;
                }
            }
            t[i] = GfVec3f(translation);
            r[i] = sampleAt(node.rotation, node.statedRotation, code).GetNormalized();
            s[i] = GfVec3h(scale);
        }
        const UsdTimeCode time(code);
        if (!translations.Set(t, time) || !rotations.Set(r, time) || !scales.Set(s, time))
        {
            return false;
        }
    }
    return true;
}

bool
authorMorphValues(const SceneMap& map, const AnimationValues& values,
                  const UsdSkelAnimation& animation)
{
    VtTokenArray shapes;
    VtFloatArray defaults;
    std::vector<std::size_t> positions;
    std::set<double> times;
    for (std::size_t p = 0; p < map.nodes.size(); ++p)
    {
        const auto& node = map.nodes[p];
        if (node.blendShapes.empty())
        {
            continue;
        }
        positions.push_back(p);
        for (std::size_t i = 0; i < node.blendShapes.size(); ++i)
        {
            shapes.push_back(node.blendShapes[i]);
            defaults.push_back(node.morphWeights[i]);
        }
        if (const auto channel = values.weights.find(p); channel != values.weights.end())
        {
            addSampleTimes(channel->second, times);
        }
    }
    if (shapes.empty())
    {
        return true;
    }
    const auto attr = animation.CreateBlendShapeWeightsAttr();
    if (!animation.CreateBlendShapesAttr().Set(shapes) || !attr.Set(defaults))
    {
        return false;
    }
    for (const auto time : times)
    {
        VtFloatArray weights;
        weights.reserve(shapes.size());
        for (const auto p : positions)
        {
            const auto& stated = map.nodes[p].morphWeights;
            const auto channel = values.weights.find(p);
            const auto sample =
                channel == values.weights.end() ? stated : sampleAt(channel->second, stated, time);
            for (const auto weight : sample)
            {
                weights.push_back(weight);
            }
        }
        if (!attr.Set(weights, UsdTimeCode(time)))
        {
            return false;
        }
    }
    return true;
}

} // namespace

bool
AuthorSkelAnimation(const SceneMap& map, const AnimationValues& values, const UsdStagePtr& stage,
                    const SdfPath& root, const TfToken& name)
{
    std::set<SdfPath> skeletons;
    for (const auto& node : map.nodes)
    {
        if (!node.morphSkeleton.IsEmpty())
        {
            skeletons.insert(node.morphSkeleton);
        }
    }
    std::vector<TfToken> moved(values.nodes.size());
    if (!values.nodes.empty())
    {
        for (const auto& entry : map.skins)
        {
            const auto& skin = entry.second;
            for (const auto& joint : skin.joints)
            {
                const auto& node = values.nodes[joint.position];
                if (node &&
                    (!node->translation.empty() || !node->rotation.empty() || !node->scale.empty()))
                {
                    moved[joint.position] = joint.token;
                    skeletons.insert(skin.path);
                }
            }
        }
    }
    if (skeletons.empty())
    {
        return true;
    }
    const auto scope = UsdGeomScope::Define(stage, root.AppendChild(TfToken("animations")));
    if (!scope)
    {
        return false;
    }
    const auto animation = UsdSkelAnimation::Define(stage, scope.GetPath().AppendChild(name));
    if (!animation || !authorJointValues(map, values, animation, moved) ||
        !authorMorphValues(map, values, animation))
    {
        return false;
    }
    for (const auto& path : skeletons)
    {
        const auto binding = UsdSkelBindingAPI::Apply(stage->GetPrimAtPath(path));
        if (!binding || !binding.CreateAnimationSourceRel().SetTargets({animation.GetPath()}))
        {
            return false;
        }
    }
    return true;
}

} // namespace gltfUsd
