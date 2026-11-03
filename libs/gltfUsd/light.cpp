// SPDX-License-Identifier: Apache-2.0
#include "light.h"

#include "pxr/base/gf/math.h"
#include "pxr/base/gf/vec3d.h"
#include "pxr/base/gf/vec3f.h"
#include "pxr/base/tf/diagnostic.h"
#include "pxr/usd/usdGeom/xformable.h"
#include "pxr/usd/usdLux/distantLight.h"
#include "pxr/usd/usdLux/lightAPI.h"
#include "pxr/usd/usdLux/shapingAPI.h"
#include "pxr/usd/usdLux/sphereLight.h"

#include <cmath>
#include <numbers>
#include <string>

namespace gltfUsd
{
namespace
{

using namespace pxr;

// The radius of the sphere that stands for a point or spot light, in meters.
constexpr double kPointRadius = 0.001;
// How far the node's axes, each of unit length, may be from lying in one plane.
constexpr double kDegenerateTolerance = 1e-6;

struct Emission
{
    // UsdLux intensity: illuminance in lux for a distant light, and the luminance of the sphere
    // for a point or spot light.
    double intensity = 0;
    double coneAngle = 0; // Degrees, for a spot light.
    double coneSoftness = 0;
};

bool
isUnitInterval(double value)
{
    return value >= 0 && value <= 1;
}

// Returns an empty string, or why the light cannot be converted.
std::string
emissionOf(const fastgltf::Light& light, Emission& result)
{
    for (std::size_t channel = 0; channel < 3; ++channel)
    {
        if (!isUnitInterval(light.color[channel]))
        {
            return "color components must be from 0 to 1";
        }
    }
    if (!std::isfinite(light.intensity) || light.intensity < 0)
    {
        return "intensity must not be negative";
    }
    if (light.range && !(std::isfinite(*light.range) && *light.range > 0))
    {
        return "range must be greater than 0";
    }
    if (light.type == fastgltf::LightType::Directional)
    {
        // A normalized distant light of angle 0 has its intensity in lux, as glTF does.
        result.intensity = light.intensity;
    }
    else
    {
        // A sphere's intensity in candela is its luminance times its projected area.
        result.intensity = light.intensity / (std::numbers::pi * kPointRadius * kPointRadius);
    }
    if (light.type == fastgltf::LightType::Spot)
    {
        const double inner = light.innerConeAngle ? double(*light.innerConeAngle) : 0.0;
        const double outer =
            light.outerConeAngle ? double(*light.outerConeAngle) : std::numbers::pi / 4;
        if (!(inner >= 0 && inner < outer && outer <= std::numbers::pi / 2))
        {
            return "cone angles must satisfy 0 <= innerConeAngle < outerConeAngle <= pi / 2";
        }
        // UsdLux softens the cone from the angle (1 - softness) * coneAngle to coneAngle.
        result.coneAngle = GfRadiansToDegrees(outer);
        result.coneSoftness = (outer - inner) / outer;
    }
    if (!std::isfinite(float(result.intensity)))
    {
        return "its intensity is beyond a float";
    }
    return {};
}

// glTF places a light at its node's origin, shining along the node's -Z axis, and leaves its other
// properties unscaled. Returns that frame without scale, or false if the node is degenerate.
bool
frameOf(const GfMatrix4d& world, GfMatrix4d& result)
{
    // A Gf row is a glTF column.
    GfVec3d axes[3];
    for (int row = 0; row < 3; ++row)
    {
        axes[row] = GfVec3d(world[row][0], world[row][1], world[row][2]);
        const double length = axes[row].GetLength();
        if (!(std::isfinite(length) && length > 0))
        {
            return false;
        }
        axes[row] /= length;
    }
    if (!(std::abs(GfDot(GfCross(axes[0], axes[1]), axes[2])) >= kDegenerateTolerance))
    {
        return false;
    }
    // Keep Z, the light's axis, and the plane of Y; the frame is right-handed even if the node
    // is reflected.
    const GfVec3d z = axes[2];
    const GfVec3d y = (axes[1] - GfDot(axes[1], z) * z).GetNormalized();
    const GfVec3d x = GfCross(y, z);
    result.SetIdentity();
    result.SetRow3(0, x);
    result.SetRow3(1, y);
    result.SetRow3(2, z);
    result.SetTranslateOnly(world.ExtractTranslation());
    return true;
}

} // namespace

bool
AuthorLight(const fastgltf::Asset& asset, std::size_t nodeIndex, std::size_t lightIndex,
            const pxr::UsdPrim& parent, const pxr::GfMatrix4d& parentWorld)
{
    using namespace pxr;
    if (lightIndex >= asset.lights.size())
    {
        TF_RUNTIME_ERROR("glTF light index %zu is out of range", lightIndex);
        return false;
    }
    const auto& source = asset.lights[lightIndex];
    Emission emission;
    if (const auto error = emissionOf(source, emission); !error.empty())
    {
        TF_RUNTIME_ERROR("glTF light %zu: %s", lightIndex, error.c_str());
        return false;
    }
    GfMatrix4d frame;
    if (!frameOf(parentWorld, frame))
    {
        TF_RUNTIME_ERROR("glTF node %zu: light %zu has no position or direction, because the "
                         "node's transform is degenerate",
                         nodeIndex, lightIndex);
        return false;
    }

    const auto stage = parent.GetStage();
    const auto path = parent.GetPath().AppendChild(TfToken("Light_" + std::to_string(lightIndex)));
    const bool directional = source.type == fastgltf::LightType::Directional;
    UsdPrim prim;
    bool authored = true;
    if (directional)
    {
        const auto light = UsdLuxDistantLight::Define(stage, path);
        prim = light.GetPrim();
        authored = light && light.CreateAngleAttr().Set(0.0f);
    }
    else
    {
        const auto light = UsdLuxSphereLight::Define(stage, path);
        prim = light.GetPrim();
        authored = light && light.CreateRadiusAttr().Set(float(kPointRadius)) &&
                   light.CreateTreatAsPointAttr().Set(true);
    }
    if (authored && source.type == fastgltf::LightType::Spot)
    {
        const auto shaping = UsdLuxShapingAPI::Apply(prim);
        authored = shaping && shaping.CreateShapingConeAngleAttr().Set(float(emission.coneAngle)) &&
                   shaping.CreateShapingConeSoftnessAttr().Set(float(emission.coneSoftness));
    }
    const UsdLuxLightAPI light(prim);
    // USD would scale the sphere and distort the cone with the node, so this cancels its scale.
    const GfMatrix4d local = frame * parentWorld.GetInverse();
    authored =
        authored &&
        (GfIsClose(local, GfMatrix4d(1.0), 1e-6) ||
         UsdGeomXformable(prim).AddTransformOp().Set(local)) &&
        light.CreateIntensityAttr().Set(float(emission.intensity)) &&
        light.CreateColorAttr().Set(GfVec3f(source.color[0], source.color[1], source.color[2])) &&
        light.CreateNormalizeAttr().Set(directional) &&
        (source.name.empty() || prim.SetDisplayName(std::string(source.name)));
    if (!authored)
    {
        TF_RUNTIME_ERROR("Could not author glTF light %zu", lightIndex);
        return false;
    }
    if (source.range && !directional)
    {
        TF_WARN("glTF node %zu: the range of light %zu is ignored, so the light falls off with "
                "the inverse square of distance and does not reach zero",
                nodeIndex, lightIndex);
    }
    return true;
}

} // namespace gltfUsd
