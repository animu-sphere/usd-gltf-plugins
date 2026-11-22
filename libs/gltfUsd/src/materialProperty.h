// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <fastgltf/types.hpp>

#include <array>
#include <bitset>
#include <cstddef>
#include <string>
#include <string_view>
#include <vector>

namespace gltfUsd
{

// The texture slots that conversion reads, each of which may have a KHR_texture_transform.
enum class TextureSlot
{
    BaseColor,
    MetallicRoughness,
    Normal,
    Occlusion,
    Emissive,
    Clearcoat,
    ClearcoatRoughness,
    ClearcoatNormal,
    Transmission,
    Thickness,
    Specular,
    SpecularColor,
    SheenColor,
    SheenRoughness,
    Iridescence,
    IridescenceThickness,
    Anisotropy,
    Count,
};
inline constexpr std::size_t TextureSlotCount = std::size_t(TextureSlot::Count);

// The material properties of the glTF Asset Object Model that KHR_animation_pointer can animate
// and conversion authors.
enum class MaterialField
{
    BaseColor,
    Metallic,
    Roughness,
    Emissive,
    AlphaCutoff,
    NormalScale,
    OcclusionStrength,
    EmissiveStrength,
    Ior,
    Clearcoat,
    ClearcoatRoughness,
    ClearcoatNormalScale,
    Transmission,
    Thickness,
    AttenuationDistance,
    AttenuationColor,
    Dispersion,
    Specular,
    SpecularColor,
    SheenColor,
    SheenRoughness,
    Iridescence,
    IridescenceIor,
    IridescenceMinimum,
    IridescenceMaximum,
    Anisotropy,
    AnisotropyRotation,
    TextureOffset,
    TextureRotation,
    TextureScale,
    Count,
};
inline constexpr std::size_t MaterialFieldCount = std::size_t(MaterialField::Count);

struct MaterialProperty
{
    MaterialField field = MaterialField::BaseColor;
    // The slot of a texture transform field; Count otherwise.
    TextureSlot slot = TextureSlot::Count;

    [[nodiscard]] bool IsTransform() const { return slot != TextureSlot::Count; }
    // The number of float components: 1 to 4.
    [[nodiscard]] std::size_t Width() const;
    // The glTF JSON pointer after "/materials/{index}/".
    [[nodiscard]] std::string Path() const;

    friend bool operator==(const MaterialProperty&, const MaterialProperty&) = default;
};

enum class MaterialPointer
{
    // A property that conversion authors.
    Supported,
    // A property of KHR_materials_diffuse_transmission, which conversion leaves out with a warning.
    Ignored,
    // Anything else.
    Unsupported,
};

// Reads the part of a pointer after "/materials/{index}/".
[[nodiscard]] MaterialPointer ParseMaterialPointer(std::string_view path,
                                                   MaterialProperty& property);

// The texture info of a slot, or null when the material has none there.
[[nodiscard]] const fastgltf::TextureInfo* TextureInfoOf(const fastgltf::Material& material,
                                                         TextureSlot slot);

// Whether the material has the object that holds the property, which the extension requires of a
// pointer. A core property's object is always present as far as conversion can tell.
[[nodiscard]] bool HasEnclosingObject(const fastgltf::Material& material,
                                      const MaterialProperty& property);

// Rejects a keyframe value that the static conversion would reject for this property.
[[nodiscard]] bool ValidateMaterialValue(const MaterialProperty& property,
                                         const std::array<double, 4>& value, std::string& error);

// One animation's sampler on one material property, with values converted to float as the
// extension says. CUBICSPLINE samplers also have tangents.
struct MaterialChannel
{
    MaterialProperty property;
    fastgltf::AnimationInterpolation interpolation = fastgltf::AnimationInterpolation::Linear;
    std::vector<double> times; // Seconds
    std::vector<std::array<double, 4>> vertices;
    std::vector<std::array<double, 4>> ins;  // CUBICSPLINE only
    std::vector<std::array<double, 4>> outs; // CUBICSPLINE only

    // glTF's value at `seconds`, held before the first keyframe and after the last. With
    // `before`, the limit from earlier times, which differs from the value at a STEP keyframe.
    [[nodiscard]] std::array<double, 4> Evaluate(double seconds, bool before = false) const;

    friend bool operator==(const MaterialChannel&, const MaterialChannel&) = default;
};

// Which properties of one material any animation targets.
struct AnimatedProperties
{
    std::bitset<MaterialFieldCount> fields;
    // Slots whose offset, rotation or scale is animated.
    std::bitset<TextureSlotCount> transforms;

    [[nodiscard]] bool Has(MaterialField field) const { return fields.test(std::size_t(field)); }
    [[nodiscard]] bool HasTransform(TextureSlot slot) const
    {
        return transforms.test(std::size_t(slot));
    }
    [[nodiscard]] bool Any() const { return fields.any() || transforms.any(); }
};

} // namespace gltfUsd
