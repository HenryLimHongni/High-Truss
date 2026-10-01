#pragma once

#include <cstdint>
#include <vector>
#include <utility>
#include <algorithm>
#include "dt_utils.h"
#include "edge.h"
#include "2-bloom.h"

class ThreeBloom {
public:
    int       id{-1};
    uint64_t  key{0};
    uint64_t  total_paths{0};
    uint64_t  bloomNumber{0};
    uint64_t  counter{0};

    std::vector<DTBucketMember> nodt_members;
    uint32_t nodt_expected{0};
    std::vector<SparseDTBucket> dt_buckets;

    ThreeBloom() = default;
    ThreeBloom(int _id, uint64_t _key) : id(_id), key(_key) {}

    inline void ensure_buckets_by_paths(uint64_t temp) {
        bloomNumber = temp;
        nodt_members.clear();
        nodt_expected = 0;
        dt_buckets.clear();
    }

    inline void clear_buckets() {
        nodt_members.clear();
        nodt_expected = 0;
        dt_buckets.clear();
    }

    inline SparseDTBucket* find_bucket(int bucket) {
        for (auto &slot : dt_buckets) {
            if ((int)slot.id == bucket) return &slot;
        }
        return nullptr;
    }

    inline const SparseDTBucket* find_bucket(int bucket) const {
        for (const auto &slot : dt_buckets) {
            if ((int)slot.id == bucket) return &slot;
        }
        return nullptr;
    }

    inline SparseDTBucket& get_or_create_bucket(int bucket) {
        if (auto *p = find_bucket(bucket)) return *p;
        dt_buckets.push_back(SparseDTBucket{});
        auto &slot = dt_buckets.back();
        slot.id = static_cast<uint8_t>(bucket);
        return slot;
    }

    inline void count_member(int bucket) {
        if (bucket == -2) {
            ++nodt_expected;
            return;
        }
        ++get_or_create_bucket(bucket).expected;
    }

    inline void reserve_counted() {
        nodt_members.reserve(nodt_expected);
        for (auto &slot : dt_buckets) slot.members.reserve(slot.expected);
        std::sort(dt_buckets.begin(), dt_buckets.end(),
                  [](const SparseDTBucket &x, const SparseDTBucket &y) { return x.id < y.id; });
    }

    pair_t add_member_edge(ui edgeID, ui indexInMemberEdge, std::vector<Edge>&) {
        nodt_members.push_back({edgeID, indexInMemberEdge});
        return {-2, (int)nodt_members.size() - 1};
    }

    pair_t add_member_edge2(uint32_t bucket,
                            ui edgeID,
                            ui indexInMemberEdge,
                            std::vector<Edge>& edge) {
        const uint64_t slackValue = edge[edgeID].get_slack_value();
        if (slackValue > bloomNumber || bucket > 63u) return {-1, 0};
        auto &slot = get_or_create_bucket((int)bucket);
        slot.members.push_back({edgeID, indexInMemberEdge});
        return {(int)bucket, (int)slot.members.size() - 1};
    }

    affect_edge_t remove_member_by_index(pair_t index) {
        if (index.first == -1) return {-1, 0};

        std::vector<DTBucketMember> *records = nullptr;
        if (index.first == -2) {
            records = &nodt_members;
        } else {
            auto *slot = find_bucket(index.first);
            if (!slot) return {-1, 0};
            records = &slot->members;
        }

        if (index.second < 0 || (size_t)index.second >= records->size()) return {-1, 0};
        const size_t pos = (size_t)index.second;
        const size_t last = records->size() - 1;
        if (pos != last) {
            const DTBucketMember moved = (*records)[last];
            (*records)[pos] = moved;
            records->pop_back();
            return {(int)moved.edge, moved.host};
        }
        records->pop_back();
        return {-1, 0};
    }
};
