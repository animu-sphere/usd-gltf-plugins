// SPDX-License-Identifier: Apache-2.0
#include "tangents.h"

#include <mikktspace.h>

#include "pxr/base/gf/vec2f.h"
#include "pxr/base/gf/vec3f.h"
#include "pxr/base/gf/vec4f.h"
#include "pxr/base/tf/hash.h"
#include "pxr/base/vt/array.h"

#include <algorithm>
#include <cmath>
#include <limits>
#include <unordered_map>

namespace gltfUsd
{
namespace
{

using namespace pxr;

struct Mesh
{
    const VtVec3fArray& points;
    const VtVec3fArray& normals;
    const VtVec2fArray& texcoords;
    const VtIntArray& indices;
    VtVec4fArray& tangents;
    bool flatNormals;

    static const Mesh&
    Get(const SMikkTSpaceContext* context)
    {
        return *static_cast<const Mesh*>(context->m_pUserData);
    }

    int
    Vertex(int face, int corner) const
    {
        return indices[face * 3 + corner];
    }
};

// Keep MikkTSpace's float edge products and squared lengths in range. Extremes are
// still importable, with the renderer-derived frame used for shading as usual.
constexpr float MaxFrameComponent = 1e8f;
// The upstream grouping pass is recursive and its subgroup pass can be quadratic
// in the number of corners welded at one position.
constexpr int MaxCornersAtPosition = 1024;

template <class Vector>
bool
inFrameRange(const Vector& value)
{
    return std::all_of(value.data(), value.data() + Vector::dimension,
                       [](float component) { return std::abs(component) <= MaxFrameComponent; });
}

bool
hasTangentTriangle(const VtVec3fArray& points, const VtVec2fArray& texcoords,
                   const VtIntArray& indices)
{
    for (std::size_t i = 0; i < indices.size(); i += 3)
    {
        const int a = indices[i], b = indices[i + 1], c = indices[i + 2];
        const auto u = texcoords[b] - texcoords[a];
        const auto v = texcoords[c] - texcoords[a];
        if (std::abs(u[0] * v[1] - u[1] * v[0]) > std::numeric_limits<float>::min() &&
            GfCross(points[b] - points[a], points[c] - points[a]) != GfVec3f(0))
        {
            return true;
        }
    }
    return false;
}

} // namespace

pxr::VtVec4fArray
GenerateTangents(const VtVec3fArray& points, const VtVec3fArray& normals,
                 const VtVec2fArray& texcoords, const VtIntArray& indices, bool flatNormals)
{
    // MikkTSpace packs a triangle index and a two-bit corner in a signed int.
    if (indices.empty() || indices.size() / 3 > std::numeric_limits<int>::max() / 4 ||
        !std::all_of(points.begin(), points.end(), inFrameRange<GfVec3f>) ||
        !std::all_of(texcoords.begin(), texcoords.end(), inFrameRange<GfVec2f>) ||
        !hasTangentTriangle(points, texcoords, indices))
    {
        return {};
    }
    std::unordered_map<GfVec3f, int, TfHash> cornerCounts;
    for (int index : indices)
    {
        if (++cornerCounts[points[index]] > MaxCornersAtPosition)
        {
            return {};
        }
    }
    VtVec3fArray unitNormals(normals.size());
    for (std::size_t i = 0; i < normals.size(); ++i)
    {
        const auto& n = normals[i];
        const double length = std::hypot(double(n[0]), double(n[1]), double(n[2]));
        if (length == 0.0)
        {
            return {};
        }
        unitNormals[i] = GfVec3f(float(n[0] / length), float(n[1] / length), float(n[2] / length));
    }
    VtVec4fArray tangents(indices.size());
    Mesh mesh{points, unitNormals, texcoords, indices, tangents, flatNormals};
    SMikkTSpaceInterface callbacks{};
    callbacks.m_getNumFaces = [](const SMikkTSpaceContext* context)
    { return static_cast<int>(Mesh::Get(context).indices.size() / 3); };
    callbacks.m_getNumVerticesOfFace = [](const SMikkTSpaceContext*, int) { return 3; };
    callbacks.m_getPosition =
        [](const SMikkTSpaceContext* context, float* out, int face, int corner)
    {
        const auto& mesh = Mesh::Get(context);
        std::copy_n(mesh.points[mesh.Vertex(face, corner)].data(), 3, out);
    };
    callbacks.m_getNormal = [](const SMikkTSpaceContext* context, float* out, int face, int corner)
    {
        const auto& mesh = Mesh::Get(context);
        std::copy_n(mesh.normals[mesh.flatNormals ? face : mesh.Vertex(face, corner)].data(), 3,
                    out);
    };
    callbacks.m_getTexCoord =
        [](const SMikkTSpaceContext* context, float* out, int face, int corner)
    {
        const auto& mesh = Mesh::Get(context);
        std::copy_n(mesh.texcoords[mesh.Vertex(face, corner)].data(), 2, out);
    };
    callbacks.m_setTSpaceBasic = [](const SMikkTSpaceContext* context, const float* tangent,
                                    float sign, int face, int corner)
    {
        // glTF's normal-map +Y points opposite the direction of increasing source V.
        Mesh::Get(context).tangents[face * 3 + corner] =
            GfVec4f(tangent[0], tangent[1], tangent[2], -sign);
    };
    const SMikkTSpaceContext context{&callbacks, &mesh};
    if (!genTangSpaceDefault(&context) ||
        !std::all_of(tangents.cbegin(), tangents.cend(),
                     [](const GfVec4f& t)
                     {
                         const float length = GfVec3f(t[0], t[1], t[2]).GetLength();
                         return std::isfinite(length) && std::abs(length - 1.0f) < 1e-3f &&
                                std::abs(t[3]) == 1.0f;
                     }))
    {
        return {};
    }
    return tangents;
}

} // namespace gltfUsd
