cmake_minimum_required(VERSION 3.22)
project(UsdGltfPlugins LANGUAGES CXX)

include(CTest)
set(USD_GLTF_OUTPUT_ROOT "${CMAKE_BINARY_DIR}")

include(cmake/UsdGltfDependencies.cmake)
add_subdirectory(libs/gltfCore)
add_subdirectory(libs/gltfUsd)
add_subdirectory(plugins/usdGltfFileFormat)
if(BUILD_TESTING)
    add_subdirectory(tests)
endif()
