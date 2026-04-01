if(TARGET osmand_render_helper)
    return()
endif()

add_subdirectory("${CMAKE_CURRENT_LIST_DIR}" "tools/cpp-tools/osmand_render_helper")
