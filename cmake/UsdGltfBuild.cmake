# SPDX-License-Identifier: Apache-2.0
# Helpers shared by every member of the workspace: the repository root, each library under libs/
# and the plugin bundle. Each member is its own CMake project, so it includes this file itself, and
# it can be included any number of times.
include_guard(GLOBAL)

# A cache entry, so that it is visible in every directory although this file runs only once.
get_filename_component(_usd_gltf_root "${CMAKE_CURRENT_LIST_DIR}/.." ABSOLUTE)
set(USD_GLTF_REPOSITORY_ROOT "${_usd_gltf_root}" CACHE INTERNAL "The usd-gltf-plugins checkout")
unset(_usd_gltf_root)

# The release version, from the VERSION file at the repository root. Every descriptor mirrors it,
# and scripts/make_release_notes.py checks that they agree.
function(usd_gltf_read_version out)
    file(STRINGS "${USD_GLTF_REPOSITORY_ROOT}/VERSION" version LIMIT_COUNT 1)
    string(STRIP "${version}" version)
    set(${out} "${version}" PARENT_SCOPE)
endfunction()

# The installs we build against are Release only, so a single-config generator without a build
# type would otherwise pull debug-only imported dependencies.
macro(usd_gltf_default_build_type)
    get_property(_usd_gltf_multi_config GLOBAL PROPERTY GENERATOR_IS_MULTI_CONFIG)
    if(NOT _usd_gltf_multi_config AND NOT CMAKE_BUILD_TYPE)
        set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
    endif()
    unset(_usd_gltf_multi_config)
endmacro()

# C++20, no extensions, and hidden symbols. The plugin links every library statically, and a host
# process can load other versions of the vendored libraries, so nothing may bind to or replace
# their symbols. OpenUSD's own headers keep their default visibility.
function(usd_gltf_configure_target target)
    target_compile_features(${target} PUBLIC cxx_std_20)
    set_target_properties(${target} PROPERTIES
        CXX_EXTENSIONS OFF
        POSITION_INDEPENDENT_CODE ON
        C_VISIBILITY_PRESET hidden
        CXX_VISIBILITY_PRESET hidden
        VISIBILITY_INLINES_HIDDEN ON)
endfunction()

# Links OpenUSD's monolithic library when it has one, and otherwise the named components.
function(usd_gltf_link_openusd)
    cmake_parse_arguments(ARG "" "TARGET;VISIBILITY" "COMPONENTS" ${ARGN})
    if(NOT ARG_VISIBILITY)
        set(ARG_VISIBILITY PRIVATE)
    endif()
    if(NOT pxr_FOUND)
        message(FATAL_ERROR "usd_gltf_link_openusd: find_package(pxr) has not run")
    endif()
    foreach(monolithic usd_ms pxr::usd_ms)
        if(TARGET ${monolithic})
            target_link_libraries(${ARG_TARGET} ${ARG_VISIBILITY} ${monolithic})
            return()
        endif()
    endforeach()
    set(targets)
    set(missing)
    foreach(component IN LISTS ARG_COMPONENTS)
        if(TARGET ${component})
            list(APPEND targets ${component})
        elseif(TARGET pxr::${component})
            list(APPEND targets pxr::${component})
        else()
            list(APPEND missing ${component})
        endif()
    endforeach()
    if(missing)
        message(FATAL_ERROR "OpenUSD must provide usd_ms or these targets: ${missing}")
    endif()
    target_link_libraries(${ARG_TARGET} ${ARG_VISIBILITY} ${targets})
endfunction()

# Whether a library installs its development package: headers, archives and a CMake package
# configuration. That is what `ost library build` consumes, where the library is the top-level
# project. When the plugin builds a library in its own tree, the library is linked into the plugin
# and nothing of it is installed.
function(usd_gltf_library_installs out)
    if(DEFINED USD_GLTF_INSTALL_LIBRARIES)
        set(${out} ${USD_GLTF_INSTALL_LIBRARIES} PARENT_SCOPE)
    else()
        set(${out} ${PROJECT_IS_TOP_LEVEL} PARENT_SCOPE)
    endif()
endfunction()

# Installs a library's development package as <PACKAGE>::<PACKAGE>, with the vendored static
# libraries it links (TARGETS after the first) in the same export set, so that a consumer of the
# installed package links everything the archives need. HEADERS are further header directories
# installed below include/, and DEPENDENCIES are find_dependency() arguments, one string each.
function(usd_gltf_install_library)
    cmake_parse_arguments(ARG "" "PACKAGE" "TARGETS;HEADERS;DEPENDENCIES" ${ARGN})
    include(GNUInstallDirs)
    include(CMakePackageConfigHelpers)
    install(TARGETS ${ARG_TARGETS}
        EXPORT ${ARG_PACKAGE}Targets
        ARCHIVE DESTINATION "${CMAKE_INSTALL_LIBDIR}"
        LIBRARY DESTINATION "${CMAKE_INSTALL_LIBDIR}"
        RUNTIME DESTINATION "${CMAKE_INSTALL_BINDIR}"
        INCLUDES DESTINATION "${CMAKE_INSTALL_INCLUDEDIR}")
    install(DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/include/" ${ARG_HEADERS}
        DESTINATION "${CMAKE_INSTALL_INCLUDEDIR}")

    set(config_dir "${CMAKE_INSTALL_LIBDIR}/cmake/${ARG_PACKAGE}")
    set(USD_GLTF_PACKAGE "${ARG_PACKAGE}")
    set(USD_GLTF_FIND_DEPENDENCIES "")
    foreach(dependency IN LISTS ARG_DEPENDENCIES)
        string(APPEND USD_GLTF_FIND_DEPENDENCIES "find_dependency(${dependency})\n")
    endforeach()
    configure_package_config_file(
        "${USD_GLTF_REPOSITORY_ROOT}/cmake/UsdGltfLibraryConfig.cmake.in"
        "${CMAKE_CURRENT_BINARY_DIR}/${ARG_PACKAGE}Config.cmake"
        INSTALL_DESTINATION "${config_dir}")
    write_basic_package_version_file(
        "${CMAKE_CURRENT_BINARY_DIR}/${ARG_PACKAGE}ConfigVersion.cmake"
        VERSION "${PROJECT_VERSION}"
        COMPATIBILITY SameMinorVersion)
    install(EXPORT ${ARG_PACKAGE}Targets
        FILE ${ARG_PACKAGE}Targets.cmake
        NAMESPACE ${ARG_PACKAGE}::
        DESTINATION "${config_dir}")
    install(FILES
        "${CMAKE_CURRENT_BINARY_DIR}/${ARG_PACKAGE}Config.cmake"
        "${CMAKE_CURRENT_BINARY_DIR}/${ARG_PACKAGE}ConfigVersion.cmake"
        DESTINATION "${config_dir}")
endfunction()

# Makes the workspace's libraries available to the project that calls it: each one that is not a
# target yet is added from this repository. The plugin calls it when it is the top-level project,
# because it links the libraries statically and needs their sources rather than an installed
# package.
function(usd_gltf_add_libraries)
    foreach(library gltfCore gltfTexture gltfUsd)
        if(NOT TARGET ${library}::${library})
            add_subdirectory("${USD_GLTF_REPOSITORY_ROOT}/libs/${library}"
                "${CMAKE_BINARY_DIR}/libs/${library}")
        endif()
    endforeach()
endfunction()

# Finds a workspace library that is not already a target, which is the case when a library is
# the top-level project and its prerequisites are installed packages.
macro(usd_gltf_require_library library)
    if(NOT TARGET ${library}::${library})
        find_package(${library} "${PROJECT_VERSION}" CONFIG REQUIRED)
    endif()
endmacro()
