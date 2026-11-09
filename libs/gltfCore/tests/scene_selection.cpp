// SPDX-License-Identifier: Apache-2.0
#include "gltfCore/sceneSelection.h"
#include "gltf_test_data.h"

#include <fastgltf/core.hpp>

#include <cstddef>
#include <iostream>
#include <string>
#include <string_view>
#include <variant>
#include <vector>

namespace
{

bool
checkSelection(std::string_view label, const std::vector<std::byte>& bytes,
               const gltfCore::SceneSelection& expected)
{
    auto data = fastgltf::GltfDataBuffer::FromBytes(bytes.data(), bytes.size());
    if (data.error() != fastgltf::Error::None)
    {
        std::cerr << label << ": " << fastgltf::getErrorMessage(data.error()) << '\n';
        return false;
    }
    fastgltf::Parser parser;
    auto asset = parser.loadGltf(data.get(), {}, fastgltf::Options::None);
    if (asset.error() != fastgltf::Error::None)
    {
        std::cerr << label << ": " << fastgltf::getErrorMessage(asset.error()) << '\n';
        return false;
    }
    const auto result = gltfCore::SelectScene(asset.get());
    const auto* selected = std::get_if<gltfCore::SelectedScene>(&result);
    const auto* expectedSelected = std::get_if<gltfCore::SelectedScene>(&expected);
    if (selected && expectedSelected && selected->index == expectedSelected->index &&
        selected->usedFallback == expectedSelected->usedFallback)
    {
        return true;
    }
    const auto* error = std::get_if<gltfCore::SceneSelectionError>(&result);
    const auto* expectedError = std::get_if<gltfCore::SceneSelectionError>(&expected);
    if (error && expectedError && *error == *expectedError)
    {
        return true;
    }
    std::cerr << label << ": unexpected scene selection\n";
    return false;
}

bool
checkBothFormats(std::string_view label, const std::string& json,
                 const gltfCore::SceneSelection& expected)
{
    const bool gltf = checkSelection(label, gltfTestData::bytesOf(json), expected);
    const bool glb = checkSelection(label, gltfTestData::glbOf(json), expected);
    return gltf && glb;
}

} // namespace

int
main()
{
    const bool explicitDefault = checkBothFormats(
        "explicit default", R"({"asset":{"version":"2.0"},"scenes":[{},{}],"scene":1})",
        gltfCore::SelectedScene{1, false});
    const bool noDefault =
        checkBothFormats("missing default", R"({"asset":{"version":"2.0"},"scenes":[{},{}]})",
                         gltfCore::SelectedScene{0, true});
    const bool firstDefault = checkBothFormats(
        "first default", R"({"asset":{"version":"2.0"},"scenes":[{},{}],"scene":0})",
        gltfCore::SelectedScene{0, false});
    const bool singleScene = checkBothFormats("single scene without default",
                                              R"({"asset":{"version":"2.0"},"scenes":[{}]})",
                                              gltfCore::SelectedScene{0, true});
    const bool noScenes = checkBothFormats("missing scenes", R"({"asset":{"version":"2.0"}})",
                                           gltfCore::SceneSelectionError::NoScenes);
    const bool invalidDefault = checkBothFormats(
        "invalid default", R"({"asset":{"version":"2.0"},"scenes":[{}],"scene":1})",
        gltfCore::SceneSelectionError::InvalidDefaultScene);
    const bool emptyScenes =
        checkBothFormats("empty scenes", R"({"asset":{"version":"2.0"},"scenes":[]})",
                         gltfCore::SceneSelectionError::NoScenes);
    const bool noScenesWithDefault =
        checkBothFormats("missing scenes with default", R"({"asset":{"version":"2.0"},"scene":0})",
                         gltfCore::SceneSelectionError::NoScenes);
    return explicitDefault && noDefault && firstDefault && singleScene && noScenes &&
                   invalidDefault && emptyScenes && noScenesWithDefault
               ? 0
               : 1;
}
