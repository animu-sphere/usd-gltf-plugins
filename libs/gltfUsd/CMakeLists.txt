# SPDX-License-Identifier: Apache-2.0
add_library(usd_gltf_usd STATIC
    camera.cpp
    material.cpp
    mesh.cpp
    scene.cpp)
target_compile_features(usd_gltf_usd PUBLIC cxx_std_20)
target_include_directories(usd_gltf_usd PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}" ${PXR_INCLUDE_DIRS})
target_link_libraries(usd_gltf_usd PUBLIC usd_gltf_core usd_gltf_texture)
set_target_properties(usd_gltf_usd PROPERTIES POSITION_INDEPENDENT_CODE ON CXX_EXTENSIONS OFF)

if(TARGET usd_ms)
    target_link_libraries(usd_gltf_usd PUBLIC usd_ms)
elseif(TARGET usdShade AND TARGET usdGeom AND TARGET usd AND TARGET sdf AND TARGET ar AND TARGET tf)
    target_link_libraries(usd_gltf_usd PUBLIC usdShade usdGeom usd sdf ar tf)
elseif(TARGET pxr::usdShade AND TARGET pxr::usdGeom AND TARGET pxr::usd AND TARGET pxr::sdf AND TARGET pxr::ar AND TARGET pxr::tf)
    target_link_libraries(usd_gltf_usd PUBLIC pxr::usdShade pxr::usdGeom pxr::usd pxr::sdf pxr::ar pxr::tf)
else()
    message(FATAL_ERROR "OpenUSD must provide usd_ms or the usdShade, usdGeom, usd, sdf, ar and tf targets.")
endif()
