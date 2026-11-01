# SPDX-License-Identifier: Apache-2.0
# Finds OpenUSD and adds the vendored glTF parsing libraries. Included by both
# build entry points: the repository root and the standalone plugin bundle.

get_filename_component(USD_GLTF_REPOSITORY_ROOT "${CMAKE_CURRENT_LIST_DIR}/.." ABSOLUTE)

find_package(pxr REQUIRED CONFIG)

function(usd_gltf_add_fastgltf)
    set(third_party_dir "${USD_GLTF_REPOSITORY_ROOT}/third_party")
    add_library(usd_gltf_simdjson STATIC "${third_party_dir}/simdjson/simdjson.cpp")
    add_library(simdjson::simdjson ALIAS usd_gltf_simdjson)
    target_include_directories(usd_gltf_simdjson PUBLIC "${third_party_dir}/simdjson")
    target_compile_features(usd_gltf_simdjson PUBLIC cxx_std_20)
    set_target_properties(usd_gltf_simdjson PROPERTIES POSITION_INDEPENDENT_CODE ON)

    add_library(usd_gltf_simdutf STATIC "${third_party_dir}/simdutf/simdutf.cpp")
    add_library(simdutf::simdutf ALIAS usd_gltf_simdutf)
    target_include_directories(usd_gltf_simdutf PUBLIC "${third_party_dir}/simdutf")
    target_compile_features(usd_gltf_simdutf PUBLIC cxx_std_20)
    target_compile_definitions(usd_gltf_simdutf PUBLIC
        SIMDUTF_FEATURE_BASE64=1
        SIMDUTF_FEATURE_DETECT_ENCODING=0
        SIMDUTF_FEATURE_ASCII=0
        SIMDUTF_FEATURE_LATIN1=0
        SIMDUTF_FEATURE_UTF8=0
        SIMDUTF_FEATURE_UTF16=0
        SIMDUTF_FEATURE_UTF32=0)
    set_target_properties(usd_gltf_simdutf PROPERTIES POSITION_INDEPENDENT_CODE ON)

    # These settings stay in this function's scope.
    set(BUILD_SHARED_LIBS OFF)
    set(FASTGLTF_ENABLE_INSTALL OFF)
    set(FASTGLTF_ENABLE_TESTS OFF)
    set(FASTGLTF_ENABLE_EXAMPLES OFF)
    set(FASTGLTF_ENABLE_DOCS OFF)
    set(FASTGLTF_ENABLE_FUZZING OFF)
    set(FASTGLTF_ENABLE_CPP_MODULES OFF)
    set(FASTGLTF_DOWNLOAD_SIMDJSON OFF)
    set(FASTGLTF_DOWNLOAD_SIMDUTF OFF)
    add_subdirectory("${third_party_dir}/fastgltf" "${CMAKE_CURRENT_BINARY_DIR}/fastgltf")
    set_target_properties(fastgltf PROPERTIES POSITION_INDEPENDENT_CODE ON)
endfunction()
usd_gltf_add_fastgltf()
