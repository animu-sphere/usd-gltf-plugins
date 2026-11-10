// SPDX-License-Identifier: Apache-2.0
#include "animation.h"

#include "component.h"

#include "pxr/base/gf/matrix4d.h"
#include "pxr/base/gf/quatd.h"
#include "pxr/base/gf/vec3d.h"
#include "pxr/base/tf/diagnostic.h"

#include <fastgltf/core.hpp>

#include <algorithm>
#include <array>
#include <bit>
#include <cmath>
#include <cstdint>
#include <limits>
#include <set>
#include <string>
#include <utility>
#include <variant>

namespace gltfUsd
{
namespace
{

using namespace pxr;

// A STEP sampler's earlier value is also authored this many time codes before each keyframe.
// UsdStage holds the earlier of two samples less than 1e-6 time codes apart rather than
// interpolating them, so the step is exact; without that, a ramp this short would remain.
constexpr double HoldTimeCodes = 1e-7;
// How far the lines between a CUBICSPLINE sampler's time samples may stray from its curve: in a
// quaternion's components, or relative to the sampler's largest translation, scale or weight.
constexpr double CubicTolerance = 1e-4;
// The most pieces a CUBICSPLINE segment is cut into, so that extreme tangents cannot exhaust memory.
constexpr std::size_t MaxPieces = 256;
// How far a camera's or light's correction for scale may be from right, as in the node checks of
// cameras and lights.
constexpr double ViewTolerance = 0.001;

using Value = std::array<double, 4>;

// A sampler's keyframes, as glTF gives them.
template <class T> struct Keyframes
{
    fastgltf::AnimationPath path = fastgltf::AnimationPath::Translation;
    fastgltf::AnimationInterpolation interpolation = fastgltf::AnimationInterpolation::Linear;
    std::size_t width = 3;
    std::vector<double> times; // Seconds
    std::vector<T> vertices;
    std::vector<T> ins;  // CUBICSPLINE only
    std::vector<T> outs; // CUBICSPLINE only
};

// A float, or a normalized integer as glTF decodes it.
double
readComponent(const std::byte* data, fastgltf::ComponentType type)
{
    const auto bits = ReadUnsigned(data, fastgltf::getComponentByteSize(type));
    switch (type)
    {
    case fastgltf::ComponentType::Float:
        return std::bit_cast<float>(bits);
    case fastgltf::ComponentType::Byte:
        return std::max(double(std::int8_t(bits)) / 127.0, -1.0);
    case fastgltf::ComponentType::UnsignedByte:
        return double(bits) / 255.0;
    case fastgltf::ComponentType::Short:
        return std::max(double(std::int16_t(bits)) / 32767.0, -1.0);
    case fastgltf::ComponentType::UnsignedShort:
        return double(bits) / 65535.0;
    default:
        return std::numeric_limits<double>::quiet_NaN();
    }
}

template <class T>
double
norm(const T& value, std::size_t width)
{
    double sum = 0.0;
    for (std::size_t i = 0; i < width; ++i)
    {
        sum += value[i] * value[i];
    }
    return std::sqrt(sum);
}

bool
readTimes(const fastgltf::Asset& asset, std::size_t index, const gltfCore::BufferLoader& loadBuffer,
          std::vector<double>& times, std::string& error)
{
    gltfCore::AccessorData data;
    if (!gltfCore::GetAccessorData(asset, index, fastgltf::AccessorType::Scalar,
                                   fastgltf::ComponentType::Float, 4, false, data, error,
                                   loadBuffer))
    {
        error = "input " + error;
        return false;
    }
    times.resize(data.count);
    for (std::size_t i = 0; i < data.count; ++i)
    {
        times[i] =
            readComponent(data.bytes.data() + i * data.stride, fastgltf::ComponentType::Float);
        if (!std::isfinite(times[i]) || times[i] < 0.0 || (i > 0 && times[i] <= times[i - 1]))
        {
            error = "input times are not finite, nonnegative and strictly increasing";
            return false;
        }
    }
    return true;
}

template <class T>
bool
readKeyframes(const fastgltf::Asset& asset, const gltfCore::BufferLoader& loadBuffer,
              const fastgltf::AnimationSampler& sampler, fastgltf::AnimationPath path, T initial,
              std::size_t width, Keyframes<T>& result, std::string& error)
{
    result.path = path;
    result.interpolation = sampler.interpolation;
    const bool cubic = sampler.interpolation == fastgltf::AnimationInterpolation::CubicSpline;
    if (!readTimes(asset, sampler.inputAccessor, loadBuffer, result.times, error))
    {
        return false;
    }
    if (cubic && result.times.size() < 2)
    {
        error = "a CUBICSPLINE sampler has fewer than two keyframes";
        return false;
    }

    const bool rotation = path == fastgltf::AnimationPath::Rotation;
    const bool weights = path == fastgltf::AnimationPath::Weights;
    result.width = width;
    if (sampler.outputAccessor >= asset.accessors.size())
    {
        error = "output accessor index is out of range";
        return false;
    }
    const auto componentType = asset.accessors[sampler.outputAccessor].componentType;
    const bool quantized = componentType != fastgltf::ComponentType::Float;
    if (quantized &&
        ((!rotation && !weights) || (componentType != fastgltf::ComponentType::Byte &&
                                     componentType != fastgltf::ComponentType::UnsignedByte &&
                                     componentType != fastgltf::ComponentType::Short &&
                                     componentType != fastgltf::ComponentType::UnsignedShort)))
    {
        error = "output has an invalid component type";
        return false;
    }
    const auto componentSize = fastgltf::getComponentByteSize(componentType);
    gltfCore::AccessorData data;
    const auto type =
        weights ? fastgltf::AccessorType::Scalar
                : (rotation ? fastgltf::AccessorType::Vec4 : fastgltf::AccessorType::Vec3);
    const auto elementsPerValue = weights ? width : 1;
    if (!gltfCore::GetAccessorData(asset, sampler.outputAccessor, type, componentType,
                                   (weights ? 1 : width) * componentSize, false, data, error,
                                   loadBuffer, quantized, weights))
    {
        error = "output " + error;
        return false;
    }
    const std::size_t perKeyframe = cubic ? 3 : 1;
    if (data.count % elementsPerValue != 0 || data.count / elementsPerValue % perKeyframe != 0 ||
        data.count / elementsPerValue / perKeyframe != result.times.size())
    {
        error = "output count does not match the input's";
        return false;
    }

    const auto valueCount = data.count / elementsPerValue;
    for (std::size_t i = 0; i < valueCount; ++i)
    {
        T value = initial;
        for (std::size_t c = 0; c < width; ++c)
        {
            const auto* element = data.Element(weights ? i * width + c : i);
            value[c] =
                element ? readComponent(element + (weights ? 0 : c * componentSize), componentType)
                        : 0.0;
            if (!std::isfinite(value[c]))
            {
                error = "output values are not finite";
                return false;
            }
        }
        if (cubic && i % 3 == 0)
        {
            result.ins.push_back(std::move(value));
        }
        else if (cubic && i % 3 == 2)
        {
            result.outs.push_back(std::move(value));
        }
        else
        {
            result.vertices.push_back(std::move(value));
        }
    }

    if (rotation)
    {
        // Quantization to a byte moves a unit quaternion's squared length by up to about 0.016.
        const double tolerance = componentSize == 1 ? 0.02 : 0.001;
        for (auto& vertex : result.vertices)
        {
            const double length = norm(vertex, 4);
            if (std::abs(length * length - 1.0) > tolerance)
            {
                error = "output rotations are not unit quaternions";
                return false;
            }
            for (auto& component : vertex)
            {
                component /= length;
            }
        }
    }
    return true;
}

// The CUBICSPLINE curve of segment k, at the fraction u of it.
template <class T>
T
hermite(const Keyframes<T>& keyframes, std::size_t k, double u)
{
    const double delta = keyframes.times[k + 1] - keyframes.times[k];
    const double u2 = u * u;
    const double u3 = u2 * u;
    T result = keyframes.vertices[k];
    for (std::size_t c = 0; c < keyframes.width; ++c)
    {
        result[c] = (2 * u3 - 3 * u2 + 1) * keyframes.vertices[k][c] +
                    (u3 - 2 * u2 + u) * delta * keyframes.outs[k][c] +
                    (-2 * u3 + 3 * u2) * keyframes.vertices[k + 1][c] +
                    (u3 - u2) * delta * keyframes.ins[k + 1][c];
    }
    return result;
}

// How many equal pieces keep the lines between samples of segment k within the tolerance of its
// curve. The curve's second derivative is linear in u, so its largest norm is at an end, and a
// piece of length h strays from the curve by at most that norm times h^2 / 8.
template <class T>
std::size_t
pieceCount(const Keyframes<T>& keyframes, std::size_t k, double scale)
{
    const double delta = keyframes.times[k + 1] - keyframes.times[k];
    double startSquared = 0.0;
    double endSquared = 0.0;
    for (std::size_t c = 0; c < keyframes.width; ++c)
    {
        const double v0 = keyframes.vertices[k][c];
        const double v1 = keyframes.vertices[k + 1][c];
        const double b0 = delta * keyframes.outs[k][c];
        const double a1 = delta * keyframes.ins[k + 1][c];
        const double start = -6 * v0 - 4 * b0 + 6 * v1 - 2 * a1;
        const double end = 6 * v0 + 2 * b0 - 6 * v1 + 4 * a1;
        startSquared += start * start;
        endSquared += end * end;
    }
    const double largest = std::sqrt(std::max(startSquared, endSquared));
    const double pieces = std::ceil(std::sqrt(largest / (8 * CubicTolerance * scale)));
    if (!(pieces <= double(MaxPieces + 1)))
    {
        return MaxPieces + 1;
    }
    return std::max<std::size_t>(1, std::size_t(pieces));
}

// The time samples that reproduce the sampler: its keyframes, a hold before each STEP keyframe, and
// points of each CUBICSPLINE segment. Sets `capped` when a segment needed more than MaxPieces.
template <class T>
bool
expand(const Keyframes<T>& keyframes, TimeSamples<T>& result, bool& capped, std::string& error)
{
    const bool rotation = keyframes.path == fastgltf::AnimationPath::Rotation;
    double scale = 1.0;
    if (!rotation)
    {
        scale = 1e-9;
        for (const auto& vertex : keyframes.vertices)
        {
            for (std::size_t c = 0; c < keyframes.width; ++c)
            {
                scale = std::max(scale, std::abs(vertex[c]));
            }
        }
    }
    const auto count = keyframes.times.size();
    for (std::size_t k = 0; k < count; ++k)
    {
        const double code = keyframes.times[k] * TimeCodesPerSecond;
        if (keyframes.interpolation == fastgltf::AnimationInterpolation::Step && k > 0 &&
            code - HoldTimeCodes > result.back().first)
        {
            result.emplace_back(code - HoldTimeCodes, keyframes.vertices[k - 1]);
        }
        result.emplace_back(code, keyframes.vertices[k]);
        if (keyframes.interpolation != fastgltf::AnimationInterpolation::CubicSpline ||
            k + 1 == count)
        {
            continue;
        }
        auto pieces = pieceCount(keyframes, k, scale);
        if (pieces > MaxPieces)
        {
            pieces = MaxPieces;
            capped = true;
        }
        const double next = keyframes.times[k + 1] * TimeCodesPerSecond;
        for (std::size_t i = 1; i < pieces; ++i)
        {
            const double u = double(i) / double(pieces);
            const double time =
                (keyframes.times[k] + (keyframes.times[k + 1] - keyframes.times[k]) * u) *
                TimeCodesPerSecond;
            // A segment too short for the precision of its times gets fewer samples.
            if (time <= result.back().first || time >= next)
            {
                continue;
            }
            auto value = hermite(keyframes, k, u);
            if (rotation)
            {
                // glTF normalizes a cubic rotation after interpolating it.
                const double length = norm(value, 4);
                if (!(length > 1e-6))
                {
                    error = "a CUBICSPLINE rotation passes through a zero quaternion";
                    return false;
                }
                for (auto& component : value)
                {
                    component /= length;
                }
            }
            result.emplace_back(time, value);
        }
    }
    return true;
}

GfQuatd
toQuat(const Value& value)
{
    // glTF orders a quaternion x, y, z, w.
    return GfQuatd(value[3], value[0], value[1], value[2]);
}

GfMatrix4d
trsMatrix(const GfVec3d& translation, const GfQuatd& rotation, const GfVec3d& scale)
{
    // Gf matrices use row vectors, so T * R * S is S, then R, then T.
    GfMatrix4d s(1.0);
    s.SetScale(scale);
    GfMatrix4d r(1.0);
    r.SetRotate(rotation);
    GfMatrix4d t(1.0);
    t.SetTranslate(translation);
    return s * r * t;
}

// Whether the matrix's linear part is a rotation, possibly reflected, times a uniform scale.
bool
isSimilarity(const GfMatrix4d& matrix)
{
    GfVec3d axes[3];
    double lengths[3];
    for (int i = 0; i < 3; ++i)
    {
        axes[i] = GfVec3d(matrix[i][0], matrix[i][1], matrix[i][2]);
        lengths[i] = axes[i].GetLength();
    }
    const double shortest = std::min({lengths[0], lengths[1], lengths[2]});
    const double longest = std::max({lengths[0], lengths[1], lengths[2]});
    if (!(shortest > 0.0) || longest / shortest - 1.0 > ViewTolerance)
    {
        return false;
    }
    for (int i = 0; i < 3; ++i)
    {
        const int j = (i + 1) % 3;
        if (std::abs(GfDot(axes[i], axes[j])) > ViewTolerance * lengths[i] * lengths[j])
        {
            return false;
        }
    }
    return true;
}

// Whether a scale takes a value at any time, or when no animation plays, other than its first.
bool
scaleChanges(const NodeAnimation& animation)
{
    if (animation.scale.empty())
    {
        return false;
    }
    const GfVec3d first(animation.scale.front().second);
    const double magnitude = std::max({std::abs(first[0]), std::abs(first[1]), std::abs(first[2])});
    auto differs = [&](const GfVec3f& value)
    {
        const GfVec3d difference = GfVec3d(value) - first;
        return std::max({std::abs(difference[0]), std::abs(difference[1]),
                         std::abs(difference[2])}) > ViewTolerance * magnitude;
    };
    return differs(animation.statedScale) ||
           std::any_of(animation.scale.begin(), animation.scale.end(),
                       [&](const auto& sample) { return differs(sample.second); });
}

bool
rotationChanges(const NodeAnimation& animation)
{
    if (animation.rotation.empty())
    {
        return false;
    }
    const GfQuatf first = animation.rotation.front().second;
    auto differs = [&](const GfQuatf& value)
    {
        const double dot = std::abs(double(GfDot(first, value)));
        return 2.0 * std::acos(std::min(1.0, dot)) > ViewTolerance;
    };
    return differs(animation.statedRotation) ||
           std::any_of(animation.rotation.begin(), animation.rotation.end(),
                       [&](const auto& sample) { return differs(sample.second); });
}

// Sets each node's transform at the animation's time 0, and rejects a camera or light whose
// correction for scale, computed from those, would not hold at every time. That correction is
// constant when no scale on the node's path changes and each rotation that changes is below a
// similarity: then the node's world transform at any time is a rotation of the one at time 0.
bool
placeAtTimeZero(const fastgltf::Asset& asset, const SceneMap& map, std::size_t animationIndex,
                AnimationValues& values)
{
    values.world.resize(map.nodes.size());
    std::vector<char> belowScale(map.nodes.size(), 0);
    std::vector<char> belowTurn(map.nodes.size(), 0);
    for (std::size_t position = 0; position < map.nodes.size(); ++position)
    {
        const auto& mapped = map.nodes[position];
        const GfMatrix4d parentWorld =
            mapped.parent ? values.world[*mapped.parent] : GfMatrix4d(1.0);
        if (mapped.parent)
        {
            belowScale[position] = belowScale[*mapped.parent];
            belowTurn[position] = belowTurn[*mapped.parent];
        }
        GfMatrix4d local = mapped.local;
        if (const auto& animation = values.nodes[position])
        {
            local =
                trsMatrix(animation->translation.empty() ? animation->statedTranslation
                                                         : animation->translation.front().second,
                          GfQuatd(animation->rotation.empty() ? animation->statedRotation
                                                              : animation->rotation.front().second),
                          GfVec3d(animation->scale.empty() ? animation->statedScale
                                                           : animation->scale.front().second));
            if (scaleChanges(*animation))
            {
                belowScale[position] = 1;
            }
            if (rotationChanges(*animation) && !isSimilarity(parentWorld))
            {
                belowTurn[position] = 1;
            }
        }
        values.world[position] = mapped.parent ? local * parentWorld : local;

        const auto& node = asset.nodes[mapped.index];
        if (!node.cameraIndex && !node.lightIndex)
        {
            continue;
        }
        if (belowScale[position])
        {
            TF_RUNTIME_ERROR("glTF animation %zu, node %zu: a camera or light below an animated "
                             "scale that changes is not supported",
                             animationIndex, mapped.index);
            return false;
        }
        if (belowTurn[position])
        {
            TF_RUNTIME_ERROR("glTF animation %zu, node %zu: a camera or light below a rotation "
                             "animated under a nonuniform scale or shear is not supported",
                             animationIndex, mapped.index);
            return false;
        }
    }
    return true;
}

// Reads one animation's channels on the selected scene into `values`, whose nodes have an entry per
// node of the scene. Returns false after reporting an error.
bool
readAnimation(const fastgltf::Asset& asset, const gltfCore::BufferLoader& loadBuffer,
              const SceneMap& map, std::size_t animationIndex, AnimationValues& values)
{
    const auto& animation = asset.animations[animationIndex];
    values.name = std::string(animation.name);
    std::set<std::pair<std::size_t, fastgltf::AnimationPath>> targets;
    bool capped = false;
    for (std::size_t c = 0; c < animation.channels.size(); ++c)
    {
        const auto& channel = animation.channels[c];
        auto fail = [&](const std::string& message)
        {
            TF_RUNTIME_ERROR("glTF animation %zu channel %zu: %s", animationIndex, c,
                             message.c_str());
            return false;
        };
        if (channel.samplerIndex >= animation.samplers.size())
        {
            return fail("sampler index is out of range");
        }
        // glTF ignores a channel without a node, unless an extension says what it animates.
        if (!channel.nodeIndex)
        {
            continue;
        }
        const auto nodeIndex = *channel.nodeIndex;
        if (nodeIndex >= asset.nodes.size())
        {
            return fail("node index is out of range");
        }
        const auto path = channel.path;
        if (path != fastgltf::AnimationPath::Translation &&
            path != fastgltf::AnimationPath::Rotation && path != fastgltf::AnimationPath::Scale &&
            path != fastgltf::AnimationPath::Weights)
        {
            return fail("target path is not translation, rotation, scale or weights");
        }
        if (!targets.emplace(nodeIndex, path).second)
        {
            return fail("targets the same node and path as another channel");
        }
        // Only the selected scene is authored.
        const auto position = map.positions[nodeIndex];
        if (!position)
        {
            continue;
        }
        if (!std::holds_alternative<fastgltf::TRS>(asset.nodes[nodeIndex].transform))
        {
            return fail("animates node " + std::to_string(nodeIndex) + ", which has a matrix");
        }
        if (path == fastgltf::AnimationPath::Weights)
        {
            const auto count = map.nodes[*position].morphWeights.size();
            if (count == 0)
            {
                return fail("morph weight animation requires a mesh with targets");
            }
            Keyframes<std::vector<double>> keyframes;
            TimeSamples<std::vector<double>> samples;
            std::string error;
            if (!readKeyframes(asset, loadBuffer, animation.samplers[channel.samplerIndex], path,
                               std::vector<double>(count), count, keyframes, error) ||
                !expand(keyframes, samples, capped, error))
            {
                return fail(error);
            }
            auto& weights = values.weights[*position];
            for (const auto& [code, value] : samples)
            {
                VtFloatArray sample;
                for (const auto weight : value)
                {
                    if (!std::isfinite(weight) ||
                        std::abs(weight) > std::numeric_limits<float>::max())
                    {
                        return fail("animated morph weights exceed the finite float range");
                    }
                    sample.push_back(static_cast<float>(weight));
                }
                weights.emplace_back(code, std::move(sample));
            }
            values.endTimeCode =
                std::max(values.endTimeCode, keyframes.times.back() * TimeCodesPerSecond);
            continue;
        }
        Keyframes<Value> keyframes;
        std::vector<std::pair<double, Value>> samples;
        std::string error;
        if (!readKeyframes(asset, loadBuffer, animation.samplers[channel.samplerIndex], path,
                           Value{}, path == fastgltf::AnimationPath::Rotation ? 4 : 3, keyframes,
                           error) ||
            !expand(keyframes, samples, capped, error))
        {
            return fail(error);
        }
        values.endTimeCode =
            std::max(values.endTimeCode, keyframes.times.back() * TimeCodesPerSecond);

        auto& nodeAnimation = values.nodes[*position];
        if (!nodeAnimation)
        {
            nodeAnimation.emplace();
        }
        for (const auto& [code, value] : samples)
        {
            switch (path)
            {
            case fastgltf::AnimationPath::Translation:
                nodeAnimation->translation.emplace_back(code,
                                                        GfVec3d(value[0], value[1], value[2]));
                break;
            case fastgltf::AnimationPath::Rotation:
                nodeAnimation->rotation.emplace_back(code, GfQuatf(toQuat(value)));
                break;
            default:
                nodeAnimation->scale.emplace_back(code, GfVec3f(value[0], value[1], value[2]));
                break;
            }
        }
    }
    if (capped)
    {
        TF_WARN("glTF animation %zu: a CUBICSPLINE segment is cut into only %zu pieces, which "
                "can stray from its curve by more than %g",
                animationIndex, MaxPieces, CubicTolerance);
    }
    return true;
}

} // namespace

bool
PlanAnimation(const fastgltf::Asset& asset, const gltfCore::BufferLoader& loadBuffer,
              const SceneMap& map, AnimationPlan& plan)
{
    plan = {};
    plan.moved.assign(map.nodes.size(), 0);
    plan.animations.resize(asset.animations.size());
    for (std::size_t a = 0; a < asset.animations.size(); ++a)
    {
        auto& values = plan.animations[a];
        values.nodes.resize(map.nodes.size());
        if (!readAnimation(asset, loadBuffer, map, a, values))
        {
            return false;
        }
        plan.endTimeCode = std::max(plan.endTimeCode, values.endTimeCode);
        plan.hasChannels = plan.hasChannels || !values.weights.empty();
        for (std::size_t position = 0; position < map.nodes.size(); ++position)
        {
            if (values.nodes[position])
            {
                plan.moved[position] = 1;
                plan.hasChannels = true;
            }
        }
    }

    // Every animation has the stated values of every node that any animation moves.
    plan.animated.assign(map.nodes.size(), 0);
    for (std::size_t position = 0; position < map.nodes.size(); ++position)
    {
        const auto& mapped = map.nodes[position];
        plan.animated[position] =
            plan.moved[position] || (mapped.parent && plan.animated[*mapped.parent]);
        if (!plan.moved[position])
        {
            continue;
        }
        // A moved node has TRS, and MapScene has checked that its rotation is close to unit length.
        const auto& trs = std::get<fastgltf::TRS>(asset.nodes[mapped.index].transform);
        const auto& q = trs.rotation;
        const GfVec3d translation(trs.translation.x(), trs.translation.y(), trs.translation.z());
        const GfQuatf rotation(GfQuatd(q.w(), q.x(), q.y(), q.z()).GetNormalized());
        const GfVec3f scale(trs.scale.x(), trs.scale.y(), trs.scale.z());
        for (auto& values : plan.animations)
        {
            auto& node = values.nodes[position];
            if (!node)
            {
                node.emplace();
            }
            node->statedTranslation = translation;
            node->statedRotation = rotation;
            node->statedScale = scale;
        }
    }
    for (std::size_t a = 0; a < plan.animations.size(); ++a)
    {
        if (!placeAtTimeZero(asset, map, a, plan.animations[a]))
        {
            return false;
        }
    }
    return true;
}

} // namespace gltfUsd
