cmake_minimum_required(VERSION 3.24)

project(osmand_render_helper_native LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

set(OSMAND_VENDOR_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/vendor/osmand" CACHE PATH "Directory that contains 'core' and 'core-legacy' directories or junctions")
set(QT_ROOT "C:/Qt/6.10.1/mingw_64" CACHE PATH "Qt 6 SDK root used to build the helper")
set(MINGW_ROOT "C:/Qt/Tools/mingw1310_64" CACHE PATH "MinGW toolchain root used to build the helper")

if(NOT EXISTS "${OSMAND_VENDOR_ROOT}/core/CMakeLists.txt")
    message(FATAL_ERROR "Missing '${OSMAND_VENDOR_ROOT}/core/CMakeLists.txt'. Run build_helper.ps1 to prepare the local vendor junctions.")
endif()
if(NOT EXISTS "${OSMAND_VENDOR_ROOT}/core-legacy/CMakeLists.txt")
    message(FATAL_ERROR "Missing '${OSMAND_VENDOR_ROOT}/core-legacy/CMakeLists.txt'. Run build_helper.ps1 to prepare the local vendor junctions.")
endif()

list(PREPEND CMAKE_PREFIX_PATH "${QT_ROOT}")
set(Qt5Core_DIR "${CMAKE_CURRENT_SOURCE_DIR}/cmake/Qt5Core" CACHE PATH "Qt5Core compatibility shim")
set(Qt5Network_DIR "${CMAKE_CURRENT_SOURCE_DIR}/cmake/Qt5Network" CACHE PATH "Qt5Network compatibility shim")

find_package(Qt6 REQUIRED COMPONENTS Core Network Gui Widgets OpenGL OpenGLWidgets)

set(OSMAND_ROOT "${OSMAND_VENDOR_ROOT}" CACHE PATH "Synthetic OsmAnd root that exposes core and core-legacy side by side")
set(OSMAND_OWN_SHIPPED_QT OFF CACHE BOOL "Use system Qt instead of OsmAnd-shipped Qt")
set(CMAKE_TARGET_OS "windows" CACHE STRING "OsmAnd target OS")
set(CMAKE_COMPILER_FAMILY "gcc" CACHE STRING "OsmAnd compiler family")
set(CMAKE_TARGET_CPU_ARCH "amd64" CACHE STRING "OsmAnd CPU architecture")
set(CMAKE_TARGET_CPU_ARCH_FAMILY "x86" CACHE STRING "OsmAnd CPU architecture family")
set(CMAKE_TARGET_CPU_NAME "amd64" CACHE STRING "OsmAnd CPU name")
set(CMAKE_SHARED_LIBS_ALLOWED_ON_TARGET OFF CACHE BOOL "Build shared libraries when supported")
set(CMAKE_STATIC_LIBS_ALLOWED_ON_TARGET ON CACHE BOOL "Build static libraries when supported")
set(ws2_32_LIBRARY ws2_32 CACHE STRING "Winsock library used by MinGW builds")

include("${OSMAND_VENDOR_ROOT}/core/core.cmake")

set(OSMAND_COMMON_SOURCES
    src/file_system_core_resources_provider.cpp
    src/file_system_core_resources_provider.h
)

add_executable(osmand_render_helper
    ${OSMAND_COMMON_SOURCES}
    src/main.cpp
)

add_library(osmand_native_widget SHARED
    ${OSMAND_COMMON_SOURCES}
    src/osmand_native_map_widget.cpp
    src/osmand_native_map_widget.h
    src/osmand_native_widget_bridge.cpp
)

target_link_libraries(osmand_render_helper
    PRIVATE
        OsmAndCoreTools_static
        Qt6::Core
        Qt6::Network
        Qt6::Gui
        Qt6::Widgets
)

target_link_libraries(osmand_native_widget
    PRIVATE
        OsmAndCoreTools_static
        Qt6::Core
        Qt6::Gui
        Qt6::Widgets
        Qt6::OpenGL
        Qt6::OpenGLWidgets
)

target_include_directories(osmand_render_helper
    PRIVATE
        "${CMAKE_CURRENT_SOURCE_DIR}/src"
)

target_include_directories(osmand_native_widget
    PRIVATE
        "${CMAKE_CURRENT_SOURCE_DIR}/src"
)

set_target_properties(osmand_render_helper PROPERTIES
    OUTPUT_NAME "osmand_render_helper"
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/dist"
    RUNTIME_OUTPUT_DIRECTORY_DEBUG "${CMAKE_CURRENT_SOURCE_DIR}/dist"
    RUNTIME_OUTPUT_DIRECTORY_RELEASE "${CMAKE_CURRENT_SOURCE_DIR}/dist"
    RUNTIME_OUTPUT_DIRECTORY_RELWITHDEBINFO "${CMAKE_CURRENT_SOURCE_DIR}/dist"
    RUNTIME_OUTPUT_DIRECTORY_MINSIZEREL "${CMAKE_CURRENT_SOURCE_DIR}/dist"
)

set_target_properties(osmand_native_widget PROPERTIES
    PREFIX ""
    OUTPUT_NAME "osmand_native_widget"
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/dist"
    RUNTIME_OUTPUT_DIRECTORY_DEBUG "${CMAKE_CURRENT_SOURCE_DIR}/dist"
    RUNTIME_OUTPUT_DIRECTORY_RELEASE "${CMAKE_CURRENT_SOURCE_DIR}/dist"
    RUNTIME_OUTPUT_DIRECTORY_RELWITHDEBINFO "${CMAKE_CURRENT_SOURCE_DIR}/dist"
    RUNTIME_OUTPUT_DIRECTORY_MINSIZEREL "${CMAKE_CURRENT_SOURCE_DIR}/dist"
    LIBRARY_OUTPUT_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/dist"
    LIBRARY_OUTPUT_DIRECTORY_DEBUG "${CMAKE_CURRENT_SOURCE_DIR}/dist"
    LIBRARY_OUTPUT_DIRECTORY_RELEASE "${CMAKE_CURRENT_SOURCE_DIR}/dist"
    LIBRARY_OUTPUT_DIRECTORY_RELWITHDEBINFO "${CMAKE_CURRENT_SOURCE_DIR}/dist"
    LIBRARY_OUTPUT_DIRECTORY_MINSIZEREL "${CMAKE_CURRENT_SOURCE_DIR}/dist"
)

set(_helper_dist_dir "${CMAKE_CURRENT_SOURCE_DIR}/dist")
set(_windeployqt "${QT_ROOT}/bin/windeployqt.exe")

if(EXISTS "${_windeployqt}")
    add_custom_command(TARGET osmand_render_helper POST_BUILD
        COMMAND "${_windeployqt}"
            --no-translations
            --no-system-d3d-compiler
            --no-opengl-sw
            "$<TARGET_FILE:osmand_render_helper>"
        COMMAND_EXPAND_LISTS
        VERBATIM
    )
    add_custom_command(TARGET osmand_native_widget POST_BUILD
        COMMAND "${_windeployqt}"
            --no-translations
            --no-system-d3d-compiler
            --no-opengl-sw
            "$<TARGET_FILE:osmand_native_widget>"
        COMMAND_EXPAND_LISTS
        VERBATIM
    )
endif()

foreach(_mingw_runtime IN ITEMS libgcc_s_seh-1.dll libstdc++-6.dll libwinpthread-1.dll)
    if(EXISTS "${MINGW_ROOT}/bin/${_mingw_runtime}")
        add_custom_command(TARGET osmand_render_helper POST_BUILD
            COMMAND "${CMAKE_COMMAND}" -E copy_if_different
                "${MINGW_ROOT}/bin/${_mingw_runtime}"
                "${_helper_dist_dir}/${_mingw_runtime}"
            VERBATIM
        )
        add_custom_command(TARGET osmand_native_widget POST_BUILD
            COMMAND "${CMAKE_COMMAND}" -E copy_if_different
                "${MINGW_ROOT}/bin/${_mingw_runtime}"
                "${_helper_dist_dir}/${_mingw_runtime}"
            VERBATIM
        )
    endif()
endforeach()
