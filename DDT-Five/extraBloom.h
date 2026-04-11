#pragma once
#include <cstdint>
#include <vector>
#include <utility>
#include "dt_utils.h"
#include "edge.h"


class ExtraBloom {
public:
    //int       id{-1};
    //uint64_t  key{0};                 // (a,c)
    //uint64_t  total_paths{0};         

    // DT buckets

    uint64_t bloomNumber;
    uint64_t maxSupport;               
    uint64_t counter{0};                   

    vector<vector<int>>      memberEdge;
    //vector<vector<ui>>       reverseIndexInMemberEdge;
    //vector<vector<ui>>       cnt;

    ExtraBloom() = default;
    //TwoBloom(int _id, uint64_t _key) : id(_id), key(_key) {}

    

    inline void ensure_buckets_by_paths() {
        bloomNumber = log2_32(maxSupport-1)+1;
        memberEdge.assign(bloomNumber, {});
        //reverseIndexInMemberEdge.assign(bloomNumber, {});
        //cnt.assign(bloomNumber, {});
    }

    inline void clear_buckets() {
        for (int b=0;b<(int)memberEdge.size();++b) {
            memberEdge[b].clear();
            //reverseIndexInMemberEdge[b].clear();
            //cnt[b].clear();
        }
    }

    pair_t add_member_edge(ui edgeID, vector<Edge>& edge) {
        if(!edge[edgeID].isDT){
            memberEdge[0].push_back(edgeID);
            return std::make_pair(0, memberEdge[0].size()-1);
        }
        int slackValue = edge[edgeID].get_slack_value();
        if (slackValue >= maxSupport)
            return std::make_pair(-1, 0);
        int bucket = log2_32(slackValue);
        memberEdge[bucket].push_back(edgeID);
        edge[edgeID].extraBloom_cnt = counter;
        return std::make_pair(bucket, memberEdge[bucket].size() - 1);
    }


    void send_value_to_member(vector<ui> &matureList, vector<ui> &peelList, vector<Edge>&edge) {
        int old_counter = counter;
        counter++;  // Increment counter

        // Cache memberEdge[0] for quick access
        auto& bucket0 = memberEdge[0];  // Cache first bucket
        for (ui i = 0;i <bucket0.size() ;i++) {
            ui edgeID = bucket0[i];
            edge[edgeID].accumulate_value(1);

            if (edge[edgeID].check_maturity()) {
                //cout<<edgeID<<endl;
                if(edge[edgeID].isPeel == false){
                edge[edgeID].isPeel = true;
                peelList.push_back(edgeID);
                }
            }
        }

        if (counter < 16) return;

        int temp = log2_32((old_counter ^ counter)+1);
        int bucket = 4;

        // Cache memberEdge sizes to avoid multiple accesses
        size_t memberEdgeSize = memberEdge.size();

        // Iterate through buckets based on the diff
        while (bucket< temp) {
            if (bucket < memberEdgeSize) {
                auto& currentBucket = memberEdge[bucket];  // Cache current bucket
                for (ui i = 0; i< currentBucket.size();i++) {
                    ui edgeID = memberEdge[bucket][i];
                    ui cnt = edge[edgeID].extraBloom_cnt;
                    edge[edgeID].accumulate_value(counter-cnt);
                    edge[edgeID].extraBloom_cnt = counter;

                    if (edge[edgeID].check_maturity()) {
                        //cout<<"edgeid:"<<edgeID<<" counter:"<<counter<<endl;
                        matureList.push_back(edgeID);
                    }
                }
            }
            bucket++;
        }
    }

    ui remove_member_by_index_id_only(pair_t index) {
        if (index.first == -1) {
            return -1;
        }
        int bucket = index.first;
        int length = memberEdge[bucket].size();
        if(bucket == 0){
            if (index.second < length - 1) {
                //cout<<"second:"<<index.second<<endl;
                //cout<<"length:"<<length<<endl;
                ui affectEdgeID = memberEdge[bucket][length - 1];
                memberEdge[bucket][index.second] = affectEdgeID;
                memberEdge[bucket].pop_back();
                return affectEdgeID;
            } else {
                memberEdge[bucket].pop_back();
                return -1;
            }
        }else{
            if (index.second < length - 1) {
                ui affectEdgeID = memberEdge[bucket][length - 1];
                //ui cb = cnt[bucket][length-1];
                memberEdge[bucket][index.second] = affectEdgeID;
                //cnt[bucket][index.second] = cb;
                memberEdge[bucket].pop_back();
                //cnt[bucket].pop_back();
                return affectEdgeID;
            } else {
                memberEdge[bucket].pop_back();
                //cnt[bucket].pop_back();
                return -1;
            }
        }
    }
};

