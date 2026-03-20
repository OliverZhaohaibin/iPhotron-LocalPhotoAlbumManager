include_guard(GLOBAL)

find_package(Qt6 REQUIRED COMPONENTS Core Network)

if(NOT TARGET Qt5::Core)
    add_library(Qt5::Core INTERFACE IMPORTED GLOBAL)
    target_link_libraries(Qt5::Core INTERFACE Qt6::Core)
endif()

if(NOT TARGET Qt5::Network)
    add_library(Qt5::Network INTERFACE IMPORTED GLOBAL)
    target_link_libraries(Qt5::Network INTERFACE Qt6::Network Qt5::Core)
endif()

set(Qt5Network_FOUND TRUE)
set(Qt5Network_VERSION "${Qt6Network_VERSION}")
set(Qt5Network_LIBRARIES Qt5::Network)

