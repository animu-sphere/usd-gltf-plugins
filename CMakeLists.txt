cmake_minimum_required(VERSION 3.22)
project(UsdGltfPlugins LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

find_package(pxr REQUIRED CONFIG)

add_library(UsdGltfFileFormat SHARED
    plugins/usdGltfFileFormat/UsdGltfFileFormat.cpp)
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
    LIBRARY_OUTPUT_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}/lib"
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}/lib")
foreach(config DEBUG RELEASE RELWITHDEBINFO MINSIZEREL)
    set_target_properties(UsdGltfFileFormat PROPERTIES
        LIBRARY_OUTPUT_DIRECTORY_${config} "${CMAKE_CURRENT_BINARY_DIR}/lib"
        RUNTIME_OUTPUT_DIRECTORY_${config} "${CMAKE_CURRENT_BINARY_DIR}/lib")
endforeach()

configure_file(
    plugins/usdGltfFileFormat/plugInfo.json.in
    "${CMAKE_CURRENT_BINARY_DIR}/plugin/resources/usdGltfFileFormat/plugInfo.json"
    @ONLY)
install(TARGETS UsdGltfFileFormat
    RUNTIME DESTINATION lib
    LIBRARY DESTINATION lib)
install(FILES "${CMAKE_CURRENT_BINARY_DIR}/plugin/resources/usdGltfFileFormat/plugInfo.json"
    DESTINATION plugin/resources/usdGltfFileFormat)

include(CTest)
if(BUILD_TESTING)
    find_package(Python3 REQUIRED COMPONENTS Interpreter)
    add_test(NAME file_format_registration
        COMMAND "${CMAKE_COMMAND}" -E env
            "PXR_PLUGINPATH_NAME=${CMAKE_CURRENT_BINARY_DIR}/plugin/resources/usdGltfFileFormat"
            "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/test_file_format.py")
endif()
