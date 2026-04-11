#pragma once
#include <cstdint>
#include <algorithm>
#include<queue>
#include <utility>
#include <vector>
#include "dt_utils.h"
using namespace std;
typedef std::tuple<int, pair_t, ui, ui> affect_2bloom_t;
typedef std::tuple<int, pair_t, pair_t, pair_t> affect_3bloom_t;

static inline uint64_t pack_edge(uint32_t a, uint32_t b) {
    uint32_t x = std::min(a, b);
    uint32_t y = std::max(a, b);
    return (uint64_t(x) << 32) | uint64_t(y);
}
struct TwinInfo {
    public:
      ui twinEdgeID{0};
      ui hostBloomIndex{0};
  
      TwinInfo(ui _id, ui _index)
          : twinEdgeID(_id), hostBloomIndex(_index) {}
  };
class Edge {
public:
    bool isPeel{false};
    int id{-1};
    int u{-1};
    int v{-1};
    pair_t reverseIndexInExtraBloom;
    uint64_t targetValue{0};
    int extraBloom_cnt{0};

    
    int balance{0};
    int hostbloomnumber{0};
    vector<int>     host2Bloom;                 // 
    vector<pair_t>  reverseIndexInHost2Bloom;   // 
    vector<int>     twinEdge;                   // 
    vector<int>     hostBloomIndexInTwin;
    vector<int>     cnt2;
    vector<bool>     samekey2to3check;
    vector<int>    samekey2to3index;
    
    vector<int>     host3Bloom;                 // 
    vector<pair_t>  reverseIndexInHost3Bloom;   // 
    //vector<vector<size_t>>       otherTwoEdgesIn3Bloom;     // 
    vector<uint32_t> local3idx;
    vector<int>    host3Cnt;                   // 
    vector<int>    cnt3;
    vector<bool>   samekey3to2check;
    vector<int>    samekey3to2index;
    uint64_t accumulatedValue{0};
    int delta{0};
    
    bool      isDT{true};     
    uint64_t        slackValue{0};   // floor(support / ((#host2+#host3+1) * 2))

    
    uint64_t  five_cycle_support{0};

    Edge() = default;
    Edge(int _id, int _u, int _v) : id(_id), u(_u), v(_v) {}

    inline uint64_t get_slack_value() const { return slackValue; }
    /*
    void printinfo(){
        cout<<"hostBloom:"<<endl;
        for(int i = 0;i < host2Bloom.size();i++){
            cout<<host2Bloom[i]<<" ";
        }
        cout<<endl;
        cout<<"twinEdge:"<<endl;
        for(int i = 0;i < twinEdge.size();i++){
            cout<<twinEdge[i]<<" ";
        }
        for(int i = 0;i < samekey2to3check.size();i++){
            cout<<samekey2to3check[i]<<" ";
        }
        for(int i = 0;i < host2Bloom.size();i++){
            cout<<host2Bloom[i];
        }
    }
        */
    void set_reverse_index_in_extra_bloom(pair_t reverseIndex) {
        reverseIndexInExtraBloom = reverseIndex;
    }

    inline const TwinInfo get_twin_edge_info_by_index(const ui index) {
        return TwinInfo(twinEdge[index], hostBloomIndexInTwin[index]);
    }

    inline void set_reverse_2bloom_index_by_index(ui index, pair_t reverseIndex) {
        reverseIndexInHost2Bloom[index] = reverseIndex;
    }

    inline void set_reverse_3bloom_index_by_index(ui index, pair_t reverseIndex) {
        reverseIndexInHost3Bloom[index] = reverseIndex;
    }

    inline pair_t get_reverse_index_in_extra_bloom() {
        return reverseIndexInExtraBloom;
    }

    inline const pair_t get_reverse_index_in_host_2bloom_by_index(const ui index) {
        return reverseIndexInHost2Bloom[index];
    }

    inline const pair_t get_reverse_index_in_host_3bloom_by_index(const ui index) {
        return reverseIndexInHost3Bloom[index];
    }

    inline const int get_host_2bloom_id_by_index(ui index) {
        return host2Bloom[index];
    }

    inline const int get_host_3bloom_id_by_index(ui index) {
        return host3Bloom[index];
    }

    ui check_maturity() {
        if (accumulatedValue < targetValue) {
            return false;
        } else {
            delta = accumulatedValue;
            //if(id == 6530) std::cout<<accumulatedValue<<" "<<targetValue<<std::endl;
            accumulatedValue = 0;
            return true;
        }
    }

    void compute_slack_value() {
        
        targetValue = five_cycle_support / 2;
        slackValue = targetValue / (uint64_t)hostbloomnumber;
        if(slackValue < 16){
            isDT = false;
            targetValue = five_cycle_support;
            //if(id == 19559) cout<<"get slack"<<endl;
    
        }
        
    }

    inline void set_twin_index_by_index(const ui index, const ui twinIndex) {
        hostBloomIndexInTwin[index] = twinIndex;
    }
    /*
    inline void set_other_two_edge_index_by_index(ui index, ui two_edge_Index) {
        otherTwoEdgesIndex3Bloom[index] = two_edge_Index;
    }
    */
    inline void accumulate_value(const int value) { accumulatedValue += value; }
    inline void decrease_value(const int value) { accumulatedValue -= value; }

    void remove_host_2bloom_by_index(ui index) {
        host2Bloom[index]= -1;
        
    }
    
    
    void remove_host_3bloom_by_index(ui index) {
        host3Bloom[index] = -1;
    }
    

    






};
