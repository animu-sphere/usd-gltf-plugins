cmake_minimum_required(VERSION 3.22)

if(CMAKE_SOURCE_DIR STREQUAL CMAKE_CURRENT_SOURCE_DIR)
    project(UsdGltfFileFormatBundle LANGUAGES CXX)
    include(CTest)
    set(USD_GLTF_OUTPUT_ROOT "${CMAKE_CURRENT_SOURCE_DIR}")
else()
    set(USD_GLTF_OUTPUT_ROOT "${CMAKE_BINARY_DIR}")
endif()

find_package(pxr REQUIRED CONFIG)

function(usd_gltf_add_fastgltf)
    set(third_party_dir "${CMAKE_CURRENT_SOURCE_DIR}/../../third_party")
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

add_library(usd_gltf_core STATIC
    "${CMAKE_CURRENT_SOURCE_DIR}/../../libs/gltfCore/sceneSelection.cpp")
target_compile_features(usd_gltf_core PUBLIC cxx_std_20)
target_include_directories(usd_gltf_core PUBLIC
    "${CMAKE_CURRENT_SOURCE_DIR}/../../libs/gltfCore")
target_link_libraries(usd_gltf_core PUBLIC fastgltf::fastgltf)
set_target_properties(usd_gltf_core PROPERTIES POSITION_INDEPENDENT_CODE ON CXX_EXTENSIONS OFF)

add_library(UsdGltfFileFormat SHARED UsdGltfFileFormat.cpp)
target_compile_features(UsdGltfFileFormat PRIVATE cxx_std_20)
set_target_properties(UsdGltfFileFormat PROPERTIES CXX_EXTENSIONS OFF)
target_include_directories(UsdGltfFileFormat PRIVATE ${PXR_INCLUDE_DIRS})
target_link_libraries(UsdGltfFileFormat PRIVATE usd_gltf_core)
if(TARGET usd_ms)
    target_link_libraries(UsdGltfFileFormat PRIVATE usd_ms)
elseif(TARGET sdf AND TARGET tf)
    target_link_libraries(UsdGltfFileFormat PRIVATE sdf tf)
elseif(TARGET pxr::sdf AND TARGET pxr::tf)
    target_link_libraries(UsdGltfFileFormat PRIVATE pxr::sdf pxr::tf)
else()
    message(FATAL_ERROR "OpenUSD must provide usd_ms or the sdf and tf targets.")
endif()

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
    "${CMAKE_CURRENT_SOURCE_DIR}/../../third_party/NOTICES.txt"
    "${USD_GLTF_OUTPUT_ROOT}/plugin/resources/usdGltfFileFormat/NOTICES.txt"
    COPYONLY)
install(TARGETS UsdGltfFileFormat
    RUNTIME DESTINATION lib
    LIBRARY DESTINATION lib)
install(FILES
    "${USD_GLTF_OUTPUT_ROOT}/plugin/resources/usdGltfFileFormat/plugInfo.json"
    "${USD_GLTF_OUTPUT_ROOT}/plugin/resources/usdGltfFileFormat/NOTICES.txt"
    DESTINATION plugin/resources/usdGltfFileFormat)
install(FILES "${CMAKE_CURRENT_SOURCE_DIR}/../../third_party/NOTICES.txt"
    DESTINATION share/licenses/usd-gltf-plugins)

if(BUILD_TESTING)
    find_package(Python3 REQUIRED COMPONENTS Interpreter)
    add_executable(fastgltf_smoke "${CMAKE_CURRENT_SOURCE_DIR}/../../tests/fastgltf_smoke.cpp")
    target_link_libraries(fastgltf_smoke PRIVATE fastgltf::fastgltf)
    add_test(NAME fastgltf_smoke COMMAND fastgltf_smoke)
    add_executable(scene_selection_test "${CMAKE_CURRENT_SOURCE_DIR}/../../tests/scene_selection.cpp")
    target_link_libraries(scene_selection_test PRIVATE usd_gltf_core)
    add_test(NAME scene_selection COMMAND scene_selection_test)
    add_test(NAME file_format_registration
        COMMAND "${CMAKE_COMMAND}" -E env
            "PXR_PLUGINPATH_NAME=${USD_GLTF_OUTPUT_ROOT}/plugin/resources/usdGltfFileFormat"
            "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/../../tests/test_file_format.py")
    add_test(NAME layer_authoring
        COMMAND "${CMAKE_COMMAND}" -E env
            "PXR_PLUGINPATH_NAME=${USD_GLTF_OUTPUT_ROOT}/plugin/resources/usdGltfFileFormat"
            "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/../../tests/test_layer_authoring.py")
endif()
