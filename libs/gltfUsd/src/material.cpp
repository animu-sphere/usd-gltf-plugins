// SPDX-License-Identifier: Apache-2.0
#include "material.h"

#include "gltfTexture/image.h"

#include "pxr/base/gf/math.h"
#include "pxr/base/gf/vec2f.h"
#include "pxr/base/gf/vec3f.h"
#include "pxr/base/gf/vec4f.h"
#include "pxr/base/tf/diagnostic.h"
#include "pxr/base/tf/pathUtils.h"
#include "pxr/usd/ar/packageUtils.h"
#include "pxr/usd/sdf/assetPath.h"
#include "pxr/usd/sdf/types.h"
#include "pxr/usd/usdGeom/mesh.h"
#include "pxr/usd/usdGeom/primvarsAPI.h"
#include "pxr/usd/usdGeom/tokens.h"
#include "pxr/usd/usdShade/connectableAPI.h"
#include "pxr/usd/usdShade/materialBindingAPI.h"
#include "pxr/usd/usdShade/nodeGraph.h"
#include "pxr/usd/usdShade/shader.h"
#include "pxr/usd/usdShade/tokens.h"

#include <algorithm>
#include <array>
#include <cmath>
#include <limits>
#include <optional>
#include <string>
#include <type_traits>
#include <utility>

namespace gltfUsd
{
namespace
{

using namespace pxr;

// A STEP keyframe's earlier value is also authored this many time codes before it, as for nodes.
constexpr double HoldTimeCodes = 1e-7;
// How far the lines between an animated input's time samples may stray from its value, relative to
// the larger of 1 and the input's largest component in the segment.
constexpr double SampleTolerance = 1e-4;
// The most pieces a segment is cut into.
constexpr std::size_t MaxPieces = 256;

bool
inUnitRange(float value)
{
    return std::isfinite(value) && value >= 0.0f && value <= 1.0f;
}

// An output of a shader or node graph, which inputs connect to.
struct Port
{
    SdfPath path;
    SdfValueTypeName type;

    explicit operator bool() const { return !path.IsEmpty(); }
};

enum class Variability
{
    // An ordinary input, whose values an animation may sample.
    Varying,
    // An input that gltf_pbr declares uniform.
    Uniform,
    // An input whose value no animation changes, such as a primvar reader's fallback.
    Fixed,
};

// Authors shader networks on a stage. Without a stage, it only records the values that authoring
// would give their inputs, which is how an animation's values are computed by the same functions as
// the static networks.
class Network
{
  public:
    using Values = std::vector<std::pair<SdfPath, VtValue>>;

    explicit Network(UsdStageRefPtr stage = {}) : _stage(std::move(stage)) {}

    // The values given to inputs, in the order authoring gives them, including on a stage.
    Values values;

    [[nodiscard]] SdfPath Graph(const SdfPath& path) const
    {
        return !_stage || UsdShadeNodeGraph::Define(_stage, path) ? path : SdfPath();
    }

    [[nodiscard]] SdfPath Node(const SdfPath& graph, const std::string& name, const char* id) const
    {
        if (graph.IsEmpty())
        {
            return {};
        }
        const auto path = graph.AppendChild(TfToken(name));
        if (!_stage)
        {
            return path;
        }
        const auto shader = UsdShadeShader::Define(_stage, path);
        return shader && shader.CreateIdAttr().Set(TfToken(id)) ? path : SdfPath();
    }

    [[nodiscard]] Port Output(const SdfPath& node, const char* name,
                              const SdfValueTypeName& type) const
    {
        if (node.IsEmpty() ||
            (_stage &&
             !UsdShadeConnectableAPI(_stage->GetPrimAtPath(node)).CreateOutput(TfToken(name), type)))
        {
            return {};
        }
        return {node.AppendProperty(TfToken(UsdShadeTokens->outputs.GetString() + name)), type};
    }

    template <typename T>
    [[nodiscard]] bool Set(const SdfPath& node, const char* name, const SdfValueTypeName& type,
                           const T& value, Variability variability = Variability::Varying)
    {
        if (node.IsEmpty())
        {
            return false;
        }
        if (variability != Variability::Fixed)
        {
            values.emplace_back(
                node.AppendProperty(TfToken(UsdShadeTokens->inputs.GetString() + name)),
                VtValue(value));
        }
        if (!_stage)
        {
            return true;
        }
        const auto input =
            UsdShadeConnectableAPI(_stage->GetPrimAtPath(node)).CreateInput(TfToken(name), type);
        return input &&
               (variability != Variability::Uniform ||
                input.GetAttr().SetVariability(SdfVariabilityUniform)) &&
               input.Set(value);
    }

    // Connects an input, keeping its type if it exists. A connected input's own value is not
    // drawn, so no animation changes it.
    [[nodiscard]] bool Connect(const SdfPath& node, const char* name, const SdfValueTypeName& type,
                               const Port& source)
    {
        if (node.IsEmpty() || !source)
        {
            return false;
        }
        const auto connected =
            node.AppendProperty(TfToken(UsdShadeTokens->inputs.GetString() + name));
        std::erase_if(values, [&](const auto& value) { return value.first == connected; });
        if (!_stage)
        {
            return true;
        }
        const UsdShadeConnectableAPI connectable(_stage->GetPrimAtPath(node));
        auto input = connectable.GetInput(TfToken(name));
        if (!input)
        {
            input = connectable.CreateInput(TfToken(name), type);
        }
        return input && input.ConnectToSource(source.path);
    }

    // A KHR_xmp_json_ld packet, if any, as the prim's assetInfo["xmp"].
    [[nodiscard]] bool SetXmp(const SdfPath& node, const VtValue* xmp) const
    {
        if (!_stage || !xmp)
        {
            return true;
        }
        static const TfToken key("xmp");
        const auto prim = _stage->GetPrimAtPath(node);
        prim.SetAssetInfoByKey(key, *xmp);
        return prim.HasAuthoredAssetInfoKey(key);
    }

    [[nodiscard]] bool SetColorSpace(const SdfPath& node, const char* name,
                                     const TfToken& colorSpace) const
    {
        if (!_stage)
        {
            return true;
        }
        const auto input = UsdShadeConnectableAPI(_stage->GetPrimAtPath(node)).GetInput(TfToken(name));
        if (!input)
        {
            return false;
        }
        input.GetAttr().SetColorSpace(colorSpace);
        return true;
    }

    // Connects a material's surface output, for a render context, through the graph's output.
    [[nodiscard]] bool ConnectSurface(const SdfPath& material, const TfToken& context,
                                      const SdfPath& graph, const SdfPath& shader,
                                      const TfToken& outputName) const
    {
        if (material.IsEmpty() || graph.IsEmpty() || shader.IsEmpty())
        {
            return false;
        }
        if (!_stage)
        {
            return true;
        }
        const auto shaderOutput = UsdShadeShader(_stage->GetPrimAtPath(shader))
                                      .CreateOutput(outputName, SdfValueTypeNames->Token);
        const auto graphOutput = UsdShadeNodeGraph(_stage->GetPrimAtPath(graph))
                                     .CreateOutput(TfToken("surface"), SdfValueTypeNames->Token);
        const auto materialOutput =
            UsdShadeMaterial(_stage->GetPrimAtPath(material)).CreateSurfaceOutput(context);
        return shaderOutput && graphOutput && materialOutput &&
               graphOutput.ConnectToSource(shaderOutput) &&
               materialOutput.ConnectToSource(graphOutput);
    }

  private:
    UsdStageRefPtr _stage;
};

struct PbrFactors
{
    GfVec3f baseColor;
    GfVec3f emissive;
    float emissiveStrength;
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
    // KHR_materials_volume: a thickness of 0, its default, is a thin material, and an infinite
    // attenuation distance, its default, attenuates nothing.
    float thickness;
    float attenuationDistance;
    GfVec3f attenuationColor;
    // KHR_materials_dispersion, 20 over the Abbe number; 0, its default, is none.
    float dispersion;
    // KHR_materials_specular, 1 and white without it, which change nothing.
    float specular;
    GfVec3f specularColor;
    // KHR_materials_sheen, black and 0 without it: a black color disables the layer.
    GfVec3f sheenColor;
    float sheenRoughness;
    // KHR_materials_iridescence: strength, film ior and thickness range in nanometers.
    float iridescence;
    float iridescenceIor;
    float iridescenceMinimum;
    float iridescenceMaximum;
    // KHR_materials_anisotropy: strength and counter-clockwise rotation in radians.
    float anisotropy;
    float anisotropyRotation;
    // Added to the emitted base color of an unlit point or line without NORMAL, which glTF draws
    // as the sum of its base color and emissive values.
    GfVec3f unlitEmission = GfVec3f(0);
    // The properties that an animation changes. What each controls is authored whatever its value,
    // so that the networks have the same inputs at every time.
    AnimatedProperties animated;
    // Whether an animation changes unlitEmission.
    bool unlitEmissionAnimated = false;

    [[nodiscard]] bool animates(MaterialField field) const { return animated.Has(field); }
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
            float(source.emissiveStrength),
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
            source.transmission ? float(source.transmission->transmissionFactor) : 0.0f,
            source.volume ? float(source.volume->thicknessFactor) : 0.0f,
            source.volume ? float(source.volume->attenuationDistance)
                          : std::numeric_limits<float>::infinity(),
            source.volume
                ? GfVec3f(source.volume->attenuationColor[0], source.volume->attenuationColor[1],
                          source.volume->attenuationColor[2])
                : GfVec3f(1),
            float(source.dispersion),
            source.specular ? float(source.specular->specularFactor) : 1.0f,
            source.specular ? GfVec3f(source.specular->specularColorFactor[0],
                                      source.specular->specularColorFactor[1],
                                      source.specular->specularColorFactor[2])
                            : GfVec3f(1),
            source.sheen
                ? GfVec3f(source.sheen->sheenColorFactor[0], source.sheen->sheenColorFactor[1],
                          source.sheen->sheenColorFactor[2])
                : GfVec3f(0),
            source.sheen ? float(source.sheen->sheenRoughnessFactor) : 0.0f,
            source.iridescence ? float(source.iridescence->iridescenceFactor) : 0.0f,
            source.iridescence ? float(source.iridescence->iridescenceIor) : 1.3f,
            source.iridescence ? float(source.iridescence->iridescenceThicknessMinimum) : 100.0f,
            source.iridescence ? float(source.iridescence->iridescenceThicknessMaximum) : 400.0f,
            source.anisotropy ? float(source.anisotropy->anisotropyStrength) : 0.0f,
            source.anisotropy ? float(std::remainder(double(source.anisotropy->anisotropyRotation),
                                                     2.0 * std::acos(-1.0)))
                              : 0.0f};
}

// The factors of a material, with the properties that animations change. An animated rotation is
// not reduced modulo a turn, so that its samples do not wrap.
PbrFactors
pbrFactors(const fastgltf::Material& source, const AnimatedProperties& animated)
{
    auto factors = pbrFactors(source);
    factors.animated = animated;
    if (animated.Has(MaterialField::AnisotropyRotation) && source.anisotropy)
    {
        factors.anisotropyRotation = float(source.anisotropy->anisotropyRotation);
    }
    return factors;
}

// A point or line without NORMAL draws an unlit sum of its base color and emissive factor.
void
adjustForUse(PbrFactors& factors, MaterialUse use, bool emissiveTexture)
{
    if (use != MaterialUse::Unlit)
    {
        return;
    }
    factors.unlit = true;
    // The factor is the whole emission only without a texture to multiply it.
    if (!emissiveTexture)
    {
        factors.unlitEmission = factors.emissive * factors.emissiveStrength;
        factors.unlitEmissionAnimated = factors.animates(MaterialField::Emissive) ||
                                        factors.animates(MaterialField::EmissiveStrength);
    }
    // gltf_pbr's strength would scale the emitted base color too.
    factors.emissiveStrength = 1.0f;
    factors.animated.fields.reset(std::size_t(MaterialField::EmissiveStrength));
}

// glTF disables the clearcoat layer at a factor of 0, and an unlit material reflects nothing.
bool
hasClearcoat(const PbrFactors& factors)
{
    return !factors.unlit &&
           (factors.clearcoat > 0.0f || factors.animates(MaterialField::Clearcoat));
}

// A transmission of 0 is none, and an unlit material transmits nothing.
bool
hasTransmission(const PbrFactors& factors)
{
    return !factors.unlit &&
           (factors.transmission > 0.0f || factors.animates(MaterialField::Transmission));
}

// A volume is the medium that transmitted light passes through, so a material that transmits none
// has no use for it, and a thickness of 0 is a thin material.
bool
hasVolume(const PbrFactors& factors)
{
    return hasTransmission(factors) &&
           (factors.thickness > 0.0f || factors.animates(MaterialField::Thickness));
}

// glTF disperses only the light that a volume refracts, so a thin material has no use for it.
bool
hasDispersion(const PbrFactors& factors)
{
    return hasVolume(factors) &&
           (factors.dispersion > 0.0f || factors.animates(MaterialField::Dispersion));
}

// glTF disables the sheen layer at a black color factor, and an unlit material reflects nothing.
bool
hasSheen(const PbrFactors& factors)
{
    return !factors.unlit &&
           (factors.sheenColor != GfVec3f(0) || factors.animates(MaterialField::SheenColor));
}

// The sheen roughness is the factor times its texture, and gltf_pbr's default is glTF's 0.
bool
hasSheenRoughness(const PbrFactors& factors)
{
    return hasSheen(factors) &&
           (factors.sheenRoughness != 0.0f || factors.animates(MaterialField::SheenRoughness));
}

// A strength of 0 disables the film, and an unlit material reflects nothing.
bool
hasIridescence(const PbrFactors& factors)
{
    return !factors.unlit &&
           (factors.iridescence > 0.0f || factors.animates(MaterialField::Iridescence));
}

bool
hasAnisotropy(const PbrFactors& factors)
{
    return !factors.unlit &&
           (factors.anisotropy > 0.0f || factors.animates(MaterialField::Anisotropy));
}

// The ior to author, if it is not the shaders' default. An unlit material reflects nothing, so
// its ior has no effect.
std::optional<float>
authoredIor(const PbrFactors& factors)
{
    if (factors.unlit || (factors.ior == kDefaultIor && !factors.animates(MaterialField::Ior)))
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

// Names a material bound for a use other than the full one.
const char*
materialUseSuffix(MaterialUse use)
{
    switch (use)
    {
    case MaterialUse::Full:
        break;
    case MaterialUse::WithoutNormalTextures:
        return "_NoNormalTexture";
    case MaterialUse::Unlit:
        return "_Unlit";
    }
    return "";
}

// A UsdPreviewSurface threshold of 0 means no masking, whereas every fragment of
// a glTF MASK material with cutoff 0 passes and is fully opaque.
bool
previewUsesOpacity(const PbrFactors& factors)
{
    return factors.alphaMode == fastgltf::AlphaMode::Blend ||
           (factors.alphaMode == fastgltf::AlphaMode::Mask &&
            (factors.alphaCutoff > 0.0f || factors.animates(MaterialField::AlphaCutoff)));
}

// UsdPreviewSurface has no multiply node, so a factor other than 1 is applied to
// a primvar on each mesh. The product interpolates exactly like COLOR_0. Names
// these graphs read avoid colons, which break MaterialX shaders in Storm. An animation does not
// change these products.
const char*
previewColorPrimvar(const PbrFactors& factors)
{
    return factors.baseColor == GfVec3f(1) && factors.unlitEmission == GfVec3f(0)
               ? "displayColor"
               : "gltf_previewBaseColor";
}

const char*
previewAlphaPrimvar(const PbrFactors& factors)
{
    return factors.alpha == 1.0f ? "gltf_colorAlpha" : "gltf_previewAlpha";
}

template <typename T>
Port
authorPrimvarReader(Network& net, const SdfPath& graph, const char* name, const char* id,
                    const char* primvarInput, const char* primvar, const char* fallbackInput,
                    const SdfValueTypeName& type, const T& fallback, const char* output)
{
    const auto reader = net.Node(graph, name, id);
    if (!net.Set(reader, primvarInput, SdfValueTypeNames->String, std::string(primvar),
                 Variability::Fixed) ||
        !net.Set(reader, fallbackInput, type, fallback, Variability::Fixed))
    {
        return {};
    }
    return net.Output(reader, output, type);
}

bool
connectPreviewVertexColor(Network& net, const SdfPath& graph, const SdfPath& surface,
                          const PbrFactors& factors)
{
    const auto color =
        authorPrimvarReader(net, graph, "displayColorReader", "UsdPrimvarReader_float3", "varname",
                            previewColorPrimvar(factors), "fallback", SdfValueTypeNames->Float3,
                            factors.baseColor + factors.unlitEmission, "result");
    if (!color || !net.Connect(surface, previewColorInput(factors), SdfValueTypeNames->Color3f,
                               color))
    {
        return false;
    }
    if (!previewUsesOpacity(factors))
    {
        return true;
    }
    // A primitive without vertex alpha has no primvar and uses the factor alone.
    const auto alpha = authorPrimvarReader(net, graph, "colorAlphaReader", "UsdPrimvarReader_float",
                                           "varname", previewAlphaPrimvar(factors), "fallback",
                                           SdfValueTypeNames->Float, factors.alpha, "result");
    return alpha && net.Connect(surface, "opacity", SdfValueTypeNames->Float, alpha);
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
    // The glTF image that `file` is made from, and its KHR_xmp_json_ld packet or null.
    std::size_t image = 0;
    const VtValue* xmp = nullptr;
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
    // Read by MaterialX only: UsdPreviewSurface has no volume.
    std::optional<TextureInput> thickness;
    // Read by MaterialX only: UsdPreviewSurface's metallic workflow has no specular weight or color.
    std::optional<TextureInput> specular;
    std::optional<TextureInput> specularColor;
    // Read by MaterialX only: UsdPreviewSurface has no sheen.
    std::optional<TextureInput> sheenColor;
    std::optional<TextureInput> sheenRoughness;
    // Read by MaterialX only: UsdPreviewSurface has no thin film.
    std::optional<TextureInput> iridescence;
    std::optional<TextureInput> iridescenceThickness;
    std::optional<TextureInput> anisotropy;

    std::optional<TextureInput>& operator[](TextureSlot slot)
    {
        switch (slot)
        {
        case TextureSlot::BaseColor:
            return baseColor;
        case TextureSlot::MetallicRoughness:
            return metallicRoughness;
        case TextureSlot::Normal:
            return normal;
        case TextureSlot::Occlusion:
            return occlusion;
        case TextureSlot::Emissive:
            return emissive;
        case TextureSlot::Clearcoat:
            return clearcoat;
        case TextureSlot::ClearcoatRoughness:
            return clearcoatRoughness;
        case TextureSlot::ClearcoatNormal:
            return clearcoatNormal;
        case TextureSlot::Transmission:
            return transmission;
        case TextureSlot::Thickness:
            return thickness;
        case TextureSlot::Specular:
            return specular;
        case TextureSlot::SpecularColor:
            return specularColor;
        case TextureSlot::SheenColor:
            return sheenColor;
        case TextureSlot::SheenRoughness:
            return sheenRoughness;
        case TextureSlot::Iridescence:
            return iridescence;
        case TextureSlot::IridescenceThickness:
            return iridescenceThickness;
        case TextureSlot::Anisotropy:
        case TextureSlot::Count:
            break;
        }
        return anisotropy;
    }
};

// gltf_pbr's specular weight and color are glTF's: the weight scales a dielectric's F0 and F90,
// and the color tints its F0. Each is authored only where it differs from the default of 1, and
// an unlit material reflects nothing.
bool
hasSpecularWeight(const PbrFactors& factors, const MaterialTextures& textures)
{
    return !factors.unlit && (factors.specular != 1.0f || textures.specular ||
                              factors.animates(MaterialField::Specular));
}

bool
hasSpecularColor(const PbrFactors& factors, const MaterialTextures& textures)
{
    return !factors.unlit && (factors.specularColor != GfVec3f(1) || textures.specularColor ||
                              factors.animates(MaterialField::SpecularColor));
}

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

Port
authorPreviewTransform(Network& net, const SdfPath& graph, const std::string& image,
                       const Port& st, const UvTransform& transform)
{
    const auto node = net.Node(graph, image + "Transform", "UsdTransform2d");
    return net.Connect(node, "in", SdfValueTypeNames->Float2, st) &&
                   net.Set(node, "scale", SdfValueTypeNames->Float2, transform.scale) &&
                   net.Set(node, "rotation", SdfValueTypeNames->Float, transform.rotationDegrees) &&
                   net.Set(node, "translation", SdfValueTypeNames->Float2, transform.translation)
               ? net.Output(node, "result", SdfValueTypeNames->Float2)
               : Port();
}

// UsdUVTexture computes texture * scale + bias, which expresses glTF's factors
// and occlusion strength exactly. A bias of 0 is left out unless an animation changes it.
SdfPath
authorPreviewTexture(Network& net, const SdfPath& graph, const char* name,
                     const TextureInput& texture, const char* colorSpace, const GfVec4f& scale,
                     const GfVec4f& bias = GfVec4f(0), bool animatedBias = false)
{
    auto st = authorPrimvarReader(net, graph, texCoordReaderName(texture).c_str(),
                                  "UsdPrimvarReader_float2", "varname", texture.primvar.c_str(),
                                  "fallback", SdfValueTypeNames->Float2, GfVec2f(0), "result");
    if (st && texture.transform)
    {
        st = authorPreviewTransform(net, graph, name, st, *texture.transform);
    }
    const auto image = st ? net.Node(graph, name, "UsdUVTexture") : SdfPath();
    if (!net.Connect(image, "st", SdfValueTypeNames->Float2, st) ||
        !net.SetXmp(image, texture.xmp) ||
        !net.Set(image, "file", SdfValueTypeNames->Asset, SdfAssetPath(texture.file),
                 Variability::Fixed) ||
        !net.Set(image, "sourceColorSpace", SdfValueTypeNames->Token, TfToken(colorSpace),
                 Variability::Fixed) ||
        !net.Set(image, "wrapS", SdfValueTypeNames->Token, TfToken(previewWrap(texture.wrapS)),
                 Variability::Fixed) ||
        !net.Set(image, "wrapT", SdfValueTypeNames->Token, TfToken(previewWrap(texture.wrapT)),
                 Variability::Fixed) ||
        !net.Set(image, "scale", SdfValueTypeNames->Float4, scale) ||
        ((bias != GfVec4f(0) || animatedBias) &&
         !net.Set(image, "bias", SdfValueTypeNames->Float4, bias)))
    {
        return {};
    }
    return image;
}

bool
connectPreviewOutput(Network& net, const SdfPath& image, const char* output,
                     const SdfValueTypeName& type, const SdfPath& surface, const char* input)
{
    const auto source = net.Output(image, output, type);
    return source && net.Connect(surface, input, type, source);
}

bool
connectPreviewBaseColor(Network& net, const SdfPath& graph, const SdfPath& surface,
                        const PbrFactors& factors, const TextureInput& texture)
{
    const auto& emission = factors.unlitEmission;
    const auto image = authorPreviewTexture(
        net, graph, "baseColorTexture", texture, "sRGB",
        GfVec4f(factors.baseColor[0], factors.baseColor[1], factors.baseColor[2], factors.alpha),
        GfVec4f(emission[0], emission[1], emission[2], 0), factors.unlitEmissionAnimated);
    return !image.IsEmpty() &&
           connectPreviewOutput(net, image, "rgb", SdfValueTypeNames->Float3, surface,
                                previewColorInput(factors)) &&
           (!previewUsesOpacity(factors) ||
            connectPreviewOutput(net, image, "a", SdfValueTypeNames->Float, surface, "opacity"));
}

bool
connectPreviewMetallicRoughness(Network& net, const SdfPath& graph, const SdfPath& surface,
                                const PbrFactors& factors, const TextureInput& texture)
{
    const auto image = authorPreviewTexture(net, graph, "metallicRoughnessTexture", texture, "raw",
                                            GfVec4f(1, factors.roughness, factors.metallic, 1));
    return !image.IsEmpty() &&
           connectPreviewOutput(net, image, "g", SdfValueTypeNames->Float, surface, "roughness") &&
           connectPreviewOutput(net, image, "b", SdfValueTypeNames->Float, surface, "metallic");
}

// Maps the image to a tangent-space vector, with glTF's scale on X and Y only.
bool
connectPreviewNormal(Network& net, const SdfPath& graph, const SdfPath& surface,
                     const PbrFactors& factors, const TextureInput& texture)
{
    const float scale = factors.normalScale;
    const auto image =
        authorPreviewTexture(net, graph, "normalTexture", texture, "raw",
                             GfVec4f(2 * scale, 2 * scale, 2, 1), GfVec4f(-scale, -scale, -1, 0));
    return !image.IsEmpty() &&
           net.Set(surface, "normal", SdfValueTypeNames->Normal3f, GfVec3f(0, 0, 1),
                   Variability::Fixed) &&
           connectPreviewOutput(net, image, "rgb", SdfValueTypeNames->Float3, surface, "normal");
}

bool
connectPreviewOcclusion(Network& net, const SdfPath& graph, const SdfPath& surface,
                        const PbrFactors& factors, const TextureInput& texture)
{
    const float strength = factors.occlusionStrength;
    const auto image = authorPreviewTexture(net, graph, "occlusionTexture", texture, "raw",
                                            GfVec4f(strength), GfVec4f(1 - strength),
                                            factors.animates(MaterialField::OcclusionStrength));
    return !image.IsEmpty() &&
           net.Set(surface, "occlusion", SdfValueTypeNames->Float, 1.0f, Variability::Fixed) &&
           connectPreviewOutput(net, image, "r", SdfValueTypeNames->Float, surface, "occlusion");
}

bool
connectPreviewEmissive(Network& net, const SdfPath& graph, const SdfPath& surface,
                       const PbrFactors& factors, const TextureInput& texture)
{
    const auto color = factors.emissive * factors.emissiveStrength;
    const auto image = authorPreviewTexture(net, graph, "emissiveTexture", texture, "sRGB",
                                            GfVec4f(color[0], color[1], color[2], 1));
    return !image.IsEmpty() && connectPreviewOutput(net, image, "rgb", SdfValueTypeNames->Float3,
                                                    surface, "emissiveColor");
}

// glTF's clearcoat is R and its roughness G, each times its factor.
bool
connectPreviewClearcoat(Network& net, const SdfPath& graph, const SdfPath& surface,
                        const PbrFactors& factors, const MaterialTextures& textures)
{
    if (textures.clearcoat)
    {
        const auto image = authorPreviewTexture(net, graph, "clearcoatTexture", *textures.clearcoat,
                                                "raw", GfVec4f(factors.clearcoat, 1, 1, 1));
        if (image.IsEmpty() ||
            !connectPreviewOutput(net, image, "r", SdfValueTypeNames->Float, surface, "clearcoat"))
        {
            return false;
        }
    }
    if (textures.clearcoatRoughness)
    {
        const auto image = authorPreviewTexture(net, graph, "clearcoatRoughnessTexture",
                                                *textures.clearcoatRoughness, "raw",
                                                GfVec4f(1, factors.clearcoatRoughness, 1, 1));
        return !image.IsEmpty() && connectPreviewOutput(net, image, "g", SdfValueTypeNames->Float,
                                                        surface, "clearcoatRoughness");
    }
    return true;
}

bool
connectPreviewTextures(Network& net, const SdfPath& graph, const SdfPath& surface,
                       const PbrFactors& factors, const MaterialTextures& textures)
{
    return (!textures.metallicRoughness ||
            connectPreviewMetallicRoughness(net, graph, surface, factors,
                                            *textures.metallicRoughness)) &&
           (!textures.normal ||
            connectPreviewNormal(net, graph, surface, factors, *textures.normal)) &&
           (!textures.occlusion ||
            connectPreviewOcclusion(net, graph, surface, factors, *textures.occlusion)) &&
           (!textures.emissive ||
            connectPreviewEmissive(net, graph, surface, factors, *textures.emissive)) &&
           (!hasClearcoat(factors) ||
            connectPreviewClearcoat(net, graph, surface, factors, textures));
}

// `second` is a constant, or another node's output.
template <typename T>
Port
authorMtlxMultiply(Network& net, const SdfPath& graph, const std::string& name, const char* id,
                   const SdfValueTypeName& type, const Port& first, const T& second)
{
    const auto multiply = net.Node(graph, name, id);
    if (!net.Connect(multiply, "in1", type, first))
    {
        return {};
    }
    if constexpr (std::is_same_v<T, Port>)
    {
        if (!net.Connect(multiply, "in2", type, second))
        {
            return {};
        }
    }
    else if (!net.Set(multiply, "in2", type, second))
    {
        return {};
    }
    return net.Output(multiply, "out", type);
}

// place2d cannot express a scale of 0, because it divides by it. rotate2d turns clockwise.
Port
authorMtlxTransform(Network& net, const SdfPath& graph, const std::string& image, const Port& st,
                    const UvTransform& transform)
{
    const auto type = SdfValueTypeNames->Float2;
    const auto scaled = authorMtlxMultiply(net, graph, image + "Scale", "ND_multiply_vector2", type,
                                           st, transform.scale);
    const auto rotate =
        scaled ? net.Node(graph, image + "Rotate", "ND_rotate2d_vector2") : SdfPath();
    if (!net.Connect(rotate, "in", type, scaled) ||
        !net.Set(rotate, "amount", SdfValueTypeNames->Float, -transform.rotationDegrees))
    {
        return {};
    }
    const auto translate = net.Node(graph, image + "Translate", "ND_add_vector2");
    return net.Connect(translate, "in1", type, net.Output(rotate, "out", type)) &&
                   net.Set(translate, "in2", type, transform.translation)
               ? net.Output(translate, "out", type)
               : Port();
}

// Non-color data is a vector image, which no color space transform applies to.
Port
authorMtlxImage(Network& net, const SdfPath& graph, const std::string& name,
                const TextureInput& texture, bool color)
{
    auto st = authorPrimvarReader(net, graph, texCoordReaderName(texture).c_str(),
                                  "ND_geompropvalue_vector2", "geomprop", texture.primvar.c_str(),
                                  "default", SdfValueTypeNames->Float2, GfVec2f(0), "out");
    if (st && texture.transform)
    {
        st = authorMtlxTransform(net, graph, name, st, *texture.transform);
    }
    const auto type = color ? SdfValueTypeNames->Color4f : SdfValueTypeNames->Float4;
    const auto image =
        st ? net.Node(graph, name, color ? "ND_image_color4" : "ND_image_vector4") : SdfPath();
    if (!net.Connect(image, "texcoord", SdfValueTypeNames->Float2, st) ||
        !net.SetXmp(image, texture.xmp) ||
        !net.Set(image, "file", SdfValueTypeNames->Asset, SdfAssetPath(texture.file),
                 Variability::Fixed) ||
        !net.Set(image, "default", type, GfVec4f(0, 0, 0, 1), Variability::Fixed) ||
        !net.Set(image, "uaddressmode", SdfValueTypeNames->String,
                 std::string(mtlxWrap(texture.wrapS)), Variability::Fixed) ||
        !net.Set(image, "vaddressmode", SdfValueTypeNames->String,
                 std::string(mtlxWrap(texture.wrapT)), Variability::Fixed) ||
        (color && !net.SetColorSpace(image, "file", TfToken("srgb_texture"))))
    {
        return {};
    }
    return net.Output(image, "out", type);
}

Port
authorMtlxImageChannel(Network& net, const SdfPath& graph, const std::string& name, const char* id,
                       const Port& image, const SdfValueTypeName& type)
{
    const auto node = net.Node(graph, name, id);
    return net.Connect(node, "in", image.type, image) ? net.Output(node, "out", type) : Port();
}

Port
authorMtlxImageComponent(Network& net, const SdfPath& graph, const std::string& name,
                         const Port& image, int index)
{
    const bool color = image.type == SdfValueTypeNames->Color4f;
    const auto output =
        authorMtlxImageChannel(net, graph, name, color ? "ND_extract_color4" : "ND_extract_vector4",
                               image, SdfValueTypeNames->Float);
    return output && net.Set(output.path.GetPrimPath(), "index", SdfValueTypeNames->Int, index,
                             Variability::Fixed)
               ? output
               : Port();
}

// Connects texture * vertex value * factor + addend, leaving out the terms that are absent. A
// factor of 1 and an addend of 0 are left out unless an animation changes them.
template <typename T>
bool
connectMtlxInput(Network& net, const SdfPath& graph, const SdfPath& surface, const char* input,
                 Port source, const Port& vertex, const char* vertexName, const std::string& factorName,
                 const char* multiplyId, const SdfValueTypeName& type, const T& factor,
                 bool animatedFactor, const T& addend = T(0), const char* addName = nullptr,
                 const char* addId = nullptr, bool animatedAddend = false)
{
    if (source && vertex)
    {
        source = authorMtlxMultiply(net, graph, vertexName, multiplyId, type, source, vertex);
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
    if (factor != T(1) || animatedFactor)
    {
        source = authorMtlxMultiply(net, graph, factorName, multiplyId, type, source, factor);
    }
    if (source && (addend != T(0) || animatedAddend))
    {
        source = authorMtlxMultiply(net, graph, addName, addId, type, source, addend);
    }
    return source && net.Connect(surface, input, type, source);
}

bool
connectMtlxInputs(Network& net, const SdfPath& graph, const SdfPath& surface,
                  const PbrFactors& factors, bool vertexColor, const TextureInput* texture)
{
    const bool usesAlpha = factors.alphaMode != fastgltf::AlphaMode::Opaque;
    Port imageColor;
    Port imageAlpha;
    if (texture)
    {
        const auto image = authorMtlxImage(net, graph, "baseColorImage", *texture, true);
        if (!image)
        {
            return false;
        }
        imageColor = authorMtlxImageChannel(net, graph, "baseColorRgb", "ND_convert_color4_color3",
                                            image, SdfValueTypeNames->Color3f);
        if (!imageColor)
        {
            return false;
        }
        if (usesAlpha)
        {
            imageAlpha = authorMtlxImageComponent(net, graph, "baseColorA", image, 3);
            if (!imageAlpha)
            {
                return false;
            }
        }
    }
    Port vertexColorValue;
    Port vertexAlpha;
    if (vertexColor)
    {
        vertexColorValue = authorPrimvarReader(
            net, graph, "displayColorReader", "ND_geompropvalue_color3", "geomprop", "displayColor",
            "default", SdfValueTypeNames->Color3f, GfVec3f(1), "out");
        if (!vertexColorValue)
        {
            return false;
        }
        if (usesAlpha)
        {
            vertexAlpha = authorPrimvarReader(net, graph, "colorAlphaReader",
                                              "ND_geompropvalue_float", "geomprop",
                                              "gltf_colorAlpha", "default",
                                              SdfValueTypeNames->Float, 1.0f, "out");
            if (!vertexAlpha)
            {
                return false;
            }
        }
    }
    const bool animated = factors.animates(MaterialField::BaseColor);
    return connectMtlxInput(net, graph, surface, mtlxColorInput(factors), imageColor,
                            vertexColorValue, "baseColorVertex", "baseColorFactor",
                            "ND_multiply_color3", SdfValueTypeNames->Color3f, factors.baseColor,
                            animated, factors.unlitEmission, "emissiveAdd", "ND_add_color3",
                            factors.unlitEmissionAnimated) &&
           connectMtlxInput(net, graph, surface, "alpha", imageAlpha, vertexAlpha, "alphaVertex",
                            "alphaFactor", "ND_multiply_float", SdfValueTypeNames->Float,
                            factors.alpha, animated);
}

bool
connectMtlxMetallicRoughness(Network& net, const SdfPath& graph, const SdfPath& surface,
                             const PbrFactors& factors, const TextureInput& texture)
{
    const auto image = authorMtlxImage(net, graph, "metallicRoughnessImage", texture, false);
    if (!image)
    {
        return false;
    }
    const auto roughness = authorMtlxImageComponent(net, graph, "roughnessChannel", image, 1);
    const auto metallic = authorMtlxImageComponent(net, graph, "metallicChannel", image, 2);
    return roughness && metallic &&
           connectMtlxInput(net, graph, surface, "roughness", roughness, Port(), nullptr,
                            "roughnessFactor", "ND_multiply_float", SdfValueTypeNames->Float,
                            factors.roughness, factors.animates(MaterialField::Roughness)) &&
           connectMtlxInput(net, graph, surface, "metallic", metallic, Port(), nullptr,
                            "metallicFactor", "ND_multiply_float", SdfValueTypeNames->Float,
                            factors.metallic, factors.animates(MaterialField::Metallic));
}

// `name` prefixes the nodes, and `input` is the surface's normal input.
bool
connectMtlxNormal(Network& net, const SdfPath& graph, const SdfPath& surface,
                  const std::string& name, const char* input, float scale, bool animatedScale,
                  const TextureInput& texture)
{
    const auto image = authorMtlxImage(net, graph, name + "Image", texture, false);
    const auto xyz =
        image ? authorMtlxImageChannel(net, graph, name + "Xyz", "ND_convert_vector4_vector3",
                                       image, SdfValueTypeNames->Vector3f)
              : Port();
    const auto map = xyz ? net.Node(graph, name + "Map", "ND_normalmap_float") : SdfPath();
    return net.Connect(map, "in", SdfValueTypeNames->Vector3f, xyz) &&
           ((scale == 1.0f && !animatedScale) ||
            net.Set(map, "scale", SdfValueTypeNames->Float, scale)) &&
           net.Connect(surface, input, SdfValueTypeNames->Vector3f,
                       net.Output(map, "out", SdfValueTypeNames->Vector3f));
}

// glTF's 1 + strength * (occlusion - 1) is a mix from 1 to the image.
bool
connectMtlxOcclusion(Network& net, const SdfPath& graph, const SdfPath& surface,
                     const PbrFactors& factors, const TextureInput& texture)
{
    const auto image = authorMtlxImage(net, graph, "occlusionImage", texture, false);
    auto source = image ? authorMtlxImageComponent(net, graph, "occlusionChannel", image, 0) : Port();
    if (!source ||
        !net.Set(surface, "occlusion", SdfValueTypeNames->Float, 1.0f, Variability::Fixed))
    {
        return false;
    }
    if (factors.occlusionStrength != 1.0f || factors.animates(MaterialField::OcclusionStrength))
    {
        const auto mix = net.Node(graph, "occlusionStrength", "ND_mix_float");
        if (!net.Connect(mix, "fg", SdfValueTypeNames->Float, source) ||
            !net.Set(mix, "bg", SdfValueTypeNames->Float, 1.0f, Variability::Fixed) ||
            !net.Set(mix, "mix", SdfValueTypeNames->Float, factors.occlusionStrength))
        {
            return false;
        }
        source = net.Output(mix, "out", SdfValueTypeNames->Float);
    }
    return source && net.Connect(surface, "occlusion", SdfValueTypeNames->Float, source);
}

// The color of a color image times its factor.
bool
connectMtlxColor(Network& net, const SdfPath& graph, const SdfPath& surface,
                 const std::string& name, const char* input, const GfVec3f& factor,
                 bool animatedFactor, const TextureInput& texture)
{
    const auto image = authorMtlxImage(net, graph, name + "Image", texture, true);
    const auto rgb =
        image ? authorMtlxImageChannel(net, graph, name + "Rgb", "ND_convert_color4_color3", image,
                                       SdfValueTypeNames->Color3f)
              : Port();
    return rgb && connectMtlxInput(net, graph, surface, input, rgb, Port(), nullptr,
                                   name + "Factor", "ND_multiply_color3",
                                   SdfValueTypeNames->Color3f, factor, animatedFactor);
}

// One channel of a non-color image times its factor.
bool
connectMtlxChannel(Network& net, const SdfPath& graph, const SdfPath& surface,
                   const std::string& name, const char* input, int index, float factor,
                   bool animatedFactor, const TextureInput& texture)
{
    const auto image = authorMtlxImage(net, graph, name + "Image", texture, false);
    const auto channel =
        image ? authorMtlxImageComponent(net, graph, name + "Channel", image, index) : Port();
    return channel && connectMtlxInput(net, graph, surface, input, channel, Port(), nullptr,
                                       name + "Factor", "ND_multiply_float",
                                       SdfValueTypeNames->Float, factor, animatedFactor);
}

// glTF uses the G channel to interpolate the thickness range, which may be descending.
bool
connectMtlxIridescenceThickness(Network& net, const SdfPath& graph, const SdfPath& surface,
                                const PbrFactors& factors, const TextureInput& texture)
{
    const auto image = authorMtlxImage(net, graph, "iridescenceThicknessImage", texture, false);
    const auto channel =
        image ? authorMtlxImageComponent(net, graph, "iridescenceThicknessChannel", image, 1)
              : Port();
    const auto mix =
        channel ? net.Node(graph, "iridescenceThicknessRange", "ND_mix_float") : SdfPath();
    return net.Connect(mix, "mix", SdfValueTypeNames->Float, channel) &&
           net.Set(mix, "bg", SdfValueTypeNames->Float, factors.iridescenceMinimum) &&
           net.Set(mix, "fg", SdfValueTypeNames->Float, factors.iridescenceMaximum) &&
           net.Connect(surface, "iridescence_thickness", SdfValueTypeNames->Float,
                       net.Output(mix, "out", SdfValueTypeNames->Float));
}

// glTF's linear B is strength, and remapped RG supplies a direction in the renderer's frame.
bool
connectMtlxAnisotropy(Network& net, const SdfPath& graph, const SdfPath& surface,
                      const PbrFactors& factors, const TextureInput& texture)
{
    const auto float1 = SdfValueTypeNames->Float;
    const auto image = authorMtlxImage(net, graph, "anisotropyImage", texture, false);
    const auto strength =
        image ? authorMtlxImageComponent(net, graph, "anisotropyChannel", image, 2) : Port();
    const auto scaled =
        image ? authorMtlxMultiply(net, graph, "anisotropyDirectionScale", "ND_multiply_vector4",
                                   SdfValueTypeNames->Float4, image, GfVec4f(2))
              : Port();
    const auto remap =
        scaled ? net.Node(graph, "anisotropyDirection", "ND_subtract_vector4") : SdfPath();
    if (!strength || !net.Connect(remap, "in1", SdfValueTypeNames->Float4, scaled) ||
        !net.Set(remap, "in2", SdfValueTypeNames->Float4, GfVec4f(1), Variability::Fixed))
    {
        return false;
    }
    const auto direction = net.Output(remap, "out", SdfValueTypeNames->Float4);
    const auto x = authorMtlxImageComponent(net, graph, "anisotropyX", direction, 0);
    const auto y = authorMtlxImageComponent(net, graph, "anisotropyY", direction, 1);
    const auto xy = authorMtlxImageChannel(net, graph, "anisotropyXy", "ND_convert_vector4_vector2",
                                           direction, SdfValueTypeNames->Float2);
    const auto length = net.Node(graph, "anisotropyDirectionLength", "ND_dotproduct_vector2");
    const auto angle = net.Node(graph, "anisotropyAngle", "ND_atan2_float");
    const auto selected = net.Node(graph, "anisotropyValidAngle", "ND_ifgreater_float");
    if (!x || !y || !xy || !net.Connect(length, "in1", SdfValueTypeNames->Float2, xy) ||
        !net.Connect(length, "in2", SdfValueTypeNames->Float2, xy) ||
        !net.Connect(angle, "inx", float1, x) || !net.Connect(angle, "iny", float1, y) ||
        !net.Connect(selected, "value1", float1, net.Output(length, "out", float1)) ||
        !net.Set(selected, "value2", float1, 0.0f, Variability::Fixed) ||
        !net.Connect(selected, "in1", float1, net.Output(angle, "out", float1)) ||
        !net.Set(selected, "in2", float1, 0.0f, Variability::Fixed))
    {
        return false;
    }
    // atan2 supplies the angle without needing to normalize RG. For a zero direction, use +X.
    auto rotation = net.Output(selected, "out", float1);
    if (factors.anisotropyRotation != 0.0f || factors.animates(MaterialField::AnisotropyRotation))
    {
        const auto add = net.Node(graph, "anisotropyRotation", "ND_add_float");
        if (!net.Connect(add, "in1", float1, rotation) ||
            !net.Set(add, "in2", float1, factors.anisotropyRotation))
        {
            return false;
        }
        rotation = net.Output(add, "out", float1);
    }
    return rotation && net.Connect(surface, "anisotropy_rotation", float1, rotation) &&
           connectMtlxInput(net, graph, surface, "anisotropy_strength", strength, Port(), nullptr,
                            "anisotropyFactor", "ND_multiply_float", float1, factors.anisotropy,
                            factors.animates(MaterialField::Anisotropy));
}

// The clearcoat normal defaults to the geometric normal, as in glTF, not to the base normal map.
bool
connectMtlxClearcoat(Network& net, const SdfPath& graph, const SdfPath& surface,
                     const PbrFactors& factors, const MaterialTextures& textures)
{
    return (!textures.clearcoat ||
            connectMtlxChannel(net, graph, surface, "clearcoat", "clearcoat", 0, factors.clearcoat,
                               factors.animates(MaterialField::Clearcoat), *textures.clearcoat)) &&
           (!textures.clearcoatRoughness ||
            connectMtlxChannel(net, graph, surface, "clearcoatRoughness", "clearcoat_roughness", 1,
                               factors.clearcoatRoughness,
                               factors.animates(MaterialField::ClearcoatRoughness),
                               *textures.clearcoatRoughness)) &&
           (!textures.clearcoatNormal ||
            connectMtlxNormal(net, graph, surface, "clearcoatNormal", "clearcoat_normal",
                              factors.clearcoatNormalScale,
                              factors.animates(MaterialField::ClearcoatNormalScale),
                              *textures.clearcoatNormal));
}

bool
connectMtlxTextures(Network& net, const SdfPath& graph, const SdfPath& surface,
                    const PbrFactors& factors, const MaterialTextures& textures)
{
    const auto animates = [&](MaterialField field) { return factors.animates(field); };
    return (!textures.metallicRoughness ||
            connectMtlxMetallicRoughness(net, graph, surface, factors,
                                         *textures.metallicRoughness)) &&
           (!textures.normal ||
            connectMtlxNormal(net, graph, surface, "normal", "normal", factors.normalScale,
                              animates(MaterialField::NormalScale), *textures.normal)) &&
           (!textures.occlusion ||
            connectMtlxOcclusion(net, graph, surface, factors, *textures.occlusion)) &&
           (!textures.emissive ||
            connectMtlxColor(net, graph, surface, "emissive", "emissive", factors.emissive,
                             animates(MaterialField::Emissive), *textures.emissive)) &&
           (!hasClearcoat(factors) ||
            connectMtlxClearcoat(net, graph, surface, factors, textures)) &&
           (!textures.transmission ||
            connectMtlxChannel(net, graph, surface, "transmission", "transmission", 0,
                               factors.transmission, animates(MaterialField::Transmission),
                               *textures.transmission)) &&
           (!textures.thickness ||
            connectMtlxChannel(net, graph, surface, "thickness", "thickness", 1, factors.thickness,
                               animates(MaterialField::Thickness), *textures.thickness)) &&
           (!textures.specular ||
            connectMtlxChannel(net, graph, surface, "specular", "specular", 3, factors.specular,
                               animates(MaterialField::Specular), *textures.specular)) &&
           (!textures.specularColor ||
            connectMtlxColor(net, graph, surface, "specularColor", "specular_color",
                             factors.specularColor, animates(MaterialField::SpecularColor),
                             *textures.specularColor)) &&
           (!textures.sheenColor ||
            connectMtlxColor(net, graph, surface, "sheenColor", "sheen_color", factors.sheenColor,
                             animates(MaterialField::SheenColor), *textures.sheenColor)) &&
           (!textures.sheenRoughness ||
            connectMtlxChannel(net, graph, surface, "sheenRoughness", "sheen_roughness", 3,
                               factors.sheenRoughness, animates(MaterialField::SheenRoughness),
                               *textures.sheenRoughness)) &&
           (!textures.iridescence ||
            connectMtlxChannel(net, graph, surface, "iridescence", "iridescence", 0,
                               factors.iridescence, animates(MaterialField::Iridescence),
                               *textures.iridescence)) &&
           (!textures.iridescenceThickness ||
            connectMtlxIridescenceThickness(net, graph, surface, factors,
                                            *textures.iridescenceThickness)) &&
           (!textures.anisotropy ||
            connectMtlxAnisotropy(net, graph, surface, factors, *textures.anisotropy));
}

bool
authorPreview(Network& net, const SdfPath& material, const PbrFactors& factors, bool vertexColor,
              const MaterialTextures& textures)
{
    const auto graph = net.Graph(material.AppendChild(TfToken("preview")));
    const auto surface = net.Node(graph, "surface", "UsdPreviewSurface");
    const bool opacity = previewUsesOpacity(factors);
    // A black metal has no diffuse term, and its specular color is black at every angle.
    const bool unlit = factors.unlit;
    // In the metallic workflow, a dielectric's F0 is ((1 - ior) / (1 + ior))^2, as in glTF.
    const auto ior = authoredIor(factors);
    // The clearcoat's roughness defaults to 0.01, not glTF's 0.
    const bool clearcoat = hasClearcoat(factors);
    const auto color3 = SdfValueTypeNames->Color3f;
    const auto float1 = SdfValueTypeNames->Float;
    return net.Set(surface, "diffuseColor", color3, unlit ? GfVec3f(0) : factors.baseColor) &&
           net.Set(surface, "metallic", float1, unlit ? 1.0f : factors.metallic) &&
           net.Set(surface, "roughness", float1, unlit ? 1.0f : factors.roughness) &&
           net.Set(surface, "emissiveColor", color3,
                   unlit ? factors.baseColor + factors.unlitEmission
                         : factors.emissive * factors.emissiveStrength) &&
           (!ior || net.Set(surface, "ior", float1, *ior)) &&
           (!clearcoat || (net.Set(surface, "clearcoat", float1, factors.clearcoat) &&
                           net.Set(surface, "clearcoatRoughness", float1,
                                   factors.clearcoatRoughness))) &&
           (!opacity || net.Set(surface, "opacity", float1, factors.alpha)) &&
           (!opacity || factors.alphaMode != fastgltf::AlphaMode::Mask ||
            net.Set(surface, "opacityThreshold", float1, factors.alphaCutoff)) &&
           net.ConnectSurface(material, TfToken(), graph, surface, TfToken("surface")) &&
           (textures.baseColor
                ? connectPreviewBaseColor(net, graph, surface, factors, *textures.baseColor)
                : (!vertexColor || connectPreviewVertexColor(net, graph, surface, factors))) &&
           connectPreviewTextures(net, graph, surface, factors, textures);
}

bool
authorMtlx(Network& net, const SdfPath& material, const PbrFactors& factors, bool vertexColor,
           const MaterialTextures& textures)
{
    const auto graph = net.Graph(material.AppendChild(TfToken("mtlx")));
    const auto surface = net.Node(graph, "surface", "ND_gltf_pbr_surfaceshader");
    if (surface.IsEmpty())
    {
        return false;
    }
    const auto color3 = SdfValueTypeNames->Color3f;
    const auto float1 = SdfValueTypeNames->Float;
    const bool opaque = factors.alphaMode == fastgltf::AlphaMode::Opaque;
    const bool mask = factors.alphaMode == fastgltf::AlphaMode::Mask;
    // A dielectric's specular input scales both its F0 and F90, so 0 removes its reflection.
    const bool unlit = factors.unlit;
    const bool hasEmissiveStrength =
        factors.emissiveStrength != 1.0f || factors.animates(MaterialField::EmissiveStrength);
    // gltf_pbr computes its dielectric F0 from ior as glTF does, and declares the input uniform.
    const auto ior = authoredIor(factors);
    // gltf_pbr layers a dielectric of ior 1.5 over the base, as glTF does.
    const bool clearcoat = hasClearcoat(factors);
    // gltf_pbr mixes its diffuse term toward a transmission tinted by the base color, as glTF does.
    // Without KHR_materials_volume the material is thin, and thickness keeps its default of 0.
    const bool transmission = hasTransmission(factors);
    // gltf_pbr's volume inputs are glTF's, and the attenuation ones are uniform. An infinite
    // distance attenuates nothing, and is left out, because gltf_pbr has no default for it.
    const bool volume = hasVolume(factors);
    const bool attenuation = volume && std::isfinite(factors.attenuationDistance);
    // gltf_pbr's dispersion is glTF's, 20 over the Abbe number.
    const bool dispersion = hasDispersion(factors);
    // gltf_pbr's dielectric F0 is min(F0 of the ior * specular_color, 1) * specular, and its F90
    // specular, as glTF's are.
    const bool specular = hasSpecularWeight(factors, textures);
    const bool specularColor = hasSpecularColor(factors, textures);
    // gltf_pbr layers a sheen weighted by the color's largest component over the base, dimming the
    // base by its albedo, with a Charlie roughness of sheen_roughness squared, as glTF does.
    const bool sheen = hasSheen(factors);
    const bool sheenRoughness = hasSheenRoughness(factors);
    const bool iridescence = hasIridescence(factors);
    const bool anisotropy = hasAnisotropy(factors);
    return net.Set(surface, "base_color", color3, unlit ? GfVec3f(0) : factors.baseColor) &&
           net.Set(surface, "metallic", float1, unlit ? 0.0f : factors.metallic) &&
           net.Set(surface, "roughness", float1, factors.roughness) &&
           (!anisotropy ||
            (net.Set(surface, "anisotropy_strength", float1, factors.anisotropy) &&
             net.Set(surface, "anisotropy_rotation", float1, factors.anisotropyRotation))) &&
           (!unlit || net.Set(surface, "specular", float1, 0.0f)) &&
           (!specular || net.Set(surface, "specular", float1, factors.specular)) &&
           (!specularColor || net.Set(surface, "specular_color", color3, factors.specularColor)) &&
           (!sheen || net.Set(surface, "sheen_color", color3, factors.sheenColor)) &&
           (!sheenRoughness || net.Set(surface, "sheen_roughness", float1, factors.sheenRoughness)) &&
           (!iridescence ||
            (net.Set(surface, "iridescence", float1, factors.iridescence) &&
             net.Set(surface, "iridescence_ior", float1, factors.iridescenceIor,
                     Variability::Uniform) &&
             net.Set(surface, "iridescence_thickness", float1, factors.iridescenceMaximum))) &&
           net.Set(surface, "emissive", color3,
                   unlit ? factors.baseColor + factors.unlitEmission : factors.emissive) &&
           (!hasEmissiveStrength || net.Set(surface, "emissive_strength", float1,
                                            factors.emissiveStrength, Variability::Uniform)) &&
           (!ior || net.Set(surface, "ior", float1, *ior, Variability::Uniform)) &&
           (!clearcoat || (net.Set(surface, "clearcoat", float1, factors.clearcoat) &&
                           net.Set(surface, "clearcoat_roughness", float1,
                                   factors.clearcoatRoughness))) &&
           (!transmission || net.Set(surface, "transmission", float1, factors.transmission)) &&
           (!volume || net.Set(surface, "thickness", float1, factors.thickness)) &&
           (!attenuation ||
            (net.Set(surface, "attenuation_distance", float1, factors.attenuationDistance,
                     Variability::Uniform) &&
             net.Set(surface, "attenuation_color", color3, factors.attenuationColor,
                     Variability::Uniform))) &&
           (!dispersion || net.Set(surface, "dispersion", float1, factors.dispersion)) &&
           net.Set(surface, "alpha_mode", SdfValueTypeNames->Int, opaque ? 0 : (mask ? 1 : 2),
                   Variability::Uniform) &&
           (opaque || net.Set(surface, "alpha", float1, factors.alpha)) &&
           (!mask || net.Set(surface, "alpha_cutoff", float1, factors.alphaCutoff)) &&
           net.ConnectSurface(material, TfToken("mtlx"), graph, surface, TfToken("out")) &&
           connectMtlxInputs(net, graph, surface, factors, vertexColor,
                             textures.baseColor ? &*textures.baseColor : nullptr) &&
           connectMtlxTextures(net, graph, surface, factors, textures);
}

UsdShadeMaterial
authorMaterial(const fastgltf::Material& source, const PbrFactors& factors, const std::string& name,
               const UsdStageRefPtr& stage, const SdfPath& scope, bool vertexColor = false,
               const MaterialTextures& textures = {}, Network::Values* values = nullptr,
               const VtValue* xmp = nullptr)
{
    const auto material = UsdShadeMaterial::Define(stage, scope.AppendChild(TfToken(name)));
    if (!material || (!source.name.empty() && !material.GetPrim().SetDisplayName(source.name)))
    {
        return UsdShadeMaterial();
    }
    Network net(stage);
    if (!net.SetXmp(material.GetPath(), xmp))
    {
        return UsdShadeMaterial();
    }
    if (!authorPreview(net, material.GetPath(), factors, vertexColor, textures) ||
        !authorMtlx(net, material.GetPath(), factors, vertexColor, textures))
    {
        return UsdShadeMaterial();
    }
    if (values)
    {
        *values = std::move(net.values);
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
// counter-clockwise, and a translation that also moves the origin from the upper left. A transform
// of all defaults is none, unless an animation changes it.
std::optional<UvTransform>
uvTransform(const fastgltf::TextureTransform& source, bool animated = false)
{
    if (!animated && source.rotation == 0 && source.uvOffset[0] == 0 &&
        source.uvOffset[1] == 0 && source.uvScale[0] == 1 && source.uvScale[1] == 1)
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
               const std::string& sourcePath, const gltfCore::BufferReader& buffers,
               TextureInput& result, std::string& error, bool animatedTransform)
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
    // Ar would not route a path inside a packaged source to the package resolver, and a sibling
    // file would not be the package's. See docs/adr/0025-packaged-sources.md.
    if (ArIsPackageRelativePath(sourcePath))
    {
        error = "images are not supported in a source inside a package";
        return false;
    }
    gltfTexture::ImageLocation location;
    if (!gltfTexture::LocateImage(asset, *imageIndex, sourcePath, buffers, location, error))
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
    result.image = *imageIndex;
    const auto texCoord = texCoordOf(info);
    result.primvar = texCoord == 0 ? "st" : "st" + std::to_string(texCoord);
    if (info.transform)
    {
        result.transform = uvTransform(*info.transform, animatedTransform);
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

void
preparePreviewPrimvars(const PbrFactors& factors, const VtVec3fArray& colors,
                       const VtFloatArray& alpha, MaterialBinding& binding)
{
    if (factors.baseColor != GfVec3f(1) || factors.unlitEmission != GfVec3f(0))
    {
        binding.previewColors.resize(colors.size());
        for (std::size_t i = 0; i < colors.size(); ++i)
        {
            binding.previewColors[i] =
                GfCompMult(colors[i], factors.baseColor) + factors.unlitEmission;
        }
    }
    if (previewUsesOpacity(factors) && factors.alpha != 1.0f && !alpha.empty())
    {
        binding.previewAlpha.resize(alpha.size());
        for (std::size_t i = 0; i < alpha.size(); ++i)
        {
            binding.previewAlpha[i] = alpha[i] * factors.alpha;
        }
    }
}

// Sets one property of the factors or raw texture transforms to a glTF value.
void
applyValue(PbrFactors& factors, std::array<fastgltf::TextureTransform, TextureSlotCount>& transforms,
           const MaterialProperty& property, const std::array<double, 4>& value)
{
    const auto f = [&](std::size_t i) { return float(value[i]); };
    const auto color = [&]() { return GfVec3f(f(0), f(1), f(2)); };
    if (property.IsTransform())
    {
        auto& transform = transforms[std::size_t(property.slot)];
        switch (property.field)
        {
        case MaterialField::TextureOffset:
            transform.uvOffset = fastgltf::math::nvec2(value[0], value[1]);
            break;
        case MaterialField::TextureRotation:
            transform.rotation = fastgltf::num(value[0]);
            break;
        default:
            transform.uvScale = fastgltf::math::nvec2(value[0], value[1]);
            break;
        }
        return;
    }
    switch (property.field)
    {
    case MaterialField::BaseColor:
        factors.baseColor = color();
        factors.alpha = f(3);
        break;
    case MaterialField::Metallic:
        factors.metallic = f(0);
        break;
    case MaterialField::Roughness:
        factors.roughness = f(0);
        break;
    case MaterialField::Emissive:
        factors.emissive = color();
        break;
    case MaterialField::AlphaCutoff:
        factors.alphaCutoff = f(0);
        break;
    case MaterialField::NormalScale:
        factors.normalScale = f(0);
        break;
    case MaterialField::OcclusionStrength:
        factors.occlusionStrength = f(0);
        break;
    case MaterialField::EmissiveStrength:
        factors.emissiveStrength = f(0);
        break;
    case MaterialField::Ior:
        factors.ior = f(0);
        break;
    case MaterialField::Clearcoat:
        factors.clearcoat = f(0);
        break;
    case MaterialField::ClearcoatRoughness:
        factors.clearcoatRoughness = f(0);
        break;
    case MaterialField::ClearcoatNormalScale:
        factors.clearcoatNormalScale = f(0);
        break;
    case MaterialField::Transmission:
        factors.transmission = f(0);
        break;
    case MaterialField::Thickness:
        factors.thickness = f(0);
        break;
    case MaterialField::AttenuationDistance:
        factors.attenuationDistance = f(0);
        break;
    case MaterialField::AttenuationColor:
        factors.attenuationColor = color();
        break;
    case MaterialField::Dispersion:
        factors.dispersion = f(0);
        break;
    case MaterialField::Specular:
        factors.specular = f(0);
        break;
    case MaterialField::SpecularColor:
        factors.specularColor = color();
        break;
    case MaterialField::SheenColor:
        factors.sheenColor = color();
        break;
    case MaterialField::SheenRoughness:
        factors.sheenRoughness = f(0);
        break;
    case MaterialField::Iridescence:
        factors.iridescence = f(0);
        break;
    case MaterialField::IridescenceIor:
        factors.iridescenceIor = f(0);
        break;
    case MaterialField::IridescenceMinimum:
        factors.iridescenceMinimum = f(0);
        break;
    case MaterialField::IridescenceMaximum:
        factors.iridescenceMaximum = f(0);
        break;
    case MaterialField::Anisotropy:
        factors.anisotropy = f(0);
        break;
    case MaterialField::AnisotropyRotation:
        factors.anisotropyRotation = f(0);
        break;
    default:
        break;
    }
}

// The components of a value that USD interpolates linearly, or 0 for one that it holds.
std::size_t
components(const VtValue& value, std::array<double, 4>& result)
{
    if (value.IsHolding<float>())
    {
        result[0] = value.UncheckedGet<float>();
        return 1;
    }
    if (value.IsHolding<GfVec2f>())
    {
        const auto& v = value.UncheckedGet<GfVec2f>();
        result = {v[0], v[1], 0, 0};
        return 2;
    }
    if (value.IsHolding<GfVec3f>())
    {
        const auto& v = value.UncheckedGet<GfVec3f>();
        result = {v[0], v[1], v[2], 0};
        return 3;
    }
    if (value.IsHolding<GfVec4f>())
    {
        const auto& v = value.UncheckedGet<GfVec4f>();
        result = {v[0], v[1], v[2], v[3]};
        return 4;
    }
    return 0;
}

// Whether the lines from `start` to `end` pass within the tolerance of `middle` at their middles.
bool
linearAt(const Network::Values& start, const Network::Values& end, const Network::Values& middle)
{
    for (std::size_t i = 0; i < start.size(); ++i)
    {
        std::array<double, 4> a{};
        std::array<double, 4> b{};
        std::array<double, 4> m{};
        const auto width = components(start[i].second, a);
        if (width == 0 || components(end[i].second, b) != width ||
            components(middle[i].second, m) != width)
        {
            continue;
        }
        double scale = 1.0;
        for (std::size_t c = 0; c < width; ++c)
        {
            scale = std::max({scale, std::abs(a[c]), std::abs(b[c])});
        }
        for (std::size_t c = 0; c < width; ++c)
        {
            if (!(std::abs(m[c] - (a[c] + b[c]) / 2) <= SampleTolerance * scale))
            {
                return false;
            }
        }
    }
    return true;
}

// How far a sample that is left out may be from the line through the samples kept around it, as a
// fraction of the larger of 1 and their largest component: float rounding of a linear value.
constexpr double PruneTolerance = 1e-6;

// Whether the stage's interpolation between (t0, v0) and (t1, v1) gives v at t.
bool
interpolates(double t0, const VtValue& v0, double t1, const VtValue& v1, double t, const VtValue& v)
{
    std::array<double, 4> a{};
    std::array<double, 4> b{};
    std::array<double, 4> m{};
    const auto width = components(v0, a);
    if (width == 0 || components(v1, b) != width || components(v, m) != width)
    {
        // The stage holds a value that it cannot interpolate.
        return v == v0;
    }
    const double u = (t - t0) / (t1 - t0);
    double scale = 1.0;
    for (std::size_t c = 0; c < width; ++c)
    {
        scale = std::max({scale, std::abs(a[c]), std::abs(b[c])});
    }
    for (std::size_t c = 0; c < width; ++c)
    {
        if (!(std::abs(m[c] - (a[c] + (b[c] - a[c]) * u)) <= PruneTolerance * scale))
        {
            return false;
        }
    }
    return true;
}

// The positions of the samples of one input to author: the others are on the lines between them.
std::vector<std::size_t>
keptSamples(const std::vector<std::pair<double, Network::Values>>& series, std::size_t position)
{
    const auto value = [&](std::size_t i) -> const VtValue& { return series[i].second[position].second; };
    std::vector<std::size_t> kept{0};
    for (std::size_t i = 1; i < series.size(); ++i)
    {
        if (i + 1 == series.size())
        {
            kept.push_back(i);
            break;
        }
        // Leave sample i out if the line from the last kept sample to the next one gives every
        // sample since the last kept one.
        const auto from = kept.back();
        bool onLine = true;
        for (std::size_t j = from + 1; j <= i && onLine; ++j)
        {
            onLine = interpolates(series[from].first, value(from), series[i + 1].first,
                                  value(i + 1), series[j].first, value(j));
        }
        if (!onLine)
        {
            kept.push_back(i);
        }
    }
    return kept;
}

} // namespace

// A USD material made from a glTF material that an animation changes, with what is needed to
// compute its inputs at any time.
struct AnimatedMaterial
{
    std::size_t index = 0;
    MaterialUse use = MaterialUse::Full;
    bool vertexColor = false;
    bool emissiveTexture = false;
    SdfPath path;
    // The factors before adjustForUse, and each slot's raw transform.
    PbrFactors factors;
    std::array<fastgltf::TextureTransform, TextureSlotCount> transforms{};
    MaterialTextures textures;
    // The inputs' values without animation, in authoring order.
    Network::Values stated;
    // Positions in `stated` of the inputs that an animation changes, each input's last position.
    std::vector<std::size_t> animated;
    // By animation, the values of every input at each time code.
    std::map<std::size_t, std::vector<std::pair<double, Network::Values>>> samples;

    // The inputs' values with each channel's value at `seconds`.
    Network::Values Evaluate(const std::vector<MaterialChannel>& channels, double seconds,
                             bool before) const
    {
        auto at = factors;
        auto raw = transforms;
        for (const auto& channel : channels)
        {
            applyValue(at, raw, channel.property, channel.Evaluate(seconds, before));
        }
        adjustForUse(at, use, emissiveTexture);
        auto inputs = textures;
        for (std::size_t slot = 0; slot < TextureSlotCount; ++slot)
        {
            auto& texture = inputs[TextureSlot(slot)];
            if (texture && factors.animated.HasTransform(TextureSlot(slot)))
            {
                texture->transform = uvTransform(raw[slot], true);
            }
        }
        Network net;
        if (!authorPreview(net, path, at, vertexColor, inputs) ||
            !authorMtlx(net, path, at, vertexColor, inputs))
        {
            net.values.clear();
        }
        return std::move(net.values);
    }
};

MaterialAuthor::MaterialAuthor(const fastgltf::Asset& asset, const std::string& sourcePath,
                               const gltfCore::BufferReader& buffers,
                               const pxr::UsdStageRefPtr& stage, const pxr::SdfPath& scope,
                               const AnimationPlan& animation, const XmpPackets& xmp)
    : _asset(asset), _sourcePath(sourcePath), _buffers(buffers), _stage(stage), _scope(scope),
      _animation(animation), _xmp(xmp)
{
}

MaterialAuthor::~MaterialAuthor() = default;

namespace
{

AnimatedProperties
animatedProperties(const AnimationPlan& plan, std::size_t index)
{
    const auto found = plan.materials.find(index);
    return found == plan.materials.end() ? AnimatedProperties{} : found->second;
}

// Whether a layer is drawn: its stated factor is positive, or an animation changes it, as
// hasClearcoat and hasAnisotropy decide.
bool
everPositive(const AnimationPlan& plan, std::size_t index, MaterialField field, float stated)
{
    return stated > 0.0f || animatedProperties(plan, index).Has(field);
}

// Whether a normal map's transform rotates or mirrors the image when stated or at a keyframe.
bool
turnsNormalFrame(const AnimationPlan& plan, std::size_t index, TextureSlot slot,
                 const std::optional<TextureInput>& normal)
{
    if (!normal || !normal->transform)
    {
        return false;
    }
    const auto& transform = *normal->transform;
    if (transform.rotationDegrees != 0.0f || transform.scale[0] < 0.0f || transform.scale[1] < 0.0f)
    {
        return true;
    }
    for (const auto& animation : plan.animations)
    {
        const auto channels = animation.materials.find(index);
        if (channels == animation.materials.end())
        {
            continue;
        }
        for (const auto& channel : channels->second)
        {
            if (channel.property.slot != slot)
            {
                continue;
            }
            for (const auto& value : channel.vertices)
            {
                if ((channel.property.field == MaterialField::TextureRotation && value[0] != 0.0) ||
                    (channel.property.field == MaterialField::TextureScale &&
                     (value[0] < 0.0 || value[1] < 0.0)))
                {
                    return true;
                }
            }
        }
    }
    return false;
}

} // namespace

bool
MaterialAuthor::HasNormalTextures(std::size_t index) const
{
    const auto& material = _asset.materials[index];
    return !material.unlit &&
           (material.normalTexture ||
            (material.clearcoat &&
             everPositive(_animation, index, MaterialField::Clearcoat,
                          material.clearcoat->clearcoatFactor) &&
             material.clearcoat->clearcoatNormalTexture));
}

std::optional<std::size_t>
MaterialAuthor::TangentTexCoord(std::size_t index) const
{
    // Called after Prepare validates the material and its texture coordinates.
    const auto& material = _asset.materials[index];
    if (material.unlit)
    {
        return std::nullopt;
    }
    if (material.normalTexture)
    {
        return texCoordOf(*material.normalTexture);
    }
    if (material.clearcoat &&
        everPositive(_animation, index, MaterialField::Clearcoat,
                     material.clearcoat->clearcoatFactor) &&
        material.clearcoat->clearcoatNormalTexture)
    {
        return texCoordOf(*material.clearcoat->clearcoatNormalTexture);
    }
    if (material.anisotropy &&
        everPositive(_animation, index, MaterialField::Anisotropy,
                     material.anisotropy->anisotropyStrength))
    {
        const auto& texture = material.anisotropy->anisotropyTexture;
        return texture ? texCoordOf(*texture) : 0;
    }
    return std::nullopt;
}

bool
MaterialAuthor::Prepare(std::optional<std::size_t> materialIndex, std::size_t uvSetCount,
                        const pxr::VtVec3fArray* colors, const pxr::VtFloatArray& alpha,
                        MaterialBinding& binding, std::string& error, MaterialUse use)
{
    if (!materialIndex)
    {
        return PrepareDefault(colors != nullptr, use, binding, error);
    }
    const auto index = *materialIndex;
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
    const auto* volume = source.volume ? &*source.volume : nullptr;
    const auto* specular = source.specular ? &*source.specular : nullptr;
    const auto* sheen = source.sheen ? &*source.sheen : nullptr;
    const auto* iridescence = source.iridescence ? &*source.iridescence : nullptr;
    const auto* anisotropy = source.anisotropy ? &*source.anisotropy : nullptr;
    if (!hasUvSet(pbr.baseColorTexture) || !hasUvSet(pbr.metallicRoughnessTexture) ||
        !hasUvSet(source.normalTexture) || !hasUvSet(source.occlusionTexture) ||
        !hasUvSet(source.emissiveTexture) ||
        (clearcoat && (!hasUvSet(clearcoat->clearcoatTexture) ||
                       !hasUvSet(clearcoat->clearcoatRoughnessTexture) ||
                       !hasUvSet(clearcoat->clearcoatNormalTexture))) ||
        (transmission && !hasUvSet(transmission->transmissionTexture)) ||
        (volume && !hasUvSet(volume->thicknessTexture)) ||
        (specular &&
         (!hasUvSet(specular->specularTexture) || !hasUvSet(specular->specularColorTexture))) ||
        (sheen &&
         (!hasUvSet(sheen->sheenColorTexture) || !hasUvSet(sheen->sheenRoughnessTexture))) ||
        (iridescence && (!hasUvSet(iridescence->iridescenceTexture) ||
                         !hasUvSet(iridescence->iridescenceThicknessTexture))) ||
        (anisotropy && !hasUvSet(anisotropy->anisotropyTexture)))
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
    if (volume && !(std::isfinite(volume->thicknessFactor) && volume->thicknessFactor >= 0))
    {
        error = "material thickness factor is non-finite or negative";
        return false;
    }
    // The default is infinite, which JSON cannot express, so an infinite value is the default.
    if (volume && !(volume->attenuationDistance > 0))
    {
        error = "material attenuation distance is not positive";
        return false;
    }
    for (std::size_t i = 0; volume && i < 3; ++i)
    {
        if (!inUnitRange(volume->attenuationColor[i]))
        {
            error = "material attenuation color is non-finite or out of range";
            return false;
        }
    }
    if (specular && !inUnitRange(specular->specularFactor))
    {
        error = "material specular factor is non-finite or out of range";
        return false;
    }
    // glTF bounds the specular color below only: its product with the ior's F0 is clamped to 1.
    for (std::size_t i = 0; specular && i < 3; ++i)
    {
        if (!(std::isfinite(specular->specularColorFactor[i]) &&
              specular->specularColorFactor[i] >= 0))
        {
            error = "material specular color factor is non-finite or negative";
            return false;
        }
    }
    for (std::size_t i = 0; sheen && i < 3; ++i)
    {
        if (!inUnitRange(sheen->sheenColorFactor[i]))
        {
            error = "material sheen color factor is non-finite or out of range";
            return false;
        }
    }
    if (sheen && !inUnitRange(sheen->sheenRoughnessFactor))
    {
        error = "material sheen roughness factor is non-finite or out of range";
        return false;
    }
    if (anisotropy && !inUnitRange(anisotropy->anisotropyStrength))
    {
        error = "material anisotropy strength is non-finite or out of range";
        return false;
    }
    if (anisotropy && !std::isfinite(anisotropy->anisotropyRotation))
    {
        error = "material anisotropy rotation is non-finite";
        return false;
    }
    if (iridescence && !inUnitRange(iridescence->iridescenceFactor))
    {
        error = "material iridescence factor is non-finite or out of range";
        return false;
    }
    if (iridescence &&
        !(std::isfinite(iridescence->iridescenceIor) && iridescence->iridescenceIor >= 1))
    {
        error = "material iridescence ior is non-finite or below 1";
        return false;
    }
    if (iridescence && !(std::isfinite(iridescence->iridescenceThicknessMinimum) &&
                         iridescence->iridescenceThicknessMinimum >= 0 &&
                         std::isfinite(iridescence->iridescenceThicknessMaximum) &&
                         iridescence->iridescenceThicknessMaximum >= 0))
    {
        error = "material iridescence thickness is non-finite or negative";
        return false;
    }
    // An unlit material is unlit on points and lines too, and one without normal textures draws
    // the same without them.
    if (source.unlit || (use == MaterialUse::WithoutNormalTextures && !HasNormalTextures(index)))
    {
        use = MaterialUse::Full;
    }
    const auto animated = animatedProperties(_animation, index);
    const auto base = pbrFactors(source, animated);
    auto factors = base;
    adjustForUse(factors, use, bool(source.emissiveTexture));
    auto& material = _materials[{index, colors != nullptr, use}];
    if (!material)
    {
        const auto resolve = [&](const auto& info, std::optional<TextureInput>& texture,
                                 TextureSlot slot)
        {
            if (!info)
            {
                return true;
            }
            if (!resolveTexture(_asset, *info, _sourcePath, _buffers, texture.emplace(), error,
                                animated.HasTransform(slot)))
            {
                return false;
            }
            texture->xmp = _xmp.Get(gltfCore::XmpPacketOf(_xmp.Metadata().images, texture->image));
            return true;
        };
        const auto animates = [&](MaterialField field) { return factors.animates(field); };
        // KHR_materials_unlit defines every other texture as a fallback for clients without it,
        // and an unlit point or line draws only its base color and emissive factor.
        // A clearcoat or transmission of factor 0 is disabled, and so is a volume without
        // transmission or thickness, so their textures are not read either. A specular weight of
        // 0 removes the reflection whatever its texture or color, and a black specular color
        // factor makes the color black whatever its texture. A black sheen color factor disables the
        // sheen layer, and a sheen roughness factor of 0 makes the roughness 0 whatever its texture.
        // An iridescence factor of 0 disables the film; equal thickness endpoints fix its thickness.
        // A texture is read whatever these values when an animation changes them.
        const bool coated = hasClearcoat(factors);
        const bool transmits = hasTransmission(factors);
        const bool thick = hasVolume(factors);
        const bool reflects = specular && !factors.unlit &&
                              (factors.specular > 0.0f || animates(MaterialField::Specular));
        const bool tinted = reflects && (factors.specularColor != GfVec3f(0) ||
                                         animates(MaterialField::SpecularColor));
        const bool sheened = hasSheen(factors);
        const bool film = hasIridescence(factors);
        MaterialTextures textures;
        if (!resolve(pbr.baseColorTexture, textures.baseColor, TextureSlot::BaseColor) ||
            (!factors.unlit &&
             (!resolve(pbr.metallicRoughnessTexture, textures.metallicRoughness,
                       TextureSlot::MetallicRoughness) ||
              (use != MaterialUse::WithoutNormalTextures &&
               !resolve(source.normalTexture, textures.normal, TextureSlot::Normal)) ||
              !resolve(source.occlusionTexture, textures.occlusion, TextureSlot::Occlusion) ||
              !resolve(source.emissiveTexture, textures.emissive, TextureSlot::Emissive))) ||
            (coated &&
             (!resolve(clearcoat->clearcoatTexture, textures.clearcoat, TextureSlot::Clearcoat) ||
              !resolve(clearcoat->clearcoatRoughnessTexture, textures.clearcoatRoughness,
                       TextureSlot::ClearcoatRoughness) ||
              (use != MaterialUse::WithoutNormalTextures &&
               !resolve(clearcoat->clearcoatNormalTexture, textures.clearcoatNormal,
                        TextureSlot::ClearcoatNormal)))) ||
            (transmits && !resolve(transmission->transmissionTexture, textures.transmission,
                                   TextureSlot::Transmission)) ||
            (thick &&
             !resolve(volume->thicknessTexture, textures.thickness, TextureSlot::Thickness)) ||
            (reflects &&
             !resolve(specular->specularTexture, textures.specular, TextureSlot::Specular)) ||
            (tinted && !resolve(specular->specularColorTexture, textures.specularColor,
                                TextureSlot::SpecularColor)) ||
            (sheened &&
             !resolve(sheen->sheenColorTexture, textures.sheenColor, TextureSlot::SheenColor)) ||
            (hasSheenRoughness(factors) && !resolve(sheen->sheenRoughnessTexture,
                                                    textures.sheenRoughness,
                                                    TextureSlot::SheenRoughness)) ||
            (film && (!resolve(iridescence->iridescenceTexture, textures.iridescence,
                               TextureSlot::Iridescence) ||
                      ((factors.iridescenceMinimum != factors.iridescenceMaximum ||
                        animates(MaterialField::IridescenceMinimum) ||
                        animates(MaterialField::IridescenceMaximum)) &&
                       !resolve(iridescence->iridescenceThicknessTexture,
                                textures.iridescenceThickness,
                                TextureSlot::IridescenceThickness)))) ||
            (hasAnisotropy(factors) &&
             !resolve(anisotropy->anisotropyTexture, textures.anisotropy, TextureSlot::Anisotropy)))
        {
            return false;
        }
        if (turnsNormalFrame(_animation, index, TextureSlot::Normal, textures.normal) ||
            turnsNormalFrame(_animation, index, TextureSlot::ClearcoatNormal,
                             textures.clearcoatNormal))
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
        if (source.unlit && volume)
        {
            TF_WARN("glTF material %zu: KHR_materials_volume is ignored, because the material is "
                    "unlit",
                    index);
        }
        if (source.unlit && source.dispersion != 0)
        {
            TF_WARN("glTF material %zu: KHR_materials_dispersion is ignored, because the material "
                    "is unlit",
                    index);
        }
        if (source.unlit && specular)
        {
            TF_WARN("glTF material %zu: KHR_materials_specular is ignored, because the material "
                    "is unlit",
                    index);
        }
        if (source.unlit && anisotropy)
        {
            TF_WARN("glTF material %zu: KHR_materials_anisotropy is ignored, because the material "
                    "is unlit",
                    index);
        }
        if (hasAnisotropy(factors))
        {
            TF_WARN("glTF material %zu: UsdPreviewSurface has no anisotropy; its network ignores "
                    "KHR_materials_anisotropy",
                    index);
            TF_WARN(
                "glTF material %zu: MaterialX anisotropy uses a renderer-derived tangent frame; "
                "supplied glTF tangents and handedness are not used for shading",
                index);
        }
        if (source.unlit && iridescence)
        {
            TF_WARN("glTF material %zu: KHR_materials_iridescence is ignored, because the material "
                    "is unlit",
                    index);
        }
        if (film)
        {
            TF_WARN("glTF material %zu: UsdPreviewSurface has no iridescence; its network ignores "
                    "KHR_materials_iridescence",
                    index);
        }
        if (source.unlit && sheen)
        {
            TF_WARN("glTF material %zu: KHR_materials_sheen is ignored, because the material is "
                    "unlit",
                    index);
        }
        if (sheened)
        {
            TF_WARN("glTF material %zu: UsdPreviewSurface has no sheen; its network ignores "
                    "KHR_materials_sheen",
                    index);
        }
        if (hasSpecularWeight(factors, textures) || hasSpecularColor(factors, textures))
        {
            TF_WARN("glTF material %zu: UsdPreviewSurface's metallic workflow has no specular "
                    "weight or color; its network ignores KHR_materials_specular",
                    index);
        }
        if (transmits)
        {
            TF_WARN("glTF material %zu: UsdPreviewSurface has no transmission; its network ignores "
                    "KHR_materials_transmission",
                    index);
        }
        if (thick)
        {
            TF_WARN("glTF material %zu: UsdPreviewSurface has no volume; its network ignores "
                    "KHR_materials_volume",
                    index);
        }
        if (hasDispersion(factors))
        {
            TF_WARN("glTF material %zu: UsdPreviewSurface has no dispersion; its network ignores "
                    "KHR_materials_dispersion",
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
        if (use == MaterialUse::Unlit && source.emissiveTexture)
        {
            TF_WARN("glTF material %zu: points and lines without NORMAL are drawn without the "
                    "emission of an emissive texture",
                    index);
        }
        if (colors && !textures.baseColor &&
            (factors.animates(MaterialField::BaseColor) || factors.unlitEmissionAnimated))
        {
            TF_WARN("glTF material %zu: UsdPreviewSurface keeps the stated base color factor on "
                    "primitives with COLOR_0, which an animation does not change",
                    index);
        }
        Network::Values values;
        material = authorMaterial(source, factors,
                                  "Material_" + std::to_string(index) +
                                      (colors ? "_VertexColor" : "") + materialUseSuffix(use),
                                  _stage, _scope, colors != nullptr, textures, &values,
                                  _xmp.Get(gltfCore::XmpPacketOf(_xmp.Metadata().materials,
                                                                 index)));
        if (!material)
        {
            error = "could not author USD material";
            return false;
        }
        if (animated.Any())
        {
            auto record = std::make_unique<AnimatedMaterial>();
            record->index = index;
            record->use = use;
            record->vertexColor = colors != nullptr;
            record->emissiveTexture = bool(source.emissiveTexture);
            record->path = material.GetPath();
            record->factors = base;
            for (std::size_t slot = 0; slot < TextureSlotCount; ++slot)
            {
                if (const auto* info = TextureInfoOf(source, TextureSlot(slot));
                    info && info->transform)
                {
                    record->transforms[slot] = *info->transform;
                }
            }
            record->textures = std::move(textures);
            record->stated = std::move(values);
            _animated.push_back(std::move(record));
        }
    }
    if (colors && !pbr.baseColorTexture)
    {
        preparePreviewPrimvars(factors, *colors, alpha, binding);
    }
    binding.material = material;
    binding.doubleSided = source.doubleSided;
    return true;
}

bool
MaterialAuthor::PrepareDefault(bool hasVertexColor, MaterialUse use, MaterialBinding& binding,
                               std::string& error)
{
    // The default material has no textures, and its emissive factor is black.
    const bool unlit = use == MaterialUse::Unlit;
    auto& material = _materials[{std::nullopt, hasVertexColor, unlit ? use : MaterialUse::Full}];
    if (!material)
    {
        const fastgltf::Material source;
        auto factors = pbrFactors(source);
        factors.unlit = unlit;
        material = authorMaterial(source, factors,
                                  std::string(hasVertexColor ? "DefaultVertexColor" : "Default") +
                                      (unlit ? "Unlit" : ""),
                                  _stage, _scope, hasVertexColor);
        if (!material)
        {
            error = "could not author default USD material";
            return false;
        }
    }
    binding.material = material;
    return true;
}

bool
MaterialAuthor::SampleAnimations(std::string& error)
{
    bool capped = false;
    for (auto& record : _animated)
    {
        auto& material = *record;
        for (std::size_t a = 0; a < _animation.animations.size(); ++a)
        {
            const auto found = _animation.animations[a].materials.find(material.index);
            if (found == _animation.animations[a].materials.end())
            {
                continue;
            }
            const auto& channels = found->second;
            std::vector<double> keys;
            for (const auto& channel : channels)
            {
                keys.insert(keys.end(), channel.times.begin(), channel.times.end());
            }
            std::sort(keys.begin(), keys.end());
            keys.erase(std::unique(keys.begin(), keys.end()), keys.end());
            // Every time has the same inputs as the stated values, because what an animated
            // property controls is authored whatever its value.
            bool mismatched = false;
            const auto evaluate = [&](double seconds, bool before)
            {
                auto values = material.Evaluate(channels, seconds, before);
                if (values.size() != material.stated.size() ||
                    !std::equal(values.begin(), values.end(), material.stated.begin(),
                                [](const auto& a, const auto& b) { return a.first == b.first; }))
                {
                    mismatched = true;
                    return material.stated;
                }
                return values;
            };
            auto& samples = material.samples[a];
            auto previous = evaluate(keys.front(), false);
            samples.emplace_back(keys.front() * TimeCodesPerSecond, previous);
            for (std::size_t k = 1; k < keys.size(); ++k)
            {
                const double start = keys[k - 1];
                const double duration = keys[k] - start;
                // The points of equal pieces of the segment, from its start to the limit at its end.
                std::vector<Network::Values> points{previous, evaluate(keys[k], true)};
                for (std::size_t pieces = 1;; pieces *= 2)
                {
                    std::vector<Network::Values> middles;
                    bool linear = true;
                    for (std::size_t i = 0; i < pieces; ++i)
                    {
                        middles.push_back(
                            evaluate(start + duration * (double(i) + 0.5) / double(pieces), false));
                        linear = linear && linearAt(points[i], points[i + 1], middles.back());
                    }
                    if (linear || pieces * 2 > MaxPieces)
                    {
                        capped = capped || !linear;
                        break;
                    }
                    std::vector<Network::Values> refined;
                    for (std::size_t i = 0; i < pieces; ++i)
                    {
                        refined.push_back(std::move(points[i]));
                        refined.push_back(std::move(middles[i]));
                    }
                    refined.push_back(std::move(points.back()));
                    points = std::move(refined);
                }
                for (std::size_t i = 1; i + 1 < points.size(); ++i)
                {
                    const double time = start + duration * double(i) / double(points.size() - 1);
                    samples.emplace_back(time * TimeCodesPerSecond, std::move(points[i]));
                }
                const double code = keys[k] * TimeCodesPerSecond;
                auto at = evaluate(keys[k], false);
                // A STEP keyframe holds the earlier value until just before it.
                if (at != points.back() && code - HoldTimeCodes > samples.back().first)
                {
                    samples.emplace_back(code - HoldTimeCodes, std::move(points.back()));
                }
                samples.emplace_back(code, at);
                previous = std::move(at);
            }
            if (mismatched)
            {
                error = "glTF material " + std::to_string(material.index) +
                        " has different inputs at different times of animation " +
                        std::to_string(a);
                return false;
            }
        }
        // Each input's last value is the one authored.
        std::map<SdfPath, std::size_t> last;
        for (std::size_t i = 0; i < material.stated.size(); ++i)
        {
            last[material.stated[i].first] = i;
        }
        for (const auto& [path, position] : last)
        {
            const auto& stated = material.stated[position].second;
            const bool changes = std::any_of(
                material.samples.begin(), material.samples.end(),
                [&](const auto& animation)
                {
                    return std::any_of(animation.second.begin(), animation.second.end(),
                                       [&](const auto& sample)
                                       { return sample.second[position].second != stated; });
                });
            if (!changes)
            {
                continue;
            }
            material.animated.push_back(position);
            // A value outside the variants would be stronger than theirs.
            const auto attribute = _stage->GetAttributeAtPath(path);
            if (!attribute || !attribute.ClearDefault())
            {
                error = "could not remove the stated value of " + path.GetString();
                return false;
            }
        }
    }
    if (capped)
    {
        TF_WARN("A glTF material animation segment is cut into only %zu pieces, which can stray "
                "from its values by more than %g",
                MaxPieces, SampleTolerance);
    }
    return true;
}

bool
MaterialAuthor::AuthorAnimation(std::size_t animationIndex, std::string& error) const
{
    for (const auto& record : _animated)
    {
        const auto& material = *record;
        const auto samples = material.samples.find(animationIndex);
        for (const auto position : material.animated)
        {
            const auto& [path, stated] = material.stated[position];
            const auto attribute = _stage->GetAttributeAtPath(path);
            if (!attribute || !attribute.Set(stated))
            {
                error = "could not author the stated value of " + path.GetString();
                return false;
            }
            if (samples == material.samples.end() ||
                std::all_of(samples->second.begin(), samples->second.end(),
                            [&](const auto& sample) { return sample.second[position].second == stated; }))
            {
                continue;
            }
            const auto& series = samples->second;
            for (const auto i : keptSamples(series, position))
            {
                if (!attribute.Set(series[i].second[position].second, UsdTimeCode(series[i].first)))
                {
                    error = "could not author the time samples of " + path.GetString();
                    return false;
                }
            }
        }
    }
    return true;
}

bool
MaterialBinding::Author(const pxr::UsdGeomGprim& gprim, std::string& error) const
{
    using namespace pxr;
    if (!UsdShadeMaterialBindingAPI(gprim.GetPrim()).Bind(material) ||
        (gprim.GetPrim().IsA<UsdGeomMesh>() && !gprim.CreateDoubleSidedAttr().Set(doubleSided)))
    {
        error = "could not author USD material binding or sidedness";
        return false;
    }
    const UsdGeomPrimvarsAPI primvars(gprim);
    if ((!previewColors.empty() &&
         !primvars
              .CreatePrimvar(TfToken("gltf_previewBaseColor"), SdfValueTypeNames->Color3fArray,
                             UsdGeomTokens->vertex)
              .Set(previewColors)) ||
        (!previewAlpha.empty() &&
         !primvars
              .CreatePrimvar(TfToken("gltf_previewAlpha"), SdfValueTypeNames->FloatArray,
                             UsdGeomTokens->vertex)
              .Set(previewAlpha)) ||
        (!generatedTangents.empty() &&
         !primvars
              .CreatePrimvar(TfToken("gltf_generatedTangent"), SdfValueTypeNames->Float4Array,
                             UsdGeomTokens->faceVarying)
              .Set(generatedTangents)))
    {
        error = "could not author USD material primvars";
        return false;
    }
    return true;
}

} // namespace gltfUsd
