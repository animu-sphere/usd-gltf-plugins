#include "mesh.h"

#include "accessor.h"

#include "pxr/base/gf/vec3f.h"
#include "pxr/base/tf/diagnostic.h"
#include "pxr/base/vt/array.h"
#include "pxr/base/vt/value.h"
#include "pxr/usd/sdf/attributeSpec.h"
#include "pxr/usd/sdf/schema.h"
#include "pxr/usd/sdf/types.h"

#include <bit>
#include <cmath>
#include <cstdint>
#include <limits>
#include <string>

namespace gltfUsd {
namespace {

using namespace pxr;

static_assert(sizeof(float) == 4 && std::numeric_limits<float>::is_iec559);

std::uint32_t readUnsigned(const std::byte* data, std::size_t size)
{
    std::uint32_t value = 0;
    for (std::size_t i = 0; i < size; ++i) {
        value |= std::uint32_t(std::to_integer<unsigned char>(data[i])) << (8 * i);
    }
    return value;
}

bool readVectors(const fastgltf::Asset& asset, std::size_t index, VtVec3fArray& values,
                 std::string& error, const gltfCore::BufferLoader& loadBuffer)
{
    gltfCore::AccessorData data;
    if (!gltfCore::GetAccessorData(asset, index, fastgltf::AccessorType::Vec3,
                                   fastgltf::ComponentType::Float, 12, true, data, error, loadBuffer)) {
        return false;
    }
    if (data.count > std::size_t(std::numeric_limits<int>::max())) {
        error = "vertex count exceeds USD's index range";
        return false;
    }
    values.resize(data.count);
    for (std::size_t i = 0; i < data.count; ++i) {
        for (std::size_t component = 0; component < 3; ++component) {
            const auto bits = readUnsigned(data.bytes.data() + i * data.stride + component * 4, 4);
            const float value = std::bit_cast<float>(bits);
            if (!std::isfinite(value)) {
                error = "vertex attribute contains a non-finite value";
                return false;
            }
            values[i][component] = value;
        }
    }
    return true;
}

bool readIndices(const fastgltf::Asset& asset, std::size_t index, std::size_t pointCount,
                 VtIntArray& values, std::string& error, const gltfCore::BufferLoader& loadBuffer)
{
    if (index >= asset.accessors.size()) {
        error = "index accessor is out of range";
        return false;
    }
    const auto componentType = asset.accessors[index].componentType;
    std::size_t size;
    switch (componentType) {
    case fastgltf::ComponentType::UnsignedByte: size = 1; break;
    case fastgltf::ComponentType::UnsignedShort: size = 2; break;
    case fastgltf::ComponentType::UnsignedInt: size = 4; break;
    default:
        error = "index accessor has an unsupported component type";
        return false;
    }
    gltfCore::AccessorData data;
    if (!gltfCore::GetAccessorData(asset, index, fastgltf::AccessorType::Scalar,
                                   componentType, size, false, data, error, loadBuffer)) {
        return false;
    }
    if (data.count > std::size_t(std::numeric_limits<int>::max()) || data.count % 3 != 0) {
        error = "triangle index count is invalid";
        return false;
    }
    values.resize(data.count);
    for (std::size_t i = 0; i < data.count; ++i) {
        const auto value = readUnsigned(data.bytes.data() + i * data.stride, size);
        const auto reserved = size == 4 ? std::numeric_limits<std::uint32_t>::max()
                                        : (std::uint32_t(1) << (size * 8)) - 1;
        if (value >= pointCount || value == reserved) {
            error = "triangle index is out of range";
            return false;
        }
        values[i] = static_cast<int>(value);
    }
    return true;
}

bool setAttribute(const SdfPrimSpecHandle& prim, const char* name,
                  const SdfValueTypeName& type, const VtValue& value)
{
    const auto attribute = SdfAttributeSpec::New(prim, name, type);
    return attribute && attribute->SetDefaultValue(value);
}

bool authorPrimitive(const fastgltf::Asset& asset, const fastgltf::Primitive& primitive,
                     const SdfPrimSpecHandle& parent, std::size_t index, std::string& error,
                     const gltfCore::BufferLoader& loadBuffer)
{
    if (primitive.type != fastgltf::PrimitiveType::Triangles || primitive.materialIndex ||
        !primitive.targets.empty() || primitive.dracoCompression || !primitive.mappings.empty()) {
        error = "primitive mode, material, morph targets, or compression is not supported";
        return false;
    }
    const fastgltf::Attribute* position = nullptr;
    const fastgltf::Attribute* normal = nullptr;
    for (const auto& attribute : primitive.attributes) {
        if (attribute.name == "POSITION" && !position) {
            position = &attribute;
        } else if (attribute.name == "NORMAL" && !normal) {
            normal = &attribute;
        } else {
            error = "unsupported or repeated vertex attribute: " + attribute.name;
            return false;
        }
    }
    if (!position) {
        error = "primitive has no POSITION attribute";
        return false;
    }
    VtVec3fArray points;
    if (!readVectors(asset, position->accessorIndex, points, error, loadBuffer)) {
        return false;
    }
    VtVec3fArray normals;
    if (normal && (!readVectors(asset, normal->accessorIndex, normals, error, loadBuffer) ||
                   normals.size() != points.size())) {
        if (error.empty()) {
            error = "normal and position counts differ";
        }
        return false;
    }
    VtIntArray indices;
    if (primitive.indicesAccessor) {
        if (!readIndices(asset, *primitive.indicesAccessor, points.size(), indices, error, loadBuffer)) {
            return false;
        }
    } else {
        if (points.size() % 3 != 0) {
            error = "non-indexed triangle vertex count is invalid";
            return false;
        }
        indices.resize(points.size());
        for (std::size_t i = 0; i < points.size(); ++i) {
            indices[i] = static_cast<int>(i);
        }
    }
    if (!normal) {
        normals.resize(indices.size() / 3);
        for (std::size_t i = 0; i < normals.size(); ++i) {
            const auto& a = points[indices[i * 3]];
            const auto& b = points[indices[i * 3 + 1]];
            const auto& c = points[indices[i * 3 + 2]];
            const double ux = double(b[0]) - a[0], uy = double(b[1]) - a[1], uz = double(b[2]) - a[2];
            const double vx = double(c[0]) - a[0], vy = double(c[1]) - a[1], vz = double(c[2]) - a[2];
            const double x = uy * vz - uz * vy, y = uz * vx - ux * vz, z = ux * vy - uy * vx;
            const double length = std::hypot(x, y, z);
            normals[i] = length == 0 ? GfVec3f(0) :
                GfVec3f(float(x / length), float(y / length), float(z / length));
        }
    }
    const auto mesh = SdfPrimSpec::New(parent, "Primitive_" + std::to_string(index),
                                       SdfSpecifierDef, "Mesh");
    const VtIntArray counts(indices.size() / 3, 3);
    if (!mesh || !setAttribute(mesh, "points", SdfValueTypeNames->Point3fArray, VtValue(points)) ||
        !setAttribute(mesh, "faceVertexCounts", SdfValueTypeNames->IntArray, VtValue(counts)) ||
        !setAttribute(mesh, "faceVertexIndices", SdfValueTypeNames->IntArray, VtValue(indices)) ||
        !setAttribute(mesh, "subdivisionScheme", SdfValueTypeNames->Token, VtValue(TfToken("none")))) {
        error = "could not author USD mesh";
        return false;
    }
    const auto attr = SdfAttributeSpec::New(mesh, "normals", SdfValueTypeNames->Normal3fArray);
    if (!attr || !attr->SetDefaultValue(VtValue(normals))) {
        error = "could not author USD normals";
        return false;
    }
    attr->SetInfo(TfToken("interpolation"), VtValue(TfToken(normal ? "vertex" : "uniform")));
    return true;
}

} // namespace

bool AuthorMesh(const fastgltf::Asset& asset, std::size_t meshIndex,
                const pxr::SdfPrimSpecHandle& parent, const gltfCore::BufferLoader& loadBuffer)
{
    if (meshIndex >= asset.meshes.size()) {
        TF_RUNTIME_ERROR("glTF mesh index %zu is out of range", meshIndex);
        return false;
    }
    const auto& source = asset.meshes[meshIndex];
    if (source.primitives.empty() || !source.weights.empty()) {
        TF_RUNTIME_ERROR("glTF mesh %zu has no primitives or uses unsupported morph weights", meshIndex);
        return false;
    }
    const auto group = SdfPrimSpec::New(parent, "Mesh_" + std::to_string(meshIndex),
                                        SdfSpecifierDef, "Xform");
    if (!group) {
        TF_RUNTIME_ERROR("Could not author glTF mesh %zu", meshIndex);
        return false;
    }
    if (!source.name.empty()) {
        group->SetInfo(SdfFieldKeys->DisplayName, VtValue(source.name));
    }
    for (std::size_t i = 0; i < source.primitives.size(); ++i) {
        std::string error;
        if (!authorPrimitive(asset, source.primitives[i], group, i, error, loadBuffer)) {
            TF_RUNTIME_ERROR("glTF mesh %zu primitive %zu: %s", meshIndex, i, error.c_str());
            return false;
        }
    }
    return true;
}

} // namespace gltfUsd
