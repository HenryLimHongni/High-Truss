
aux_source_directory(. srcs)
include_directories(${PROJECT_SOURCE_DIR}/include)
add_library(utils STATIC ${srcs}
        ../../include/utils/my_vector.h)