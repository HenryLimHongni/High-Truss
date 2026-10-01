#pragma once

#include <cstdint>
#include <vector>
#include <limits>
#include <stdexcept>
#include <cstddef>
#include <algorithm>

// Compact open-addressing map specialized for uint64_t -> uint64_t.
// Used for construction-time aggregation of (bid,eid) -> count.
class FlatU64U64Map {
public:
    static constexpr uint64_t EMPTY_KEY = std::numeric_limits<uint64_t>::max();

    FlatU64U64Map() = default;

    void clear() {
        if (!keys_.empty()) {
            std::fill(keys_.begin(), keys_.end(), EMPTY_KEY);
            std::fill(vals_.begin(), vals_.end(), 0ull);
        }
        size_ = 0;
    }

    void release() {
        std::vector<uint64_t>().swap(keys_);
        std::vector<uint64_t>().swap(vals_);
        size_ = 0;
    }

    std::size_t size() const { return size_; }
    std::size_t capacity() const { return keys_.size(); }

    void reserve(std::size_t expected) {
        if (expected == 0) return;
        std::size_t cap = 1;
        const std::size_t need = expected + expected / 2 + 8;
        while (cap < need) cap <<= 1;
        if (cap > keys_.size()) rehash(cap);
    }

    uint64_t get(uint64_t key, uint64_t missing = 0) const {
        if (keys_.empty() || key == EMPTY_KEY) return missing;
        const std::size_t mask = keys_.size() - 1;
        std::size_t pos = mix(key) & mask;
        while (true) {
            uint64_t k = keys_[pos];
            if (k == EMPTY_KEY) return missing;
            if (k == key) return vals_[pos];
            pos = (pos + 1) & mask;
        }
    }

    void add(uint64_t key, uint64_t delta) {
        if (key == EMPTY_KEY) throw std::runtime_error("FlatU64U64Map: EMPTY_KEY cannot be used");
        if (keys_.empty() || (size_ + 1) * 10 >= keys_.size() * 7) {
            rehash(keys_.empty() ? 1024 : keys_.size() * 2);
        }
        const std::size_t mask = keys_.size() - 1;
        std::size_t pos = mix(key) & mask;
        while (true) {
            uint64_t k = keys_[pos];
            if (k == EMPTY_KEY) {
                keys_[pos] = key;
                vals_[pos] = delta;
                ++size_;
                return;
            }
            if (k == key) {
                vals_[pos] += delta;
                return;
            }
            pos = (pos + 1) & mask;
        }
    }

    template <class Fn>
    void for_each(Fn&& fn) const {
        for (std::size_t i = 0; i < keys_.size(); ++i) {
            if (keys_[i] != EMPTY_KEY) fn(keys_[i], vals_[i]);
        }
    }

private:
    static inline std::size_t mix(uint64_t x) {
        x += 0x9e3779b97f4a7c15ull;
        x = (x ^ (x >> 30)) * 0xbf58476d1ce4e5b9ull;
        x = (x ^ (x >> 27)) * 0x94d049bb133111ebull;
        x = x ^ (x >> 31);
        return static_cast<std::size_t>(x);
    }

    void rehash(std::size_t new_cap) {
        if (new_cap < 1024) new_cap = 1024;
        if ((new_cap & (new_cap - 1)) != 0) {
            std::size_t p = 1;
            while (p < new_cap) p <<= 1;
            new_cap = p;
        }
        std::vector<uint64_t> old_keys;
        std::vector<uint64_t> old_vals;
        old_keys.swap(keys_);
        old_vals.swap(vals_);
        keys_.assign(new_cap, EMPTY_KEY);
        vals_.assign(new_cap, 0ull);
        size_ = 0;
        const std::size_t mask = keys_.size() - 1;
        for (std::size_t i = 0; i < old_keys.size(); ++i) {
            uint64_t k = old_keys[i];
            if (k == EMPTY_KEY) continue;
            std::size_t pos = mix(k) & mask;
            while (keys_[pos] != EMPTY_KEY) pos = (pos + 1) & mask;
            keys_[pos] = k;
            vals_[pos] = old_vals[i];
            ++size_;
        }
    }

    std::vector<uint64_t> keys_;
    std::vector<uint64_t> vals_;
    std::size_t size_{0};
};
