// SPDX-License-Identifier: Apache-2.0
#include "mesh.h"

#include "accessor.h"
#include "material.h"

#include <fastgltf/core.hpp>

#include "pxr/base/gf/vec2f.h"
#include "pxr/base/gf/vec3f.h"
#include "pxr/base/gf/vec4f.h"
#include "pxr/base/tf/diagnostic.h"
#include "pxr/base/vt/array.h"
#include "pxr/base/vt/value.h"
#include "pxr/usd/sdf/types.h"
#include "pxr/usd/usdGeom/mesh.h"
#include "pxr/usd/usdGeom/primvarsAPI.h"
#include "pxr/usd/usdGeom/tokens.h"
#include "pxr/usd/usdGeom/xform.h"

#include <algorithm>
#include <bit>
#include <charconv>
#include <cmath>
#include <cstdint>
#include <limits>
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

std::uint32_t
readUnsigned(const std::byte* data, std::size_t size)
{
    std::uint32_t value = 0;
    for (std::size_t i = 0; i < size; ++i)
    {
        value |= std::uint32_t(std::to_integer<unsigned char>(data[i])) << (8 * i);
    }
    return value;
}

float
readRealComponent(const std::byte* data, std::size_t size, fastgltf::ComponentType type,
                  bool normalized)
{
    const auto bits = readUnsigned(data, size);
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

bool
readVectors(const fastgltf::Asset& asset, std::size_t index, VertexAttribute attribute,
            VtVec3fArray& values, std::string& error, const gltfCore::BufferLoader& loadBuffer)
{
    VertexFormat format;
    if (!selectVertexFormat(asset, index, attribute, format, error))
    {
        return false;
    }
    gltfCore::AccessorData data;
    if (!gltfCore::GetAccessorData(asset, index, fastgltf::AccessorType::Vec3, format.type,
                                   format.size * 3, true, data, error, loadBuffer,
                                   format.normalized))
    {
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
        for (std::size_t component = 0; component < 3; ++component)
        {
            const float value =
                readRealComponent(data.bytes.data() + i * data.stride + component * format.size,
                                  format.size, format.type, format.normalized);
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
readTangents(const fastgltf::Asset& asset, std::size_t index, VtVec4fArray& values,
             std::string& error, const gltfCore::BufferLoader& loadBuffer)
{
    VertexFormat format;
    if (!selectVertexFormat(asset, index, VertexAttribute::Tangent, format, error))
    {
        return false;
    }
    gltfCore::AccessorData data;
    if (!gltfCore::GetAccessorData(asset, index, fastgltf::AccessorType::Vec4, format.type,
                                   format.size * 4, true, data, error, loadBuffer,
                                   format.normalized))
    {
        return false;
    }
    values.resize(data.count);
    for (std::size_t i = 0; i < data.count; ++i)
    {
        for (std::size_t component = 0; component < 4; ++component)
        {
            const float value =
                readRealComponent(data.bytes.data() + i * data.stride + component * format.size,
                                  format.size, format.type, format.normalized);
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
readTexcoords(const fastgltf::Asset& asset, std::size_t index, VtVec2fArray& values,
              std::string& error, const gltfCore::BufferLoader& loadBuffer)
{
    VertexFormat format;
    if (!selectVertexFormat(asset, index, VertexAttribute::Texcoord, format, error))
    {
        return false;
    }
    gltfCore::AccessorData data;
    if (!gltfCore::GetAccessorData(asset, index, fastgltf::AccessorType::Vec2, format.type,
                                   format.size * 2, true, data, error, loadBuffer,
                                   format.normalized))
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
        for (std::size_t component = 0; component < 2; ++component)
        {
            const float value =
                readRealComponent(data.bytes.data() + i * data.stride + component * format.size,
                                  format.size, format.type, format.normalized);
            if (!std::isfinite(value))
            {
                error = "texture coordinate contains a non-finite value";
                return false;
            }
            values[i][component] = component == 0 ? value : 1.0f - value;
        }
    }
    return true;
}

bool
readColors(const fastgltf::Asset& asset, std::size_t index, VtVec3fArray& colors,
           VtFloatArray& alpha, std::string& error, const gltfCore::BufferLoader& loadBuffer)
{
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
                                   error, loadBuffer, type != fastgltf::ComponentType::Float))
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
        for (std::size_t component = 0; component < components; ++component)
        {
            const float value =
                readRealComponent(data.bytes.data() + i * data.stride + component * size, size,
                                  type, type != fastgltf::ComponentType::Float);
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
validateIndexCount(std::size_t count, fastgltf::PrimitiveType type, std::string& error)
{
    const auto limit = std::size_t(std::numeric_limits<int>::max());
    if (type == fastgltf::PrimitiveType::Triangles)
    {
        if (count < 3 || count > limit || count % 3 != 0)
        {
            error = "triangle index count is invalid";
            return false;
        }
    }
    else if (count < 3 || count - 2 > limit / 3)
    {
        error = "triangle strip or fan index count is invalid";
        return false;
    }
    return true;
}

bool
readIndices(const fastgltf::Asset& asset, std::size_t index, std::size_t pointCount,
            fastgltf::PrimitiveType type, VtIntArray& values, std::string& error,
            const gltfCore::BufferLoader& loadBuffer)
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
                                   size, false, data, error, loadBuffer))
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
        const auto value = readUnsigned(data.bytes.data() + i * data.stride, size);
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

// Returns what a primitive uses that cannot be converted, or an empty string.
std::string
unsupportedPrimitiveContent(const fastgltf::Primitive& primitive)
{
    switch (primitive.type)
    {
    case fastgltf::PrimitiveType::Points:
        return "primitive mode POINTS is not supported";
    case fastgltf::PrimitiveType::Lines:
        return "primitive mode LINES is not supported";
    case fastgltf::PrimitiveType::LineLoop:
        return "primitive mode LINE_LOOP is not supported";
    case fastgltf::PrimitiveType::LineStrip:
        return "primitive mode LINE_STRIP is not supported";
    default:
        break;
    }
    if (!primitive.targets.empty())
    {
        return "morph targets are not supported";
    }
    if (primitive.dracoCompression)
    {
        return "KHR_draco_mesh_compression is not supported";
    }
    return primitive.mappings.empty() ? "" : "KHR_materials_variants is not supported";
}

bool
authorPrimitive(const fastgltf::Asset& asset, const fastgltf::Primitive& primitive,
                const UsdPrim& parent, std::size_t index, std::string& error,
                const gltfCore::BufferLoader& loadBuffer, MaterialAuthor& materials)
{
    error = unsupportedPrimitiveContent(primitive);
    if (!error.empty())
    {
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
        else
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
    if (!readVectors(asset, position->accessorIndex, VertexAttribute::Position, points, error,
                     loadBuffer))
    {
        return false;
    }
    VtVec3fArray normals;
    if (normal && (!readVectors(asset, normal->accessorIndex, VertexAttribute::Normal, normals,
                                error, loadBuffer) ||
                   normals.size() != points.size()))
    {
        if (error.empty())
        {
            error = "normal and position counts differ";
        }
        return false;
    }
    VtVec4fArray tangents;
    if (tangent && !readTangents(asset, tangent->accessorIndex, tangents, error, loadBuffer))
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
    if (color && !readColors(asset, color->accessorIndex, colors, alpha, error, loadBuffer))
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
        if (!readTexcoords(asset, texcoords[i].second->accessorIndex, uvSets[i], error, loadBuffer))
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
    if (primitive.indicesAccessor)
    {
        if (!readIndices(asset, *primitive.indicesAccessor, points.size(), primitive.type, indices,
                         error, loadBuffer))
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
    if (primitive.type != fastgltf::PrimitiveType::Triangles)
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
    if (!normal)
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
    auto mesh = UsdGeomMesh::Define(parent.GetStage(), parent.GetPath().AppendChild(TfToken(
                                                           "Primitive_" + std::to_string(index))));
    const VtIntArray counts(indices.size() / 3, 3);
    VtVec3fArray extent;
    if (!mesh || !mesh.CreatePointsAttr().Set(points) ||
        !mesh.CreateFaceVertexCountsAttr().Set(counts) ||
        !mesh.CreateFaceVertexIndicesAttr().Set(indices) ||
        !mesh.CreateSubdivisionSchemeAttr().Set(UsdGeomTokens->none) ||
        !UsdGeomPointBased::ComputeExtent(points, &extent) || !mesh.CreateExtentAttr().Set(extent))
    {
        error = "could not author USD mesh";
        return false;
    }
    if (primitive.materialIndex && !materials.Bind(*primitive.materialIndex, uvSets.size(),
                                                   color ? &colors : nullptr, alpha, mesh, error))
    {
        return false;
    }
    if (!primitive.materialIndex && !materials.BindDefault(color != nullptr, mesh, error))
    {
        return false;
    }
    if (!mesh.CreateNormalsAttr().Set(normals) ||
        !mesh.SetNormalsInterpolation(normal ? UsdGeomTokens->vertex : UsdGeomTokens->uniform))
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
        if (!primvars
                 .CreatePrimvar(TfToken(name), SdfValueTypeNames->TexCoord2fArray,
                                UsdGeomTokens->vertex)
                 .Set(uvSets[i]))
        {
            error = "could not author USD texture coordinates";
            return false;
        }
    }
    return true;
}

} // namespace

bool
ConvertMesh(const fastgltf::Asset& asset, std::size_t meshIndex, const pxr::UsdStagePtr& stage,
            const pxr::SdfPath& path, const gltfCore::BufferLoader& loadBuffer,
            MaterialAuthor& materials)
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
    if (!source.weights.empty())
    {
        TF_RUNTIME_ERROR("glTF mesh %zu: morph weights are not supported yet", meshIndex);
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
        std::string error;
        if (!authorPrimitive(asset, source.primitives[i], group.GetPrim(), i, error, loadBuffer,
                             materials))
        {
            TF_RUNTIME_ERROR("glTF mesh %zu primitive %zu: %s", meshIndex, i, error.c_str());
            return false;
        }
    }
    return true;
}

} // namespace gltfUsd
