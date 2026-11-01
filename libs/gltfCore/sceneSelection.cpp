#include "sceneSelection.h"

namespace gltfCore {

SceneSelection SelectScene(const fastgltf::Asset& asset)
{
    if (asset.scenes.empty()) {
        return SceneSelectionError::NoScenes;
    }
    if (!asset.defaultScene.has_value()) {
        return SelectedScene{0, true};
    }
    if (*asset.defaultScene >= asset.scenes.size()) {
        return SceneSelectionError::InvalidDefaultScene;
    }
    return SelectedScene{*asset.defaultScene, false};
}

} // namespace gltfCore
