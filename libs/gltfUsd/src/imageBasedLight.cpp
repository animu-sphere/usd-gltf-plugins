// SPDX-License-Identifier: Apache-2.0
#include "imageBasedLight.h"

#include "gltfTexture/imageBasedLight.h"

#include "pxr/base/gf/quatf.h"
#include "pxr/base/tf/diagnostic.h"
#include "pxr/base/tf/pathUtils.h"
#include "pxr/base/tf/token.h"
#include "pxr/usd/ar/packageUtils.h"
#include "pxr/usd/sdf/assetPath.h"
#include "pxr/usd/usdLux/domeLight.h"
#include "pxr/usd/usdLux/tokens.h"

#include <cmath>

namespace gltfUsd
{

bool
AuthorImageBasedLight(const fastgltf::Asset& asset, const gltfCore::ImageBasedLight& light,
                      const std::string& sourcePath, const gltfCore::BufferReader& buffers,
                      const pxr::UsdPrim& scene)
{
    using namespace pxr;
    const auto fail = [&](const std::string& reason)
    {
        TF_RUNTIME_ERROR("glTF EXT_lights_image_based light %zu: %s", light.index, reason.c_str());
        return false;
    };
    // As for a material's images: Ar would not route the latlong to the package resolver. See
    // docs/adr/0025-packaged-sources.md.
    if (ArIsPackageRelativePath(sourcePath))
    {
        return fail("images are not supported in a source inside a package");
    }
    if (std::string error;
        !gltfTexture::CheckImageBasedLight(asset, light, sourcePath, buffers, error))
    {
        return fail(error);
    }
    if (!std::isfinite(float(light.intensity)))
    {
        return fail("its intensity is beyond a float");
    }
    const auto path =
        scene.GetPath().AppendChild(TfToken("ImageBasedLight_" + std::to_string(light.index)));
    const auto dome = UsdLuxDomeLight::Define(scene.GetStage(), path);
    const auto file = ArJoinPackageRelativePath("./" + TfGetBaseName(sourcePath),
                                                gltfTexture::MakeLightEntry(light.index));
    // The dome shows the cube's value at q w in world direction w, so its orientation is q's
    // inverse. glTF orders a quaternion x, y, z, w.
    const auto& q = light.rotation;
    const GfQuatf orient(float(q[3]), -float(q[0]), -float(q[1]), -float(q[2]));
    if (!dome || !dome.CreateTextureFileAttr().Set(SdfAssetPath(file)) ||
        !dome.CreateTextureFormatAttr().Set(UsdLuxTokens->latlong) ||
        !dome.CreateIntensityAttr().Set(float(light.intensity)) ||
        !dome.AddOrientOp().Set(orient) ||
        (!light.name.empty() && !dome.GetPrim().SetDisplayName(light.name)))
    {
        return fail("could not author " + path.GetString());
    }
    return true;
}

} // namespace gltfUsd
