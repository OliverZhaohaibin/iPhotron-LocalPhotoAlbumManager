cmake_minimum_required(VERSION 3.21)

project(osmand_render_helper_native LANGUAGES CXX)

if(NOT TARGET OsmAndCoreTools_shared)
    message(FATAL_ERROR "OsmAndCoreTools_shared must be available before building osmand_render_helper")
endif()

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

find_package(Qt5Core REQUIRED CONFIG)
find_package(Qt5Network REQUIRED CONFIG)

set(OSMAND_RENDER_HELPER_SOURCE_ROOT "${CMAKE_CURRENT_LIST_DIR}/.." CACHE PATH "Absolute path to the osmand_render_helper_native workspace root")
set(_helper_source_root "${OSMAND_RENDER_HELPER_SOURCE_ROOT}")
set(_helper_src_dir "${_helper_source_root}/src")
if(NOT EXISTS "${_helper_src_dir}/main.cpp")
    message(FATAL_ERROR "Unable to locate helper sources under '${_helper_src_dir}'. Set OSMAND_RENDER_HELPER_SOURCE_ROOT to the workspace helper root.")
endif()

set(_helper_sources
    "${_helper_src_dir}/file_system_core_resources_provider.cpp"
    "${_helper_src_dir}/file_system_core_resources_provider.h"
    "${_helper_src_dir}/main.cpp"
)

add_executable(osmand_render_helper ${_helper_sources})

target_include_directories(osmand_render_helper
    PRIVATE
        "${_helper_src_dir}"
)

target_link_libraries(osmand_render_helper
    PRIVATE
        OsmAndCoreTools_shared
        Qt5::Core
        Qt5::Network
)

set_target_properties(osmand_render_helper PROPERTIES
    OUTPUT_NAME "osmand_render_helper"
)

function(_copy_helper_runtime_dll source_path)
    add_custom_command(TARGET osmand_render_helper POST_BUILD
        COMMAND "${CMAKE_COMMAND}" -E copy_if_different
            "${source_path}"
            "$<TARGET_FILE_DIR:osmand_render_helper>/"
        VERBATIM
    )
endfunction()

if(TARGET Qt6::Core)
    _copy_helper_runtime_dll("$<TARGET_FILE:Qt6::Core>")
endif()

if(TARGET Qt6::Network)
    _copy_helper_runtime_dll("$<TARGET_FILE:Qt6::Network>")
endif()

if(DEFINED OSMAND_MINGW_ROOT)
    set(_helper_mingw_root "${OSMAND_MINGW_ROOT}")
elseif(DEFINED MINGW_ROOT)
    set(_helper_mingw_root "${MINGW_ROOT}")
else()
    set(_helper_mingw_root "")
endif()

if(_helper_mingw_root)
    foreach(_mingw_runtime IN ITEMS libgcc_s_seh-1.dll libstdc++-6.dll libwinpthread-1.dll)
        if(EXISTS "${_helper_mingw_root}/bin/${_mingw_runtime}")
            _copy_helper_runtime_dll("${_helper_mingw_root}/bin/${_mingw_runtime}")
        endif()
    endforeach()
endif()


