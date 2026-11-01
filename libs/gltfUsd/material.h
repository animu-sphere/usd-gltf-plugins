// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <fastgltf/types.hpp>
#include "pxr/usd/sdf/primSpec.h"
#include "pxr/usd/usd/stage.h"
#include "pxr/usd/usdShade/material.h"

#include <cstddef>
#include <string>
#include <vector>

namespace gltfUsd
{

class MaterialAuthor
{
  public:
    MaterialAuthor(const fastgltf::Asset& asset, const pxr::UsdStageRefPtr& stage,
                   const pxr::SdfPath& scope);

    [[nodiscard]] bool Bind(std::size_t index, bool hasVertexColor,
                            const pxr::SdfPrimSpecHandle& mesh, std::string& error);
    [[nodiscard]] bool BindDefault(bool hasVertexColor, const pxr::SdfPrimSpecHandle& mesh,
                                   std::string& error);

  private:
    [[nodiscard]] bool BindMaterial(const pxr::UsdShadeMaterial& material, bool doubleSided,
                                    const pxr::SdfPrimSpecHandle& mesh, std::string& error);

    const fastgltf::Asset& _asset;
    pxr::UsdStageRefPtr _stage;
    pxr::SdfPath _scope;
    std::vector<pxr::UsdShadeMaterial> _materials;
    pxr::UsdShadeMaterial _defaultMaterial;
    pxr::UsdShadeMaterial _defaultVertexColorMaterial;
};

} // namespace gltfUsd
