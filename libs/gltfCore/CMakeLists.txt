# SPDX-License-Identifier: Apache-2.0
# gltfCore: parsing with fastgltf, and checked access to glTF data, URIs and buffers. It does not
# depend on OpenUSD.
#
# Its own CMake project, so that `ost library build libs/gltfCore` can build it alone, and added
# with add_subdirectory() by the repository root and by the plugin.
cmake_minimum_required(VERSION 3.22)

include("${CMAKE_CURRENT_LIST_DIR}/../../cmake/UsdGltfBuild.cmake")
include("${CMAKE_CURRENT_LIST_DIR}/../../cmake/UsdGltfThirdParty.cmake")
usd_gltf_read_version(_usd_gltf_version)
project(gltfCore
    VERSION ${_usd_gltf_version}
    DESCRIPTION "glTF parsing and checked data access for usd-gltf-plugins"
    LANGUAGES CXX)
usd_gltf_default_build_type()
if(PROJECT_IS_TOP_LEVEL)
    include(CTest)
endif()

usd_gltf_add_fastgltf()
usd_gltf_add_draco()
usd_gltf_add_meshoptimizer()

add_library(gltfCore STATIC
    src/accessor.cpp
    src/buffer.cpp
    src/draco.cpp
    src/imageBasedLight.cpp
    src/localFile.cpp
    src/parse.cpp
    src/sceneSelection.cpp
    src/xmp.cpp)
add_library(gltfCore::gltfCore ALIAS gltfCore)
usd_gltf_configure_target(gltfCore)
target_include_directories(gltfCore PUBLIC
    "$<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>"
    "$<INSTALL_INTERFACE:include>")
target_link_libraries(gltfCore PUBLIC fastgltf::fastgltf
    PRIVATE simdjson::simdjson usd_gltf_draco usd_gltf_meshoptimizer)

usd_gltf_library_installs(_usd_gltf_install)
if(_usd_gltf_install)
    # The gltfCore headers include fastgltf's.
    usd_gltf_install_library(PACKAGE gltfCore
        TARGETS gltfCore fastgltf usd_gltf_simdjson usd_gltf_simdutf usd_gltf_draco usd_gltf_meshoptimizer
        HEADERS "${USD_GLTF_THIRD_PARTY_DIR}/fastgltf/include/fastgltf")
    install(FILES "${USD_GLTF_THIRD_PARTY_DIR}/draco/LICENSE"
                  "${USD_GLTF_THIRD_PARTY_DIR}/draco/AUTHORS"
        DESTINATION "share/licenses/gltfCore/draco")
    install(FILES "${USD_GLTF_THIRD_PARTY_DIR}/meshoptimizer/LICENSE.md"
        DESTINATION "share/licenses/gltfCore/meshoptimizer")
endif()

if(BUILD_TESTING)
    add_subdirectory(tests)
endif()
