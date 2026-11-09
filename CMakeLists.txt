# SPDX-License-Identifier: Apache-2.0
# The workspace root. It builds every member with plain CMake and an OpenUSD install, and it is the
# project `ost build` and `ost test` drive:
#
#   cmake -S . -B build -DCMAKE_PREFIX_PATH=<openusd-prefix>
#   cmake --build build --config Release
#   ctest --test-dir build -C Release
#
# It resolves OpenUSD once, adds the libraries under libs/ and the bundles under plugins/, and
# registers the tests that need the whole workspace. Each member is a CMake project of its own, so
# that `ost library build` and `ost plugin build` can build it alone; the member list that
# OpenStrata reads is openstrata.toml.
cmake_minimum_required(VERSION 3.22)

include("${CMAKE_CURRENT_LIST_DIR}/cmake/UsdGltfBuild.cmake")
usd_gltf_read_version(_usd_gltf_version)
project(UsdGltfPlugins
    VERSION ${_usd_gltf_version}
    DESCRIPTION "OpenUSD plugins for glTF 2.0"
    LANGUAGES C CXX)
usd_gltf_default_build_type()
include(CTest)

option(USD_GLTF_INSTALL_LIBRARIES
    "Also install the libraries' headers, archives and CMake packages" OFF)
set(USD_GLTF_OUTPUT_ROOT "${CMAKE_BINARY_DIR}")

find_package(pxr REQUIRED CONFIG)

# In dependency order. gltfCore and gltfTexture do not use OpenUSD.
add_subdirectory(libs/gltfCore)
add_subdirectory(libs/gltfTexture)
add_subdirectory(libs/gltfUsd)

# Every directory under plugins/ with a bundle manifest and a CMakeLists.txt, as in OpenStrata's
# plugin-workspace template, so that a bundle made with `ost plugin new` is built without an edit
# here. openstrata.toml still has to list it.
file(GLOB _usd_gltf_bundles LIST_DIRECTORIES true CONFIGURE_DEPENDS RELATIVE
    "${CMAKE_CURRENT_SOURCE_DIR}/plugins" "${CMAKE_CURRENT_SOURCE_DIR}/plugins/*")
foreach(_bundle IN LISTS _usd_gltf_bundles)
    set(_bundle_dir "${CMAKE_CURRENT_SOURCE_DIR}/plugins/${_bundle}")
    if(EXISTS "${_bundle_dir}/openstrata.plugin.yaml" AND EXISTS "${_bundle_dir}/CMakeLists.txt")
        message(STATUS "usd-gltf-plugins: adding plugin bundle plugins/${_bundle}")
        add_subdirectory("plugins/${_bundle}")
    endif()
endforeach()

if(BUILD_TESTING)
    add_subdirectory(tests)
endif()
