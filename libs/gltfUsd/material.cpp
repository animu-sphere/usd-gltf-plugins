// SPDX-License-Identifier: Apache-2.0
#include "material.h"

#include "image.h"

#include "pxr/base/gf/vec2f.h"
#include "pxr/base/gf/vec3f.h"
#include "pxr/base/gf/vec4f.h"
#include "pxr/base/tf/diagnostic.h"
#include "pxr/base/tf/pathUtils.h"
#include "pxr/usd/ar/packageUtils.h"
#include "pxr/usd/sdf/assetPath.h"
#include "pxr/usd/sdf/types.h"
#include "pxr/usd/usdGeom/primvarsAPI.h"
#include "pxr/usd/usdGeom/tokens.h"
#include "pxr/usd/usdShade/materialBindingAPI.h"
#include "pxr/usd/usdShade/nodeGraph.h"
#include "pxr/usd/usdShade/shader.h"

#include <cmath>
#include <string>
#include <type_traits>

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

// The base color image, with the primvar holding its coordinates.
struct TextureInput
{
    std::string file;
    std::string primvar;
    fastgltf::Wrap wrapS;
    fastgltf::Wrap wrapT;
};

const char*
previewWrap(fastgltf::Wrap wrap)
{
    switch (wrap)
    {
    case fastgltf::Wrap::ClampToEdge:
        return "clamp";
    case fastgltf::Wrap::MirroredRepeat:
        return "mirror";
    default:
        return "repeat";
    }
}

const char*
mtlxWrap(fastgltf::Wrap wrap)
{
    switch (wrap)
    {
    case fastgltf::Wrap::ClampToEdge:
        return "clamp";
    case fastgltf::Wrap::MirroredRepeat:
        return "mirror";
    default:
        return "periodic";
    }
}

// UsdUVTexture's scale multiplies exactly as glTF's factor does.
bool
connectPreviewTexture(const UsdShadeNodeGraph& graph, UsdShadeShader& surface,
                      const PbrFactors& factors, const TextureInput& texture)
{
    const auto st = authorPrimvarReader(graph, "texCoordReader", "UsdPrimvarReader_float2",
                                        "varname", texture.primvar.c_str(), "fallback",
                                        SdfValueTypeNames->Float2, GfVec2f(0), "result");
    auto image = UsdShadeShader::Define(graph.GetPrim().GetStage(),
                                        graph.GetPath().AppendChild(TfToken("baseColorTexture")));
    const auto stInput =
        image ? image.CreateInput(TfToken("st"), SdfValueTypeNames->Float2) : UsdShadeInput();
    if (!st || !stInput || !image.CreateIdAttr().Set(TfToken("UsdUVTexture")) ||
        !stInput.ConnectToSource(st) ||
        !setInput(image, "file", SdfValueTypeNames->Asset, SdfAssetPath(texture.file)) ||
        !setInput(image, "sourceColorSpace", SdfValueTypeNames->Token, TfToken("sRGB")) ||
        !setInput(image, "wrapS", SdfValueTypeNames->Token, TfToken(previewWrap(texture.wrapS))) ||
        !setInput(image, "wrapT", SdfValueTypeNames->Token, TfToken(previewWrap(texture.wrapT))) ||
        !setInput(image, "scale", SdfValueTypeNames->Float4,
                  GfVec4f(factors.baseColor[0], factors.baseColor[1], factors.baseColor[2],
                          factors.alpha)))
    {
        return false;
    }
    const auto rgb = image.CreateOutput(TfToken("rgb"), SdfValueTypeNames->Float3);
    if (!rgb || !surface.GetInput(TfToken("diffuseColor")).ConnectToSource(rgb))
    {
        return false;
    }
    if (!previewUsesOpacity(factors))
    {
        return true;
    }
    const auto a = image.CreateOutput(TfToken("a"), SdfValueTypeNames->Float);
    return a && surface.GetInput(TfToken("opacity")).ConnectToSource(a);
}

UsdShadeShader
defineMtlxNode(const UsdShadeNodeGraph& graph, const char* name, const char* id)
{
    auto node = UsdShadeShader::Define(graph.GetPrim().GetStage(),
                                       graph.GetPath().AppendChild(TfToken(name)));
    return node && node.CreateIdAttr().Set(TfToken(id)) ? node : UsdShadeShader();
}

// `second` is a constant, or another node's output.
template <typename T>
UsdShadeOutput
authorMtlxMultiply(const UsdShadeNodeGraph& graph, const char* name, const char* id,
                   const SdfValueTypeName& type, const UsdShadeOutput& first, const T& second)
{
    auto multiply = defineMtlxNode(graph, name, id);
    const auto in1 = multiply ? multiply.CreateInput(TfToken("in1"), type) : UsdShadeInput();
    if (!in1 || !in1.ConnectToSource(first))
    {
        return UsdShadeOutput();
    }
    if constexpr (std::is_same_v<T, UsdShadeOutput>)
    {
        const auto in2 = multiply.CreateInput(TfToken("in2"), type);
        if (!in2 || !in2.ConnectToSource(second))
        {
            return UsdShadeOutput();
        }
    }
    else if (!setInput(multiply, "in2", type, second))
    {
        return UsdShadeOutput();
    }
    return multiply.CreateOutput(TfToken("out"), type);
}

UsdShadeOutput
authorMtlxImage(const UsdShadeNodeGraph& graph, const TextureInput& texture)
{
    const auto st = authorPrimvarReader(graph, "texCoordReader", "ND_geompropvalue_vector2",
                                        "geomprop", texture.primvar.c_str(), "default",
                                        SdfValueTypeNames->Float2, GfVec2f(0), "out");
    auto image = defineMtlxNode(graph, "baseColorImage", "ND_image_color4");
    const auto stInput =
        image ? image.CreateInput(TfToken("texcoord"), SdfValueTypeNames->Float2) : UsdShadeInput();
    const auto file =
        image ? image.CreateInput(TfToken("file"), SdfValueTypeNames->Asset) : UsdShadeInput();
    if (!st || !stInput || !file || !stInput.ConnectToSource(st) ||
        !file.Set(SdfAssetPath(texture.file)) ||
        !setInput(image, "default", SdfValueTypeNames->Color4f, GfVec4f(0, 0, 0, 1)) ||
        !setInput(image, "uaddressmode", SdfValueTypeNames->String,
                  std::string(mtlxWrap(texture.wrapS))) ||
        !setInput(image, "vaddressmode", SdfValueTypeNames->String,
                  std::string(mtlxWrap(texture.wrapT))))
    {
        return UsdShadeOutput();
    }
    file.GetAttr().SetColorSpace(TfToken("srgb_texture"));
    return image.CreateOutput(TfToken("out"), SdfValueTypeNames->Color4f);
}

UsdShadeOutput
authorMtlxImageChannel(const UsdShadeNodeGraph& graph, const char* name, const char* id,
                       const UsdShadeOutput& image, const SdfValueTypeName& type)
{
    auto node = defineMtlxNode(graph, name, id);
    const auto in =
        node ? node.CreateInput(TfToken("in"), SdfValueTypeNames->Color4f) : UsdShadeInput();
    return in && in.ConnectToSource(image) ? node.CreateOutput(TfToken("out"), type)
                                           : UsdShadeOutput();
}

// Connects texture * vertex value * factor, leaving out the terms that are absent.
template <typename T>
bool
connectMtlxInput(const UsdShadeNodeGraph& graph, UsdShadeShader& surface, const char* input,
                 UsdShadeOutput source, const UsdShadeOutput& vertex, const char* vertexName,
                 const char* factorName, const char* multiplyId, const SdfValueTypeName& type,
                 const T& factor)
{
    if (source && vertex)
    {
        source = authorMtlxMultiply(graph, vertexName, multiplyId, type, source, vertex);
        if (!source)
        {
            return false;
        }
    }
    else if (vertex)
    {
        source = vertex;
    }
    if (!source)
    {
        return true;
    }
    if (factor != T(1))
    {
        source = authorMtlxMultiply(graph, factorName, multiplyId, type, source, factor);
    }
    return source && surface.GetInput(TfToken(input)).ConnectToSource(source);
}

bool
connectMtlxInputs(const UsdShadeNodeGraph& graph, UsdShadeShader& surface,
                  const PbrFactors& factors, bool vertexColor, const TextureInput* texture)
{
    const bool usesAlpha = factors.alphaMode != fastgltf::AlphaMode::Opaque;
    UsdShadeOutput imageColor;
    UsdShadeOutput imageAlpha;
    if (texture)
    {
        const auto image = authorMtlxImage(graph, *texture);
        if (!image)
        {
            return false;
        }
        imageColor = authorMtlxImageChannel(graph, "baseColorRgb", "ND_convert_color4_color3",
                                            image, SdfValueTypeNames->Color3f);
        if (!imageColor)
        {
            return false;
        }
        if (usesAlpha)
        {
            imageAlpha = authorMtlxImageChannel(graph, "baseColorA", "ND_extract_color4", image,
                                                SdfValueTypeNames->Float);
            UsdShadeShader extract(imageAlpha.GetPrim());
            if (!imageAlpha || !setInput(extract, "index", SdfValueTypeNames->Int, 3))
            {
                return false;
            }
        }
    }
    UsdShadeOutput vertexColorValue;
    UsdShadeOutput vertexAlpha;
    if (vertexColor)
    {
        vertexColorValue = authorPrimvarReader(
            graph, "displayColorReader", "ND_geompropvalue_color3", "geomprop", "displayColor",
            "default", SdfValueTypeNames->Color3f, GfVec3f(1), "out");
        if (!vertexColorValue)
        {
            return false;
        }
        if (usesAlpha)
        {
            vertexAlpha = authorPrimvarReader(graph, "colorAlphaReader", "ND_geompropvalue_float",
                                              "geomprop", "gltf_colorAlpha", "default",
                                              SdfValueTypeNames->Float, 1.0f, "out");
            if (!vertexAlpha)
            {
                return false;
            }
        }
    }
    return connectMtlxInput(graph, surface, "base_color", imageColor, vertexColorValue,
                            "baseColorVertex", "baseColorFactor", "ND_multiply_color3",
                            SdfValueTypeNames->Color3f, factors.baseColor) &&
           connectMtlxInput(graph, surface, "alpha", imageAlpha, vertexAlpha, "alphaVertex",
                            "alphaFactor", "ND_multiply_float", SdfValueTypeNames->Float,
                            factors.alpha);
}

bool
authorPreview(const UsdShadeMaterial& material, const PbrFactors& factors, bool vertexColor,
              const TextureInput* texture)
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
           (texture ? connectPreviewTexture(graph, surface, factors, *texture)
                    : (!vertexColor || connectPreviewVertexColor(graph, surface, factors)));
}

bool
authorMtlx(const UsdShadeMaterial& material, const PbrFactors& factors, bool vertexColor,
           const TextureInput* texture)
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
           connectMtlxInputs(graph, surface, factors, vertexColor, texture);
}

UsdShadeMaterial
authorMaterial(const fastgltf::Material& source, const std::string& name,
               const UsdStageRefPtr& stage, const SdfPath& scope, bool vertexColor = false,
               const TextureInput* texture = nullptr)
{
    const auto material = UsdShadeMaterial::Define(stage, scope.AppendChild(TfToken(name)));
    if (!material || (!source.name.empty() && !material.GetPrim().SetDisplayName(source.name)))
    {
        return UsdShadeMaterial();
    }
    const auto factors = pbrFactors(source);
    if (!authorPreview(material, factors, vertexColor, texture) ||
        !authorMtlx(material, factors, vertexColor, texture))
    {
        return UsdShadeMaterial();
    }
    return material;
}

bool
resolveTexture(const fastgltf::Asset& asset, const fastgltf::TextureInfo& info,
               const std::string& sourcePath, const gltfCore::BufferLoader& loadBuffer,
               TextureInput& result, std::string& error)
{
    if (info.textureIndex >= asset.textures.size())
    {
        error = "texture index is out of range";
        return false;
    }
    const auto& texture = asset.textures[info.textureIndex];
    if (!texture.imageIndex)
    {
        error = "texture has no PNG or JPEG image source";
        return false;
    }
    fastgltf::Sampler sampler{};
    if (texture.samplerIndex)
    {
        if (*texture.samplerIndex >= asset.samplers.size())
        {
            error = "texture sampler index is out of range";
            return false;
        }
        sampler.wrapS = asset.samplers[*texture.samplerIndex].wrapS;
        sampler.wrapT = asset.samplers[*texture.samplerIndex].wrapT;
    }
    gltfTexture::ImageLocation location;
    if (!gltfTexture::LocateImage(asset, *texture.imageIndex, sourcePath, loadBuffer, location,
                                  error))
    {
        return false;
    }
    result.file = location.embedded
                      ? ArJoinPackageRelativePath("./" + TfGetBaseName(sourcePath), location.path)
                      : "./" + location.path;
    result.primvar = info.texCoordIndex == 0 ? "st" : "st" + std::to_string(info.texCoordIndex);
    result.wrapS = sampler.wrapS;
    result.wrapT = sampler.wrapT;
    return true;
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

MaterialAuthor::MaterialAuthor(const fastgltf::Asset& asset, const std::string& sourcePath,
                               const gltfCore::BufferLoader& loadBuffer,
                               const pxr::UsdStageRefPtr& stage, const pxr::SdfPath& scope)
    : _asset(asset), _sourcePath(sourcePath), _loadBuffer(loadBuffer), _stage(stage), _scope(scope),
      _materials(asset.materials.size()), _vertexColorMaterials(asset.materials.size())
{
}

bool
MaterialAuthor::Bind(std::size_t index, std::size_t uvSetCount, const pxr::VtVec3fArray* colors,
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
    if (pbr.metallicRoughnessTexture || source.normalTexture || source.occlusionTexture ||
        source.emissiveTexture)
    {
        error = "material textures other than base color are not supported";
        return false;
    }
    if (pbr.baseColorTexture && pbr.baseColorTexture->texCoordIndex >= uvSetCount)
    {
        error = "material texture uses a texture coordinate set the primitive does not have";
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
        TextureInput texture;
        if (pbr.baseColorTexture && !resolveTexture(_asset, *pbr.baseColorTexture, _sourcePath,
                                                    _loadBuffer, texture, error))
        {
            return false;
        }
        if (pbr.baseColorTexture && colors)
        {
            TF_WARN("glTF material %zu: UsdPreviewSurface cannot multiply a texture by vertex "
                    "colors; its network ignores COLOR_0",
                    index);
        }
        material = authorMaterial(
            source, "Material_" + std::to_string(index) + (colors ? "_VertexColor" : ""), _stage,
            _scope, colors != nullptr, pbr.baseColorTexture ? &texture : nullptr);
        if (!material)
        {
            error = "could not author USD material";
            return false;
        }
    }
    if (colors && !pbr.baseColorTexture &&
        !authorPreviewPrimvars(pbrFactors(source), *colors, alpha, mesh))
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
