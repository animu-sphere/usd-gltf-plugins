cmake_minimum_required(VERSION 3.22)

if(CMAKE_SOURCE_DIR STREQUAL CMAKE_CURRENT_SOURCE_DIR)
    project(UsdGltfFileFormatBundle LANGUAGES CXX)
    include(CTest)
    set(USD_GLTF_OUTPUT_ROOT "${CMAKE_CURRENT_SOURCE_DIR}")
else()
    set(USD_GLTF_OUTPUT_ROOT "${CMAKE_BINARY_DIR}")
endif()

find_package(pxr REQUIRED CONFIG)

add_library(UsdGltfFileFormat SHARED UsdGltfFileFormat.cpp)
target_compile_features(UsdGltfFileFormat PRIVATE cxx_std_20)
set_target_properties(UsdGltfFileFormat PROPERTIES CXX_EXTENSIONS OFF)
target_include_directories(UsdGltfFileFormat PRIVATE ${PXR_INCLUDE_DIRS})
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
install(TARGETS UsdGltfFileFormat
    RUNTIME DESTINATION lib
    LIBRARY DESTINATION lib)
install(FILES "${USD_GLTF_OUTPUT_ROOT}/plugin/resources/usdGltfFileFormat/plugInfo.json"
    DESTINATION plugin/resources/usdGltfFileFormat)

if(BUILD_TESTING)
    find_package(Python3 REQUIRED COMPONENTS Interpreter)
    add_test(NAME file_format_registration
        COMMAND "${CMAKE_COMMAND}" -E env
            "PXR_PLUGINPATH_NAME=${USD_GLTF_OUTPUT_ROOT}/plugin/resources/usdGltfFileFormat"
            "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/../../tests/test_file_format.py")
endif()
