# SPDX-License-Identifier: Apache-2.0
add_library(usd_gltf_core STATIC
    accessor.cpp
    parse.cpp
    sceneSelection.cpp)
target_compile_features(usd_gltf_core PUBLIC cxx_std_20)
target_include_directories(usd_gltf_core PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}")
target_link_libraries(usd_gltf_core PUBLIC fastgltf::fastgltf)
set_target_properties(usd_gltf_core PROPERTIES POSITION_INDEPENDENT_CODE ON CXX_EXTENSIONS OFF)
