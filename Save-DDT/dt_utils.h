#pragma once

#include <cstdint>
#include <utility>
#include <limits>

using edge_id_t  = uint32_t;
using local_id_t = uint32_t;
using path_id_t  = uint64_t;
using offset_t   = uint64_t;
using count_t    = uint64_t;
using ui = edge_id_t;

using pair_t = std::pair<int, int>;
using affect_edge_t = std::pair<int, ui>;

#if defined(__GNUC__) || defined(__clang__)
#define FC_ALWAYS_INLINE inline __attribute__((always_inline))
#define FC_LIKELY(x)   (__builtin_expect(!!(x), 1))
#define FC_UNLIKELY(x) (__builtin_expect(!!(x), 0))
#else
#define FC_ALWAYS_INLINE inline
#define FC_LIKELY(x)   (x)
#define FC_UNLIKELY(x) (x)
#endif

FC_ALWAYS_INLINE uint32_t log2_32(uint64_t x) {
    if (x == 0) return 0;
#if defined(__GNUC__) || defined(__clang__)
    return 63u - static_cast<uint32_t>(__builtin_clzll(x));
#else
    uint32_t r = 0;
    while (x >>= 1) ++r;
    return r;
#endif
}

FC_ALWAYS_INLINE uint64_t pack_u32_pair(uint32_t a, uint32_t b) {
    return (uint64_t(a) << 32) | uint64_t(b);
}

// A DT bucket record stores the global edge id and the edge-local membership index.
FC_ALWAYS_INLINE uint64_t pack_member_record(ui edge_id, ui host_index) {
    return (uint64_t(edge_id) << 32) | uint64_t(host_index);
}
FC_ALWAYS_INLINE ui member_record_edge(uint64_t r) {
    return static_cast<ui>(r >> 32);
}
FC_ALWAYS_INLINE ui member_record_host_index(uint64_t r) {
    return static_cast<ui>(r & 0xffffffffull);
}

// Reverse bucket locations are represented by one byte plus a uint32 position.
// Real DT bucket ids are at most 63 because all counters are uint64_t.
static constexpr uint8_t REV_BUCKET_NODT    = 254u;
static constexpr uint8_t REV_BUCKET_INVALID = 255u;

FC_ALWAYS_INLINE uint8_t encode_bucket_id(int bucket) {
    if (bucket == -2) return REV_BUCKET_NODT;
    if (bucket < 0) return REV_BUCKET_INVALID;
    return static_cast<uint8_t>(bucket);
}
FC_ALWAYS_INLINE int decode_bucket_id(uint8_t bucket) {
    if (bucket == REV_BUCKET_NODT) return -2;
    if (bucket == REV_BUCKET_INVALID) return -1;
    return static_cast<int>(bucket);
}
