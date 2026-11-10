// SPDX-License-Identifier: Apache-2.0
#include "camera.h"

#include "pxr/base/gf/camera.h"
#include "pxr/base/gf/vec2f.h"
#include "pxr/base/gf/vec3d.h"
#include "pxr/base/tf/diagnostic.h"
#include "pxr/usd/usdGeom/camera.h"
#include "pxr/usd/usdGeom/tokens.h"

#include <cmath>
#include <numbers>
#include <string>
#include <variant>

namespace gltfUsd
{
namespace
{

using namespace pxr;

// USD's fallback far plane, used for a glTF camera with an infinite projection.
constexpr double kInfiniteFar = 1000000.0;
// How far the determinant of the node's rotation may be from 1.
constexpr double kRotationTolerance = 0.001;

struct Projection
{
    TfToken projection;
    double horizontalAperture = 0;
    double verticalAperture = 0;
    double focalLength = 0; // Not authored for an orthographic camera.
    double znear = 0;
    double zfar = 0;
};

bool
isPositive(double value)
{
    return std::isfinite(value) && value > 0;
}

// Returns an empty string, or why the camera cannot be converted.
std::string
projectionOf(const fastgltf::Camera& camera, Projection& result)
{
    if (const auto* perspective = std::get_if<fastgltf::Camera::Perspective>(&camera.camera))
    {
        const double yfov = perspective->yfov;
        // The specification only recommends less than pi, but no aperture holds pi or more.
        if (!isPositive(yfov) || yfov >= std::numbers::pi)
        {
            return "yfov must be greater than 0 and less than pi";
        }
        // A viewport's aspect ratio cannot be authored, so a square aperture stands for it.
        const double aspectRatio = perspective->aspectRatio ? *perspective->aspectRatio : 1.0;
        if (!isPositive(aspectRatio))
        {
            return "aspectRatio must be greater than 0";
        }
        result.znear = perspective->znear;
        if (!isPositive(result.znear))
        {
            return "znear must be greater than 0";
        }
        result.zfar = perspective->zfar ? double(*perspective->zfar) : kInfiniteFar;
        if (!std::isfinite(result.zfar) || result.zfar <= result.znear)
        {
            return perspective->zfar ? "zfar must be greater than znear"
                                     : "znear must be less than the far plane of an infinite "
                                       "projection, 1000000";
        }
        // GfCamera's convention: the default horizontal aperture, and the focal length for yfov.
        result.projection = UsdGeomTokens->perspective;
        result.horizontalAperture = GfCamera::DEFAULT_HORIZONTAL_APERTURE;
        result.verticalAperture = result.horizontalAperture / aspectRatio;
        result.focalLength = result.verticalAperture * GfCamera::APERTURE_UNIT /
                             (2 * std::tan(yfov / 2)) / GfCamera::FOCAL_LENGTH_UNIT;
    }
    else
    {
        const auto& orthographic = std::get<fastgltf::Camera::Orthographic>(camera.camera);
        // A negative magnification, which the specification discourages, would mirror the view.
        if (!isPositive(orthographic.xmag) || !isPositive(orthographic.ymag))
        {
            return "xmag and ymag must be greater than 0";
        }
        result.znear = orthographic.znear;
        result.zfar = orthographic.zfar;
        if (!std::isfinite(result.znear) || result.znear < 0)
        {
            return "znear must not be negative";
        }
        if (!std::isfinite(result.zfar) || result.zfar <= result.znear)
        {
            return "zfar must be greater than znear";
        }
        result.projection = UsdGeomTokens->orthographic;
        result.horizontalAperture = 2 * orthographic.xmag / GfCamera::APERTURE_UNIT;
        result.verticalAperture = 2 * orthographic.ymag / GfCamera::APERTURE_UNIT;
    }
    for (const double value : {result.horizontalAperture, result.verticalAperture,
                               result.focalLength, result.znear, result.zfar})
    {
        if (!std::isfinite(float(value)))
        {
            return "its projection is beyond a float";
        }
    }
    return {};
}

// glTF defines a camera's view by the rotation and translation of its node, which must be neither
// degenerate nor reflected. Returns false if it is.
bool
viewOf(const GfMatrix4d& world, GfMatrix4d& result)
{
    GfMatrix4d normalized = world;
    for (int row = 0; row < 3; ++row)
    {
        // A Gf row is a glTF column.
        const double length = GfVec3d(world[row][0], world[row][1], world[row][2]).GetLength();
        if (!isPositive(length))
        {
            return false;
        }
        for (int column = 0; column < 3; ++column)
        {
            normalized[row][column] /= length;
        }
    }
    if (std::abs(normalized.GetDeterminant3() - 1.0) > kRotationTolerance)
    {
        return false;
    }
    result = world.RemoveScaleShear();
    return true;
}

} // namespace

std::optional<pxr::GfMatrix4d>
CameraCorrection(std::size_t nodeIndex, std::size_t cameraIndex, const pxr::GfMatrix4d& parentWorld)
{
    using namespace pxr;
    GfMatrix4d view;
    if (!viewOf(parentWorld, view))
    {
        TF_RUNTIME_ERROR("glTF node %zu: camera %zu has no view, because the node's transform is "
                         "degenerate, reflected or sheared",
                         nodeIndex, cameraIndex);
        return std::nullopt;
    }
    // USD would scale the view with the node, so this cancels the node's scale and shear.
    return view * parentWorld.GetInverse();
}

pxr::UsdPrim
AuthorCamera(const fastgltf::Asset& asset, std::size_t cameraIndex, const pxr::UsdPrim& parent)
{
    using namespace pxr;
    if (cameraIndex >= asset.cameras.size())
    {
        TF_RUNTIME_ERROR("glTF camera index %zu is out of range", cameraIndex);
        return {};
    }
    const auto& source = asset.cameras[cameraIndex];
    Projection projection;
    if (const auto error = projectionOf(source, projection); !error.empty())
    {
        TF_RUNTIME_ERROR("glTF camera %zu: %s", cameraIndex, error.c_str());
        return {};
    }

    const auto camera = UsdGeomCamera::Define(
        parent.GetStage(),
        parent.GetPath().AppendChild(TfToken("Camera_" + std::to_string(cameraIndex))));
    if (!camera ||
        (!source.name.empty() && !camera.GetPrim().SetDisplayName(std::string(source.name))))
    {
        TF_RUNTIME_ERROR("Could not author glTF camera %zu", cameraIndex);
        return {};
    }
    bool authored =
        camera.CreateProjectionAttr().Set(projection.projection) &&
        camera.CreateHorizontalApertureAttr().Set(float(projection.horizontalAperture)) &&
        camera.CreateVerticalApertureAttr().Set(float(projection.verticalAperture)) &&
        camera.CreateClippingRangeAttr().Set(
            GfVec2f(float(projection.znear), float(projection.zfar)));
    if (authored && projection.projection == UsdGeomTokens->perspective)
    {
        authored = camera.CreateFocalLengthAttr().Set(float(projection.focalLength));
    }
    if (!authored)
    {
        TF_RUNTIME_ERROR("Could not author glTF camera %zu", cameraIndex);
        return {};
    }
    return camera.GetPrim();
}

} // namespace gltfUsd
