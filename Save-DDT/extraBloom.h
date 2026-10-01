#pragma once

#include <cstdint>
#include <vector>
#include <utility>
#include "dt_utils.h"
#include "edge.h"

class ExtraBloom {
public:
    uint64_t bloomNumber{1};
    uint64_t maxSupport{1};
    uint64_t counter{0};

    std::vector<std::vector<int>> memberEdge;
    std::vector<uint32_t> expected;

    ExtraBloom() = default;

    inline void ensure_buckets_by_paths() {
        if (maxSupport == 0) maxSupport = 1;
        bloomNumber = (maxSupport <= 1) ? 1 : ((uint64_t)log2_32(maxSupport - 1) + 1ull);
        memberEdge.assign((size_t)bloomNumber, {});
        expected.assign((size_t)bloomNumber, 0u);
    }

    inline void clear_buckets() {
        for (auto &b : memberEdge) b.clear();
        std::fill(expected.begin(), expected.end(), 0u);
    }

    inline int bucket_for(const Edge &e) const {
        if (!e.isDT) return 0;
        const uint64_t slackValue = e.get_slack_value();
        if (slackValue >= maxSupport) return -1;
        const int bucket = (int)log2_32(slackValue);
        return ((size_t)bucket < memberEdge.size()) ? bucket : -1;
    }

    inline void count_member(const Edge &e) {
        const int bucket = bucket_for(e);
        if (bucket >= 0) ++expected[(size_t)bucket];
    }

    inline void reserve_counted() {
        for (size_t i = 0; i < memberEdge.size(); ++i) memberEdge[i].reserve(expected[i]);
    }

    pair_t add_member_edge(ui edgeID, std::vector<Edge>& edge) {
        const int bucket = bucket_for(edge[edgeID]);
        if (bucket < 0) return {-1, 0};
        memberEdge[(size_t)bucket].push_back((int)edgeID);
        if (edge[edgeID].isDT) edge[edgeID].extraBloom_cnt = counter;
        return {bucket, (int)memberEdge[(size_t)bucket].size() - 1};
    }

    void send_value_to_member(std::vector<ui> &matureList,
                              std::vector<ui> &peelList,
                              std::vector<Edge>& edge) {
        const uint64_t old_counter = counter;
        ++counter;

        auto& bucket0 = memberEdge[0];
        for (ui i = 0; i < bucket0.size(); ++i) {
            ui edgeID = (ui)bucket0[i];
            if (edge[edgeID].isPeel) continue;
            edge[edgeID].accumulate_value(1);
            if (edge[edgeID].check_maturity()) {
                if (!edge[edgeID].isPeel) {
                    edge[edgeID].isPeel = true;
                    peelList.push_back(edgeID);
                }
            }
        }

        if (counter < 16) return;

        const int temp = (int)log2_32((old_counter ^ counter) + 1);
        for (int bucket = 4; bucket < temp && (size_t)bucket < memberEdge.size(); ++bucket) {
            auto& currentBucket = memberEdge[(size_t)bucket];
            for (ui i = 0; i < currentBucket.size(); ++i) {
                ui edgeID = (ui)currentBucket[i];
                if (edge[edgeID].isPeel) continue;
                const uint64_t cnt = edge[edgeID].extraBloom_cnt;
                edge[edgeID].accumulate_value(counter - cnt);
                edge[edgeID].extraBloom_cnt = counter;
                if (edge[edgeID].check_maturity()) matureList.push_back(edgeID);
            }
        }
    }

    int remove_member_by_index_id_only(pair_t index) {
        if (index.first < 0 || (size_t)index.first >= memberEdge.size()) return -1;
        auto &records = memberEdge[(size_t)index.first];
        if (index.second < 0 || (size_t)index.second >= records.size()) return -1;
        const size_t pos = (size_t)index.second;
        const size_t last = records.size() - 1;
        if (pos != last) {
            const int moved = records[last];
            records[pos] = moved;
            records.pop_back();
            return moved;
        }
        records.pop_back();
        return -1;
    }
};
