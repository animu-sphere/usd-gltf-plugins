// SPDX-License-Identifier: Apache-2.0
#pragma once

#include "sceneMap.h"

#include "gltfCore/accessor.h"

#include "pxr/pxr.h"
#include "pxr/base/gf/matrix4d.h"
#include "pxr/base/gf/quatf.h"
#include "pxr/base/gf/vec3d.h"
#include "pxr/base/gf/vec3f.h"

#include <fastgltf/types.hpp>

#include <cstddef>
#include <map>
#include <optional>
#include <string>
#include <utility>
#include <vector>

namespace gltfUsd
{

// glTF times are seconds; a time code is a second times this.
inline constexpr double TimeCodesPerSecond = 24.0;

// Values at time codes, in increasing order of time.
template <class T> using TimeSamples = std::vector<std::pair<double, T>>;

// What one glTF animation does to one node. An empty list means that the animation leaves that
// property as the node states it.
struct NodeAnimation
{
    TimeSamples<pxr::GfVec3d> translation;
    TimeSamples<pxr::GfQuatf> rotation;
    TimeSamples<pxr::GfVec3f> scale;
    // The node's own values, which hold when no animation plays. The rotation is normalized.
    pxr::GfVec3d statedTranslation{0.0};
    pxr::GfQuatf statedRotation{1.0f};
    pxr::GfVec3f statedScale{1.0f};
};

// What one glTF animation does to the nodes of the selected scene.
struct AnimationValues
{
    // The glTF animation's name, which may be empty.
    std::string name;
    // The last keyframe of its channels on the selected scene, in time codes, or 0 without any. Its
    // timeline starts at 0.
    double endTimeCode = 0.0;
    // By position in SceneMap::nodes; nullopt for a node that no animation moves. A node that
    // another animation moves has its stated values here, without time samples.
    std::vector<std::optional<NodeAnimation>> nodes;
    // By position in SceneMap::nodes. Empty or absent for nodes without a weight channel.
    std::map<std::size_t, TimeSamples<pxr::VtFloatArray>> weights;
    // By position in SceneMap::nodes, the node's transform relative to the asset at time 0.
    std::vector<pxr::GfMatrix4d> world;
};

// Every animation that conversion authors, as time samples.
struct AnimationPlan
{
    // Every glTF animation, in glTF order, including one that moves nothing in the selected scene.
    std::vector<AnimationValues> animations;
    // By position in SceneMap::nodes, whether any animation moves the node.
    std::vector<char> moved;
    // By position in SceneMap::nodes, whether any animation moves the node or one of its ancestors.
    std::vector<char> animated;
    // The last keyframe of any animation's channels on the selected scene, in time codes.
    double endTimeCode = 0.0;
    // Whether any animation has a channel on the selected scene.
    bool hasChannels = false;
};

// Reads every animation's channels that target nodes of the selected scene and turns their samplers
// into time samples, with each node's transforms at each animation's time 0. Channels without a
// node, or with a node outside the scene, are ignored. Reports an error and returns false for an
// invalid channel or sampler in any animation, for content that cannot be converted yet, and for a
// camera or light whose correction for its node's scale would have to change during an animation.
[[nodiscard]] bool PlanAnimation(const fastgltf::Asset& asset,
                                 const gltfCore::BufferLoader& loadBuffer, const SceneMap& map,
                                 AnimationPlan& plan);

} // namespace gltfUsd
