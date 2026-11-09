# SPDX-License-Identifier: Apache-2.0
# gltfUsd: translating a parsed glTF scene, its meshes, materials, cameras and lights onto an
# SdfLayer. It does not register a plugin.
#
# Its own CMake project, so that `ost library build libs/gltfUsd` can build it alone, and added with
# add_subdirectory() by the repository root and by the plugin.
cmake_minimum_required(VERSION 3.22)

include("${CMAKE_CURRENT_LIST_DIR}/../../cmake/UsdGltfBuild.cmake")
usd_gltf_read_version(_usd_gltf_version)
project(gltfUsd
    VERSION ${_usd_gltf_version}
    DESCRIPTION "glTF to OpenUSD translation for usd-gltf-plugins"
    LANGUAGES CXX)
usd_gltf_default_build_type()
if(PROJECT_IS_TOP_LEVEL)
    include(CTest)
endif()

if(NOT pxr_FOUND)
    find_package(pxr REQUIRED CONFIG)
endif()
usd_gltf_require_library(gltfCore)
usd_gltf_require_library(gltfTexture)

add_library(gltfUsd STATIC
    src/camera.cpp
    src/light.cpp
    src/material.cpp
    src/mesh.cpp
    src/scene.cpp
    src/sceneMap.cpp)
add_library(gltfUsd::gltfUsd ALIAS gltfUsd)
usd_gltf_configure_target(gltfUsd)
target_include_directories(gltfUsd
    PUBLIC
        "$<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>"
        "$<INSTALL_INTERFACE:include>"
    PRIVATE
        "${CMAKE_CURRENT_SOURCE_DIR}/src")
target_link_libraries(gltfUsd PUBLIC gltfCore::gltfCore PRIVATE gltfTexture::gltfTexture)
usd_gltf_link_openusd(TARGET gltfUsd VISIBILITY PUBLIC
    COMPONENTS usdShade usdLux usdGeom usd sdf ar tf)

usd_gltf_library_installs(_usd_gltf_install)
if(_usd_gltf_install)
    usd_gltf_install_library(PACKAGE gltfUsd
        TARGETS gltfUsd
        DEPENDENCIES "pxr CONFIG" "gltfCore ${PROJECT_VERSION} CONFIG"
            "gltfTexture ${PROJECT_VERSION} CONFIG")
endif()

if(BUILD_TESTING)
    add_subdirectory(tests)
endif()
