include_guard(GLOBAL)

find_package(Qt6 REQUIRED COMPONENTS Core)

if(NOT TARGET Qt5::Core)
    add_library(Qt5::Core INTERFACE IMPORTED GLOBAL)
    target_link_libraries(Qt5::Core INTERFACE Qt6::Core)
endif()

set(Qt5Core_FOUND TRUE)
set(Qt5Core_VERSION "${Qt6Core_VERSION}")
set(Qt5Core_LIBRARIES Qt5::Core)

