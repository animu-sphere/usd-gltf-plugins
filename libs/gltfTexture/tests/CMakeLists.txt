# SPDX-License-Identifier: Apache-2.0
add_executable(image_test image.cpp)
target_link_libraries(image_test PRIVATE gltfTexture::gltfTexture)
add_test(NAME image COMMAND image_test "${USD_GLTF_REPOSITORY_ROOT}/tests/fixtures/textures")
set_tests_properties(image PROPERTIES TIMEOUT 60)

add_executable(image_based_light_test imageBasedLight.cpp)
target_link_libraries(image_based_light_test PRIVATE gltfTexture::gltfTexture)
add_test(NAME image_based_light
    COMMAND image_based_light_test "${USD_GLTF_REPOSITORY_ROOT}/tests/fixtures/textures")
set_tests_properties(image_based_light PROPERTIES TIMEOUT 60)
