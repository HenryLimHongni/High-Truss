#pragma once
#include <cstdint>
#include <vector>
#include <utility>
#include "dt_utils.h"
#include "edge.h"
typedef std::pair<ui, ui> affect_edge_t;


class TwoBloom {
public:
    int       id{-1};
    uint64_t  key{0};                 // (a,c)
    uint64_t  total_paths{0};         

   

    std::vector<int>      memberEdge;
    std::vector<ui>       reverseIndexInMemberEdge;
    //std::vector<std::vector<ui>>       cnt;

    TwoBloom() = default;
    TwoBloom(int _id, uint64_t _key) : id(_id), key(_key) {}

    // 在 total_paths 已确定后调用
    inline void ensure_buckets_by_paths(int temp) {
    }

    inline void clear_buckets() {
        for (int b=0;b<(int)memberEdge.size();++b) {
            memberEdge.clear();
            reverseIndexInMemberEdge.clear();
            //cnt[b].clear();
        }
    }

    int add_member_edge(ui edgeID, ui indexInMemberEdge, vector<Edge>&edge) {
        
            memberEdge.push_back(edgeID);
            reverseIndexInMemberEdge.push_back(indexInMemberEdge);
            //cnt[0].push_back((ui)counter);
            return memberEdge.size()-1;
        
        
    }
    void send_value_to_member(int increasenum,  std::queue<ui> &peelList, vector<Edge>&edge, int globalCounter) {
        //int old_counter = counter;
        //counter+= increasenum;  // Increment counter

        // Cache memberEdge[0] for quick access
        //auto& bucket0 = memberEdge[0];  // Cache first bucket
        for (ui i = 0;i < memberEdge.size() ;i++) {
            
            int edgeID = memberEdge[i];
            //cout<<edgeID<<endl;
            edge[edgeID].five_cycle_support -= increasenum;
            //if(edgeID == 19559)cout<<"a5:"<<increasenum<<endl;
            //if(edgeID == 19559)cout<<"decrease:a5 "<<edge[edgeID].balance<<endl;
            edge[edgeID].five_cycle_support += edge[edgeID].balance;

            edge[edgeID].balance = 0;

            if (edge[edgeID].check_maturity(globalCounter)) {
                if(edge[edgeID].isPeel == false){
                edge[edgeID].isPeel = true;
                peelList.push(edgeID);
                }
            }
        }

        
    }

    inline void set_reverse_index_by_index(int index, ui reverseIndex) {
        reverseIndexInMemberEdge[index] = reverseIndex;
    }






    affect_edge_t remove_member_by_index(int index) {
        /*
        if (index.first == -1) {
            return std::make_pair(-1, 0);
        }
        */
        //int bucket = index.first;
        int length = memberEdge.size();
        //cout<<"bucket:"<<memberEdge[0].size()<<endl;
        
            if (index < length - 1) {
                ui affectEdgeID = memberEdge[length - 1];
                ui affectedIndex = reverseIndexInMemberEdge[length - 1];
                memberEdge[index] = affectEdgeID;
                reverseIndexInMemberEdge[index] = affectedIndex;
                memberEdge.pop_back();
                reverseIndexInMemberEdge.pop_back();
                //cout<<index.first<<" "<<index.second<<endl;
                return std::make_pair(affectEdgeID, affectedIndex);
            } else {
                memberEdge.pop_back();
                reverseIndexInMemberEdge.pop_back();
                //cout<<index.first<<" "<<index.second<<endl;
                return std::make_pair(-1, 0);
            }
        
        
    }

};
