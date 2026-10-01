#pragma once

#include <cstdint>
#include <vector>
#include <algorithm>
#include <limits>
#include <stdexcept>
#include <cstddef>
#include "dt_utils.h"

// Compact linear-probing map. A load factor of about 0.84 substantially lowers
// the table footprint while splitmix64 keeps probe sequences short in practice.
class FlatU64U32Map {
public:
    static constexpr uint64_t EMPTY_KEY = std::numeric_limits<uint64_t>::max();
    static constexpr std::size_t LOAD_NUM = 84;
    static constexpr std::size_t LOAD_DEN = 100;

    FlatU64U32Map() = default;

    void clear() {
        if (!keys_.empty()) std::fill(keys_.begin(), keys_.end(), EMPTY_KEY);
        size_ = 0;
    }

    void release() {
        std::vector<uint64_t>().swap(keys_);
        std::vector<uint32_t>().swap(vals_);
        size_ = 0;
    }

    std::size_t size() const { return size_; }
    std::size_t capacity() const { return keys_.size(); }

    void reserve(std::size_t expected) {
        if (expected == 0) return;
        std::size_t need = expected + expected / 5 + 16; // about 1 / 0.84
        std::size_t cap = 1;
        while (cap < need) cap <<= 1;
        if (cap > keys_.size()) rehash(cap);
    }

    FC_ALWAYS_INLINE uint32_t get(uint64_t key,
                 uint32_t missing = std::numeric_limits<uint32_t>::max()) const {
        if (keys_.empty() || key == EMPTY_KEY) return missing;
        const std::size_t mask = keys_.size() - 1;
        std::size_t pos = mix(key) & mask;
        while (true) {
            const uint64_t k = keys_[pos];
            if (k == EMPTY_KEY) return missing;
            if (k == key) return vals_[pos];
            pos = (pos + 1) & mask;
        }
    }

    template <class Fn>
    void for_each(Fn&& fn) const {
        for (std::size_t i = 0; i < keys_.size(); ++i) {
            if (keys_[i] != EMPTY_KEY) fn(keys_[i], vals_[i]);
        }
    }

    FC_ALWAYS_INLINE uint32_t get_or_insert(uint64_t key, uint32_t value_if_new, bool &inserted) {
        if (key == EMPTY_KEY) throw std::runtime_error("FlatU64U32Map: reserved key");
        ensure_for_insert();
        const std::size_t mask = keys_.size() - 1;
        std::size_t pos = mix(key) & mask;
        while (true) {
            const uint64_t k = keys_[pos];
            if (k == EMPTY_KEY) {
                keys_[pos] = key;
                vals_[pos] = value_if_new;
                ++size_;
                inserted = true;
                return value_if_new;
            }
            if (k == key) {
                inserted = false;
                return vals_[pos];
            }
            pos = (pos + 1) & mask;
        }
    }

    // Remap existing values in place without rebuilding the table. Values
    // mapped to a negative number become the caller supplied invalid code.
    template <class Mapping>
    void remap_values(const Mapping& mapping, uint32_t invalid_code) {
        for (std::size_t i = 0; i < keys_.size(); ++i) {
            if (keys_[i] == EMPTY_KEY) continue;
            const uint32_t old = vals_[i];
            const int next = old < mapping.size() ? mapping[old] : -1;
            vals_[i] = next < 0 ? invalid_code : static_cast<uint32_t>(next);
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
    }

    std::vector<uint64_t> keys_;
    std::vector<uint32_t> vals_;
    std::size_t size_{0};
};
