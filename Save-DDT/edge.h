#pragma once

#include <cstdint>
#include <utility>
#include "dt_utils.h"

class Edge {
public:
    // Keep frequently updated 64-bit state together to avoid padding.
    uint64_t targetValue{0};
    uint64_t extraBloom_cnt{0};
    uint64_t balance{0};
    uint64_t accumulatedValue{0};
    uint64_t delta{0};
    uint64_t slackValue{0};
    uint64_t five_cycle_support{0};

    pair_t reverseIndexInExtraBloom{-1, 0};

    uint32_t u{0};
    uint32_t v{0};
    uint32_t hostbloomnumber{0};
    uint32_t h2_off{0};
    uint32_t h2_len{0};
    uint32_t h3_off{0};
    uint32_t h3_len{0};

    bool isPeel{false};
    bool isDT{true};

    Edge() = default;
    Edge(int, int _u, int _v)
        : u(static_cast<uint32_t>(_u)), v(static_cast<uint32_t>(_v)) {}

    inline uint64_t get_slack_value() const { return slackValue; }

    inline void set_reverse_index_in_extra_bloom(pair_t reverseIndex) {
        reverseIndexInExtraBloom = reverseIndex;
    }

    inline pair_t get_reverse_index_in_extra_bloom() const {
        return reverseIndexInExtraBloom;
    }

    inline bool check_maturity() {
        if (accumulatedValue < targetValue) return false;
        delta = accumulatedValue;
        accumulatedValue = 0;
        return true;
    }

    inline void compute_slack_value() {
        targetValue = five_cycle_support / 2ull;
        const uint64_t denom = hostbloomnumber ? uint64_t(hostbloomnumber) : 1ull;
        slackValue = targetValue / denom;
        isDT = slackValue >= 16ull;
        if (!isDT) targetValue = five_cycle_support;
    }

    inline void accumulate_value(uint64_t value) { accumulatedValue += value; }
    inline void decrease_value(uint64_t value) { accumulatedValue -= value; }
};
