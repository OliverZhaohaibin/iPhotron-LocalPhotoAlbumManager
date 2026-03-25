cmake_minimum_required(VERSION 3.21)

project(osmand_render_helper_native LANGUAGES CXX)

if(NOT TARGET OsmAndCoreTools_shared)
    message(FATAL_ERROR "OsmAndCoreTools_shared must be available before building OsmAnd native tools")
endif()

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

find_package(Qt5Core REQUIRED CONFIG)
find_package(Qt5Network REQUIRED CONFIG)
find_package(Qt6 REQUIRED COMPONENTS Core Gui Widgets OpenGL OpenGLWidgets)

set(OSMAND_RENDER_HELPER_SOURCE_ROOT "${CMAKE_CURRENT_LIST_DIR}/.." CACHE PATH "Absolute path to the osmand_render_helper_native workspace root")
set(_helper_source_root "${OSMAND_RENDER_HELPER_SOURCE_ROOT}")
set(_helper_src_dir "${_helper_source_root}/src")
if(NOT EXISTS "${_helper_src_dir}/main.cpp")
    message(FATAL_ERROR "Unable to locate helper sources under '${_helper_src_dir}'. Set OSMAND_RENDER_HELPER_SOURCE_ROOT to the workspace helper root.")
endif()

set(_helper_common_sources
    "${_helper_src_dir}/file_system_core_resources_provider.cpp"
    "${_helper_src_dir}/file_system_core_resources_provider.h"
)

add_executable(osmand_render_helper
    ${_helper_common_sources}
    "${_helper_src_dir}/main.cpp"
)

add_library(osmand_native_widget SHARED
    ${_helper_common_sources}
    "${_helper_src_dir}/osmand_native_map_widget.cpp"
    "${_helper_src_dir}/osmand_native_map_widget.h"
    "${_helper_src_dir}/osmand_native_widget_bridge.cpp"
)

target_include_directories(osmand_render_helper
    PRIVATE
        "${_helper_src_dir}"
)

target_include_directories(osmand_native_widget
    PRIVATE
        "${_helper_src_dir}"
)

target_link_libraries(osmand_render_helper
    PRIVATE
        OsmAndCoreTools_shared
        Qt5::Core
        Qt5::Network
)

target_link_libraries(osmand_native_widget
    PRIVATE
        OsmAndCoreTools_shared
        Qt6::Core
        Qt6::Gui
        Qt6::Widgets
        Qt6::OpenGL
        Qt6::OpenGLWidgets
)

set_target_properties(osmand_render_helper PROPERTIES
    OUTPUT_NAME "osmand_render_helper"
)

set_target_properties(osmand_native_widget PROPERTIES
    OUTPUT_NAME "osmand_native_widget"
)

function(_copy_target_runtime_dll target_name source_path)
    add_custom_command(TARGET ${target_name} POST_BUILD
        COMMAND "${CMAKE_COMMAND}" -E copy_if_different
            "${source_path}"
            "$<TARGET_FILE_DIR:${target_name}>/"
        VERBATIM
    )
endfunction()

function(_copy_common_runtime_dlls target_name)
    foreach(_qt_target IN ITEMS Qt6::Core Qt6::Network Qt6::Gui Qt6::Widgets Qt6::OpenGL Qt6::OpenGLWidgets)
        if(TARGET ${_qt_target})
            _copy_target_runtime_dll(${target_name} "$<TARGET_FILE:${_qt_target}>")
        endif()
    endforeach()

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
                _copy_target_runtime_dll(${target_name} "${_helper_mingw_root}/bin/${_mingw_runtime}")
            endif()
        endforeach()
    endif()
endfunction()

_copy_common_runtime_dlls(osmand_render_helper)
_copy_common_runtime_dlls(osmand_native_widget)
