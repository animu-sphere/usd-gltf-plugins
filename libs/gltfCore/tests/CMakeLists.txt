# SPDX-License-Identifier: Apache-2.0
add_executable(fastgltf_smoke fastgltf_smoke.cpp)
target_link_libraries(fastgltf_smoke PRIVATE fastgltf::fastgltf)
add_test(NAME fastgltf_smoke COMMAND fastgltf_smoke)

add_executable(scene_selection_test scene_selection.cpp)
target_link_libraries(scene_selection_test PRIVATE gltfCore::gltfCore)
add_test(NAME scene_selection COMMAND scene_selection_test)

add_executable(accessor_test accessor.cpp)
target_link_libraries(accessor_test PRIVATE gltfCore::gltfCore)
add_test(NAME accessor COMMAND accessor_test)
