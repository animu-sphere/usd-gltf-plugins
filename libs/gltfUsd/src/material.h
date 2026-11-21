// SPDX-License-Identifier: Apache-2.0
#pragma once

#include "gltfCore/accessor.h"

#include <fastgltf/types.hpp>
#include "pxr/base/vt/types.h"
#include "pxr/usd/usd/stage.h"
#include "pxr/usd/usdGeom/gprim.h"
#include "pxr/usd/usdShade/material.h"

#include <cstddef>
#include <map>
#include <optional>
#include <string>
#include <tuple>
#include <vector>

namespace gltfUsd
{

// How a primitive's mode and NORMAL decide what its material draws.
enum class MaterialUse
{
    // Triangles, and points and lines bound to a KHR_materials_unlit material.
    Full,
    // Points and lines with NORMAL: lit, without normal textures.
    WithoutNormalTextures,
    // Points and lines without NORMAL: the base color plus the emissive factor, without lighting.
    Unlit,
};

// The material-dependent opinions of one primitive. Materials are shared, but the Preview
// primvars combine their factors with this primitive's vertex colors.
struct MaterialBinding
{
    pxr::UsdShadeMaterial material;
    bool doubleSided = false;
    pxr::VtVec3fArray previewColors;
    pxr::VtFloatArray previewAlpha;
    pxr::VtVec4fArray generatedTangents;

    // Sidedness is authored on meshes only, because points and curves have no faces.
    [[nodiscard]] bool Author(const pxr::UsdGeomGprim& gprim, std::string& error) const;
};

class MaterialAuthor
{
  public:
    MaterialAuthor(const fastgltf::Asset& asset, const std::string& sourcePath,
                   const gltfCore::BufferReader& buffers, const pxr::UsdStageRefPtr& stage,
                   const pxr::SdfPath& scope);

    [[nodiscard]] std::optional<std::size_t> TangentTexCoord(std::size_t index) const;

    // Whether WithoutNormalTextures leaves out a texture the material would otherwise draw.
    [[nodiscard]] bool HasNormalTextures(std::size_t index) const;

    // `uvSetCount` is the primitive's number of TEXCOORD_n sets; `colors` is null
    // for a primitive without COLOR_0; `alpha` is empty without vertex alpha.
    [[nodiscard]] bool Prepare(std::optional<std::size_t> index, std::size_t uvSetCount,
                               const pxr::VtVec3fArray* colors, const pxr::VtFloatArray& alpha,
                               MaterialBinding& binding, std::string& error,
                               MaterialUse use = MaterialUse::Full);

  private:
    [[nodiscard]] bool PrepareDefault(bool hasVertexColor, MaterialUse use,
                                      MaterialBinding& binding, std::string& error);

    const fastgltf::Asset& _asset;
    const std::string& _sourcePath;
    const gltfCore::BufferReader& _buffers;
    pxr::UsdStageRefPtr _stage;
    pxr::SdfPath _scope;
    // Keyed by material index, or none for the default material, whether the primitive has
    // vertex colors, and the use.
    std::map<std::tuple<std::optional<std::size_t>, bool, MaterialUse>, pxr::UsdShadeMaterial>
        _materials;
};

} // namespace gltfUsd
