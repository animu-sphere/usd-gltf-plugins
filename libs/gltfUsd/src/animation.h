// SPDX-License-Identifier: Apache-2.0
#pragma once

#include "sceneMap.h"

#include "gltfCore/accessor.h"

#include "pxr/pxr.h"
#include "pxr/base/gf/quatf.h"
#include "pxr/base/gf/vec3d.h"
#include "pxr/base/gf/vec3f.h"

#include <fastgltf/types.hpp>

#include <cstddef>
#include <optional>
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

// The animation that conversion authors, as time samples.
struct AnimationPlan
{
    // The glTF animation, or nullopt when nothing in the selected scene is animated.
    std::optional<std::size_t> index;
    // The last keyframe of the authored channels, in time codes. The timeline starts at 0.
    double endTimeCode = 0.0;
    // By position in SceneMap::nodes; nullopt for a node that the animation does not move.
    std::vector<std::optional<NodeAnimation>> nodes;
};

// Reads the first animation's channels that target nodes of the selected scene and turns their
// samplers into time samples, and sets the local and world transforms in `map` to their values at
// time 0. Channels without a node, or with a node outside the scene, are ignored. Reports an error
// and returns false for an invalid channel or sampler, for content that cannot be converted yet, and
// for a camera or light whose correction for its node's scale would have to change over time.
[[nodiscard]] bool PlanAnimation(const fastgltf::Asset& asset,
                                 const gltfCore::BufferLoader& loadBuffer, SceneMap& map,
                                 AnimationPlan& plan);

} // namespace gltfUsd
