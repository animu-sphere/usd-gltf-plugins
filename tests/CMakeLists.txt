# SPDX-License-Identifier: Apache-2.0
# The tests that need the whole workspace: the plugin as OpenUSD loads it, its install, and the
# repository's documents and versions. Each library's unit tests are in its own tests/.
find_package(Python3 REQUIRED COMPONENTS Interpreter)

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
add_test(NAME workspace_versions
    COMMAND "${Python3_EXECUTABLE}" "${USD_GLTF_REPOSITORY_ROOT}/scripts/make_release_notes.py"
        --check-versions)
