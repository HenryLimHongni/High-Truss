#pragma once
#include <cstdint>
#include <algorithm>
#include <vector>
#include <utility>
#include "dt_utils.h"

using namespace std;


static inline uint64_t pack_edge(uint32_t a, uint32_t b) {
    uint32_t x = std::min(a, b);
    uint32_t y = std::max(a, b);
    return (uint64_t(x) << 32) | uint64_t(y);
}

struct TwinInfo {
public:
    ui twinEdgeID{0};
    ui hostBloomIndex{0};
    TwinInfo(ui _id, ui _index) : twinEdgeID(_id), hostBloomIndex(_index) {}
};

class Edge {
public:
    bool isPeel{false};
    int  id{-1};
    int  u{-1};
    int  v{-1};

    int storageIndex{-1};

    
    int balance{0};                 
    uint64_t five_cycle_support{0};

    // ================= 2-Bloom membership =================
    vector<int>  host2Bloom;                 // bid
    vector<int>  reverseIndexInHost2Bloom;   
    vector<int>  twinEdge;                   
    vector<int>  hostBloomIndexInTwin;       
    //vector<int>  cnt2;                       
    vector<bool> samekey2to3check;
    vector<int>  samekey2to3index;           

    // ================= 3-Bloom membership =================
    vector<int>      host3Bloom;                 // bid
    vector<int>      reverseIndexInHost3Bloom;   
    vector<uint32_t> local3idx;                  
    vector<int>      host3Cnt;                   
    //vector<int>      cnt3;                       
    vector<bool>     samekey3to2check;
    vector<int>      samekey3to2index;          

    Edge() = default;
    Edge(int _id, int _u, int _v) : id(_id), u(_u), v(_v) {}

    inline const TwinInfo get_twin_edge_info_by_index(const ui index) const {
        return TwinInfo((ui)twinEdge[index], (ui)hostBloomIndexInTwin[index]);
    }

    inline void set_reverse_2bloom_index_by_index(ui index, int reverseIndex) {
        reverseIndexInHost2Bloom[index] = reverseIndex;
    }
    inline void set_reverse_3bloom_index_by_index(ui index, int reverseIndex) {
        reverseIndexInHost3Bloom[index] = reverseIndex;
    }

    inline int get_reverse_index_in_host_2bloom_by_index(const ui index) const {
        return reverseIndexInHost2Bloom[index];
    }
    inline int get_reverse_index_in_host_3bloom_by_index(const ui index) const {
        return reverseIndexInHost3Bloom[index];
    }

    inline int get_host_2bloom_id_by_index(ui index) const { return host2Bloom[index]; }
    inline int get_host_3bloom_id_by_index(ui index) const { return host3Bloom[index]; }

    inline bool check_maturity(int globalCounter) const {
        return (five_cycle_support <= (uint64_t)globalCounter);
    }

    inline void set_twin_index_by_index(const ui index, const ui twinIndex) {
        hostBloomIndexInTwin[index] = (int)twinIndex;
    }

    
    inline void remove_host_2bloom_by_index(ui index) { host2Bloom[index] = -1; }
    inline void remove_host_3bloom_by_index(ui index) { host3Bloom[index] = -1; }
};
