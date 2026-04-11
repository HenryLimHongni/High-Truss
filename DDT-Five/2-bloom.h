#pragma once
#include <cstdint>
#include <vector>
#include<queue>
#include <utility>
#include "dt_utils.h"
#include "edge.h"
typedef std::pair<ui, ui> affect_edge_t;
using namespace std;


class TwoBloom {
public:
    //int       id{-1};
    //uint64_t  key{0};                 // (a,c)
    uint64_t  total_paths{0};         

    // DT buckets
    int bloomNumber{0};               
    int counter{0};                   // 3-bloom的counter

    vector<vector<int>>      memberEdge;
    vector<vector<ui>>       reverseIndexInMemberEdge;
    //std::vector<std::vector<ui>>       cnt;

    TwoBloom() = default;
    TwoBloom(int /*_id*/, uint64_t /*_key*/) {};

    // 在 total_paths 已确定后调用
    inline void ensure_buckets_by_paths(int temp) {
        bloomNumber = temp;
        int tmp = log2_32(temp)+1;
        memberEdge.assign(tmp, {});
        reverseIndexInMemberEdge.assign(tmp, {});
        //cnt.assign(bloomNumber, {});
    }

    inline void clear_buckets() {
        for (int b=0;b<(int)memberEdge.size();++b) {
            memberEdge[b].clear();
            reverseIndexInMemberEdge[b].clear();
            //cnt[b].clear();
        }
    }

    pair_t add_member_edge(ui edgeID, ui indexInMemberEdge, vector<Edge>&edge) {
        
            memberEdge[0].push_back((int)edgeID);
            reverseIndexInMemberEdge[0].push_back(indexInMemberEdge);
            //cnt[0].push_back((ui)counter);
            return std::make_pair(0, (int)memberEdge[0].size()-1);
        
    }

    pair_t add_member_edge2(uint32_t bucket, ui edgeID, ui indexInMemberEdge, vector<Edge>&edge) {
        
        ui slackValue = edge[edgeID].get_slack_value();
        if (slackValue >= bloomNumber)
            return std::make_pair(-1, 0);
        //int bucket = log2_32((ui)slackValue);
        /*
        if(memberEdge.size() <= bucket || reverseIndexInMemberEdge.size() <= bucket)
        cout<<"bucket:"<<bucket<<"slack:"<<slackValue<<" memberEdge:" << memberEdge.size()<<" reverseIndexInMemberEdge"<<reverseIndexInMemberEdge.size()<<" hostbloomnumber:"<<edge[edgeID].hostbloomnumber<<" five_cyclenum:"<<edge[edgeID].five_cycle_support<<endl;
        */
        memberEdge[bucket].push_back((int)edgeID);
        
        //edge[edgeID].cnt2[indexInMemberEdge] = counter;
        reverseIndexInMemberEdge[bucket].push_back(indexInMemberEdge);
        return std::make_pair(bucket, (int)memberEdge[bucket].size() - 1);
    }



    void send_value_to_member(int increasenum, vector<ui> &matureList, vector<ui> &peelList, vector<Edge>&edge) {
        int old_counter = counter;
        counter+= increasenum;  // Increment counter

        // Cache memberEdge[0] for quick access
        auto& bucket0 = memberEdge[0];  // Cache first bucket
        for (ui i = 0;i <bucket0.size() ;i++) {
            ui edgeID = bucket0[i];
            if(!edge[edgeID].isPeel){
            edge[edgeID].accumulate_value(increasenum);
            //if(edgeID == 19559)cout<<"a5:"<<increasenum<<endl;
            //if(edgeID == 19559)cout<<"decrease:a5 "<<edge[edgeID].balance<<endl;
            edge[edgeID].decrease_value(edge[edgeID].balance);

            edge[edgeID].balance = 0;

            if (edge[edgeID].check_maturity()) {
                if(edge[edgeID].isPeel == false){
                edge[edgeID].isPeel = true;
                peelList.push_back(edgeID);
                }
            }
        }
        }

        if (counter < 16) return;

        int temp = log2_32((old_counter ^ counter));
        int bucket = 4;

        // Cache memberEdge sizes to avoid multiple accesses
        size_t memberEdgeSize = memberEdge.size();

        // Iterate through buckets based on the diff
        while (bucket<= temp) {
            if (bucket < memberEdgeSize) {
                auto& currentBucket = memberEdge[bucket];  // Cache current bucket
                for (ui i = 0; i< currentBucket.size();i++) {
                    ui edgeID = memberEdge[bucket][i];
                    if (!edge[edgeID].isPeel){
                    ui cnt = edge[edgeID].cnt2[reverseIndexInMemberEdge[bucket][i]];
                    edge[edgeID].accumulate_value(counter-cnt);
                    //if(edgeID == 19559)cout<<"a6:"<<counter-cnt<<endl;
                    edge[edgeID].cnt2[reverseIndexInMemberEdge[bucket][i]] = counter;

                    if (edge[edgeID].check_maturity()) {
                        matureList.push_back(edgeID);
                    }
                }
                }
            }
            bucket++;
        }
    }

    inline void set_reverse_index_by_index(pair_t index, ui reverseIndex) {
        reverseIndexInMemberEdge[index.first][index.second] = reverseIndex;
    }






    affect_edge_t remove_member_by_index(pair_t index) {

        if (index.first == -1) {
            return std::make_pair(-1, 0);
        }
        int bucket = index.first;
        int length = memberEdge[bucket].size();
        //cout<<"bucket:"<<memberEdge[0].size()<<endl;
        if(bucket == 0){
            if (index.second < length - 1) {
                ui affectEdgeID = memberEdge[bucket][length - 1];
                ui affectedIndex = reverseIndexInMemberEdge[bucket][length - 1];
                memberEdge[bucket][index.second] = affectEdgeID;
                reverseIndexInMemberEdge[bucket][index.second] = affectedIndex;
                memberEdge[bucket].pop_back();
                reverseIndexInMemberEdge[bucket].pop_back();
                //cout<<index.first<<" "<<index.second<<endl;
                return std::make_pair(affectEdgeID, affectedIndex);
            } else {
                memberEdge[bucket].pop_back();
                reverseIndexInMemberEdge[bucket].pop_back();
                //cout<<index.first<<" "<<index.second<<endl;
                return std::make_pair(-1, 0);
            }
        }
        else{
            if (index.second < length - 1) {
                ui affectEdgeID = memberEdge[bucket][length - 1];
                ui affectedIndex = reverseIndexInMemberEdge[bucket][length - 1];
                memberEdge[bucket][index.second] = affectEdgeID;
                //ui cb = edge[edgeID]
                //ui cb = cnt[bucket][length - 1];
                //cnt[bucket][index.second] = cb;
                reverseIndexInMemberEdge[bucket][index.second] = affectedIndex;
                memberEdge[bucket].pop_back();
                //cnt[bucket].pop_back();
                reverseIndexInMemberEdge[bucket].pop_back();
                return std::make_pair(affectEdgeID, affectedIndex);
            } else {
                memberEdge[bucket].pop_back();
                //cnt[bucket].pop_back();
                reverseIndexInMemberEdge[bucket].pop_back();
                return std::make_pair(-1, 0);
            }
        }
    }

};
