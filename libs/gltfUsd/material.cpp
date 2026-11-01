// SPDX-License-Identifier: Apache-2.0
#include "material.h"

#include "pxr/base/gf/vec3f.h"
#include "pxr/usd/sdf/types.h"
#include "pxr/usd/usdGeom/primvarsAPI.h"
#include "pxr/usd/usdGeom/tokens.h"
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
    float alpha;
    float alphaCutoff;
    float metallic;
    float roughness;
    fastgltf::AlphaMode alphaMode;
};

PbrFactors
pbrFactors(const fastgltf::Material& source)
{
    const auto& pbr = source.pbrData;
    return {GfVec3f(pbr.baseColorFactor[0], pbr.baseColorFactor[1], pbr.baseColorFactor[2]),
            GfVec3f(source.emissiveFactor[0], source.emissiveFactor[1], source.emissiveFactor[2]),
            float(pbr.baseColorFactor[3]),
            float(source.alphaCutoff),
            float(pbr.metallicFactor),
            float(pbr.roughnessFactor),
            source.alphaMode};
}

// A UsdPreviewSurface threshold of 0 means no masking, whereas every fragment of
// a glTF MASK material with cutoff 0 passes and is fully opaque.
bool
previewUsesOpacity(const PbrFactors& factors)
{
    return factors.alphaMode == fastgltf::AlphaMode::Blend ||
           (factors.alphaMode == fastgltf::AlphaMode::Mask && factors.alphaCutoff > 0.0f);
}

// UsdPreviewSurface has no multiply node, so a factor other than 1 is applied to
// a primvar on each mesh. The product interpolates exactly like COLOR_0. Names
// these graphs read avoid colons, which break MaterialX shaders in Storm.
const char*
previewColorPrimvar(const PbrFactors& factors)
{
    return factors.baseColor == GfVec3f(1) ? "displayColor" : "gltf_previewBaseColor";
}

const char*
previewAlphaPrimvar(const PbrFactors& factors)
{
    return factors.alpha == 1.0f ? "gltf_colorAlpha" : "gltf_previewAlpha";
}

template <typename T>
UsdShadeOutput
authorPrimvarReader(const UsdShadeNodeGraph& graph, const char* name, const char* id,
                    const char* primvarInput, const char* primvar, const char* fallbackInput,
                    const SdfValueTypeName& type, const T& fallback, const char* output)
{
    auto reader = UsdShadeShader::Define(graph.GetPrim().GetStage(),
                                         graph.GetPath().AppendChild(TfToken(name)));
    if (!reader || !reader.CreateIdAttr().Set(TfToken(id)) ||
        !setInput(reader, primvarInput, SdfValueTypeNames->String, std::string(primvar)) ||
        !setInput(reader, fallbackInput, type, fallback))
    {
        return UsdShadeOutput();
    }
    return reader.CreateOutput(TfToken(output), type);
}

bool
connectPreviewVertexColor(const UsdShadeNodeGraph& graph, UsdShadeShader& surface,
                          const PbrFactors& factors)
{
    const auto color = authorPrimvarReader(graph, "displayColorReader", "UsdPrimvarReader_float3",
                                           "varname", previewColorPrimvar(factors), "fallback",
                                           SdfValueTypeNames->Float3, factors.baseColor, "result");
    if (!color || !surface.GetInput(TfToken("diffuseColor")).ConnectToSource(color))
    {
        return false;
    }
    if (!previewUsesOpacity(factors))
    {
        return true;
    }
    // A primitive without vertex alpha has no primvar and uses the factor alone.
    const auto alpha = authorPrimvarReader(graph, "colorAlphaReader", "UsdPrimvarReader_float",
                                           "varname", previewAlphaPrimvar(factors), "fallback",
                                           SdfValueTypeNames->Float, factors.alpha, "result");
    return alpha && surface.GetInput(TfToken("opacity")).ConnectToSource(alpha);
}

template <typename T>
bool
connectMtlxVertexInput(const UsdShadeNodeGraph& graph, UsdShadeShader& surface, const char* input,
                       const char* readerName, const char* readerId, const char* primvar,
                       const char* factorName, const char* factorId, const SdfValueTypeName& type,
                       const T& factor)
{
    auto source = authorPrimvarReader(graph, readerName, readerId, "geomprop", primvar, "default",
                                      type, T(1), "out");
    if (source && factor != T(1))
    {
        auto multiply = UsdShadeShader::Define(graph.GetPrim().GetStage(),
                                               graph.GetPath().AppendChild(TfToken(factorName)));
        const auto vertexValue =
            multiply ? multiply.CreateInput(TfToken("in1"), type) : UsdShadeInput();
        if (!vertexValue || !multiply.CreateIdAttr().Set(TfToken(factorId)) ||
            !vertexValue.ConnectToSource(source) || !setInput(multiply, "in2", type, factor))
        {
            return false;
        }
        source = multiply.CreateOutput(TfToken("out"), type);
    }
    return source && surface.GetInput(TfToken(input)).ConnectToSource(source);
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
    const bool opacity = previewUsesOpacity(factors);
    return surface && surface.CreateIdAttr().Set(TfToken("UsdPreviewSurface")) &&
           setInput(surface, "diffuseColor", SdfValueTypeNames->Color3f, factors.baseColor) &&
           setInput(surface, "metallic", SdfValueTypeNames->Float, factors.metallic) &&
           setInput(surface, "roughness", SdfValueTypeNames->Float, factors.roughness) &&
           setInput(surface, "emissiveColor", SdfValueTypeNames->Color3f, factors.emissive) &&
           (!opacity || setInput(surface, "opacity", SdfValueTypeNames->Float, factors.alpha)) &&
           (!opacity || factors.alphaMode != fastgltf::AlphaMode::Mask ||
            setInput(surface, "opacityThreshold", SdfValueTypeNames->Float, factors.alphaCutoff)) &&
           connectSurface(material, TfToken(), graph, surface, TfToken("surface")) &&
           (!vertexColor || connectPreviewVertexColor(graph, surface, factors));
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
    const bool opaque = factors.alphaMode == fastgltf::AlphaMode::Opaque;
    const bool mask = factors.alphaMode == fastgltf::AlphaMode::Mask;
    const auto alphaMode = surface.CreateInput(TfToken("alpha_mode"), SdfValueTypeNames->Int);
    return surface.CreateIdAttr().Set(TfToken("ND_gltf_pbr_surfaceshader")) &&
           setInput(surface, "base_color", SdfValueTypeNames->Color3f, factors.baseColor) &&
           setInput(surface, "metallic", SdfValueTypeNames->Float, factors.metallic) &&
           setInput(surface, "roughness", SdfValueTypeNames->Float, factors.roughness) &&
           setInput(surface, "emissive", SdfValueTypeNames->Color3f, factors.emissive) &&
           alphaMode && alphaMode.GetAttr().SetVariability(SdfVariabilityUniform) &&
           alphaMode.Set(opaque ? 0 : (mask ? 1 : 2)) &&
           (opaque || setInput(surface, "alpha", SdfValueTypeNames->Float, factors.alpha)) &&
           (!mask ||
            setInput(surface, "alpha_cutoff", SdfValueTypeNames->Float, factors.alphaCutoff)) &&
           connectSurface(material, TfToken("mtlx"), graph, surface, TfToken("out")) &&
           (!vertexColor ||
            connectMtlxVertexInput(graph, surface, "base_color", "displayColorReader",
                                   "ND_geompropvalue_color3", "displayColor", "baseColorFactor",
                                   "ND_multiply_color3", SdfValueTypeNames->Color3f,
                                   factors.baseColor)) &&
           (!vertexColor || opaque ||
            connectMtlxVertexInput(graph, surface, "alpha", "colorAlphaReader",
                                   "ND_geompropvalue_float", "gltf_colorAlpha", "alphaFactor",
                                   "ND_multiply_float", SdfValueTypeNames->Float, factors.alpha));
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
    const auto factors = pbrFactors(source);
    if (!authorPreview(material, factors, vertexColor) ||
        !authorMtlx(material, factors, vertexColor))
    {
        return UsdShadeMaterial();
    }
    return material;
}

bool
authorPreviewPrimvars(const PbrFactors& factors, const VtVec3fArray& colors,
                      const VtFloatArray& alpha, const UsdGeomMesh& mesh)
{
    const UsdGeomPrimvarsAPI primvars(mesh);
    if (factors.baseColor != GfVec3f(1))
    {
        VtVec3fArray scaled(colors.size());
        for (std::size_t i = 0; i < colors.size(); ++i)
        {
            scaled[i] = GfCompMult(colors[i], factors.baseColor);
        }
        if (!primvars
                 .CreatePrimvar(TfToken(previewColorPrimvar(factors)),
                                SdfValueTypeNames->Color3fArray, UsdGeomTokens->vertex)
                 .Set(scaled))
        {
            return false;
        }
    }
    if (!previewUsesOpacity(factors) || factors.alpha == 1.0f || alpha.empty())
    {
        return true;
    }
    VtFloatArray scaled(alpha.size());
    for (std::size_t i = 0; i < alpha.size(); ++i)
    {
        scaled[i] = alpha[i] * factors.alpha;
    }
    return primvars
        .CreatePrimvar(TfToken(previewAlphaPrimvar(factors)), SdfValueTypeNames->FloatArray,
                       UsdGeomTokens->vertex)
        .Set(scaled);
}

} // namespace

MaterialAuthor::MaterialAuthor(const fastgltf::Asset& asset, const pxr::UsdStageRefPtr& stage,
                               const pxr::SdfPath& scope)
    : _asset(asset), _stage(stage), _scope(scope), _materials(asset.materials.size()),
      _vertexColorMaterials(asset.materials.size())
{
}

bool
MaterialAuthor::Bind(std::size_t index, const pxr::VtVec3fArray* colors,
                     const pxr::VtFloatArray& alpha, const pxr::UsdGeomMesh& mesh,
                     std::string& error)
{
    if (index >= _asset.materials.size())
    {
        error = "material index is out of range";
        return false;
    }
    const auto& source = _asset.materials[index];
    const auto& pbr = source.pbrData;
    if (pbr.baseColorTexture || pbr.metallicRoughnessTexture || source.normalTexture ||
        source.occlusionTexture || source.emissiveTexture)
    {
        error = "material textures are not supported";
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
    if (!std::isfinite(source.alphaCutoff) || source.alphaCutoff < 0)
    {
        error = "material alpha cutoff is non-finite or negative";
        return false;
    }
    auto& material = colors ? _vertexColorMaterials[index] : _materials[index];
    if (!material)
    {
        material = authorMaterial(
            source, "Material_" + std::to_string(index) + (colors ? "_VertexColor" : ""), _stage,
            _scope, colors != nullptr);
        if (!material)
        {
            error = "could not author USD material";
            return false;
        }
    }
    if (colors && !authorPreviewPrimvars(pbrFactors(source), *colors, alpha, mesh))
    {
        error = "could not author USD preview vertex colors";
        return false;
    }
    return BindMaterial(material, source.doubleSided, mesh, error);
}

bool
MaterialAuthor::BindDefault(bool hasVertexColor, const pxr::UsdGeomMesh& mesh, std::string& error)
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
                             const pxr::UsdGeomMesh& mesh, std::string& error)
{
    using namespace pxr;
    if (!UsdShadeMaterialBindingAPI::Apply(mesh.GetPrim()).Bind(material) ||
        !mesh.CreateDoubleSidedAttr().Set(doubleSided))
    {
        error = "could not author USD material binding or sidedness";
        return false;
    }
    return true;
}

} // namespace gltfUsd
