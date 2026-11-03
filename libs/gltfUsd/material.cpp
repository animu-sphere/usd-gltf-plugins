// SPDX-License-Identifier: Apache-2.0
#include "material.h"

#include "image.h"

#include "pxr/base/gf/math.h"
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
#include <optional>
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
    float occlusionStrength;
    float normalScale;
    fastgltf::AlphaMode alphaMode;
    // KHR_materials_unlit: the base color, with its alpha, is the whole result.
    bool unlit;
    // KHR_materials_ior, already validated; 0 stands for infinity.
    float ior;
    // KHR_materials_clearcoat, 0 without it, which disables the layer.
    float clearcoat;
    float clearcoatRoughness;
    float clearcoatNormalScale;
    // KHR_materials_transmission, 0 without it.
    float transmission;
};

// glTF's default, and the default of both shaders' ior inputs.
constexpr float kDefaultIor = 1.5f;

// An ior of 0 makes glTF's Fresnel term 1 at every angle, which glTF lets a renderer treat as a
// very large finite ior. This one gives an F0 of 0.999996, with 1 at grazing angles, and keeps
// 1 / ior finite for refraction.
constexpr float kInfiniteIor = 1.0e6f;

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
            source.occlusionTexture ? float(source.occlusionTexture->strength) : 1.0f,
            source.normalTexture ? float(source.normalTexture->scale) : 1.0f,
            source.alphaMode,
            source.unlit,
            float(source.ior),
            source.clearcoat ? float(source.clearcoat->clearcoatFactor) : 0.0f,
            source.clearcoat ? float(source.clearcoat->clearcoatRoughnessFactor) : 0.0f,
            source.clearcoat && source.clearcoat->clearcoatNormalTexture
                ? float(source.clearcoat->clearcoatNormalTexture->scale)
                : 1.0f,
            source.transmission ? float(source.transmission->transmissionFactor) : 0.0f};
}

// glTF disables the clearcoat layer at a factor of 0, and an unlit material reflects nothing.
bool
hasClearcoat(const PbrFactors& factors)
{
    return !factors.unlit && factors.clearcoat > 0.0f;
}

// A transmission of 0 is none, and an unlit material transmits nothing.
bool
hasTransmission(const PbrFactors& factors)
{
    return !factors.unlit && factors.transmission > 0.0f;
}

// The ior to author, if it is not the shaders' default. An unlit material reflects nothing, so
// its ior has no effect.
std::optional<float>
authoredIor(const PbrFactors& factors)
{
    if (factors.unlit || factors.ior == kDefaultIor)
    {
        return std::nullopt;
    }
    return factors.ior == 0.0f ? kInfiniteIor : factors.ior;
}

// Neither shader has an unlit mode, so an unlit material emits its base color and reflects nothing.
const char*
previewColorInput(const PbrFactors& factors)
{
    return factors.unlit ? "emissiveColor" : "diffuseColor";
}

const char*
mtlxColorInput(const PbrFactors& factors)
{
    return factors.unlit ? "emissive" : "base_color";
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
    if (!color || !surface.GetInput(TfToken(previewColorInput(factors))).ConnectToSource(color))
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

// Applied to `st` as scale, then counter-clockwise rotation about the origin, then translation.
struct UvTransform
{
    GfVec2f scale;
    float rotationDegrees;
    GfVec2f translation;
};

// An image, with the primvar holding its coordinates and any transform of them.
struct TextureInput
{
    std::string file;
    std::string primvar;
    fastgltf::Wrap wrapS;
    fastgltf::Wrap wrapT;
    std::optional<UvTransform> transform;
};

struct MaterialTextures
{
    std::optional<TextureInput> baseColor;
    std::optional<TextureInput> metallicRoughness;
    std::optional<TextureInput> normal;
    std::optional<TextureInput> occlusion;
    std::optional<TextureInput> emissive;
    std::optional<TextureInput> clearcoat;
    std::optional<TextureInput> clearcoatRoughness;
    // Read by MaterialX only: UsdPreviewSurface's clearcoat uses the base normal.
    std::optional<TextureInput> clearcoatNormal;
    // Read by MaterialX only: UsdPreviewSurface has no transmission.
    std::optional<TextureInput> transmission;
};

// Textures sharing a UV set share a reader: defining it again changes nothing.
std::string
texCoordReaderName(const TextureInput& texture)
{
    return texture.primvar + "Reader";
}

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

UsdShadeOutput
authorPreviewTransform(const UsdShadeNodeGraph& graph, const std::string& image,
                       const UsdShadeOutput& st, const UvTransform& transform)
{
    auto node = UsdShadeShader::Define(graph.GetPrim().GetStage(),
                                       graph.GetPath().AppendChild(TfToken(image + "Transform")));
    const auto in =
        node ? node.CreateInput(TfToken("in"), SdfValueTypeNames->Float2) : UsdShadeInput();
    return in && node.CreateIdAttr().Set(TfToken("UsdTransform2d")) && in.ConnectToSource(st) &&
                   setInput(node, "scale", SdfValueTypeNames->Float2, transform.scale) &&
                   setInput(node, "rotation", SdfValueTypeNames->Float,
                            transform.rotationDegrees) &&
                   setInput(node, "translation", SdfValueTypeNames->Float2, transform.translation)
               ? node.CreateOutput(TfToken("result"), SdfValueTypeNames->Float2)
               : UsdShadeOutput();
}

// UsdUVTexture computes texture * scale + bias, which expresses glTF's factors
// and occlusion strength exactly.
UsdShadeShader
authorPreviewTexture(const UsdShadeNodeGraph& graph, const char* name, const TextureInput& texture,
                     const char* colorSpace, const GfVec4f& scale, const GfVec4f& bias = GfVec4f(0))
{
    auto st = authorPrimvarReader(graph, texCoordReaderName(texture).c_str(),
                                  "UsdPrimvarReader_float2", "varname", texture.primvar.c_str(),
                                  "fallback", SdfValueTypeNames->Float2, GfVec2f(0), "result");
    if (st && texture.transform)
    {
        st = authorPreviewTransform(graph, name, st, *texture.transform);
    }
    auto image = UsdShadeShader::Define(graph.GetPrim().GetStage(),
                                        graph.GetPath().AppendChild(TfToken(name)));
    const auto stInput =
        image ? image.CreateInput(TfToken("st"), SdfValueTypeNames->Float2) : UsdShadeInput();
    if (!st || !stInput || !image.CreateIdAttr().Set(TfToken("UsdUVTexture")) ||
        !stInput.ConnectToSource(st) ||
        !setInput(image, "file", SdfValueTypeNames->Asset, SdfAssetPath(texture.file)) ||
        !setInput(image, "sourceColorSpace", SdfValueTypeNames->Token, TfToken(colorSpace)) ||
        !setInput(image, "wrapS", SdfValueTypeNames->Token, TfToken(previewWrap(texture.wrapS))) ||
        !setInput(image, "wrapT", SdfValueTypeNames->Token, TfToken(previewWrap(texture.wrapT))) ||
        !setInput(image, "scale", SdfValueTypeNames->Float4, scale) ||
        (bias != GfVec4f(0) && !setInput(image, "bias", SdfValueTypeNames->Float4, bias)))
    {
        return UsdShadeShader();
    }
    return image;
}

bool
connectPreviewOutput(UsdShadeShader& image, const char* output, const SdfValueTypeName& type,
                     UsdShadeShader& surface, const char* input)
{
    const auto source = image.CreateOutput(TfToken(output), type);
    return source && surface.GetInput(TfToken(input)).ConnectToSource(source);
}

bool
connectPreviewBaseColor(const UsdShadeNodeGraph& graph, UsdShadeShader& surface,
                        const PbrFactors& factors, const TextureInput& texture)
{
    auto image = authorPreviewTexture(
        graph, "baseColorTexture", texture, "sRGB",
        GfVec4f(factors.baseColor[0], factors.baseColor[1], factors.baseColor[2], factors.alpha));
    return image &&
           connectPreviewOutput(image, "rgb", SdfValueTypeNames->Float3, surface,
                                previewColorInput(factors)) &&
           (!previewUsesOpacity(factors) ||
            connectPreviewOutput(image, "a", SdfValueTypeNames->Float, surface, "opacity"));
}

bool
connectPreviewMetallicRoughness(const UsdShadeNodeGraph& graph, UsdShadeShader& surface,
                                const PbrFactors& factors, const TextureInput& texture)
{
    auto image = authorPreviewTexture(graph, "metallicRoughnessTexture", texture, "raw",
                                      GfVec4f(1, factors.roughness, factors.metallic, 1));
    return image &&
           connectPreviewOutput(image, "g", SdfValueTypeNames->Float, surface, "roughness") &&
           connectPreviewOutput(image, "b", SdfValueTypeNames->Float, surface, "metallic");
}

// Maps the image to a tangent-space vector, with glTF's scale on X and Y only.
bool
connectPreviewNormal(const UsdShadeNodeGraph& graph, UsdShadeShader& surface,
                     const PbrFactors& factors, const TextureInput& texture)
{
    const float scale = factors.normalScale;
    auto image =
        authorPreviewTexture(graph, "normalTexture", texture, "raw",
                             GfVec4f(2 * scale, 2 * scale, 2, 1), GfVec4f(-scale, -scale, -1, 0));
    return image && setInput(surface, "normal", SdfValueTypeNames->Normal3f, GfVec3f(0, 0, 1)) &&
           connectPreviewOutput(image, "rgb", SdfValueTypeNames->Float3, surface, "normal");
}

bool
connectPreviewOcclusion(const UsdShadeNodeGraph& graph, UsdShadeShader& surface,
                        const PbrFactors& factors, const TextureInput& texture)
{
    const float strength = factors.occlusionStrength;
    auto image = authorPreviewTexture(graph, "occlusionTexture", texture, "raw", GfVec4f(strength),
                                      GfVec4f(1 - strength));
    return image && setInput(surface, "occlusion", SdfValueTypeNames->Float, 1.0f) &&
           connectPreviewOutput(image, "r", SdfValueTypeNames->Float, surface, "occlusion");
}

bool
connectPreviewEmissive(const UsdShadeNodeGraph& graph, UsdShadeShader& surface,
                       const PbrFactors& factors, const TextureInput& texture)
{
    auto image = authorPreviewTexture(
        graph, "emissiveTexture", texture, "sRGB",
        GfVec4f(factors.emissive[0], factors.emissive[1], factors.emissive[2], 1));
    return image &&
           connectPreviewOutput(image, "rgb", SdfValueTypeNames->Float3, surface, "emissiveColor");
}

// glTF's clearcoat is R and its roughness G, each times its factor.
bool
connectPreviewClearcoat(const UsdShadeNodeGraph& graph, UsdShadeShader& surface,
                        const PbrFactors& factors, const MaterialTextures& textures)
{
    if (textures.clearcoat)
    {
        auto image = authorPreviewTexture(graph, "clearcoatTexture", *textures.clearcoat, "raw",
                                          GfVec4f(factors.clearcoat, 1, 1, 1));
        if (!image ||
            !connectPreviewOutput(image, "r", SdfValueTypeNames->Float, surface, "clearcoat"))
        {
            return false;
        }
    }
    if (textures.clearcoatRoughness)
    {
        auto image =
            authorPreviewTexture(graph, "clearcoatRoughnessTexture", *textures.clearcoatRoughness,
                                 "raw", GfVec4f(1, factors.clearcoatRoughness, 1, 1));
        return image && connectPreviewOutput(image, "g", SdfValueTypeNames->Float, surface,
                                             "clearcoatRoughness");
    }
    return true;
}

bool
connectPreviewTextures(const UsdShadeNodeGraph& graph, UsdShadeShader& surface,
                       const PbrFactors& factors, const MaterialTextures& textures)
{
    return (!textures.metallicRoughness ||
            connectPreviewMetallicRoughness(graph, surface, factors,
                                            *textures.metallicRoughness)) &&
           (!textures.normal || connectPreviewNormal(graph, surface, factors, *textures.normal)) &&
           (!textures.occlusion ||
            connectPreviewOcclusion(graph, surface, factors, *textures.occlusion)) &&
           (!textures.emissive ||
            connectPreviewEmissive(graph, surface, factors, *textures.emissive)) &&
           (!hasClearcoat(factors) || connectPreviewClearcoat(graph, surface, factors, textures));
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

// place2d cannot express a scale of 0, because it divides by it. rotate2d turns clockwise.
UsdShadeOutput
authorMtlxTransform(const UsdShadeNodeGraph& graph, const std::string& image,
                    const UsdShadeOutput& st, const UvTransform& transform)
{
    const auto type = SdfValueTypeNames->Float2;
    const auto scaled = authorMtlxMultiply(graph, (image + "Scale").c_str(), "ND_multiply_vector2",
                                           type, st, transform.scale);
    auto rotate = scaled ? defineMtlxNode(graph, (image + "Rotate").c_str(), "ND_rotate2d_vector2")
                         : UsdShadeShader();
    const auto in = rotate ? rotate.CreateInput(TfToken("in"), type) : UsdShadeInput();
    if (!in || !in.ConnectToSource(scaled) ||
        !setInput(rotate, "amount", SdfValueTypeNames->Float, -transform.rotationDegrees))
    {
        return UsdShadeOutput();
    }
    auto translate = defineMtlxNode(graph, (image + "Translate").c_str(), "ND_add_vector2");
    const auto in1 = translate ? translate.CreateInput(TfToken("in1"), type) : UsdShadeInput();
    return in1 && in1.ConnectToSource(rotate.CreateOutput(TfToken("out"), type)) &&
                   setInput(translate, "in2", type, transform.translation)
               ? translate.CreateOutput(TfToken("out"), type)
               : UsdShadeOutput();
}

// Non-color data is a vector image, which no color space transform applies to.
UsdShadeOutput
authorMtlxImage(const UsdShadeNodeGraph& graph, const char* name, const TextureInput& texture,
                bool color)
{
    auto st = authorPrimvarReader(graph, texCoordReaderName(texture).c_str(),
                                  "ND_geompropvalue_vector2", "geomprop", texture.primvar.c_str(),
                                  "default", SdfValueTypeNames->Float2, GfVec2f(0), "out");
    if (st && texture.transform)
    {
        st = authorMtlxTransform(graph, name, st, *texture.transform);
    }
    const auto type = color ? SdfValueTypeNames->Color4f : SdfValueTypeNames->Float4;
    auto image = defineMtlxNode(graph, name, color ? "ND_image_color4" : "ND_image_vector4");
    const auto stInput =
        image ? image.CreateInput(TfToken("texcoord"), SdfValueTypeNames->Float2) : UsdShadeInput();
    const auto file =
        image ? image.CreateInput(TfToken("file"), SdfValueTypeNames->Asset) : UsdShadeInput();
    if (!st || !stInput || !file || !stInput.ConnectToSource(st) ||
        !file.Set(SdfAssetPath(texture.file)) ||
        !setInput(image, "default", type, GfVec4f(0, 0, 0, 1)) ||
        !setInput(image, "uaddressmode", SdfValueTypeNames->String,
                  std::string(mtlxWrap(texture.wrapS))) ||
        !setInput(image, "vaddressmode", SdfValueTypeNames->String,
                  std::string(mtlxWrap(texture.wrapT))))
    {
        return UsdShadeOutput();
    }
    if (color)
    {
        file.GetAttr().SetColorSpace(TfToken("srgb_texture"));
    }
    return image.CreateOutput(TfToken("out"), type);
}

UsdShadeOutput
authorMtlxImageChannel(const UsdShadeNodeGraph& graph, const char* name, const char* id,
                       const UsdShadeOutput& image, const SdfValueTypeName& type)
{
    auto node = defineMtlxNode(graph, name, id);
    const auto in = node ? node.CreateInput(TfToken("in"), image.GetTypeName()) : UsdShadeInput();
    return in && in.ConnectToSource(image) ? node.CreateOutput(TfToken("out"), type)
                                           : UsdShadeOutput();
}

UsdShadeOutput
authorMtlxImageComponent(const UsdShadeNodeGraph& graph, const char* name,
                         const UsdShadeOutput& image, int index)
{
    const bool color = image.GetTypeName() == SdfValueTypeNames->Color4f;
    const auto output =
        authorMtlxImageChannel(graph, name, color ? "ND_extract_color4" : "ND_extract_vector4",
                               image, SdfValueTypeNames->Float);
    if (!output)
    {
        return UsdShadeOutput();
    }
    UsdShadeShader extract(output.GetPrim());
    return setInput(extract, "index", SdfValueTypeNames->Int, index) ? output : UsdShadeOutput();
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
        const auto image = authorMtlxImage(graph, "baseColorImage", *texture, true);
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
            imageAlpha = authorMtlxImageComponent(graph, "baseColorA", image, 3);
            if (!imageAlpha)
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
    return connectMtlxInput(graph, surface, mtlxColorInput(factors), imageColor, vertexColorValue,
                            "baseColorVertex", "baseColorFactor", "ND_multiply_color3",
                            SdfValueTypeNames->Color3f, factors.baseColor) &&
           connectMtlxInput(graph, surface, "alpha", imageAlpha, vertexAlpha, "alphaVertex",
                            "alphaFactor", "ND_multiply_float", SdfValueTypeNames->Float,
                            factors.alpha);
}

bool
connectMtlxMetallicRoughness(const UsdShadeNodeGraph& graph, UsdShadeShader& surface,
                             const PbrFactors& factors, const TextureInput& texture)
{
    const auto image = authorMtlxImage(graph, "metallicRoughnessImage", texture, false);
    if (!image)
    {
        return false;
    }
    const auto roughness = authorMtlxImageComponent(graph, "roughnessChannel", image, 1);
    const auto metallic = authorMtlxImageComponent(graph, "metallicChannel", image, 2);
    return roughness && metallic &&
           connectMtlxInput(graph, surface, "roughness", roughness, UsdShadeOutput(), nullptr,
                            "roughnessFactor", "ND_multiply_float", SdfValueTypeNames->Float,
                            factors.roughness) &&
           connectMtlxInput(graph, surface, "metallic", metallic, UsdShadeOutput(), nullptr,
                            "metallicFactor", "ND_multiply_float", SdfValueTypeNames->Float,
                            factors.metallic);
}

// `name` prefixes the nodes, and `input` is the surface's normal input.
bool
connectMtlxNormal(const UsdShadeNodeGraph& graph, UsdShadeShader& surface, const std::string& name,
                  const char* input, float scale, const TextureInput& texture)
{
    const auto image = authorMtlxImage(graph, (name + "Image").c_str(), texture, false);
    const auto xyz =
        image ? authorMtlxImageChannel(graph, (name + "Xyz").c_str(), "ND_convert_vector4_vector3",
                                       image, SdfValueTypeNames->Vector3f)
              : UsdShadeOutput();
    auto map = xyz ? defineMtlxNode(graph, (name + "Map").c_str(), "ND_normalmap_float")
                   : UsdShadeShader();
    const auto in =
        map ? map.CreateInput(TfToken("in"), SdfValueTypeNames->Vector3f) : UsdShadeInput();
    const auto normal = surface.CreateInput(TfToken(input), SdfValueTypeNames->Vector3f);
    return in && normal && in.ConnectToSource(xyz) &&
           (scale == 1.0f || setInput(map, "scale", SdfValueTypeNames->Float, scale)) &&
           normal.ConnectToSource(map.CreateOutput(TfToken("out"), SdfValueTypeNames->Vector3f));
}

// glTF's 1 + strength * (occlusion - 1) is a mix from 1 to the image.
bool
connectMtlxOcclusion(const UsdShadeNodeGraph& graph, UsdShadeShader& surface,
                     const PbrFactors& factors, const TextureInput& texture)
{
    const auto image = authorMtlxImage(graph, "occlusionImage", texture, false);
    auto source =
        image ? authorMtlxImageComponent(graph, "occlusionChannel", image, 0) : UsdShadeOutput();
    if (!source || !setInput(surface, "occlusion", SdfValueTypeNames->Float, 1.0f))
    {
        return false;
    }
    if (factors.occlusionStrength != 1.0f)
    {
        auto mix = defineMtlxNode(graph, "occlusionStrength", "ND_mix_float");
        const auto fg =
            mix ? mix.CreateInput(TfToken("fg"), SdfValueTypeNames->Float) : UsdShadeInput();
        if (!fg || !fg.ConnectToSource(source) ||
            !setInput(mix, "bg", SdfValueTypeNames->Float, 1.0f) ||
            !setInput(mix, "mix", SdfValueTypeNames->Float, factors.occlusionStrength))
        {
            return false;
        }
        source = mix.CreateOutput(TfToken("out"), SdfValueTypeNames->Float);
    }
    return source && surface.GetInput(TfToken("occlusion")).ConnectToSource(source);
}

bool
connectMtlxEmissive(const UsdShadeNodeGraph& graph, UsdShadeShader& surface,
                    const PbrFactors& factors, const TextureInput& texture)
{
    const auto image = authorMtlxImage(graph, "emissiveImage", texture, true);
    const auto rgb = image
                         ? authorMtlxImageChannel(graph, "emissiveRgb", "ND_convert_color4_color3",
                                                  image, SdfValueTypeNames->Color3f)
                         : UsdShadeOutput();
    return rgb && connectMtlxInput(graph, surface, "emissive", rgb, UsdShadeOutput(), nullptr,
                                   "emissiveFactor", "ND_multiply_color3",
                                   SdfValueTypeNames->Color3f, factors.emissive);
}

// One channel of a non-color image times its factor.
bool
connectMtlxChannel(const UsdShadeNodeGraph& graph, UsdShadeShader& surface, const std::string& name,
                   const char* input, int index, float factor, const TextureInput& texture)
{
    const auto image = authorMtlxImage(graph, (name + "Image").c_str(), texture, false);
    const auto channel =
        image ? authorMtlxImageComponent(graph, (name + "Channel").c_str(), image, index)
              : UsdShadeOutput();
    return channel && connectMtlxInput(graph, surface, input, channel, UsdShadeOutput(), nullptr,
                                       (name + "Factor").c_str(), "ND_multiply_float",
                                       SdfValueTypeNames->Float, factor);
}

// The clearcoat normal defaults to the geometric normal, as in glTF, not to the base normal map.
bool
connectMtlxClearcoat(const UsdShadeNodeGraph& graph, UsdShadeShader& surface,
                     const PbrFactors& factors, const MaterialTextures& textures)
{
    return (!textures.clearcoat || connectMtlxChannel(graph, surface, "clearcoat", "clearcoat", 0,
                                                      factors.clearcoat, *textures.clearcoat)) &&
           (!textures.clearcoatRoughness ||
            connectMtlxChannel(graph, surface, "clearcoatRoughness", "clearcoat_roughness", 1,
                               factors.clearcoatRoughness, *textures.clearcoatRoughness)) &&
           (!textures.clearcoatNormal ||
            connectMtlxNormal(graph, surface, "clearcoatNormal", "clearcoat_normal",
                              factors.clearcoatNormalScale, *textures.clearcoatNormal));
}

bool
connectMtlxTextures(const UsdShadeNodeGraph& graph, UsdShadeShader& surface,
                    const PbrFactors& factors, const MaterialTextures& textures)
{
    return (!textures.metallicRoughness ||
            connectMtlxMetallicRoughness(graph, surface, factors, *textures.metallicRoughness)) &&
           (!textures.normal || connectMtlxNormal(graph, surface, "normal", "normal",
                                                  factors.normalScale, *textures.normal)) &&
           (!textures.occlusion ||
            connectMtlxOcclusion(graph, surface, factors, *textures.occlusion)) &&
           (!textures.emissive ||
            connectMtlxEmissive(graph, surface, factors, *textures.emissive)) &&
           (!hasClearcoat(factors) || connectMtlxClearcoat(graph, surface, factors, textures)) &&
           (!textures.transmission ||
            connectMtlxChannel(graph, surface, "transmission", "transmission", 0,
                               factors.transmission, *textures.transmission));
}

bool
authorPreview(const UsdShadeMaterial& material, const PbrFactors& factors, bool vertexColor,
              const MaterialTextures& textures)
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
    // A black metal has no diffuse term, and its specular color is black at every angle.
    const bool unlit = factors.unlit;
    // In the metallic workflow, a dielectric's F0 is ((1 - ior) / (1 + ior))^2, as in glTF.
    const auto ior = authoredIor(factors);
    // The clearcoat's roughness defaults to 0.01, not glTF's 0.
    const bool clearcoat = hasClearcoat(factors);
    return surface && surface.CreateIdAttr().Set(TfToken("UsdPreviewSurface")) &&
           setInput(surface, "diffuseColor", SdfValueTypeNames->Color3f,
                    unlit ? GfVec3f(0) : factors.baseColor) &&
           setInput(surface, "metallic", SdfValueTypeNames->Float,
                    unlit ? 1.0f : factors.metallic) &&
           setInput(surface, "roughness", SdfValueTypeNames->Float,
                    unlit ? 1.0f : factors.roughness) &&
           setInput(surface, "emissiveColor", SdfValueTypeNames->Color3f,
                    unlit ? factors.baseColor : factors.emissive) &&
           (!ior || setInput(surface, "ior", SdfValueTypeNames->Float, *ior)) &&
           (!clearcoat ||
            (setInput(surface, "clearcoat", SdfValueTypeNames->Float, factors.clearcoat) &&
             setInput(surface, "clearcoatRoughness", SdfValueTypeNames->Float,
                      factors.clearcoatRoughness))) &&
           (!opacity || setInput(surface, "opacity", SdfValueTypeNames->Float, factors.alpha)) &&
           (!opacity || factors.alphaMode != fastgltf::AlphaMode::Mask ||
            setInput(surface, "opacityThreshold", SdfValueTypeNames->Float, factors.alphaCutoff)) &&
           connectSurface(material, TfToken(), graph, surface, TfToken("surface")) &&
           (textures.baseColor
                ? connectPreviewBaseColor(graph, surface, factors, *textures.baseColor)
                : (!vertexColor || connectPreviewVertexColor(graph, surface, factors))) &&
           connectPreviewTextures(graph, surface, factors, textures);
}

bool
authorMtlx(const UsdShadeMaterial& material, const PbrFactors& factors, bool vertexColor,
           const MaterialTextures& textures)
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
    // A dielectric's specular input scales both its F0 and F90, so 0 removes its reflection.
    const bool unlit = factors.unlit;
    const auto alphaMode = surface.CreateInput(TfToken("alpha_mode"), SdfValueTypeNames->Int);
    // gltf_pbr computes its dielectric F0 from ior as glTF does, and declares the input uniform.
    const auto ior = authoredIor(factors);
    const auto iorInput =
        ior ? surface.CreateInput(TfToken("ior"), SdfValueTypeNames->Float) : UsdShadeInput();
    // gltf_pbr layers a dielectric of ior 1.5 over the base, as glTF does.
    const bool clearcoat = hasClearcoat(factors);
    // gltf_pbr mixes its diffuse term toward a transmission tinted by the base color, as glTF does.
    // Without KHR_materials_volume the material is thin, and thickness keeps its default of 0.
    const bool transmission = hasTransmission(factors);
    return surface.CreateIdAttr().Set(TfToken("ND_gltf_pbr_surfaceshader")) &&
           setInput(surface, "base_color", SdfValueTypeNames->Color3f,
                    unlit ? GfVec3f(0) : factors.baseColor) &&
           setInput(surface, "metallic", SdfValueTypeNames->Float,
                    unlit ? 0.0f : factors.metallic) &&
           setInput(surface, "roughness", SdfValueTypeNames->Float, factors.roughness) &&
           (!unlit || setInput(surface, "specular", SdfValueTypeNames->Float, 0.0f)) &&
           setInput(surface, "emissive", SdfValueTypeNames->Color3f,
                    unlit ? factors.baseColor : factors.emissive) &&
           (!ior || (iorInput && iorInput.GetAttr().SetVariability(SdfVariabilityUniform) &&
                     iorInput.Set(*ior))) &&
           (!clearcoat ||
            (setInput(surface, "clearcoat", SdfValueTypeNames->Float, factors.clearcoat) &&
             setInput(surface, "clearcoat_roughness", SdfValueTypeNames->Float,
                      factors.clearcoatRoughness))) &&
           (!transmission ||
            setInput(surface, "transmission", SdfValueTypeNames->Float, factors.transmission)) &&
           alphaMode && alphaMode.GetAttr().SetVariability(SdfVariabilityUniform) &&
           alphaMode.Set(opaque ? 0 : (mask ? 1 : 2)) &&
           (opaque || setInput(surface, "alpha", SdfValueTypeNames->Float, factors.alpha)) &&
           (!mask ||
            setInput(surface, "alpha_cutoff", SdfValueTypeNames->Float, factors.alphaCutoff)) &&
           connectSurface(material, TfToken("mtlx"), graph, surface, TfToken("out")) &&
           connectMtlxInputs(graph, surface, factors, vertexColor,
                             textures.baseColor ? &*textures.baseColor : nullptr) &&
           connectMtlxTextures(graph, surface, factors, textures);
}

UsdShadeMaterial
authorMaterial(const fastgltf::Material& source, const std::string& name,
               const UsdStageRefPtr& stage, const SdfPath& scope, bool vertexColor = false,
               const MaterialTextures& textures = {})
{
    const auto material = UsdShadeMaterial::Define(stage, scope.AppendChild(TfToken(name)));
    if (!material || (!source.name.empty() && !material.GetPrim().SetDisplayName(source.name)))
    {
        return UsdShadeMaterial();
    }
    const auto factors = pbrFactors(source);
    if (!authorPreview(material, factors, vertexColor, textures) ||
        !authorMtlx(material, factors, vertexColor, textures))
    {
        return UsdShadeMaterial();
    }
    return material;
}

// The extension overrides the texture's own set.
std::size_t
texCoordOf(const fastgltf::TextureInfo& info)
{
    return info.transform && info.transform->texCoordIndex ? *info.transform->texCoordIndex
                                                           : info.texCoordIndex;
}

// glTF applies translation * rotation * scale to (u, v), with
//     rotation = | cos r   sin r |
//                | -sin r  cos r |
// and `st` holds (u, 1 - v). Substituting gives the scale and rotation unchanged, with r now
// counter-clockwise, and a translation that also moves the origin from the upper left.
std::optional<UvTransform>
uvTransform(const fastgltf::TextureTransform& source)
{
    if (source.rotation == 0 && source.uvOffset[0] == 0 && source.uvOffset[1] == 0 &&
        source.uvScale[0] == 1 && source.uvScale[1] == 1)
    {
        return std::nullopt;
    }
    const double rotation = source.rotation;
    return UvTransform{
        GfVec2f(source.uvScale[0], source.uvScale[1]), float(GfRadiansToDegrees(rotation)),
        GfVec2f(float(source.uvOffset[0] + std::sin(rotation) * source.uvScale[1]),
                float(1.0 - source.uvOffset[1] - std::cos(rotation) * source.uvScale[1]))};
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
    // The plain source is the fallback for clients without the extensions. A texture with both
    // image extensions uses KTX2. A failure in the chosen image does not fall back.
    const bool basisu = texture.basisuImageIndex.has_value();
    const bool webp = !basisu && texture.webpImageIndex.has_value();
    const auto imageIndex = basisu ? texture.basisuImageIndex
                            : webp ? texture.webpImageIndex
                                   : texture.imageIndex;
    if (!imageIndex)
    {
        error = "texture has no image source";
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
    if (!gltfTexture::LocateImage(asset, *imageIndex, sourcePath, loadBuffer, location, error))
    {
        return false;
    }
    if (basisu && location.format != gltfTexture::ImageFormat::Ktx2)
    {
        error = "KHR_texture_basisu image is not KTX2";
        return false;
    }
    if (webp && location.format != gltfTexture::ImageFormat::Webp)
    {
        error = "EXT_texture_webp image is not WebP";
        return false;
    }
    // glTF allows only PNG and JPEG as a plain source.
    if (!basisu && location.format == gltfTexture::ImageFormat::Ktx2)
    {
        error = "KTX2 image requires KHR_texture_basisu";
        return false;
    }
    if (!webp && location.format == gltfTexture::ImageFormat::Webp)
    {
        error = "WebP image requires EXT_texture_webp";
        return false;
    }
    result.file = location.packaged
                      ? ArJoinPackageRelativePath("./" + TfGetBaseName(sourcePath), location.path)
                      : "./" + location.path;
    const auto texCoord = texCoordOf(info);
    result.primvar = texCoord == 0 ? "st" : "st" + std::to_string(texCoord);
    if (info.transform)
    {
        result.transform = uvTransform(*info.transform);
        // The parser accepts each value, but a sum of very large ones can overflow.
        if (result.transform && !(std::isfinite(result.transform->translation[0]) &&
                                  std::isfinite(result.transform->translation[1])))
        {
            error = "KHR_texture_transform is out of range";
            return false;
        }
    }
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
    const auto hasUvSet = [uvSetCount](const auto& info)
    { return !info || texCoordOf(*info) < uvSetCount; };
    const auto* clearcoat = source.clearcoat ? &*source.clearcoat : nullptr;
    const auto* transmission = source.transmission ? &*source.transmission : nullptr;
    if (!hasUvSet(pbr.baseColorTexture) || !hasUvSet(pbr.metallicRoughnessTexture) ||
        !hasUvSet(source.normalTexture) || !hasUvSet(source.occlusionTexture) ||
        !hasUvSet(source.emissiveTexture) ||
        (clearcoat && (!hasUvSet(clearcoat->clearcoatTexture) ||
                       !hasUvSet(clearcoat->clearcoatRoughnessTexture) ||
                       !hasUvSet(clearcoat->clearcoatNormalTexture))) ||
        (transmission && !hasUvSet(transmission->transmissionTexture)))
    {
        error = "material texture uses a texture coordinate set the primitive does not have";
        return false;
    }
    if (source.normalTexture && !std::isfinite(source.normalTexture->scale))
    {
        error = "material normal scale is non-finite";
        return false;
    }
    if (source.occlusionTexture && !inUnitRange(source.occlusionTexture->strength))
    {
        error = "material occlusion strength is non-finite or out of range";
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
    // The parser has already rejected a value that is not finite.
    if (source.ior != 0 && !(source.ior >= 1))
    {
        error = "material ior is neither 0 nor at least 1";
        return false;
    }
    if (clearcoat && (!inUnitRange(clearcoat->clearcoatFactor) ||
                      !inUnitRange(clearcoat->clearcoatRoughnessFactor)))
    {
        error = "material clearcoat factor or roughness is non-finite or out of range";
        return false;
    }
    if (transmission && !inUnitRange(transmission->transmissionFactor))
    {
        error = "material transmission factor is non-finite or out of range";
        return false;
    }
    auto& material = colors ? _vertexColorMaterials[index] : _materials[index];
    if (!material)
    {
        const auto resolve = [&](const auto& info, std::optional<TextureInput>& texture)
        {
            return !info || resolveTexture(_asset, *info, _sourcePath, _loadBuffer,
                                           texture.emplace(), error);
        };
        // KHR_materials_unlit defines every other texture as a fallback for clients without it.
        // A clearcoat or transmission of factor 0 is disabled, so its textures are not read either.
        const auto factors = pbrFactors(source);
        const bool coated = hasClearcoat(factors);
        const bool transmits = hasTransmission(factors);
        MaterialTextures textures;
        if (!resolve(pbr.baseColorTexture, textures.baseColor) ||
            (!source.unlit && (!resolve(pbr.metallicRoughnessTexture, textures.metallicRoughness) ||
                               !resolve(source.normalTexture, textures.normal) ||
                               !resolve(source.occlusionTexture, textures.occlusion) ||
                               !resolve(source.emissiveTexture, textures.emissive))) ||
            (coated &&
             (!resolve(clearcoat->clearcoatTexture, textures.clearcoat) ||
              !resolve(clearcoat->clearcoatRoughnessTexture, textures.clearcoatRoughness) ||
              !resolve(clearcoat->clearcoatNormalTexture, textures.clearcoatNormal))) ||
            (transmits && !resolve(transmission->transmissionTexture, textures.transmission)))
        {
            return false;
        }
        const auto turnsFrame = [](const std::optional<TextureInput>& normal)
        {
            return normal && normal->transform &&
                   (normal->transform->rotationDegrees != 0.0f ||
                    normal->transform->scale[0] < 0.0f || normal->transform->scale[1] < 0.0f);
        };
        if (turnsFrame(textures.normal) || turnsFrame(textures.clearcoatNormal))
        {
            TF_WARN("glTF material %zu: a MaterialX renderer may derive the normal map's tangent "
                    "frame without its rotation or mirroring by KHR_texture_transform",
                    index);
        }
        if (source.unlit && source.ior != kDefaultIor)
        {
            TF_WARN(
                "glTF material %zu: KHR_materials_ior is ignored, because the material is unlit",
                index);
        }
        if (source.unlit && clearcoat)
        {
            TF_WARN("glTF material %zu: KHR_materials_clearcoat is ignored, because the material "
                    "is unlit",
                    index);
        }
        if (source.unlit && transmission)
        {
            TF_WARN("glTF material %zu: KHR_materials_transmission is ignored, because the "
                    "material is unlit",
                    index);
        }
        if (transmits)
        {
            TF_WARN("glTF material %zu: UsdPreviewSurface has no transmission; its network ignores "
                    "KHR_materials_transmission",
                    index);
        }
        if (textures.clearcoatNormal)
        {
            TF_WARN("glTF material %zu: UsdPreviewSurface has no clearcoat normal; its network "
                    "ignores clearcoatNormalTexture",
                    index);
        }
        if (coated && source.ior != kDefaultIor)
        {
            TF_WARN("glTF material %zu: UsdPreviewSurface computes the clearcoat's Fresnel term "
                    "from the material's ior, not from glTF's 1.5",
                    index);
        }
        if (pbr.baseColorTexture && colors)
        {
            TF_WARN("glTF material %zu: UsdPreviewSurface cannot multiply a texture by vertex "
                    "colors; its network ignores COLOR_0",
                    index);
        }
        material = authorMaterial(
            source, "Material_" + std::to_string(index) + (colors ? "_VertexColor" : ""), _stage,
            _scope, colors != nullptr, textures);
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
