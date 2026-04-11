#pragma once
#include <cstdint>
#include <utility>
#include <vector>

using ui     = uint32_t;
using pair_t = std::pair<int,int>;


static constexpr int8_t tab32[32] = {
    0,  9,  1, 10, 13, 21,  2, 29,
   11, 14, 16, 18, 22, 25,  3, 30,
    8, 12, 20, 28, 15, 17, 24,  7,
   19, 27, 23,  6, 26,  5,  4, 31
};
const int pow2[] = {1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024, 2048, 4096};
inline int log2_32(ui value) {
    value |= value >> 1;
    value |= value >> 2;
    value |= value >> 4;
    value |= value >> 8;
    value |= value >> 16;
    return tab32[(ui)(value * 0x07C4ACDDu) >> 27];
}
