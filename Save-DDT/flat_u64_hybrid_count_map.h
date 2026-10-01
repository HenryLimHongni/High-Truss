#pragma once

#include <cstdint>
#include <vector>
#include <algorithm>
#include <limits>
#include <stdexcept>
#include <cstddef>
#include "flat_u64_u64_map.h"
#include "dt_utils.h"

// Construction-only exact counter map. The common count is kept in 32 bits;
// only keys whose count grows past UINT32_MAX are placed in an overflow map.
// Compared with uint64->uint64 slots, this removes four bytes from every hash slot.
class FlatU64HybridCountMap {
public:
    static constexpr uint64_t EMPTY_KEY = std::numeric_limits<uint64_t>::max();
    static constexpr uint32_t MAX32 = std::numeric_limits<uint32_t>::max();
    static constexpr std::size_t LOAD_NUM = 84;
    static constexpr std::size_t LOAD_DEN = 100;

    void release() {
        std::vector<uint64_t>().swap(keys_);
        std::vector<uint32_t>().swap(vals_);
        overflow_.release();
        size_ = 0;
    }

    std::size_t size() const { return size_; }
    std::size_t capacity() const { return keys_.size(); }

    void reserve(std::size_t expected) {
        if (expected == 0) return;
        std::size_t need = expected + expected / 5 + 16;
        std::size_t cap = 1;
        while (cap < need) cap <<= 1;
        if (cap > keys_.size()) rehash(cap);
    }

    FC_ALWAYS_INLINE void add(uint64_t key, uint64_t delta = 1) {
        if (key == EMPTY_KEY) throw std::runtime_error("FlatU64HybridCountMap: reserved key");
        ensure_for_insert();
        const std::size_t mask = keys_.size() - 1;
        std::size_t pos = mix(key) & mask;
        while (true) {
            const uint64_t k = keys_[pos];
            if (k == EMPTY_KEY) {
                keys_[pos] = key;
                if (delta <= MAX32) {
                    vals_[pos] = static_cast<uint32_t>(delta);
                } else {
                    vals_[pos] = MAX32;
                    overflow_.add(key, delta);
                }
                ++size_;
                return;
            }
            if (k == key) {
                const uint64_t over = overflow_.get(key, 0ull);
                if (over != 0ull) {
                    overflow_.add(key, delta);
                    return;
                }
                const uint64_t next = uint64_t(vals_[pos]) + delta;
                if (next <= MAX32) {
                    vals_[pos] = static_cast<uint32_t>(next);
                } else {
                    vals_[pos] = MAX32;
                    overflow_.add(key, next);
                }
                return;
            }
            pos = (pos + 1) & mask;
        }
    }

    // Fast path used by 3-path aggregation. Counts virtually never overflow
    // 32 bits; the exact overflow map is consulted only after an overflow has
    // actually occurred.
    FC_ALWAYS_INLINE void increment(uint64_t key) {
        if (FC_UNLIKELY(key == EMPTY_KEY)) throw std::runtime_error("FlatU64HybridCountMap: reserved key");
        ensure_for_insert();
        const std::size_t mask = keys_.size() - 1;
        std::size_t pos = mix(key) & mask;
        while (true) {
            const uint64_t k = keys_[pos];
            if (k == EMPTY_KEY) {
                keys_[pos] = key;
                vals_[pos] = 1u;
                ++size_;
                return;
            }
            if (k == key) {
                if (FC_LIKELY(vals_[pos] != MAX32)) {
                    ++vals_[pos];
                    return;
                }
                // MAX32 may either be the exact value or an overflow marker.
                const uint64_t over = overflow_.get(key, 0ull);
                if (over == 0ull) overflow_.add(key, uint64_t(MAX32) + 1ull);
                else overflow_.add(key, 1ull);
                return;
            }
            pos = (pos + 1) & mask;
        }
    }

    template <class Fn>
    void for_each(Fn&& fn) const {
        for (std::size_t i = 0; i < keys_.size(); ++i) {
            const uint64_t k = keys_[i];
            if (k == EMPTY_KEY) continue;
            const uint64_t over = overflow_.get(k, 0ull);
            fn(k, over ? over : uint64_t(vals_[i]));
        }
    }

private:
    static FC_ALWAYS_INLINE std::size_t mix(uint64_t x) {
        x += 0x9e3779b97f4a7c15ull;
        x = (x ^ (x >> 30)) * 0xbf58476d1ce4e5b9ull;
        x = (x ^ (x >> 27)) * 0x94d049bb133111ebull;
        x ^= x >> 31;
        return static_cast<std::size_t>(x);
    }

    FC_ALWAYS_INLINE void ensure_for_insert() {
        if (keys_.empty()) {
            rehash(1024);
            return;
        }
        if ((size_ + 1) * LOAD_DEN >= keys_.size() * LOAD_NUM) {
            rehash(keys_.size() * 2);
        }
    }

    void rehash(std::size_t new_cap) {
        if (new_cap < 1024) new_cap = 1024;
        if ((new_cap & (new_cap - 1)) != 0) {
            std::size_t p = 1;
            while (p < new_cap) p <<= 1;
            new_cap = p;
        }
        std::vector<uint64_t> old_keys;
        std::vector<uint32_t> old_vals;
        old_keys.swap(keys_);
        old_vals.swap(vals_);
        keys_.assign(new_cap, EMPTY_KEY);
        vals_.assign(new_cap, 0u);
        const std::size_t old_size = size_;
        size_ = 0;
        const std::size_t mask = new_cap - 1;
        for (std::size_t i = 0; i < old_keys.size(); ++i) {
            const uint64_t k = old_keys[i];
            if (k == EMPTY_KEY) continue;
            std::size_t pos = mix(k) & mask;
            while (keys_[pos] != EMPTY_KEY) pos = (pos + 1) & mask;
            keys_[pos] = k;
            vals_[pos] = old_vals[i];
            ++size_;
        }
        (void)old_size;
    }

    std::vector<uint64_t> keys_;
    std::vector<uint32_t> vals_;
    FlatU64U64Map overflow_;
    std::size_t size_{0};
};
