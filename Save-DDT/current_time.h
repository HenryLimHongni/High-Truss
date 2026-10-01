#pragma once

#include <chrono>

static inline double get_current_time() {
    using clock = std::chrono::steady_clock;
    static const auto t0 = clock::now();
    auto now = clock::now();
    return std::chrono::duration<double>(now - t0).count();
}
