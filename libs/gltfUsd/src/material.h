// SPDX-License-Identifier: Apache-2.0
#pragma once

#include "gltfCore/accessor.h"

#include <fastgltf/types.hpp>
#include "pxr/base/vt/types.h"
#include "pxr/usd/usd/stage.h"
#include "pxr/usd/usdGeom/mesh.h"
#include "pxr/usd/usdShade/material.h"

#include <cstddef>
#include <optional>
#include <string>
#include <vector>

namespace gltfUsd
{

// The material-dependent opinions of one primitive. Materials are shared, but the Preview
// primvars combine their factors with this primitive's vertex colors.
struct MaterialBinding
{
    pxr::UsdShadeMaterial material;
    bool doubleSided = false;
    pxr::VtVec3fArray previewColors;
    pxr::VtFloatArray previewAlpha;
    pxr::VtVec4fArray generatedTangents;

    [[nodiscard]] bool Author(const pxr::UsdGeomMesh& mesh, std::string& error) const;
};

class MaterialAuthor
{
  public:
    MaterialAuthor(const fastgltf::Asset& asset, const std::string& sourcePath,
                   const gltfCore::BufferReader& buffers, const pxr::UsdStageRefPtr& stage,
                   const pxr::SdfPath& scope);

    [[nodiscard]] std::optional<std::size_t> TangentTexCoord(std::size_t index) const;

    // `uvSetCount` is the primitive's number of TEXCOORD_n sets; `colors` is null
    // for a primitive without COLOR_0; `alpha` is empty without vertex alpha.
    [[nodiscard]] bool Prepare(std::optional<std::size_t> index, std::size_t uvSetCount,
                               const pxr::VtVec3fArray* colors, const pxr::VtFloatArray& alpha,
                               MaterialBinding& binding, std::string& error);

  private:
    [[nodiscard]] bool PrepareDefault(bool hasVertexColor, MaterialBinding& binding,
                                      std::string& error);

    const fastgltf::Asset& _asset;
    const std::string& _sourcePath;
    const gltfCore::BufferReader& _buffers;
    pxr::UsdStageRefPtr _stage;
    pxr::SdfPath _scope;
    std::vector<pxr::UsdShadeMaterial> _materials;
    std::vector<pxr::UsdShadeMaterial> _vertexColorMaterials;
    pxr::UsdShadeMaterial _defaultMaterial;
    pxr::UsdShadeMaterial _defaultVertexColorMaterial;
};

} // namespace gltfUsd
