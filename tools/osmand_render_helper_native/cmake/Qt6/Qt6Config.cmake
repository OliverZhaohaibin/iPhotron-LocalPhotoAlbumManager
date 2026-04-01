include_guard(GLOBAL)

function(_osmand_qt6_fail message_text)
    set(Qt6_FOUND FALSE PARENT_SCOPE)
    message(FATAL_ERROR "${message_text}")
endfunction()

get_filename_component(_qt6_config_dir "${CMAKE_CURRENT_LIST_FILE}" DIRECTORY)
get_filename_component(_qt6_helper_root "${_qt6_config_dir}/../.." ABSOLUTE)

if(NOT DEFINED PYSIDE6_ROOT OR PYSIDE6_ROOT STREQUAL "")
    if(DEFINED ENV{PYSIDE6_ROOT} AND NOT "$ENV{PYSIDE6_ROOT}" STREQUAL "")
        set(PYSIDE6_ROOT "$ENV{PYSIDE6_ROOT}")
    elseif(DEFINED ENV{VIRTUAL_ENV} AND EXISTS "$ENV{VIRTUAL_ENV}/Lib/site-packages/PySide6")
        set(PYSIDE6_ROOT "$ENV{VIRTUAL_ENV}/Lib/site-packages/PySide6")
    endif()
endif()

if(NOT DEFINED PYSIDE6_ROOT OR PYSIDE6_ROOT STREQUAL "")
    _osmand_qt6_fail("PYSIDE6_ROOT was not set and could not be inferred. Point it at the PySide6 package directory.")
endif()

if(NOT EXISTS "${PYSIDE6_ROOT}/Qt6Core.dll")
    _osmand_qt6_fail("PYSIDE6_ROOT='${PYSIDE6_ROOT}' does not look like a PySide6 runtime root because Qt6Core.dll was not found there.")
endif()

if(NOT DEFINED QT6_IMPORT_LIB_ROOT OR QT6_IMPORT_LIB_ROOT STREQUAL "")
    if(DEFINED ENV{QT6_IMPORT_LIB_ROOT} AND NOT "$ENV{QT6_IMPORT_LIB_ROOT}" STREQUAL "")
        set(QT6_IMPORT_LIB_ROOT "$ENV{QT6_IMPORT_LIB_ROOT}")
    else()
        set(QT6_IMPORT_LIB_ROOT "${_qt6_helper_root}/qt6-msvc-importlibs")
    endif()
endif()

if(NOT DEFINED QT6_HEADERS_ROOT OR QT6_HEADERS_ROOT STREQUAL "")
    if(DEFINED ENV{QT6_HEADERS_ROOT} AND NOT "$ENV{QT6_HEADERS_ROOT}" STREQUAL "")
        set(QT6_HEADERS_ROOT "$ENV{QT6_HEADERS_ROOT}")
    elseif(DEFINED ENV{QTDIR} AND EXISTS "$ENV{QTDIR}/include/QtCore/QtGlobal")
        set(QT6_HEADERS_ROOT "$ENV{QTDIR}/include")
    elseif(EXISTS "C:/Qt/6.10.1/mingw_64/include/QtCore/QtGlobal")
        set(QT6_HEADERS_ROOT "C:/Qt/6.10.1/mingw_64/include")
    else()
        file(GLOB _qt6_header_roots "C:/Qt/*/mingw_64/include")
        list(SORT _qt6_header_roots COMPARE NATURAL ORDER DESCENDING)
        foreach(_qt6_header_root_candidate IN LISTS _qt6_header_roots)
            if(EXISTS "${_qt6_header_root_candidate}/QtCore/QtGlobal")
                set(QT6_HEADERS_ROOT "${_qt6_header_root_candidate}")
                break()
            endif()
        endforeach()
    endif()
endif()

set(_qt6_version "6.10.1")
if(EXISTS "${PYSIDE6_ROOT}/__init__.py")
    file(STRINGS "${PYSIDE6_ROOT}/__init__.py" _qt6_version_line REGEX "__version__ = \"[0-9.]+\"")
    if(_qt6_version_line)
        string(REGEX REPLACE ".*\"([0-9]+\.[0-9]+\.[0-9]+)\".*" "\\1" _qt6_version "${_qt6_version_line}")
    endif()
endif()

if(NOT DEFINED QT6_HEADERS_ROOT OR QT6_HEADERS_ROOT STREQUAL "")
    _osmand_qt6_fail("QT6_HEADERS_ROOT was not set and could not be inferred. Point it at a Qt SDK include directory such as C:/Qt/6.10.1/mingw_64/include.")
endif()

set(_qt6_include_root "${QT6_HEADERS_ROOT}")
if(NOT EXISTS "${_qt6_include_root}/QtCore/QtGlobal")
    _osmand_qt6_fail("Qt include root '${_qt6_include_root}' is incomplete because QtCore/QtGlobal was not found.")
endif()

function(_osmand_define_qt6_component component)
    set(options)
    set(oneValueArgs dll_basename include_subdir compile_definition)
    set(multiValueArgs dependencies)
    cmake_parse_arguments(ARG "${options}" "${oneValueArgs}" "${multiValueArgs}" ${ARGN})

    set(_dll_path "${PYSIDE6_ROOT}/${ARG_dll_basename}.dll")
    set(_implib_path "${QT6_IMPORT_LIB_ROOT}/${ARG_dll_basename}.lib")
    set(_component_include_dir "${_qt6_include_root}/${ARG_include_subdir}")

    if(NOT EXISTS "${_dll_path}")
        _osmand_qt6_fail("Required Qt runtime DLL was not found: ${_dll_path}")
    endif()
    if(NOT EXISTS "${_implib_path}")
        _osmand_qt6_fail("Required MSVC import library was not found: ${_implib_path}. Run build_native_widget_msvc.ps1 first.")
    endif()
    if(NOT EXISTS "${_component_include_dir}")
        _osmand_qt6_fail("Required Qt include directory was not found: ${_component_include_dir}")
    endif()

    if(NOT TARGET Qt6::${component})
        add_library(Qt6::${component} SHARED IMPORTED GLOBAL)
        set_target_properties(Qt6::${component} PROPERTIES
            IMPORTED_LOCATION "${_dll_path}"
            IMPORTED_IMPLIB "${_implib_path}"
            IMPORTED_NO_SYSTEM TRUE
            INTERFACE_INCLUDE_DIRECTORIES "${_qt6_include_root};${_component_include_dir}"
            INTERFACE_COMPILE_DEFINITIONS "${ARG_compile_definition};QT_NO_DEBUG"
        )
        if(MSVC)
            set_property(TARGET Qt6::${component} APPEND PROPERTY
                INTERFACE_COMPILE_OPTIONS "/Zc:__cplusplus;/permissive-"
            )
        endif()
        if(ARG_dependencies)
            set_property(TARGET Qt6::${component} APPEND PROPERTY INTERFACE_LINK_LIBRARIES "${ARG_dependencies}")
        endif()
    endif()

    set(Qt6${component}_FOUND TRUE PARENT_SCOPE)
    set(Qt6${component}_VERSION "${_qt6_version}" PARENT_SCOPE)
endfunction()

_osmand_define_qt6_component(Core
    dll_basename Qt6Core
    include_subdir QtCore
    compile_definition QT_CORE_LIB
)
_osmand_define_qt6_component(Network
    dll_basename Qt6Network
    include_subdir QtNetwork
    compile_definition QT_NETWORK_LIB
    dependencies Qt6::Core
)
_osmand_define_qt6_component(Gui
    dll_basename Qt6Gui
    include_subdir QtGui
    compile_definition QT_GUI_LIB
    dependencies Qt6::Core
)
_osmand_define_qt6_component(Widgets
    dll_basename Qt6Widgets
    include_subdir QtWidgets
    compile_definition QT_WIDGETS_LIB
    dependencies Qt6::Gui;Qt6::Core
)
_osmand_define_qt6_component(OpenGL
    dll_basename Qt6OpenGL
    include_subdir QtOpenGL
    compile_definition QT_OPENGL_LIB
    dependencies Qt6::Gui;Qt6::Core
)
_osmand_define_qt6_component(OpenGLWidgets
    dll_basename Qt6OpenGLWidgets
    include_subdir QtOpenGLWidgets
    compile_definition QT_OPENGLWIDGETS_LIB
    dependencies Qt6::OpenGL;Qt6::Widgets;Qt6::Gui;Qt6::Core
)

set(Qt6_FOUND TRUE)
set(Qt6_VERSION "${_qt6_version}")
set(Qt6Core_VERSION "${_qt6_version}")
set(Qt6Network_VERSION "${_qt6_version}")
set(Qt6Gui_VERSION "${_qt6_version}")
set(Qt6Widgets_VERSION "${_qt6_version}")
set(Qt6OpenGL_VERSION "${_qt6_version}")
set(Qt6OpenGLWidgets_VERSION "${_qt6_version}")

foreach(_requested_component IN LISTS Qt6_FIND_COMPONENTS)
    if(NOT TARGET Qt6::${_requested_component})
        set(Qt6_FOUND FALSE)
        if(Qt6_FIND_REQUIRED_${_requested_component})
            message(FATAL_ERROR "Requested unsupported Qt6 component '${_requested_component}' in custom PySide6-backed Qt6Config.cmake")
        endif()
    endif()
endforeach()
