#pragma once
#include <cstdint>
#include <vector>
#include <queue>
#include <utility>
#include <algorithm>
#include "dt_utils.h"
#include "edge.h"

using namespace std;

typedef std::pair<ui, ui> affect_edge_t;


class ThreeBloom {
public:
    struct Path3 {
        uint32_t a, b, c;   // local edge ids
    };

    int      id{-1};
    uint64_t key{0};
    uint64_t total_paths{0};        

    // ---- CSR index for incident paths ----
    vector<int>      local2eid;     // size K
    vector<uint32_t> local2hostidx; // size K
    vector<Path3>    paths;         // size P
    vector<uint32_t> off;           // size K+1
    vector<uint32_t> inc;           // size sum(deg)=3P
    vector<uint64_t> alive;         // bitset over P

    
    vector<int> nodtmemberEdge;                 // global edge ids
    vector<int> nodtreverseIndexInMemberEdge;   // index in Edge.host3Bloom (host idx)

    ThreeBloom() = default;
    ThreeBloom(int _id, uint64_t _key) : id(_id), key(_key) {}

    inline void clear_buckets() {
        nodtmemberEdge.clear();
        nodtreverseIndexInMemberEdge.clear();
    }

    
    inline int add_member_edge(ui edgeID, ui indexInMemberEdge, vector<Edge>& /*edges*/) {
        nodtmemberEdge.push_back((int)edgeID);
        nodtreverseIndexInMemberEdge.push_back((int)indexInMemberEdge);
        return (int)nodtmemberEdge.size() - 1;
    }

    
    inline affect_edge_t remove_member_by_index(int index) {
        if (index < 0) return std::make_pair((ui)-1, (ui)0);

        const int L = (int)nodtmemberEdge.size();
        if (index < L - 1) {
            ui affectEdgeID = (ui)nodtmemberEdge[L - 1];
            ui affectedHostIdx = (ui)nodtreverseIndexInMemberEdge[L - 1];
            nodtmemberEdge[index] = (int)affectEdgeID;
            nodtreverseIndexInMemberEdge[index] = (int)affectedHostIdx;
            nodtmemberEdge.pop_back();
            nodtreverseIndexInMemberEdge.pop_back();
            return std::make_pair(affectEdgeID, affectedHostIdx);
        } else {
            nodtmemberEdge.pop_back();
            nodtreverseIndexInMemberEdge.pop_back();
            return std::make_pair((ui)-1, (ui)0);
        }
    }

    
    inline void send_value_to_member(std::queue<ui> &peelList, vector<Edge>& edges, int globalCounter) {
        for (ui i = 0; i < (ui)nodtmemberEdge.size(); ++i) {
            ui eid = (ui)nodtmemberEdge[i];
            int host_idx = nodtreverseIndexInMemberEdge[i];

            
            edges[eid].five_cycle_support -= (uint64_t)edges[eid].host3Cnt[host_idx];

            
            edges[eid].five_cycle_support += (uint64_t)edges[eid].balance;
            edges[eid].balance = 0;

            if (edges[eid].check_maturity(globalCounter)) {
                if (!edges[eid].isPeel) {
                    edges[eid].isPeel = true;
                    peelList.push(eid);
                }
            }
        }
    }

    // ===== alive bitset helpers =====
    inline bool alive_path(uint32_t pid) const {
        return (alive[(size_t)pid >> 6] >> (pid & 63u)) & 1ull;
    }
    inline void kill_path(uint32_t pid) {
        alive[(size_t)pid >> 6] &= ~(1ull << (pid & 63u));
    }

    inline uint32_t incident_begin(uint32_t lid) const { return off[(size_t)lid]; }
    inline uint32_t incident_end(uint32_t lid) const { return off[(size_t)lid + 1]; }
};
