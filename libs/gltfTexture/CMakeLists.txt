# SPDX-License-Identifier: Apache-2.0
add_library(usd_gltf_texture STATIC
    image.cpp
    imageStructure.cpp
    ktx2.cpp
    webp.cpp)
target_compile_features(usd_gltf_texture PUBLIC cxx_std_20)
target_include_directories(usd_gltf_texture PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}")
target_link_libraries(usd_gltf_texture PUBLIC usd_gltf_core PRIVATE usd_gltf_basisu usd_gltf_webp)
set_target_properties(usd_gltf_texture PROPERTIES POSITION_INDEPENDENT_CODE ON CXX_EXTENSIONS OFF)
