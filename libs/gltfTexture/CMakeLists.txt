# SPDX-License-Identifier: Apache-2.0
# gltfTexture: resolving glTF images from buffer views, data URIs and external files, and decoding
# KTX2 and WebP into PNG. It depends on gltfCore and not on OpenUSD.
#
# Its own CMake project, so that `ost library build libs/gltfTexture` can build it alone, and
# added with add_subdirectory() by the repository root and by the plugin.
cmake_minimum_required(VERSION 3.22)

include("${CMAKE_CURRENT_LIST_DIR}/../../cmake/UsdGltfBuild.cmake")
include("${CMAKE_CURRENT_LIST_DIR}/../../cmake/UsdGltfThirdParty.cmake")
usd_gltf_read_version(_usd_gltf_version)
# The Zstandard and WebP decoders are C.
project(gltfTexture
    VERSION ${_usd_gltf_version}
    DESCRIPTION "glTF image resolution and decoding for usd-gltf-plugins"
    LANGUAGES C CXX)
usd_gltf_default_build_type()
if(PROJECT_IS_TOP_LEVEL)
    include(CTest)
endif()

usd_gltf_require_library(gltfCore)
usd_gltf_add_basisu()
usd_gltf_add_webp()

add_library(gltfTexture STATIC
    src/image.cpp
    src/imageStructure.cpp
    src/ktx2.cpp
    src/webp.cpp)
add_library(gltfTexture::gltfTexture ALIAS gltfTexture)
usd_gltf_configure_target(gltfTexture)
target_include_directories(gltfTexture
    PUBLIC
        "$<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>"
        "$<INSTALL_INTERFACE:include>"
    PRIVATE
        "${CMAKE_CURRENT_SOURCE_DIR}/src")
target_link_libraries(gltfTexture PUBLIC gltfCore::gltfCore PRIVATE usd_gltf_basisu usd_gltf_webp)

usd_gltf_library_installs(_usd_gltf_install)
if(_usd_gltf_install)
    usd_gltf_install_library(PACKAGE gltfTexture
        TARGETS gltfTexture usd_gltf_basisu usd_gltf_webp
        DEPENDENCIES "gltfCore ${PROJECT_VERSION} CONFIG" "Threads")
endif()

if(BUILD_TESTING)
    add_subdirectory(tests)
endif()
