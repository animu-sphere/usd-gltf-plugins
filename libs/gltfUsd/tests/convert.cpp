// SPDX-License-Identifier: Apache-2.0
// Converts a glTF scene with gltfUsd alone, without the file-format plugin, and reads the layer back
// through a stage. The plugin's own behavior is covered by tests/test_file_format.py.
#include "gltfCore/parse.h"
#include "gltfUsd/scene.h"

#include "pxr/base/vt/array.h"
#include "pxr/usd/usd/prim.h"
#include "pxr/usd/usd/primRange.h"
#include "pxr/usd/usd/stage.h"
#include "pxr/usd/usdGeom/mesh.h"

#include <cstddef>
#include <iostream>
#include <span>
#include <string>
#include <string_view>

namespace
{

// One triangle in one node of one scene, with its positions in a data URI.
constexpr std::string_view TRIANGLE = R"({
  "asset": {"version": "2.0"},
  "scene": 0,
  "scenes": [{"nodes": [0]}],
  "nodes": [{"mesh": 0}],
  "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
  "accessors": [{"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3",
                 "min": [0, 0, 0], "max": [1, 1, 0]}],
  "bufferViews": [{"buffer": 0, "byteLength": 36}],
  "buffers": [{"byteLength": 36,
               "uri": "data:application/octet-stream;base64,AAAAAAAAAAAAAAAAAACAPwAAAAAAAAAAAAAAAAAAgD8AAAAA"}]
})";

bool
fail(std::string_view message)
{
    std::cerr << message << '\n';
    return false;
}

bool
convertsATriangle()
{
    std::string error;
    auto asset = gltfCore::Parse(reinterpret_cast<const std::byte*>(TRIANGLE.data()),
                                 TRIANGLE.size(), error);
    if (asset.error() != fastgltf::Error::None)
    {
        return fail("parse: " + error);
    }
    const gltfCore::BufferLoader noExternalBuffers =
        [](const fastgltf::Buffer&, std::span<const std::byte>&, std::string& message)
    {
        message = "the test asset has no external buffers";
        return false;
    };
    const auto layer = gltfUsd::ConvertScene(asset.get(), "triangle.gltf", noExternalBuffers);
    if (!layer)
    {
        return fail("ConvertScene returned no layer");
    }

    const auto stage = pxr::UsdStage::Open(layer);
    if (stage->GetDefaultPrim().GetPath() != pxr::SdfPath("/Root"))
    {
        return fail("the default prim is not /Root");
    }
    if (!stage->GetPrimAtPath(pxr::SdfPath("/Root/Scenes/Scene_0")))
    {
        return fail("there is no /Root/Scenes/Scene_0");
    }
    std::size_t meshes = 0;
    for (const auto& prim : stage->Traverse())
    {
        if (!prim.IsA<pxr::UsdGeomMesh>())
        {
            continue;
        }
        ++meshes;
        pxr::VtVec3fArray points;
        pxr::UsdGeomMesh(prim).GetPointsAttr().Get(&points);
        if (points.size() != 3)
        {
            return fail("the mesh at " + prim.GetPath().GetString() + " does not have 3 points");
        }
    }
    if (meshes != 1)
    {
        return fail("expected 1 mesh, found " + std::to_string(meshes));
    }
    return true;
}

} // namespace

int
main()
{
    return convertsATriangle() ? 0 : 1;
}
