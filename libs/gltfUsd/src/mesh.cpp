// SPDX-License-Identifier: Apache-2.0
#include "mesh.h"

#include "animation.h"
#include "component.h"
#include "gltfCore/accessor.h"
#include "material.h"
#include "sceneMap.h"
#include "tangents.h"

#include <fastgltf/core.hpp>

#include "pxr/base/gf/vec2f.h"
#include "pxr/base/gf/range3d.h"
#include "pxr/base/gf/vec3f.h"
#include "pxr/base/gf/vec4f.h"
#include "pxr/base/tf/diagnostic.h"
#include "pxr/base/vt/array.h"
#include "pxr/base/vt/value.h"
#include "pxr/usd/sdf/types.h"
#include "pxr/usd/usdGeom/basisCurves.h"
#include "pxr/usd/usdGeom/mesh.h"
#include "pxr/usd/usdGeom/points.h"
#include "pxr/usd/usdGeom/primvarsAPI.h"
#include "pxr/usd/usdGeom/tokens.h"
#include "pxr/usd/usdGeom/xform.h"
#include "pxr/usd/usdShade/materialBindingAPI.h"
#include "pxr/usd/usdSkel/bindingAPI.h"
#include "pxr/usd/usdSkel/blendShape.h"

#include <algorithm>
#include <bit>
#include <charconv>
#include <cmath>
#include <cstdint>
#include <limits>
#include <optional>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace gltfUsd
{
namespace
{

using namespace pxr;

static_assert(sizeof(float) == 4 && std::numeric_limits<float>::is_iec559);

float
readRealComponent(const std::byte* data, std::size_t size, fastgltf::ComponentType type,
                  bool normalized)
{
    const auto bits = ReadUnsigned(data, size);
    if (type == fastgltf::ComponentType::Float)
    {
        return std::bit_cast<float>(bits);
    }
    const bool isSigned =
        type == fastgltf::ComponentType::Byte || type == fastgltf::ComponentType::Short;
    const auto range = std::uint32_t(1) << (size * 8);
    const float value = isSigned && bits >= range / 2 ? float(bits) - float(range) : float(bits);
    if (!normalized)
    {
        return value;
    }
    return std::max(value / float(isSigned ? range / 2 - 1 : range - 1), -1.0f);
}

enum class VertexAttribute
{
    Position,
    Normal,
    Tangent,
    Texcoord,
};

struct VertexFormat
{
    fastgltf::ComponentType type = fastgltf::ComponentType::Float;
    std::size_t size = 4;
    bool normalized = false;
};

// The formats KHR_mesh_quantization adds to those of glTF itself.
bool
isQuantizedFormat(VertexAttribute attribute, const fastgltf::Accessor& accessor)
{
    const auto type = accessor.componentType;
    const bool isSigned =
        type == fastgltf::ComponentType::Byte || type == fastgltf::ComponentType::Short;
    const bool isUnsigned = type == fastgltf::ComponentType::UnsignedByte ||
                            type == fastgltf::ComponentType::UnsignedShort;
    switch (attribute)
    {
    case VertexAttribute::Position:
        return isSigned || isUnsigned;
    case VertexAttribute::Normal:
    case VertexAttribute::Tangent:
        return isSigned && accessor.normalized;
    case VertexAttribute::Texcoord:
        return isSigned || (isUnsigned && !accessor.normalized);
    }
    return false;
}

// Selects the format to read an attribute as. Anything else stays float, which
// GetAccessorData then rejects.
bool
selectVertexFormat(const fastgltf::Asset& asset, std::size_t index, VertexAttribute attribute,
                   VertexFormat& format, std::string& error)
{
    if (index >= asset.accessors.size())
    {
        error = "vertex attribute accessor index is out of range";
        return false;
    }
    const auto& accessor = asset.accessors[index];
    const auto type = accessor.componentType;
    const bool isCoreInteger = attribute == VertexAttribute::Texcoord && accessor.normalized &&
                               (type == fastgltf::ComponentType::UnsignedByte ||
                                type == fastgltf::ComponentType::UnsignedShort);
    if (!isCoreInteger && !isQuantizedFormat(attribute, accessor))
    {
        return true;
    }
    // The extension's specification says that it cannot be optional, because a file has no
    // float version of the data for a client that does not implement it.
    if (!isCoreInteger &&
        std::find(asset.extensionsRequired.begin(), asset.extensionsRequired.end(),
                  fastgltf::extensions::KHR_mesh_quantization) == asset.extensionsRequired.end())
    {
        error = "vertex attribute format requires KHR_mesh_quantization in extensionsRequired";
        return false;
    }
    format = {type, fastgltf::getComponentByteSize(type), accessor.normalized};
    return true;
}

struct MeshAccessor
{
    const fastgltf::Asset& asset;
    std::size_t index;
};

MeshAccessor
meshAccessor(const fastgltf::Asset& asset, const fastgltf::Attribute& attribute,
             const gltfCore::DecodedDracoPrimitive* decoded)
{
    if (decoded)
    {
        const auto mapped = decoded->attributes.find(attribute.name);
        if (mapped != decoded->attributes.end())
        {
            return {decoded->data, mapped->second};
        }
    }
    return {asset, attribute.accessorIndex};
}

bool
readVectors(MeshAccessor source, VertexAttribute attribute, VtVec3fArray& values,
            std::string& error, const gltfCore::BufferReader& buffers,
            std::optional<std::size_t> morphCount = std::nullopt)
{
    const auto& [asset, index] = source;
    VertexFormat format;
    if (!selectVertexFormat(asset, index, attribute, format, error))
    {
        return false;
    }
    if (morphCount && (format.type == fastgltf::ComponentType::UnsignedByte ||
                       format.type == fastgltf::ComponentType::UnsignedShort))
    {
        error = "quantized morph deltas require signed components";
        return false;
    }
    gltfCore::AccessorData data;
    if (!gltfCore::GetAccessorData(asset, index, fastgltf::AccessorType::Vec3, format.type,
                                   format.size * 3, true, data, error, buffers, format.normalized))
    {
        return false;
    }
    if (morphCount && data.count != *morphCount)
    {
        error = "morph target accessor count does not match vertex count";
        return false;
    }
    if (data.count > std::size_t(std::numeric_limits<int>::max()))
    {
        error = "vertex count exceeds USD's index range";
        return false;
    }
    values.resize(data.count);
    for (std::size_t i = 0; i < data.count; ++i)
    {
        const auto* element = data.Element(i);
        for (std::size_t component = 0; component < 3; ++component)
        {
            const float value = readRealComponent(element + component * format.size, format.size,
                                                  format.type, format.normalized);
            if (!std::isfinite(value))
            {
                error = "vertex attribute contains a non-finite value";
                return false;
            }
            values[i][component] = value;
        }
    }
    return true;
}

bool
readTangents(MeshAccessor source, VtVec4fArray& values, std::string& error,
             const gltfCore::BufferReader& buffers)
{
    const auto& [asset, index] = source;
    VertexFormat format;
    if (!selectVertexFormat(asset, index, VertexAttribute::Tangent, format, error))
    {
        return false;
    }
    gltfCore::AccessorData data;
    if (!gltfCore::GetAccessorData(asset, index, fastgltf::AccessorType::Vec4, format.type,
                                   format.size * 4, true, data, error, buffers, format.normalized))
    {
        return false;
    }
    values.resize(data.count);
    for (std::size_t i = 0; i < data.count; ++i)
    {
        const auto* element = data.Element(i);
        for (std::size_t component = 0; component < 4; ++component)
        {
            const float value = readRealComponent(element + component * format.size, format.size,
                                                  format.type, format.normalized);
            if (!std::isfinite(value))
            {
                error = "tangent contains a non-finite value";
                return false;
            }
            values[i][component] = value;
        }
    }
    return true;
}

bool
readTexcoords(MeshAccessor source, VtVec2fArray& values, std::string& error,
              const gltfCore::BufferReader& buffers)
{
    const auto& [asset, index] = source;
    VertexFormat format;
    if (!selectVertexFormat(asset, index, VertexAttribute::Texcoord, format, error))
    {
        return false;
    }
    gltfCore::AccessorData data;
    if (!gltfCore::GetAccessorData(asset, index, fastgltf::AccessorType::Vec2, format.type,
                                   format.size * 2, true, data, error, buffers, format.normalized))
    {
        return false;
    }
    if (data.count > std::size_t(std::numeric_limits<int>::max()))
    {
        error = "texture coordinate count exceeds USD's index range";
        return false;
    }
    values.resize(data.count);
    for (std::size_t i = 0; i < data.count; ++i)
    {
        const auto* element = data.Element(i);
        for (std::size_t component = 0; component < 2; ++component)
        {
            const float value = readRealComponent(element + component * format.size, format.size,
                                                  format.type, format.normalized);
            if (!std::isfinite(value))
            {
                error = "texture coordinate contains a non-finite value";
                return false;
            }
            values[i][component] = value;
        }
    }
    return true;
}

bool
readColors(MeshAccessor source, VtVec3fArray& colors, VtFloatArray& alpha, std::string& error,
           const gltfCore::BufferReader& buffers)
{
    const auto& [asset, index] = source;
    if (index >= asset.accessors.size())
    {
        error = "vertex color accessor index is out of range";
        return false;
    }
    const auto& accessor = asset.accessors[index];
    std::size_t components;
    if (accessor.type == fastgltf::AccessorType::Vec3)
    {
        components = 3;
    }
    else if (accessor.type == fastgltf::AccessorType::Vec4)
    {
        components = 4;
    }
    else
    {
        error = "vertex color accessor has an invalid type";
        return false;
    }
    const auto type = accessor.componentType;
    std::size_t size;
    switch (type)
    {
    case fastgltf::ComponentType::Float:
        size = 4;
        break;
    case fastgltf::ComponentType::UnsignedByte:
        size = 1;
        break;
    case fastgltf::ComponentType::UnsignedShort:
        size = 2;
        break;
    default:
        error = "vertex color accessor has an unsupported component type";
        return false;
    }
    gltfCore::AccessorData data;
    if (!gltfCore::GetAccessorData(asset, index, accessor.type, type, size * components, true, data,
                                   error, buffers, type != fastgltf::ComponentType::Float))
    {
        return false;
    }
    if (data.count > std::size_t(std::numeric_limits<int>::max()))
    {
        error = "vertex color count exceeds USD's index range";
        return false;
    }
    colors.resize(data.count);
    if (components == 4)
    {
        alpha.resize(data.count);
    }
    for (std::size_t i = 0; i < data.count; ++i)
    {
        const auto* element = data.Element(i);
        for (std::size_t component = 0; component < components; ++component)
        {
            const float value = readRealComponent(element + component * size, size, type,
                                                  type != fastgltf::ComponentType::Float);
            if (!std::isfinite(value) || value < 0 || value > 1)
            {
                error = "vertex color contains a non-finite or out-of-range value";
                return false;
            }
            if (component == 3)
            {
                alpha[i] = value;
            }
            else
            {
                colors[i][component] = value;
            }
        }
    }
    return true;
}

bool
isPointsOrLines(fastgltf::PrimitiveType type)
{
    return type == fastgltf::PrimitiveType::Points || type == fastgltf::PrimitiveType::Lines ||
           type == fastgltf::PrimitiveType::LineLoop || type == fastgltf::PrimitiveType::LineStrip;
}

bool
validateIndexCount(std::size_t count, fastgltf::PrimitiveType type, std::string& error)
{
    const auto limit = std::size_t(std::numeric_limits<int>::max());
    switch (type)
    {
    case fastgltf::PrimitiveType::Points:
        if (count < 1 || count > limit)
        {
            error = "point index count is invalid";
            return false;
        }
        return true;
    case fastgltf::PrimitiveType::Lines:
        if (count < 2 || count > limit || count % 2 != 0)
        {
            error = "line index count is invalid";
            return false;
        }
        return true;
    case fastgltf::PrimitiveType::LineLoop:
    case fastgltf::PrimitiveType::LineStrip:
        if (count < 2 || count > limit)
        {
            error = "line strip or loop index count is invalid";
            return false;
        }
        return true;
    case fastgltf::PrimitiveType::Triangles:
        if (count < 3 || count > limit || count % 3 != 0)
        {
            error = "triangle index count is invalid";
            return false;
        }
        return true;
    default:
        break;
    }
    if (count < 3 || count - 2 > limit / 3)
    {
        error = "triangle strip or fan index count is invalid";
        return false;
    }
    return true;
}

// The values of each index in turn, since points and curves have no indices of their own.
template <typename T>
void
gatherVertices(VtArray<T>& values, const VtIntArray& indices)
{
    if (values.empty())
    {
        return;
    }
    VtArray<T> gathered(indices.size());
    for (std::size_t i = 0; i < indices.size(); ++i)
    {
        gathered[i] = values[indices[i]];
    }
    values = std::move(gathered);
}

// Defines the points or linear curves of a POINTS, LINES, LINE_LOOP or LINE_STRIP primitive, whose
// vertices have already been gathered in index order.
UsdGeomPointBased
definePointsOrCurves(const UsdPrim& parent, const SdfPath& path, fastgltf::PrimitiveType type,
                     const VtVec3fArray& points)
{
    const auto stage = parent.GetStage();
    VtVec3fArray extent;
    if (!UsdGeomPointBased::ComputeExtent(points, &extent))
    {
        return UsdGeomPointBased();
    }
    if (type == fastgltf::PrimitiveType::Points)
    {
        // glTF gives points no size in the scene, only a recommended one in viewport space.
        auto geometry = UsdGeomPoints::Define(stage, path);
        if (!geometry || !geometry.CreatePointsAttr().Set(points) ||
            !geometry.CreateWidthsAttr().Set(VtFloatArray(1, 0.0f)) ||
            !geometry.SetWidthsInterpolation(UsdGeomTokens->constant) ||
            !geometry.CreateExtentAttr().Set(extent))
        {
            return UsdGeomPointBased();
        }
        return geometry;
    }
    VtIntArray counts;
    if (type == fastgltf::PrimitiveType::Lines)
    {
        counts.assign(points.size() / 2, 2);
    }
    else
    {
        counts.assign(1, static_cast<int>(points.size()));
    }
    auto geometry = UsdGeomBasisCurves::Define(stage, path);
    if (!geometry || !geometry.CreateTypeAttr().Set(UsdGeomTokens->linear) ||
        !geometry.CreateWrapAttr().Set(type == fastgltf::PrimitiveType::LineLoop
                                           ? UsdGeomTokens->periodic
                                           : UsdGeomTokens->nonperiodic) ||
        !geometry.CreateCurveVertexCountsAttr().Set(counts) ||
        !geometry.CreatePointsAttr().Set(points) || !geometry.CreateExtentAttr().Set(extent))
    {
        return UsdGeomPointBased();
    }
    return geometry;
}

bool
readIndices(const fastgltf::Asset& asset, std::size_t index, std::size_t pointCount,
            fastgltf::PrimitiveType type, VtIntArray& values, std::string& error,
            const gltfCore::BufferReader& buffers)
{
    if (index >= asset.accessors.size())
    {
        error = "index accessor is out of range";
        return false;
    }
    const auto componentType = asset.accessors[index].componentType;
    std::size_t size;
    switch (componentType)
    {
    case fastgltf::ComponentType::UnsignedByte:
        size = 1;
        break;
    case fastgltf::ComponentType::UnsignedShort:
        size = 2;
        break;
    case fastgltf::ComponentType::UnsignedInt:
        size = 4;
        break;
    default:
        error = "index accessor has an unsupported component type";
        return false;
    }
    gltfCore::AccessorData data;
    if (!gltfCore::GetAccessorData(asset, index, fastgltf::AccessorType::Scalar, componentType,
                                   size, false, data, error, buffers))
    {
        return false;
    }
    if (!validateIndexCount(data.count, type, error))
    {
        return false;
    }
    values.resize(data.count);
    for (std::size_t i = 0; i < data.count; ++i)
    {
        const auto value = ReadUnsigned(data.Element(i), size);
        const auto reserved = size == 4 ? std::numeric_limits<std::uint32_t>::max()
                                        : (std::uint32_t(1) << (size * 8)) - 1;
        if (value >= pointCount || value == reserved)
        {
            error = "triangle index is out of range";
            return false;
        }
        values[i] = static_cast<int>(value);
    }
    return true;
}

bool
authorMorphTargets(const fastgltf::Asset& asset, const fastgltf::Primitive& primitive,
                   const UsdGeomPointBased& mesh, std::size_t pointCount, bool hasNormals,
                   bool hasTangents, const gltfCore::BufferReader& buffers, std::string& error)
{
    SdfPathVector paths;
    for (std::size_t i = 0; i < primitive.targets.size(); ++i)
    {
        VtVec3fArray offsets, normals, tangents;
        for (const auto& attribute : primitive.targets[i])
        {
            VtVec3fArray* values;
            VertexAttribute kind;
            bool hasBase = true;
            if (attribute.name == "POSITION")
            {
                values = &offsets;
                kind = VertexAttribute::Position;
            }
            else if (attribute.name == "NORMAL")
            {
                values = &normals;
                kind = VertexAttribute::Normal;
                hasBase = hasNormals;
            }
            else if (attribute.name == "TANGENT")
            {
                values = &tangents;
                kind = VertexAttribute::Tangent;
                hasBase = hasTangents;
            }
            else
            {
                error = "unsupported morph target attribute " + attribute.name;
                return false;
            }
            if (!values->empty() || !hasBase)
            {
                error = "repeated or missing base attribute for morph target " + attribute.name;
                return false;
            }
            if (!readVectors({asset, attribute.accessorIndex}, kind, *values, error, buffers,
                             pointCount))
            {
                return false;
            }
        }
        if (offsets.empty() && normals.empty() && tangents.empty())
        {
            error = "morph target has no attributes";
            return false;
        }
        const auto path = mesh.GetPath().AppendChild(TfToken("BlendShape_" + std::to_string(i)));
        const auto shape = UsdSkelBlendShape::Define(mesh.GetPrim().GetStage(), path);
        if (offsets.empty())
        {
            offsets.resize(pointCount, GfVec3f(0));
        }
        // UsdSkel has no tangent offsets, so these are kept for consumers and not applied.
        if (!shape || !shape.CreateOffsetsAttr().Set(offsets) ||
            (!normals.empty() && !shape.CreateNormalOffsetsAttr().Set(normals)) ||
            (!tangents.empty() &&
             !shape.GetPrim()
                  .CreateAttribute(TfToken("gltf:tangentOffsets"), SdfValueTypeNames->Vector3fArray)
                  .Set(tangents)))
        {
            error = "could not author USD blend shape";
            return false;
        }
        paths.push_back(path);
    }
    if (!paths.empty())
    {
        const auto binding = UsdSkelBindingAPI::Apply(mesh.GetPrim());
        if (!binding || !binding.CreateBlendShapeTargetsRel().SetTargets(paths))
        {
            error = "could not author USD blend shape targets";
            return false;
        }
    }
    return true;
}

bool
authorPrimitive(const fastgltf::Asset& asset, const fastgltf::Primitive& primitive,
                const UsdPrim& parent, std::size_t index, std::size_t meshIndex, std::string& error,
                const gltfCore::BufferReader& buffers, MaterialAuthor& materials,
                const gltfCore::DecodedDracoPrimitive* decoded, VariantBindings& variantBindings)
{
    const bool pointsOrLines = isPointsOrLines(primitive.type);
    if (pointsOrLines && !primitive.targets.empty())
    {
        error = "morph targets on points and lines are not supported";
        return false;
    }
    const fastgltf::Attribute* position = nullptr;
    const fastgltf::Attribute* normal = nullptr;
    const fastgltf::Attribute* tangent = nullptr;
    const fastgltf::Attribute* color = nullptr;
    std::vector<std::pair<std::size_t, const fastgltf::Attribute*>> texcoords;
    for (const auto& attribute : primitive.attributes)
    {
        if (attribute.name == "POSITION" && !position)
        {
            position = &attribute;
        }
        else if (attribute.name == "NORMAL" && !normal)
        {
            normal = &attribute;
        }
        else if (attribute.name == "TANGENT" && !tangent)
        {
            tangent = &attribute;
        }
        else if (attribute.name == "COLOR_0" && !color)
        {
            color = &attribute;
        }
        else if (attribute.name.starts_with("TEXCOORD_"))
        {
            const auto suffix = std::string_view(attribute.name).substr(9);
            std::size_t set = 0;
            const auto parsed = std::from_chars(suffix.data(), suffix.data() + suffix.size(), set);
            if (suffix.empty() || (suffix.size() > 1 && suffix[0] == '0') ||
                parsed.ec != std::errc{} || parsed.ptr != suffix.data() + suffix.size())
            {
                error = "invalid texture coordinate attribute: " + attribute.name;
                return false;
            }
            texcoords.emplace_back(set, &attribute);
        }
        else if (!attribute.name.starts_with("JOINTS_") && !attribute.name.starts_with("WEIGHTS_"))
        {
            error = "unsupported or repeated vertex attribute: " + attribute.name;
            return false;
        }
    }
    if (!position)
    {
        error = "primitive has no POSITION attribute";
        return false;
    }
    std::sort(texcoords.begin(), texcoords.end(),
              [](const auto& a, const auto& b) { return a.first < b.first; });
    for (std::size_t i = 0; i < texcoords.size(); ++i)
    {
        if (texcoords[i].first != i)
        {
            error = "texture coordinate sets must start at zero and be consecutive";
            return false;
        }
    }
    VtVec3fArray points;
    if (!readVectors(meshAccessor(asset, *position, decoded), VertexAttribute::Position, points,
                     error, buffers))
    {
        return false;
    }
    VtVec3fArray normals;
    if (normal && (!readVectors(meshAccessor(asset, *normal, decoded), VertexAttribute::Normal,
                                normals, error, buffers) ||
                   normals.size() != points.size()))
    {
        if (error.empty())
        {
            error = "normal and position counts differ";
        }
        return false;
    }
    VtVec4fArray tangents;
    if (tangent && !readTangents(meshAccessor(asset, *tangent, decoded), tangents, error, buffers))
    {
        return false;
    }
    if (tangent && tangents.size() != points.size())
    {
        error = "tangent and position counts differ";
        return false;
    }
    VtVec3fArray colors;
    VtFloatArray alpha;
    if (color && !readColors(meshAccessor(asset, *color, decoded), colors, alpha, error, buffers))
    {
        return false;
    }
    if (color && colors.size() != points.size())
    {
        error = "vertex color and position counts differ";
        return false;
    }
    std::vector<VtVec2fArray> uvSets(texcoords.size());
    for (std::size_t i = 0; i < texcoords.size(); ++i)
    {
        if (!readTexcoords(meshAccessor(asset, *texcoords[i].second, decoded), uvSets[i], error,
                           buffers))
        {
            return false;
        }
        if (uvSets[i].size() != points.size())
        {
            error = "texture coordinate and position counts differ";
            return false;
        }
    }
    VtIntArray indices;
    if (decoded)
    {
        indices.assign(decoded->indices.begin(), decoded->indices.end());
    }
    else if (primitive.indicesAccessor)
    {
        if (!readIndices(asset, *primitive.indicesAccessor, points.size(), primitive.type, indices,
                         error, buffers))
        {
            return false;
        }
    }
    else
    {
        if (!validateIndexCount(points.size(), primitive.type, error))
        {
            return false;
        }
        indices.resize(points.size());
        for (std::size_t i = 0; i < points.size(); ++i)
        {
            indices[i] = static_cast<int>(i);
        }
    }
    const auto path = parent.GetPath().AppendChild(TfToken("Primitive_" + std::to_string(index)));
    UsdGeomPointBased geometry;
    bool generateNormals = false;
    if (pointsOrLines)
    {
        gatherVertices(points, indices);
        gatherVertices(normals, indices);
        gatherVertices(tangents, indices);
        gatherVertices(colors, indices);
        gatherVertices(alpha, indices);
        for (auto& uvs : uvSets)
        {
            gatherVertices(uvs, indices);
        }
        geometry = definePointsOrCurves(parent, path, primitive.type, points);
        if (!geometry)
        {
            error = "could not author USD points or curves";
            return false;
        }
    }
    else if (primitive.type != fastgltf::PrimitiveType::Triangles)
    {
        VtIntArray triangles((indices.size() - 2) * 3);
        for (std::size_t i = 0; i + 2 < indices.size(); ++i)
        {
            if (primitive.type == fastgltf::PrimitiveType::TriangleFan)
            {
                triangles[i * 3] = indices[0];
                triangles[i * 3 + 1] = indices[i + 1];
            }
            else
            {
                // Alternate strip winding, including across degenerate triangles.
                triangles[i * 3] = indices[i + (i % 2)];
                triangles[i * 3 + 1] = indices[i + 1 - (i % 2)];
            }
            triangles[i * 3 + 2] = indices[i + 2];
        }
        indices = std::move(triangles);
    }
    if (!pointsOrLines)
    {
        generateNormals = !normal && primitive.targets.empty();
    }
    if (generateNormals)
    {
        normals.resize(indices.size() / 3);
        for (std::size_t i = 0; i < normals.size(); ++i)
        {
            const auto& a = points[indices[i * 3]];
            const auto& b = points[indices[i * 3 + 1]];
            const auto& c = points[indices[i * 3 + 2]];
            const double ux = double(b[0]) - a[0], uy = double(b[1]) - a[1],
                         uz = double(b[2]) - a[2];
            const double vx = double(c[0]) - a[0], vy = double(c[1]) - a[1],
                         vz = double(c[2]) - a[2];
            const double x = uy * vz - uz * vy, y = uz * vx - ux * vz, z = ux * vy - uy * vx;
            const double length = std::hypot(x, y, z);
            normals[i] = length == 0
                             ? GfVec3f(0)
                             : GfVec3f(float(x / length), float(y / length), float(z / length));
        }
    }
    if (!pointsOrLines)
    {
        auto mesh = UsdGeomMesh::Define(parent.GetStage(), path);
        const VtIntArray counts(indices.size() / 3, 3);
        VtVec3fArray extent;
        if (!mesh || !mesh.CreatePointsAttr().Set(points) ||
            !mesh.CreateFaceVertexCountsAttr().Set(counts) ||
            !mesh.CreateFaceVertexIndicesAttr().Set(indices) ||
            !mesh.CreateSubdivisionSchemeAttr().Set(UsdGeomTokens->none) ||
            !UsdGeomPointBased::ComputeExtent(points, &extent) ||
            !mesh.CreateExtentAttr().Set(extent))
        {
            error = "could not author USD mesh";
            return false;
        }
        geometry = mesh;
    }
    auto& mesh = geometry;
    if (!UsdShadeMaterialBindingAPI::Apply(mesh.GetPrim()))
    {
        error = "could not apply USD material binding API";
        return false;
    }
    // Leave absent normals on morphing meshes to the consumer so they can follow the deformation.
    if ((normal || generateNormals) &&
        (!mesh.CreateNormalsAttr().Set(normals) ||
         !mesh.SetNormalsInterpolation(normal ? UsdGeomTokens->vertex : UsdGeomTokens->uniform)))
    {
        error = "could not author USD normals";
        return false;
    }
    const UsdGeomPrimvarsAPI primvars(mesh);
    if (tangent && !primvars
                        .CreatePrimvar(TfToken("gltf_tangent"), SdfValueTypeNames->Float4Array,
                                       UsdGeomTokens->vertex)
                        .Set(tangents))
    {
        error = "could not author USD tangents";
        return false;
    }
    // Points and lines are lit only with NORMAL, and never draw normal textures.
    const auto use = !pointsOrLines ? MaterialUse::Full
                     : normal       ? MaterialUse::WithoutNormalTextures
                                    : MaterialUse::Unlit;
    // Compute these before flipping glTF UVs. A variant can select a normal map on another set.
    std::map<std::size_t, VtVec4fArray> generatedTangents;
    const auto prepareBinding =
        [&](std::optional<std::size_t> materialIndex, MaterialBinding& binding)
    {
        if (!materials.Prepare(materialIndex, uvSets.size(), color ? &colors : nullptr, alpha,
                               binding, error, use))
        {
            return false;
        }
        if (pointsOrLines)
        {
            if (normal && tangent && materialIndex && materials.HasNormalTextures(*materialIndex))
            {
                TF_WARN("glTF mesh %zu primitive %zu: normal textures are not drawn on points and "
                        "lines, because neither network has a tangent frame for them",
                        meshIndex, index);
            }
            return true;
        }
        if (materialIndex && (!tangent || !normal) && !normals.empty())
        {
            const auto uvSet = materials.TangentTexCoord(*materialIndex);
            if (uvSet && *uvSet < uvSets.size())
            {
                auto [it, inserted] = generatedTangents.try_emplace(*uvSet);
                if (inserted)
                {
                    it->second =
                        GenerateTangents(points, normals, uvSets[*uvSet], indices, generateNormals);
                }
                binding.generatedTangents = it->second;
            }
        }
        return true;
    };
    const std::optional<std::size_t> original =
        primitive.materialIndex ? std::optional(*primitive.materialIndex) : std::nullopt;
    MaterialBinding binding;
    if (!prepareBinding(original, binding))
    {
        return false;
    }
    if (primitive.mappings.empty())
    {
        if (!binding.Author(mesh, error))
        {
            return false;
        }
    }
    else
    {
        if (primitive.mappings.size() != asset.materialVariants.size())
        {
            error = "KHR_materials_variants mappings do not match the root variant count";
            return false;
        }
        auto& bindings = variantBindings[{meshIndex, index}];
        bindings.emplace(original, std::move(binding));
        for (const auto material : primitive.mappings)
        {
            if (!material)
            {
                continue;
            }
            auto [it, inserted] = bindings.try_emplace(*material);
            if (inserted && !prepareBinding(*material, it->second))
            {
                error =
                    "KHR_materials_variants material " + std::to_string(*material) + ": " + error;
                return false;
            }
        }
    }
    if (color)
    {
        if (!mesh.CreateDisplayColorPrimvar(UsdGeomTokens->vertex).Set(colors))
        {
            error = "could not author USD vertex colors";
            return false;
        }
        if (!alpha.empty() &&
            !primvars
                 .CreatePrimvar(TfToken("gltf_colorAlpha"), SdfValueTypeNames->FloatArray,
                                UsdGeomTokens->vertex)
                 .Set(alpha))
        {
            error = "could not author USD vertex color alpha";
            return false;
        }
    }
    for (std::size_t i = 0; i < uvSets.size(); ++i)
    {
        const auto name = i == 0 ? std::string("st") : "st" + std::to_string(i);
        for (auto& uv : uvSets[i])
        {
            uv[1] = 1.0f - uv[1];
        }
        if (!primvars
                 .CreatePrimvar(TfToken(name), SdfValueTypeNames->TexCoord2fArray,
                                UsdGeomTokens->vertex)
                 .Set(uvSets[i]))
        {
            error = "could not author USD texture coordinates";
            return false;
        }
    }
    return authorMorphTargets(asset, primitive, mesh, points.size(), normal != nullptr,
                              tangent != nullptr, buffers, error);
}

// Collects the indices of a vertex attribute's numbered sets in set order.
bool
skinAccessors(const fastgltf::Primitive& primitive, std::string_view prefix,
              std::vector<std::pair<std::size_t, const fastgltf::Attribute*>>& sets,
              std::string& error)
{
    for (const auto& attribute : primitive.attributes)
    {
        if (!attribute.name.starts_with(prefix))
        {
            continue;
        }
        const auto suffix = std::string_view(attribute.name).substr(prefix.size());
        std::size_t number = 0;
        const auto parsed = std::from_chars(suffix.data(), suffix.data() + suffix.size(), number);
        if ((suffix.size() > 1 && suffix.front() == '0') || parsed.ec != std::errc{} ||
            parsed.ptr != suffix.data() + suffix.size())
        {
            error = "invalid skin attribute: " + attribute.name;
            return false;
        }
        sets.emplace_back(number, &attribute);
    }
    std::sort(sets.begin(), sets.end());
    for (std::size_t i = 0; i < sets.size(); ++i)
    {
        if (sets[i].first != i)
        {
            error = std::string(prefix) + " sets must start at zero and be consecutive";
            return false;
        }
    }
    return true;
}

bool
readInfluenceSet(MeshAccessor jointSource, MeshAccessor weightSource,
                 const gltfCore::BufferReader& buffers, const std::vector<int>& remap,
                 std::size_t pointCount, std::size_t width, std::size_t offset, VtIntArray& indices,
                 VtFloatArray& weights, std::string& error)
{
    using fastgltf::ComponentType;
    const auto& [jointAsset, jointsIndex] = jointSource;
    const auto& [weightAsset, weightsIndex] = weightSource;
    if (jointsIndex >= jointAsset.accessors.size() || weightsIndex >= weightAsset.accessors.size())
    {
        error = "skin attribute accessor index is out of range";
        return false;
    }
    const auto jointType = jointAsset.accessors[jointsIndex].componentType;
    const auto weightType = weightAsset.accessors[weightsIndex].componentType;
    const bool quantized = weightType != ComponentType::Float;
    if ((jointType != ComponentType::UnsignedByte && jointType != ComponentType::UnsignedShort) ||
        (quantized && weightType != ComponentType::UnsignedByte &&
         weightType != ComponentType::UnsignedShort))
    {
        error = "invalid skin attribute component type";
        return false;
    }
    const auto jointSize = fastgltf::getComponentByteSize(jointType);
    const auto weightSize = fastgltf::getComponentByteSize(weightType);
    gltfCore::AccessorData joints, values;
    if (!gltfCore::GetAccessorData(jointAsset, jointsIndex, fastgltf::AccessorType::Vec4, jointType,
                                   4 * jointSize, true, joints, error, buffers) ||
        !gltfCore::GetAccessorData(weightAsset, weightsIndex, fastgltf::AccessorType::Vec4,
                                   weightType, 4 * weightSize, true, values, error, buffers,
                                   quantized))
    {
        return false;
    }
    if (joints.count != pointCount || values.count != pointCount)
    {
        error = "skin attribute and position counts differ";
        return false;
    }
    for (std::size_t p = 0; p < pointCount; ++p)
    {
        const auto* joint4 = joints.Element(p);
        const auto* weight4 = values.Element(p);
        for (std::size_t c = 0; c < 4; ++c)
        {
            const auto joint = ReadUnsigned(joint4 + c * jointSize, jointSize);
            const auto weight =
                readRealComponent(weight4 + c * weightSize, weightSize, weightType, quantized);
            if (joint >= remap.size() || !std::isfinite(weight) || weight < 0.0f)
            {
                error = "skin influence has an out-of-range joint or an invalid weight";
                return false;
            }
            const auto slot = p * width + offset + c;
            indices[slot] = remap[joint];
            weights[slot] = weight;
        }
    }
    return true;
}

bool
bindPrimitive(const fastgltf::Asset& asset, const fastgltf::Primitive& primitive,
              const UsdPrim& prim, const MappedSkin& skin, const gltfCore::BufferReader& buffers,
              std::string& error, const gltfCore::DecodedDracoPrimitive* decoded)
{
    if (isPointsOrLines(primitive.type))
    {
        error = "skins on points and lines are not supported";
        return false;
    }
    std::vector<std::pair<std::size_t, const fastgltf::Attribute*>> joints, weights;
    if (!skinAccessors(primitive, "JOINTS_", joints, error) ||
        !skinAccessors(primitive, "WEIGHTS_", weights, error))
    {
        return false;
    }
    if (joints.empty() || joints.size() != weights.size())
    {
        error = "skinned primitive must have paired JOINTS_n and WEIGHTS_n attributes";
        return false;
    }
    // Geometry has already validated the presence, index and count of POSITION.
    const auto pointCount =
        asset.accessors[primitive.findAttribute("POSITION")->accessorIndex].count;
    if (joints.size() > std::numeric_limits<int>::max() / 4 ||
        pointCount > std::numeric_limits<std::size_t>::max() / sizeof(float) / 4 / joints.size())
    {
        error = "too many skin influences";
        return false;
    }
    const auto width = 4 * joints.size();
    VtIntArray indices(pointCount * width);
    VtFloatArray influences(pointCount * width);
    for (std::size_t i = 0; i < joints.size(); ++i)
    {
        if (!readInfluenceSet(meshAccessor(asset, *joints[i].second, decoded),
                              meshAccessor(asset, *weights[i].second, decoded), buffers, skin.remap,
                              pointCount, width, 4 * i, indices, influences, error))
        {
            return false;
        }
    }
    // Normalize across all sets together, including when the source weights were quantized.
    for (std::size_t p = 0; p < pointCount; ++p)
    {
        const auto start = p * width;
        double sum = 0.0;
        for (std::size_t c = 0; c < width; ++c)
        {
            sum += influences[start + c];
        }
        if (sum == 0.0)
        {
            error = "skin weights sum to zero at a vertex";
            return false;
        }
        for (std::size_t c = 0; c < width; ++c)
        {
            influences[start + c] = float(influences[start + c] / sum);
        }
    }
    const auto binding = UsdSkelBindingAPI::Apply(prim);
    const int elementSize = static_cast<int>(width);
    if (!binding || !binding.CreateSkeletonRel().SetTargets({skin.path}) ||
        !binding.CreateGeomBindTransformAttr().Set(GfMatrix4d(1.0)) ||
        !binding.CreateJointIndicesPrimvar(false, elementSize).Set(indices) ||
        !binding.CreateJointWeightsPrimvar(false, elementSize).Set(influences))
    {
        error = "could not author the skin binding";
        return false;
    }
    return true;
}

} // namespace

bool
ConvertMesh(const fastgltf::Asset& asset, std::size_t meshIndex, const pxr::UsdStagePtr& stage,
            const pxr::SdfPath& path, const gltfCore::BufferReader& buffers,
            MaterialAuthor& materials, DecodedMeshes& decodedMeshes,
            VariantBindings& variantBindings)
{
    using namespace pxr;
    if (meshIndex >= asset.meshes.size())
    {
        TF_RUNTIME_ERROR("glTF mesh index %zu is out of range", meshIndex);
        return false;
    }
    const auto& source = asset.meshes[meshIndex];
    if (source.primitives.empty())
    {
        TF_RUNTIME_ERROR("glTF mesh %zu has no primitives", meshIndex);
        return false;
    }
    const auto targetCount = source.primitives.front().targets.size();
    if (!source.weights.empty() && (source.weights.size() != targetCount ||
                                    std::any_of(source.weights.begin(), source.weights.end(),
                                                [](double weight)
                                                {
                                                    return !std::isfinite(weight) ||
                                                           std::abs(weight) >
                                                               std::numeric_limits<float>::max();
                                                })))
    {
        TF_RUNTIME_ERROR("glTF mesh %zu: morph weights must be finite floats matching target count",
                         meshIndex);
        return false;
    }
    const auto group = UsdGeomXform::Define(stage, path);
    if (!group ||
        (!source.name.empty() && !group.GetPrim().SetDisplayName(std::string(source.name))))
    {
        TF_RUNTIME_ERROR("Could not author glTF mesh %zu", meshIndex);
        return false;
    }
    for (std::size_t i = 0; i < source.primitives.size(); ++i)
    {
        if (source.primitives[i].targets.size() != targetCount)
        {
            TF_RUNTIME_ERROR("glTF mesh %zu: primitives have different morph target counts",
                             meshIndex);
            return false;
        }
        std::string error;
        const auto& primitive = source.primitives[i];
        gltfCore::DecodedDracoPrimitive decoded;
        if (!gltfCore::DecodeDracoPrimitive(asset, primitive, buffers, decoded, error) ||
            !authorPrimitive(asset, primitive, group.GetPrim(), i, meshIndex, error, buffers,
                             materials, decoded.indices.empty() ? nullptr : &decoded,
                             variantBindings))
        {
            TF_RUNTIME_ERROR("glTF mesh %zu primitive %zu: %s", meshIndex, i, error.c_str());
            return false;
        }
        if (primitive.dracoCompression && decoded.indices.empty())
        {
            TF_WARN("glTF mesh %zu primitive %zu: using uncompressed TRIANGLE_STRIP fallback for "
                    "KHR_draco_mesh_compression",
                    meshIndex, i);
        }
        // Only primitives with compressed influences need their accessors again for skin binding.
        if (std::any_of(decoded.attributes.begin(), decoded.attributes.end(),
                        [](const auto& entry)
                        {
                            return entry.first.starts_with("JOINTS_") ||
                                   entry.first.starts_with("WEIGHTS_");
                        }))
        {
            decodedMeshes.emplace(
                std::pair{meshIndex, i},
                gltfCore::DecodedDracoPrimitive{.data = std::move(decoded.data),
                                                .attributes = std::move(decoded.attributes)});
        }
    }
    return true;
}

bool
BindSkinnedMesh(const fastgltf::Asset& asset, std::size_t meshIndex, const pxr::UsdStagePtr& stage,
                const pxr::SdfPath& path, const MappedSkin& skin,
                const gltfCore::BufferReader& buffers, const DecodedMeshes& decodedMeshes)
{
    const auto& primitives = asset.meshes[meshIndex].primitives;
    for (std::size_t i = 0; i < primitives.size(); ++i)
    {
        const auto prim =
            stage->GetPrimAtPath(path.AppendChild(TfToken("Primitive_" + std::to_string(i))));
        std::string error;
        const auto decoded = decodedMeshes.find({meshIndex, i});
        if (!bindPrimitive(asset, primitives[i], prim, skin, buffers, error,
                           decoded == decodedMeshes.end() ? nullptr : &decoded->second))
        {
            TF_RUNTIME_ERROR("glTF skinned mesh %zu primitive %zu at %s: %s", meshIndex, i,
                             path.GetText(), error.c_str());
            return false;
        }
    }
    return true;
}

bool
BindMorphMesh(const fastgltf::Mesh& source, const pxr::UsdStagePtr& stage, const MappedNode& node,
              const AnimationPlan& animations, std::size_t position)
{
    // UsdSkel uses the primitive's extent to pad joint-based bounds. Include the neutral mesh
    // and every variant's weight range, since the primitive and its extent are shared by them.
    std::vector<std::pair<double, double>> ranges;
    for (const double weight : node.morphWeights)
    {
        ranges.emplace_back(std::min(0.0, weight), std::max(0.0, weight));
    }
    for (const auto& animation : animations.animations)
    {
        const auto channel = animation.weights.find(position);
        if (channel == animation.weights.end())
        {
            continue;
        }
        for (const auto& sample : channel->second)
        {
            for (std::size_t i = 0; i < ranges.size(); ++i)
            {
                const double weight = sample.second[i];
                ranges[i].first = std::min(ranges[i].first, weight);
                ranges[i].second = std::max(ranges[i].second, weight);
            }
        }
    }
    for (std::size_t i = 0; i < source.primitives.size(); ++i)
    {
        const auto path = node.mesh.AppendChild(TfToken("Primitive_" + std::to_string(i)));
        const auto mesh = UsdGeomMesh::Get(stage, path);
        const auto binding = UsdSkelBindingAPI(mesh.GetPrim());
        VtVec3fArray extent;
        SdfPathVector targets;
        if (!binding || !binding.CreateBlendShapesAttr().Set(node.blendShapes) ||
            !binding.CreateSkeletonRel().SetTargets({node.morphSkeleton}) ||
            !binding.GetBlendShapeTargetsRel().GetTargets(&targets) ||
            targets.size() != ranges.size() || !mesh.GetExtentAttr().Get(&extent) ||
            extent.size() != 2)
        {
            TF_RUNTIME_ERROR("Could not bind glTF morph targets at %s", path.GetText());
            return false;
        }
        GfVec3d lower(extent[0]), upper(extent[1]);
        for (std::size_t target = 0; target < targets.size(); ++target)
        {
            const auto& [lo, hi] = ranges[target];
            if (lo == 0 && hi == 0)
            {
                continue;
            }
            VtVec3fArray offsets;
            const auto shape = UsdSkelBlendShape::Get(stage, targets[target]);
            if (!shape || !shape.GetOffsetsAttr().Get(&offsets) || offsets.empty())
            {
                TF_RUNTIME_ERROR("Could not read blend shape offsets at %s",
                                 targets[target].GetText());
                return false;
            }
            GfRange3d delta;
            for (const auto& offset : offsets)
            {
                delta.UnionWith(GfVec3d(offset));
            }
            for (std::size_t axis = 0; axis < 3; ++axis)
            {
                const auto a = delta.GetMin()[axis], b = delta.GetMax()[axis];
                lower[axis] += std::min({lo * a, lo * b, hi * a, hi * b});
                upper[axis] += std::max({lo * a, lo * b, hi * a, hi * b});
            }
        }
        const double limit = std::numeric_limits<float>::max();
        for (std::size_t axis = 0; axis < 3; ++axis)
        {
            extent[0][axis] = float(std::max(lower[axis], -limit));
            extent[1][axis] = float(std::min(upper[axis], limit));
        }
        if (!mesh.GetExtentAttr().Set(extent))
        {
            TF_RUNTIME_ERROR("Could not author glTF morph extent at %s", path.GetText());
            return false;
        }
    }
    return true;
}

} // namespace gltfUsd
