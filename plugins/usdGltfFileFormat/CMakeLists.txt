# SPDX-License-Identifier: Apache-2.0
cmake_minimum_required(VERSION 3.22)

if(CMAKE_SOURCE_DIR STREQUAL CMAKE_CURRENT_SOURCE_DIR)
    # OpenStrata's bundle build uses this directory as the CMake entry point,
    # so it adds the shared parts that the repository root otherwise provides.
    project(UsdGltfFileFormatBundle LANGUAGES CXX)
    include(CTest)
    set(USD_GLTF_OUTPUT_ROOT "${CMAKE_CURRENT_SOURCE_DIR}")

    include(../../cmake/UsdGltfDependencies.cmake)
    add_subdirectory("${USD_GLTF_REPOSITORY_ROOT}/libs/gltfCore" "${CMAKE_CURRENT_BINARY_DIR}/libs/gltfCore")
    add_subdirectory("${USD_GLTF_REPOSITORY_ROOT}/libs/gltfTexture" "${CMAKE_CURRENT_BINARY_DIR}/libs/gltfTexture")
    add_subdirectory("${USD_GLTF_REPOSITORY_ROOT}/libs/gltfUsd" "${CMAKE_CURRENT_BINARY_DIR}/libs/gltfUsd")
    if(BUILD_TESTING)
        add_subdirectory("${USD_GLTF_REPOSITORY_ROOT}/tests" "${CMAKE_CURRENT_BINARY_DIR}/tests")
    endif()
endif()

add_library(UsdGltfFileFormat SHARED
    UsdGltfFileFormat.cpp
    UsdGltfPackageResolver.cpp
    externalBuffer.cpp)
target_compile_features(UsdGltfFileFormat PRIVATE cxx_std_20)
set_target_properties(UsdGltfFileFormat PROPERTIES CXX_EXTENSIONS OFF)
target_include_directories(UsdGltfFileFormat PRIVATE ${PXR_INCLUDE_DIRS})
target_link_libraries(UsdGltfFileFormat PRIVATE usd_gltf_usd)

set_target_properties(UsdGltfFileFormat PROPERTIES
    PREFIX "lib"
    LIBRARY_OUTPUT_DIRECTORY "${USD_GLTF_OUTPUT_ROOT}/lib"
    RUNTIME_OUTPUT_DIRECTORY "${USD_GLTF_OUTPUT_ROOT}/lib")
foreach(config DEBUG RELEASE RELWITHDEBINFO MINSIZEREL)
    set_target_properties(UsdGltfFileFormat PROPERTIES
        LIBRARY_OUTPUT_DIRECTORY_${config} "${USD_GLTF_OUTPUT_ROOT}/lib"
        RUNTIME_OUTPUT_DIRECTORY_${config} "${USD_GLTF_OUTPUT_ROOT}/lib")
endforeach()

configure_file(
    plugInfo.json.in
    "${USD_GLTF_OUTPUT_ROOT}/plugin/resources/usdGltfFileFormat/plugInfo.json"
    @ONLY)
configure_file(
    "${USD_GLTF_REPOSITORY_ROOT}/third_party/NOTICES.txt"
    "${USD_GLTF_OUTPUT_ROOT}/plugin/resources/usdGltfFileFormat/NOTICES.txt"
    COPYONLY)
# A bundle package holds only the plugin's own tree, so the license must be in it too.
configure_file(
    "${USD_GLTF_REPOSITORY_ROOT}/LICENSE"
    "${USD_GLTF_OUTPUT_ROOT}/plugin/resources/usdGltfFileFormat/LICENSE"
    COPYONLY)
install(TARGETS UsdGltfFileFormat
    RUNTIME DESTINATION lib
    LIBRARY DESTINATION lib)
install(FILES
    "${USD_GLTF_OUTPUT_ROOT}/plugin/resources/usdGltfFileFormat/plugInfo.json"
    "${USD_GLTF_OUTPUT_ROOT}/plugin/resources/usdGltfFileFormat/NOTICES.txt"
    "${USD_GLTF_OUTPUT_ROOT}/plugin/resources/usdGltfFileFormat/LICENSE"
    DESTINATION plugin/resources/usdGltfFileFormat)
install(FILES
    "${USD_GLTF_REPOSITORY_ROOT}/LICENSE"
    "${USD_GLTF_REPOSITORY_ROOT}/third_party/NOTICES.txt"
    DESTINATION share/licenses/usd-gltf-plugins)
