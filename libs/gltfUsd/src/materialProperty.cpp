// SPDX-License-Identifier: Apache-2.0
#include "materialProperty.h"

#include <algorithm>
#include <cmath>
#include <string>
#include <utility>

namespace gltfUsd
{
namespace
{

struct FieldPath
{
    MaterialField field;
    std::string_view path;
};

constexpr FieldPath kFieldPaths[] = {
    {MaterialField::BaseColor, "pbrMetallicRoughness/baseColorFactor"},
    {MaterialField::Metallic, "pbrMetallicRoughness/metallicFactor"},
    {MaterialField::Roughness, "pbrMetallicRoughness/roughnessFactor"},
    {MaterialField::Emissive, "emissiveFactor"},
    {MaterialField::AlphaCutoff, "alphaCutoff"},
    {MaterialField::NormalScale, "normalTexture/scale"},
    {MaterialField::OcclusionStrength, "occlusionTexture/strength"},
    {MaterialField::EmissiveStrength, "extensions/KHR_materials_emissive_strength/emissiveStrength"},
    {MaterialField::Ior, "extensions/KHR_materials_ior/ior"},
    {MaterialField::Clearcoat, "extensions/KHR_materials_clearcoat/clearcoatFactor"},
    {MaterialField::ClearcoatRoughness,
     "extensions/KHR_materials_clearcoat/clearcoatRoughnessFactor"},
    {MaterialField::ClearcoatNormalScale,
     "extensions/KHR_materials_clearcoat/clearcoatNormalTexture/scale"},
    {MaterialField::Transmission, "extensions/KHR_materials_transmission/transmissionFactor"},
    {MaterialField::Thickness, "extensions/KHR_materials_volume/thicknessFactor"},
    {MaterialField::AttenuationDistance, "extensions/KHR_materials_volume/attenuationDistance"},
    {MaterialField::AttenuationColor, "extensions/KHR_materials_volume/attenuationColor"},
    {MaterialField::Dispersion, "extensions/KHR_materials_dispersion/dispersion"},
    {MaterialField::Specular, "extensions/KHR_materials_specular/specularFactor"},
    {MaterialField::SpecularColor, "extensions/KHR_materials_specular/specularColorFactor"},
    {MaterialField::SheenColor, "extensions/KHR_materials_sheen/sheenColorFactor"},
    {MaterialField::SheenRoughness, "extensions/KHR_materials_sheen/sheenRoughnessFactor"},
    {MaterialField::Iridescence, "extensions/KHR_materials_iridescence/iridescenceFactor"},
    {MaterialField::IridescenceIor, "extensions/KHR_materials_iridescence/iridescenceIor"},
    {MaterialField::IridescenceMinimum,
     "extensions/KHR_materials_iridescence/iridescenceThicknessMinimum"},
    {MaterialField::IridescenceMaximum,
     "extensions/KHR_materials_iridescence/iridescenceThicknessMaximum"},
    {MaterialField::Anisotropy, "extensions/KHR_materials_anisotropy/anisotropyStrength"},
    {MaterialField::AnisotropyRotation, "extensions/KHR_materials_anisotropy/anisotropyRotation"},
};

constexpr std::string_view kSlotPaths[TextureSlotCount] = {
    "pbrMetallicRoughness/baseColorTexture",
    "pbrMetallicRoughness/metallicRoughnessTexture",
    "normalTexture",
    "occlusionTexture",
    "emissiveTexture",
    "extensions/KHR_materials_clearcoat/clearcoatTexture",
    "extensions/KHR_materials_clearcoat/clearcoatRoughnessTexture",
    "extensions/KHR_materials_clearcoat/clearcoatNormalTexture",
    "extensions/KHR_materials_transmission/transmissionTexture",
    "extensions/KHR_materials_volume/thicknessTexture",
    "extensions/KHR_materials_specular/specularTexture",
    "extensions/KHR_materials_specular/specularColorTexture",
    "extensions/KHR_materials_sheen/sheenColorTexture",
    "extensions/KHR_materials_sheen/sheenRoughnessTexture",
    "extensions/KHR_materials_iridescence/iridescenceTexture",
    "extensions/KHR_materials_iridescence/iridescenceThicknessTexture",
    "extensions/KHR_materials_anisotropy/anisotropyTexture",
};

constexpr std::string_view kTransformInfix = "/extensions/KHR_texture_transform/";

constexpr FieldPath kTransformFields[] = {
    {MaterialField::TextureOffset, "offset"},
    {MaterialField::TextureRotation, "rotation"},
    {MaterialField::TextureScale, "scale"},
};

bool
finite(const std::array<double, 4>& value, std::size_t width)
{
    return std::all_of(value.begin(), value.begin() + width,
                       [](double component) { return std::isfinite(component); });
}

bool
inUnitRange(const std::array<double, 4>& value, std::size_t width)
{
    return std::all_of(value.begin(), value.begin() + width, [](double component)
                       { return std::isfinite(component) && component >= 0 && component <= 1; });
}

bool
nonnegative(const std::array<double, 4>& value, std::size_t width)
{
    return std::all_of(value.begin(), value.begin() + width, [](double component)
                       { return std::isfinite(component) && component >= 0; });
}

// Whether a float conversion of each component stays finite, as the static values do.
bool
fitsFloat(const std::array<double, 4>& value, std::size_t width)
{
    return std::all_of(value.begin(), value.begin() + width,
                       [](double component) { return std::isfinite(float(component)); });
}

} // namespace

std::size_t
MaterialProperty::Width() const
{
    switch (field)
    {
    case MaterialField::BaseColor:
        return 4;
    case MaterialField::Emissive:
    case MaterialField::AttenuationColor:
    case MaterialField::SpecularColor:
    case MaterialField::SheenColor:
        return 3;
    case MaterialField::TextureOffset:
    case MaterialField::TextureScale:
        return 2;
    default:
        return 1;
    }
}

std::string
MaterialProperty::Path() const
{
    if (IsTransform())
    {
        for (const auto& [transformField, name] : kTransformFields)
        {
            if (transformField == field)
            {
                return std::string(kSlotPaths[std::size_t(slot)]) + std::string(kTransformInfix) +
                       std::string(name);
            }
        }
    }
    for (const auto& [candidate, path] : kFieldPaths)
    {
        if (candidate == field)
        {
            return std::string(path);
        }
    }
    return {};
}

MaterialPointer
ParseMaterialPointer(std::string_view path, MaterialProperty& property)
{
    for (const auto& [field, candidate] : kFieldPaths)
    {
        if (path == candidate)
        {
            property = {field, TextureSlot::Count};
            return MaterialPointer::Supported;
        }
    }
    for (std::size_t slot = 0; slot < TextureSlotCount; ++slot)
    {
        const auto slotPath = kSlotPaths[slot];
        if (!path.starts_with(slotPath) || !path.substr(slotPath.size()).starts_with(kTransformInfix))
        {
            continue;
        }
        const auto name = path.substr(slotPath.size() + kTransformInfix.size());
        for (const auto& [field, candidate] : kTransformFields)
        {
            if (name == candidate)
            {
                property = {field, TextureSlot(slot)};
                return MaterialPointer::Supported;
            }
        }
    }
    constexpr std::string_view diffuseTransmission =
        "extensions/KHR_materials_diffuse_transmission/";
    return path.starts_with(diffuseTransmission) ? MaterialPointer::Ignored
                                                 : MaterialPointer::Unsupported;
}

const fastgltf::TextureInfo*
TextureInfoOf(const fastgltf::Material& material, TextureSlot slot)
{
    const auto from = [](const auto& optional) -> const fastgltf::TextureInfo*
    { return optional ? &*optional : nullptr; };
    const auto& pbr = material.pbrData;
    switch (slot)
    {
    case TextureSlot::BaseColor:
        return from(pbr.baseColorTexture);
    case TextureSlot::MetallicRoughness:
        return from(pbr.metallicRoughnessTexture);
    case TextureSlot::Normal:
        return from(material.normalTexture);
    case TextureSlot::Occlusion:
        return from(material.occlusionTexture);
    case TextureSlot::Emissive:
        return from(material.emissiveTexture);
    case TextureSlot::Clearcoat:
        return material.clearcoat ? from(material.clearcoat->clearcoatTexture) : nullptr;
    case TextureSlot::ClearcoatRoughness:
        return material.clearcoat ? from(material.clearcoat->clearcoatRoughnessTexture) : nullptr;
    case TextureSlot::ClearcoatNormal:
        return material.clearcoat ? from(material.clearcoat->clearcoatNormalTexture) : nullptr;
    case TextureSlot::Transmission:
        return material.transmission ? from(material.transmission->transmissionTexture) : nullptr;
    case TextureSlot::Thickness:
        return material.volume ? from(material.volume->thicknessTexture) : nullptr;
    case TextureSlot::Specular:
        return material.specular ? from(material.specular->specularTexture) : nullptr;
    case TextureSlot::SpecularColor:
        return material.specular ? from(material.specular->specularColorTexture) : nullptr;
    case TextureSlot::SheenColor:
        return material.sheen ? from(material.sheen->sheenColorTexture) : nullptr;
    case TextureSlot::SheenRoughness:
        return material.sheen ? from(material.sheen->sheenRoughnessTexture) : nullptr;
    case TextureSlot::Iridescence:
        return material.iridescence ? from(material.iridescence->iridescenceTexture) : nullptr;
    case TextureSlot::IridescenceThickness:
        return material.iridescence ? from(material.iridescence->iridescenceThicknessTexture)
                                    : nullptr;
    case TextureSlot::Anisotropy:
        return material.anisotropy ? from(material.anisotropy->anisotropyTexture) : nullptr;
    case TextureSlot::Count:
        break;
    }
    return nullptr;
}

bool
HasEnclosingObject(const fastgltf::Material& material, const MaterialProperty& property)
{
    if (property.IsTransform())
    {
        const auto* info = TextureInfoOf(material, property.slot);
        return info && info->transform;
    }
    switch (property.field)
    {
    case MaterialField::NormalScale:
        return bool(material.normalTexture);
    case MaterialField::OcclusionStrength:
        return bool(material.occlusionTexture);
    case MaterialField::Clearcoat:
    case MaterialField::ClearcoatRoughness:
        return bool(material.clearcoat);
    case MaterialField::ClearcoatNormalScale:
        return material.clearcoat && material.clearcoat->clearcoatNormalTexture;
    case MaterialField::Transmission:
        return bool(material.transmission);
    case MaterialField::Thickness:
    case MaterialField::AttenuationDistance:
    case MaterialField::AttenuationColor:
        return bool(material.volume);
    case MaterialField::Specular:
    case MaterialField::SpecularColor:
        return bool(material.specular);
    case MaterialField::SheenColor:
    case MaterialField::SheenRoughness:
        return bool(material.sheen);
    case MaterialField::Iridescence:
    case MaterialField::IridescenceIor:
    case MaterialField::IridescenceMinimum:
    case MaterialField::IridescenceMaximum:
        return bool(material.iridescence);
    case MaterialField::Anisotropy:
    case MaterialField::AnisotropyRotation:
        return bool(material.anisotropy);
    default:
        // The parser checks the objects of the extensions that fastgltf does not keep.
        return true;
    }
}

bool
ValidateMaterialValue(const MaterialProperty& property, const std::array<double, 4>& value,
                      std::string& error)
{
    const auto width = property.Width();
    bool valid = true;
    switch (property.field)
    {
    case MaterialField::BaseColor:
    case MaterialField::Metallic:
    case MaterialField::Roughness:
    case MaterialField::Emissive:
    case MaterialField::OcclusionStrength:
    case MaterialField::Clearcoat:
    case MaterialField::ClearcoatRoughness:
    case MaterialField::Transmission:
    case MaterialField::AttenuationColor:
    case MaterialField::Specular:
    case MaterialField::SheenColor:
    case MaterialField::SheenRoughness:
    case MaterialField::Iridescence:
    case MaterialField::Anisotropy:
        valid = inUnitRange(value, width);
        break;
    case MaterialField::AlphaCutoff:
    case MaterialField::EmissiveStrength:
    case MaterialField::Thickness:
    case MaterialField::Dispersion:
    case MaterialField::SpecularColor:
    case MaterialField::IridescenceMinimum:
    case MaterialField::IridescenceMaximum:
        valid = nonnegative(value, width) && fitsFloat(value, width);
        break;
    case MaterialField::Ior:
        valid = std::isfinite(value[0]) && (value[0] == 0 || value[0] >= 1) && fitsFloat(value, 1);
        break;
    case MaterialField::IridescenceIor:
        valid = std::isfinite(value[0]) && value[0] >= 1 && fitsFloat(value, 1);
        break;
    case MaterialField::AttenuationDistance:
        valid = std::isfinite(value[0]) && value[0] > 0 && fitsFloat(value, 1);
        break;
    default:
        valid = finite(value, width) && fitsFloat(value, width);
        break;
    }
    if (!valid)
    {
        error = "an animated value of " + property.Path() + " is non-finite or out of range";
    }
    return valid;
}

std::array<double, 4>
MaterialChannel::Evaluate(double seconds, bool before) const
{
    const auto count = times.size();
    if (count == 0)
    {
        return {};
    }
    if (seconds < times.front() || (before && seconds <= times.front()))
    {
        return vertices.front();
    }
    if (seconds > times.back() || (!before && seconds == times.back()))
    {
        return vertices.back();
    }
    // The segment [times[k], times[k + 1]] that holds the time, the earlier one at a keyframe
    // when `before`.
    const auto upper = before ? std::lower_bound(times.begin(), times.end(), seconds)
                              : std::upper_bound(times.begin(), times.end(), seconds);
    const auto k = std::size_t(upper - times.begin()) - 1;
    if (interpolation == fastgltf::AnimationInterpolation::Step || k + 1 >= count)
    {
        return vertices[k];
    }
    const double delta = times[k + 1] - times[k];
    const double u = (seconds - times[k]) / delta;
    std::array<double, 4> result{};
    for (std::size_t c = 0; c < 4; ++c)
    {
        if (interpolation == fastgltf::AnimationInterpolation::Linear)
        {
            result[c] = vertices[k][c] + (vertices[k + 1][c] - vertices[k][c]) * u;
            continue;
        }
        const double u2 = u * u;
        const double u3 = u2 * u;
        result[c] = (2 * u3 - 3 * u2 + 1) * vertices[k][c] + (u3 - 2 * u2 + u) * delta * outs[k][c] +
                    (-2 * u3 + 3 * u2) * vertices[k + 1][c] + (u3 - u2) * delta * ins[k + 1][c];
    }
    return result;
}

} // namespace gltfUsd
