find_package(Python3 REQUIRED COMPONENTS Interpreter)

add_executable(fastgltf_smoke fastgltf_smoke.cpp)
target_link_libraries(fastgltf_smoke PRIVATE fastgltf::fastgltf)
add_test(NAME fastgltf_smoke COMMAND fastgltf_smoke)

add_executable(scene_selection_test scene_selection.cpp)
target_link_libraries(scene_selection_test PRIVATE usd_gltf_core)
add_test(NAME scene_selection COMMAND scene_selection_test)

add_executable(accessor_test accessor.cpp)
target_link_libraries(accessor_test PRIVATE usd_gltf_core)
add_test(NAME accessor COMMAND accessor_test)

add_test(NAME file_format_registration
    COMMAND "${CMAKE_COMMAND}" -E env
        "PXR_PLUGINPATH_NAME=${USD_GLTF_OUTPUT_ROOT}/plugin/resources/usdGltfFileFormat"
        "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/test_file_format.py")
add_test(NAME layer_authoring
    COMMAND "${CMAKE_COMMAND}" -E env
        "PXR_PLUGINPATH_NAME=${USD_GLTF_OUTPUT_ROOT}/plugin/resources/usdGltfFileFormat"
        "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/test_layer_authoring.py")
add_test(NAME install_smoke
    COMMAND "${CMAKE_COMMAND}" -E env
        "USD_GLTF_TEST_CMAKE=${CMAKE_COMMAND}"
        "USD_GLTF_TEST_BUILD_DIR=${CMAKE_BINARY_DIR}"
        "USD_GLTF_TEST_CONFIG=$<CONFIG>"
        "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/test_install_smoke.py")
add_test(NAME docs_consistency
    COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/check_docs.py")
