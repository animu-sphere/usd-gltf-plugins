# SPDX-License-Identifier: Apache-2.0
add_executable(gltf_usd_convert_test convert.cpp)
target_link_libraries(gltf_usd_convert_test PRIVATE gltfUsd::gltfUsd)
add_test(NAME gltf_usd_convert COMMAND gltf_usd_convert_test)
