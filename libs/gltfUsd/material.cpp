// SPDX-License-Identifier: Apache-2.0
#include "material.h"

#include "pxr/base/gf/vec3f.h"
#include "pxr/usd/sdf/types.h"
#include "pxr/usd/usdGeom/gprim.h"
#include "pxr/usd/usdShade/materialBindingAPI.h"
#include "pxr/usd/usdShade/nodeGraph.h"
#include "pxr/usd/usdShade/shader.h"

#include <cmath>
#include <string>

namespace gltfUsd
{
namespace
{

using namespace pxr;

bool
inUnitRange(float value)
{
    return std::isfinite(value) && value >= 0.0f && value <= 1.0f;
}

template <typename T>
bool
setInput(UsdShadeShader& shader, const char* name, const SdfValueTypeName& type, const T& value)
{
    const auto input = shader.CreateInput(TfToken(name), type);
    return input && input.Set(value);
}

bool
connectSurface(const UsdShadeMaterial& material, const TfToken& context,
               const UsdShadeNodeGraph& graph, UsdShadeShader& shader, const TfToken& outputName)
{
    const auto shaderOutput = shader.CreateOutput(outputName, SdfValueTypeNames->Token);
    const auto graphOutput = graph.CreateOutput(TfToken("surface"), SdfValueTypeNames->Token);
    const auto materialOutput = material.CreateSurfaceOutput(context);
    return shaderOutput && graphOutput && materialOutput &&
           graphOutput.ConnectToSource(shaderOutput) && materialOutput.ConnectToSource(graphOutput);
}

struct PbrFactors
{
    GfVec3f baseColor;
    GfVec3f emissive;
    float metallic;
    float roughness;
};

enum class ColorReader
{
    Preview,
    MaterialX
};

bool
connectDisplayColor(const UsdShadeNodeGraph& graph, UsdShadeShader& surface, ColorReader kind)
{
    const bool mtlx = kind == ColorReader::MaterialX;
    auto reader = UsdShadeShader::Define(
        graph.GetPrim().GetStage(), graph.GetPath().AppendChild(TfToken("displayColorReader")));
    if (!reader ||
        !reader.CreateIdAttr().Set(
            TfToken(mtlx ? "ND_geompropvalue_color3" : "UsdPrimvarReader_float3")) ||
        !setInput(reader, mtlx ? "geomprop" : "varname", SdfValueTypeNames->String,
                  std::string("displayColor")) ||
        !setInput(reader, mtlx ? "default" : "fallback",
                  mtlx ? SdfValueTypeNames->Color3f : SdfValueTypeNames->Float3, GfVec3f(1)))
    {
        return false;
    }
    const auto output =
        reader.CreateOutput(TfToken(mtlx ? "out" : "result"),
                            mtlx ? SdfValueTypeNames->Color3f : SdfValueTypeNames->Float3);
    const auto input = surface.GetInput(TfToken(mtlx ? "base_color" : "diffuseColor"));
    return output && input && input.ConnectToSource(output);
}

bool
authorPreview(const UsdShadeMaterial& material, const PbrFactors& factors, bool vertexColor)
{
    const auto stage = material.GetPrim().GetStage();
    const auto graph =
        UsdShadeNodeGraph::Define(stage, material.GetPath().AppendChild(TfToken("preview")));
    if (!graph)
    {
        return false;
    }
    auto surface = UsdShadeShader::Define(stage, graph.GetPath().AppendChild(TfToken("surface")));
    return surface && surface.CreateIdAttr().Set(TfToken("UsdPreviewSurface")) &&
           setInput(surface, "diffuseColor", SdfValueTypeNames->Color3f, factors.baseColor) &&
           setInput(surface, "metallic", SdfValueTypeNames->Float, factors.metallic) &&
           setInput(surface, "roughness", SdfValueTypeNames->Float, factors.roughness) &&
           setInput(surface, "emissiveColor", SdfValueTypeNames->Color3f, factors.emissive) &&
           connectSurface(material, TfToken(), graph, surface, TfToken("surface")) &&
           (!vertexColor || connectDisplayColor(graph, surface, ColorReader::Preview));
}

bool
authorMtlx(const UsdShadeMaterial& material, const PbrFactors& factors, bool vertexColor)
{
    const auto stage = material.GetPrim().GetStage();
    const auto graph =
        UsdShadeNodeGraph::Define(stage, material.GetPath().AppendChild(TfToken("mtlx")));
    if (!graph)
    {
        return false;
    }
    auto surface = UsdShadeShader::Define(stage, graph.GetPath().AppendChild(TfToken("surface")));
    if (!surface)
    {
        return false;
    }
    const auto alphaMode = surface.CreateInput(TfToken("alpha_mode"), SdfValueTypeNames->Int);
    return surface.CreateIdAttr().Set(TfToken("ND_gltf_pbr_surfaceshader")) &&
           setInput(surface, "base_color", SdfValueTypeNames->Color3f, factors.baseColor) &&
           setInput(surface, "metallic", SdfValueTypeNames->Float, factors.metallic) &&
           setInput(surface, "roughness", SdfValueTypeNames->Float, factors.roughness) &&
           setInput(surface, "emissive", SdfValueTypeNames->Color3f, factors.emissive) &&
           alphaMode && alphaMode.GetAttr().SetVariability(SdfVariabilityUniform) &&
           alphaMode.Set(0) &&
           connectSurface(material, TfToken("mtlx"), graph, surface, TfToken("out")) &&
           (!vertexColor || connectDisplayColor(graph, surface, ColorReader::MaterialX));
}

UsdShadeMaterial
authorMaterial(const fastgltf::Material& source, const std::string& name,
               const UsdStageRefPtr& stage, const SdfPath& scope, bool vertexColor = false)
{
    const auto material = UsdShadeMaterial::Define(stage, scope.AppendChild(TfToken(name)));
    if (!material || (!source.name.empty() && !material.GetPrim().SetDisplayName(source.name)))
    {
        return UsdShadeMaterial();
    }
    const auto& pbr = source.pbrData;
    const PbrFactors factors{
        GfVec3f(pbr.baseColorFactor[0], pbr.baseColorFactor[1], pbr.baseColorFactor[2]),
        GfVec3f(source.emissiveFactor[0], source.emissiveFactor[1], source.emissiveFactor[2]),
        float(pbr.metallicFactor), float(pbr.roughnessFactor)};
    if (!authorPreview(material, factors, vertexColor) ||
        !authorMtlx(material, factors, vertexColor))
    {
        return UsdShadeMaterial();
    }
    return material;
}

} // namespace

MaterialAuthor::MaterialAuthor(const fastgltf::Asset& asset, const pxr::UsdStageRefPtr& stage,
                               const pxr::SdfPath& scope)
    : _asset(asset), _stage(stage), _scope(scope), _materials(asset.materials.size())
{
}

bool
MaterialAuthor::Bind(std::size_t index, bool hasVertexColor, const pxr::SdfPrimSpecHandle& mesh,
                     std::string& error)
{
    if (index >= _asset.materials.size())
    {
        error = "material index is out of range";
        return false;
    }
    const auto& source = _asset.materials[index];
    const auto& pbr = source.pbrData;
    if (hasVertexColor || source.alphaMode != fastgltf::AlphaMode::Opaque || pbr.baseColorTexture ||
        pbr.metallicRoughnessTexture || source.normalTexture || source.occlusionTexture ||
        source.emissiveTexture)
    {
        error =
            "material textures, non-opaque alpha, or material with vertex colors are not supported";
        return false;
    }
    for (std::size_t i = 0; i < 4; ++i)
    {
        if (!inUnitRange(pbr.baseColorFactor[i]))
        {
            error = "material base color factor is non-finite or out of range";
            return false;
        }
    }
    for (std::size_t i = 0; i < 3; ++i)
    {
        if (!inUnitRange(source.emissiveFactor[i]))
        {
            error = "material emissive factor is non-finite or out of range";
            return false;
        }
    }
    if (!inUnitRange(pbr.metallicFactor) || !inUnitRange(pbr.roughnessFactor))
    {
        error = "material metallic or roughness factor is non-finite or out of range";
        return false;
    }
    if (!_materials[index])
    {
        _materials[index] =
            authorMaterial(source, "Material_" + std::to_string(index), _stage, _scope);
        if (!_materials[index])
        {
            error = "could not author USD material";
            return false;
        }
    }
    return BindMaterial(_materials[index], source.doubleSided, mesh, error);
}

bool
MaterialAuthor::BindDefault(bool hasVertexColor, const pxr::SdfPrimSpecHandle& mesh,
                            std::string& error)
{
    auto& material = hasVertexColor ? _defaultVertexColorMaterial : _defaultMaterial;
    if (!material)
    {
        material =
            authorMaterial(fastgltf::Material{}, hasVertexColor ? "DefaultVertexColor" : "Default",
                           _stage, _scope, hasVertexColor);
        if (!material)
        {
            error = "could not author default USD material";
            return false;
        }
    }
    return BindMaterial(material, false, mesh, error);
}

bool
MaterialAuthor::BindMaterial(const pxr::UsdShadeMaterial& material, bool doubleSided,
                             const pxr::SdfPrimSpecHandle& mesh, std::string& error)
{
    using namespace pxr;
    const auto prim = _stage->GetPrimAtPath(mesh->GetPath());
    if (!prim || !UsdShadeMaterialBindingAPI::Apply(prim).Bind(material) ||
        !UsdGeomGprim(prim).CreateDoubleSidedAttr().Set(doubleSided))
    {
        error = "could not author USD material binding or sidedness";
        return false;
    }
    return true;
}

} // namespace gltfUsd
