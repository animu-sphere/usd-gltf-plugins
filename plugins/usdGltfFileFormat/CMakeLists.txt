# SPDX-License-Identifier: Apache-2.0
# usdGltfFileFormat: the SdfFileFormat for .gltf and .glb, and the ArPackageResolver that serves
# their embedded images.
#
# Its own CMake project, so that `ost plugin build plugins/usdGltfFileFormat` can build it alone,
# and added with add_subdirectory() by the repository root. The library goes to lib/ and its
# registration to plugin/resources/usdGltfFileFormat/ below the output root, and installs to the
# same layout.
cmake_minimum_required(VERSION 3.22)

include("${CMAKE_CURRENT_LIST_DIR}/../../cmake/UsdGltfBuild.cmake")
usd_gltf_read_version(_usd_gltf_version)
project(UsdGltfFileFormatPlugin
    VERSION ${_usd_gltf_version}
    DESCRIPTION "OpenUSD file format for glTF 2.0"
    LANGUAGES CXX)
usd_gltf_default_build_type()

if(NOT pxr_FOUND)
    find_package(pxr REQUIRED CONFIG)
endif()

# The libraries are linked statically, so a standalone build adds them from source rather than
# finding installed packages, and does not build their tests, which run in the workspace build.
# The bundle manifest declares no library requirement for the same reason: OpenStrata would stage
# the libraries' archives and headers in the bundle's package.
if(PROJECT_IS_TOP_LEVEL)
    set(BUILD_TESTING OFF)
    usd_gltf_add_libraries()
endif()
# The root sets the build directory. Standalone, the outputs go to this build's directory: every
# `ost plugin` command reads the installed stage, `view` and `test-view` since OpenStrata 0.23.20.
if(NOT DEFINED USD_GLTF_OUTPUT_ROOT)
    set(USD_GLTF_OUTPUT_ROOT "${CMAKE_CURRENT_BINARY_DIR}")
endif()
set(_usd_gltf_resources "${USD_GLTF_OUTPUT_ROOT}/plugin/resources/usdGltfFileFormat")

add_library(UsdGltfFileFormat SHARED
    src/UsdGltfFileFormat.cpp
    src/UsdGltfPackageResolver.cpp
    src/externalBuffer.cpp)
usd_gltf_configure_target(UsdGltfFileFormat)
target_include_directories(UsdGltfFileFormat PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/src")
target_link_libraries(UsdGltfFileFormat PRIVATE
    gltfCore::gltfCore
    gltfTexture::gltfTexture
    gltfUsd::gltfUsd)
usd_gltf_link_openusd(TARGET UsdGltfFileFormat COMPONENTS sdf ar tf)

# plugInfo.json names the library as ../../../lib/, relative to itself.
set_target_properties(UsdGltfFileFormat PROPERTIES
    PREFIX "lib"
    LIBRARY_OUTPUT_DIRECTORY "${USD_GLTF_OUTPUT_ROOT}/lib"
    RUNTIME_OUTPUT_DIRECTORY "${USD_GLTF_OUTPUT_ROOT}/lib")
foreach(_config DEBUG RELEASE RELWITHDEBINFO MINSIZEREL)
    set_target_properties(UsdGltfFileFormat PROPERTIES
        LIBRARY_OUTPUT_DIRECTORY_${_config} "${USD_GLTF_OUTPUT_ROOT}/lib"
        RUNTIME_OUTPUT_DIRECTORY_${_config} "${USD_GLTF_OUTPUT_ROOT}/lib")
endforeach()

configure_file(
    plugin/resources/usdGltfFileFormat/plugInfo.json.in
    "${_usd_gltf_resources}/plugInfo.json"
    @ONLY)
# A bundle package holds only the plugin's own tree, so the notices and the license are in it too.
configure_file("${USD_GLTF_REPOSITORY_ROOT}/third_party/NOTICES.txt" "${_usd_gltf_resources}/NOTICES.txt"
    COPYONLY)
configure_file("${USD_GLTF_REPOSITORY_ROOT}/LICENSE" "${_usd_gltf_resources}/LICENSE" COPYONLY)

install(TARGETS UsdGltfFileFormat
    RUNTIME DESTINATION lib
    LIBRARY DESTINATION lib)
install(FILES
    "${_usd_gltf_resources}/plugInfo.json"
    "${_usd_gltf_resources}/NOTICES.txt"
    "${_usd_gltf_resources}/LICENSE"
    DESTINATION plugin/resources/usdGltfFileFormat)
install(FILES
    "${USD_GLTF_REPOSITORY_ROOT}/LICENSE"
    "${USD_GLTF_REPOSITORY_ROOT}/third_party/NOTICES.txt"
    DESTINATION share/licenses/usd-gltf-plugins)
