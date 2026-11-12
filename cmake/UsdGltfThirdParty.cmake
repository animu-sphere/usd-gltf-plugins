# SPDX-License-Identifier: Apache-2.0
# Adds the vendored third-party libraries as static targets. Each library under libs/ adds the ones
# it links, and each function does nothing when its targets already exist, so the workspace builds
# them once however many members ask.
#
# Every one of them is hidden: a host process can load other versions of these libraries, so
# nothing in the plugin may bind to or replace their symbols. Their include directories are build
# interfaces only, except fastgltf's, whose headers the gltfCore headers include; the libraries
# that install them export the archives so that a consumer of the installed package can link.
include_guard(GLOBAL)

set(USD_GLTF_THIRD_PARTY_DIR "${CMAKE_CURRENT_LIST_DIR}/../third_party" CACHE INTERNAL "")
get_filename_component(USD_GLTF_THIRD_PARTY_DIR "${USD_GLTF_THIRD_PARTY_DIR}" ABSOLUTE)
set(USD_GLTF_THIRD_PARTY_DIR "${USD_GLTF_THIRD_PARTY_DIR}" CACHE INTERNAL "The vendored dependencies")

macro(_usd_gltf_hide_symbols)
    set(CMAKE_C_VISIBILITY_PRESET hidden)
    set(CMAKE_CXX_VISIBILITY_PRESET hidden)
    set(CMAKE_VISIBILITY_INLINES_HIDDEN ON)
    set(CMAKE_POSITION_INDEPENDENT_CODE ON)
endmacro()

# fastgltf, with the simdjson and simdutf it parses with. Owned by gltfCore.
function(usd_gltf_add_fastgltf)
    if(TARGET fastgltf::fastgltf)
        return()
    endif()
    _usd_gltf_hide_symbols()
    set(third_party_dir "${USD_GLTF_THIRD_PARTY_DIR}")

    add_library(usd_gltf_simdjson STATIC "${third_party_dir}/simdjson/simdjson.cpp")
    add_library(simdjson::simdjson ALIAS usd_gltf_simdjson)
    target_include_directories(usd_gltf_simdjson PUBLIC "$<BUILD_INTERFACE:${third_party_dir}/simdjson>")
    target_compile_features(usd_gltf_simdjson PUBLIC cxx_std_20)
    set_target_properties(usd_gltf_simdjson PROPERTIES EXPORT_NAME simdjson)

    add_library(usd_gltf_simdutf STATIC "${third_party_dir}/simdutf/simdutf.cpp")
    add_library(simdutf::simdutf ALIAS usd_gltf_simdutf)
    target_include_directories(usd_gltf_simdutf PUBLIC "$<BUILD_INTERFACE:${third_party_dir}/simdutf>")
    target_compile_features(usd_gltf_simdutf PUBLIC cxx_std_20)
    target_compile_definitions(usd_gltf_simdutf PUBLIC
        SIMDUTF_FEATURE_BASE64=1
        SIMDUTF_FEATURE_DETECT_ENCODING=0
        SIMDUTF_FEATURE_ASCII=0
        SIMDUTF_FEATURE_LATIN1=0
        SIMDUTF_FEATURE_UTF8=0
        SIMDUTF_FEATURE_UTF16=0
        SIMDUTF_FEATURE_UTF32=0)
    set_target_properties(usd_gltf_simdutf PROPERTIES EXPORT_NAME simdutf)

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
    add_subdirectory("${third_party_dir}/fastgltf" "${CMAKE_BINARY_DIR}/third_party/fastgltf")
endfunction()

# Draco's glTF bitstream decoder. Owned by gltfCore.
function(usd_gltf_add_draco)
    if(TARGET usd_gltf_draco)
        return()
    endif()
    _usd_gltf_hide_symbols()
    set(draco_src "${USD_GLTF_THIRD_PARTY_DIR}/draco/src")
    file(GLOB_RECURSE draco_sources CONFIGURE_DEPENDS "${draco_src}/draco/*.cc")
    add_library(usd_gltf_draco STATIC ${draco_sources})
    target_include_directories(usd_gltf_draco SYSTEM PUBLIC
        "$<BUILD_INTERFACE:${draco_src}>"
        "$<BUILD_INTERFACE:${USD_GLTF_REPOSITORY_ROOT}/cmake/draco>")
    target_compile_features(usd_gltf_draco PUBLIC cxx_std_20)
    if(MSVC)
        target_compile_options(usd_gltf_draco PRIVATE /utf-8)
    endif()
    set_target_properties(usd_gltf_draco PROPERTIES EXPORT_NAME draco)
endfunction()

# The Basis Universal transcoder, for KTX2, with its Zstandard decoder. Owned by gltfTexture.
function(usd_gltf_add_basisu)
    if(TARGET usd_gltf_basisu)
        return()
    endif()
    _usd_gltf_hide_symbols()
    set(basisu_dir "${USD_GLTF_THIRD_PARTY_DIR}/basis_universal")
    add_library(usd_gltf_basisu STATIC
        "${basisu_dir}/transcoder/basisu_transcoder.cpp"
        "${basisu_dir}/zstd/zstddeclib.c")
    target_include_directories(usd_gltf_basisu PUBLIC "$<BUILD_INTERFACE:${basisu_dir}/transcoder>")
    target_compile_features(usd_gltf_basisu PUBLIC cxx_std_20)
    target_compile_definitions(usd_gltf_basisu PUBLIC
        BASISD_SUPPORT_KTX2=1
        BASISD_SUPPORT_KTX2_ZSTD=1)
    # A host process can load its own Zstandard, so these symbols must bind locally.
    target_compile_definitions(usd_gltf_basisu PRIVATE
        ZSTDLIB_VISIBLE=
        ZSTDERRORLIB_VISIBLE=)
    if(MSVC)
        target_compile_options(usd_gltf_basisu PRIVATE /bigobj /utf-8)
    endif()
    set_target_properties(usd_gltf_basisu PROPERTIES EXPORT_NAME basisu)
endfunction()

# The libwebp decoder. Owned by gltfTexture.
function(usd_gltf_add_webp)
    if(TARGET usd_gltf_webp)
        return()
    endif()
    _usd_gltf_hide_symbols()
    set(webp_dir "${USD_GLTF_THIRD_PARTY_DIR}/libwebp")
    file(GLOB webp_sources CONFIGURE_DEPENDS
        "${webp_dir}/src/dec/*.c"
        "${webp_dir}/src/dsp/*.c"
        "${webp_dir}/src/utils/*.c")
    add_library(usd_gltf_webp STATIC ${webp_sources})
    # libwebp includes its own headers from the directory above src/, and consumers include
    # webp/decode.h.
    target_include_directories(usd_gltf_webp
        PRIVATE "${webp_dir}"
        PUBLIC "$<BUILD_INTERFACE:${webp_dir}/src>")
    # WEBP_EXTERN otherwise gives the API default visibility on GCC and Clang, and a host process
    # can load its own libwebp, so these symbols must bind locally.
    target_compile_definitions(usd_gltf_webp PUBLIC "WEBP_EXTERN=extern")
    # As in upstream's own build. The resolver can decode on several threads at once, and this
    # makes libwebp take a lock around its one-time initialization outside Windows.
    find_package(Threads REQUIRED)
    target_compile_definitions(usd_gltf_webp PRIVATE WEBP_USE_THREAD)
    target_link_libraries(usd_gltf_webp PUBLIC Threads::Threads)
    if(MSVC)
        target_compile_options(usd_gltf_webp PRIVATE /utf-8)
    endif()
    set_target_properties(usd_gltf_webp PROPERTIES EXPORT_NAME webp)
endfunction()
