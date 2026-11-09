// SPDX-License-Identifier: Apache-2.0
#pragma once

#include "gltfCore/accessor.h"

#include <fastgltf/types.hpp>
#include "pxr/base/vt/types.h"
#include "pxr/usd/usd/stage.h"
#include "pxr/usd/usdGeom/mesh.h"
#include "pxr/usd/usdShade/material.h"

#include <cstddef>
#include <string>
#include <vector>

namespace gltfUsd
{

class MaterialAuthor
{
  public:
    MaterialAuthor(const fastgltf::Asset& asset, const std::string& sourcePath,
                   const gltfCore::BufferLoader& loadBuffer, const pxr::UsdStageRefPtr& stage,
                   const pxr::SdfPath& scope);

    // `uvSetCount` is the primitive's number of TEXCOORD_n sets; `colors` is null
    // for a primitive without COLOR_0; `alpha` is empty without vertex alpha.
    [[nodiscard]] bool Bind(std::size_t index, std::size_t uvSetCount,
                            const pxr::VtVec3fArray* colors, const pxr::VtFloatArray& alpha,
                            const pxr::UsdGeomMesh& mesh, std::string& error);
    [[nodiscard]] bool BindDefault(bool hasVertexColor, const pxr::UsdGeomMesh& mesh,
                                   std::string& error);

  private:
    [[nodiscard]] bool BindMaterial(const pxr::UsdShadeMaterial& material, bool doubleSided,
                                    const pxr::UsdGeomMesh& mesh, std::string& error);

    const fastgltf::Asset& _asset;
    const std::string& _sourcePath;
    const gltfCore::BufferLoader& _loadBuffer;
    pxr::UsdStageRefPtr _stage;
    pxr::SdfPath _scope;
    std::vector<pxr::UsdShadeMaterial> _materials;
    std::vector<pxr::UsdShadeMaterial> _vertexColorMaterials;
    pxr::UsdShadeMaterial _defaultMaterial;
    pxr::UsdShadeMaterial _defaultVertexColorMaterial;
};

} // namespace gltfUsd
