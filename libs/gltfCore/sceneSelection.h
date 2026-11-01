// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <fastgltf/types.hpp>

#include <cstddef>
#include <variant>

namespace gltfCore
{

struct SelectedScene
{
    std::size_t index;
    bool usedFallback;
};

enum class SceneSelectionError
{
    NoScenes,
    InvalidDefaultScene,
};

using SceneSelection = std::variant<SelectedScene, SceneSelectionError>;

[[nodiscard]] SceneSelection SelectScene(const fastgltf::Asset& asset);

} // namespace gltfCore
