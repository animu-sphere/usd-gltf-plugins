// SPDX-License-Identifier: Apache-2.0
#include "instancing.h"

#include "component.h"
#include "sceneMap.h"

#include "pxr/base/gf/quatf.h"
#include "pxr/base/gf/vec3f.h"
#include "pxr/base/tf/diagnostic.h"
#include "pxr/base/vt/array.h"
#include "pxr/usd/usd/stage.h"
#include "pxr/usd/usdGeom/pointInstancer.h"

#include <algorithm>
#include <bit>
#include <cmath>
#include <cstdint>
#include <limits>
#include <optional>
#include <string>
#include <string_view>

namespace gltfUsd
{
namespace
{

using namespace pxr;

struct InstanceFormat
{
    fastgltf::ComponentType type = fastgltf::ComponentType::Float;
    bool normalized = false;
};

// A float, or a normalized signed integer as glTF decodes it.
float
readComponent(const std::byte* data, InstanceFormat format)
{
    switch (format.type)
    {
    case fastgltf::ComponentType::Byte:
        return std::max(float(std::int8_t(ReadUnsigned(data, 1))) / 127.0f, -1.0f);
    case fastgltf::ComponentType::Short:
        return std::max(float(std::int16_t(ReadUnsigned(data, 2))) / 32767.0f, -1.0f);
    default:
        return std::bit_cast<float>(ReadUnsigned(data, 4));
    }
}

// Reads an attribute of `components` finite values per instance, in its accessor's format.
template <class Vector>
bool
readAttribute(const fastgltf::Asset& asset, const fastgltf::Attribute& attribute,
              InstanceFormat format, std::size_t components, VtArray<Vector>& values,
              std::string& error, const gltfCore::BufferReader& buffers)
{
    const auto size = fastgltf::getComponentByteSize(format.type);
    gltfCore::AccessorData data;
    // Instance attributes are read like vertex attributes, which may be interleaved.
    if (!gltfCore::GetAccessorData(
            asset, attribute.accessorIndex,
            components == 3 ? fastgltf::AccessorType::Vec3 : fastgltf::AccessorType::Vec4,
            format.type, size * components, true, data, error, buffers, format.normalized))
    {
        error = attribute.name + ": " + error;
        return false;
    }
    values.resize(data.count);
    for (std::size_t i = 0; i < data.count; ++i)
    {
        const auto* element = data.Element(i);
        for (std::size_t component = 0; component < components; ++component)
        {
            const float value = readComponent(element + component * size, format);
            if (!std::isfinite(value))
            {
                error = attribute.name + " contains a non-finite value";
                return false;
            }
            values[i][component] = value;
        }
    }
    return true;
}

// The format to read ROTATION as: normalized signed byte or short, or otherwise float, which
// GetAccessorData then checks.
InstanceFormat
rotationFormat(const fastgltf::Asset& asset, std::size_t index)
{
    if (index < asset.accessors.size())
    {
        const auto& accessor = asset.accessors[index];
        if (accessor.normalized && (accessor.componentType == fastgltf::ComponentType::Byte ||
                                    accessor.componentType == fastgltf::ComponentType::Short))
        {
            return {accessor.componentType, true};
        }
    }
    return {};
}

// Converts XYZW quaternions to USD's, normalized. Each must have unit length within the node's
// tolerance plus the rounding of its format: with each component off by up to half a step, the
// squared length is off by up to two steps and a squared step.
bool
toOrientations(const VtVec4fArray& rotations, InstanceFormat format, VtQuatfArray& orientations,
               std::string& error)
{
    double step = 0.0;
    if (format.type == fastgltf::ComponentType::Byte)
    {
        step = 1.0 / 127.0;
    }
    else if (format.type == fastgltf::ComponentType::Short)
    {
        step = 1.0 / 32767.0;
    }
    const double tolerance = 0.001 + 2.0 * step + step * step;
    orientations.resize(rotations.size());
    for (std::size_t i = 0; i < rotations.size(); ++i)
    {
        const auto& q = rotations[i];
        const double lengthSquared =
            double(q[0]) * q[0] + double(q[1]) * q[1] + double(q[2]) * q[2] + double(q[3]) * q[3];
        if (std::abs(lengthSquared - 1.0) > tolerance)
        {
            error = "ROTATION " + std::to_string(i) + " is not a unit quaternion";
            return false;
        }
        const double length = std::sqrt(lengthSquared);
        orientations[i] = GfQuatf(float(q[3] / length), float(q[0] / length), float(q[1] / length),
                                  float(q[2] / length));
    }
    return true;
}

} // namespace

bool
DefineInstancer(const fastgltf::Asset& asset, const MappedNode& node, const UsdStagePtr& stage,
                const gltfCore::BufferReader& buffers)
{
    std::string error;
    const auto fail = [&]()
    {
        TF_RUNTIME_ERROR("glTF node %zu EXT_mesh_gpu_instancing: %s", node.index, error.c_str());
        return false;
    };
    const fastgltf::Attribute* translation = nullptr;
    const fastgltf::Attribute* rotation = nullptr;
    const fastgltf::Attribute* scale = nullptr;
    for (const auto& attribute : asset.nodes[node.index].instancingAttributes)
    {
        const std::string_view name = attribute.name;
        const auto slot = name == "TRANSLATION" ? &translation
                          : name == "ROTATION"  ? &rotation
                          : name == "SCALE"     ? &scale
                                                : nullptr;
        if (name.starts_with('_'))
        {
            error = "custom instance attributes are not supported: " + attribute.name;
            return fail();
        }
        if (!slot || *slot)
        {
            error = "unsupported or repeated instance attribute: " + attribute.name;
            return fail();
        }
        *slot = &attribute;
    }

    VtVec3fArray positions;
    VtVec4fArray rotations;
    VtVec3fArray scales;
    const auto rotationAs =
        rotation ? rotationFormat(asset, rotation->accessorIndex) : InstanceFormat{};
    if ((translation && !readAttribute(asset, *translation, {}, 3, positions, error, buffers)) ||
        (rotation && !readAttribute(asset, *rotation, rotationAs, 4, rotations, error, buffers)) ||
        (scale && !readAttribute(asset, *scale, {}, 3, scales, error, buffers)))
    {
        return fail();
    }
    // The node has at least one attribute, or it would not be instanced.
    std::optional<std::size_t> count;
    for (const auto size : {translation ? std::optional(positions.size()) : std::nullopt,
                            rotation ? std::optional(rotations.size()) : std::nullopt,
                            scale ? std::optional(scales.size()) : std::nullopt})
    {
        if (size && count && *size != *count)
        {
            error = "instance attribute counts differ";
            return fail();
        }
        count = count ? count : size;
    }
    if (!count || *count > std::size_t(std::numeric_limits<int>::max()))
    {
        error = "instance count exceeds USD's index range";
        return fail();
    }
    VtQuatfArray orientations;
    if (rotation && !toOrientations(rotations, rotationAs, orientations, error))
    {
        return fail();
    }
    if (!translation)
    {
        positions.assign(*count, GfVec3f(0.0f));
    }

    // USD composes each instance's scale, then orientation, then position, as glTF's T * R * S.
    const auto instancer = UsdGeomPointInstancer::Define(stage, node.instancer);
    if (!instancer || !instancer.CreatePrototypesRel().SetTargets({node.mesh}) ||
        !instancer.CreateProtoIndicesAttr().Set(VtIntArray(*count, 0)) ||
        !instancer.CreatePositionsAttr().Set(positions) ||
        (rotation && !instancer.CreateOrientationsfAttr().Set(orientations)) ||
        (scale && !instancer.CreateScalesAttr().Set(scales)))
    {
        error = "could not author the USD point instancer";
        return fail();
    }
    return true;
}

bool
AuthorInstancerExtent(const MappedNode& node, const UsdStagePtr& stage)
{
    const UsdGeomPointInstancer instancer(stage->GetPrimAtPath(node.instancer));
    VtVec3fArray extent;
    if (!instancer ||
        !instancer.ComputeExtentAtTime(&extent, UsdTimeCode::Default(), UsdTimeCode::Default()) ||
        !instancer.CreateExtentAttr().Set(extent))
    {
        TF_RUNTIME_ERROR("glTF node %zu EXT_mesh_gpu_instancing: could not author the extent",
                         node.index);
        return false;
    }
    return true;
}

} // namespace gltfUsd
