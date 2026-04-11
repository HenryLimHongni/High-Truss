# 设置源文件
file(GLOB srcs "*.cc")

# 创建静态库 bloom，包含所有 .cc 文件
add_library(bloom STATIC ${srcs})

# 设置 include 目录为项目的 include/bloom
target_include_directories(bloom PUBLIC ${PROJECT_SOURCE_DIR}/include)

# 如果有其他库依赖，添加链接
# target_link_libraries(bloom PUBLIC other_dependency)

# 假设 utils 是另一个库
target_link_libraries(bloom PUBLIC utils)
