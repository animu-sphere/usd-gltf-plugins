# SPDX-License-Identifier: Apache-2.0
add_executable(image_test image.cpp)
target_link_libraries(image_test PRIVATE gltfTexture::gltfTexture)
add_test(NAME image COMMAND image_test)
