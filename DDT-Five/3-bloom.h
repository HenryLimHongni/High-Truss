#pragma once
#include <cstdint>
#include<queue>
#include <utility>
#include "dt_utils.h"
#include <vector>
#include "edge.h"



typedef std::pair<ui, ui> affect_edge_t;
using namespace std;

class ThreeBloom {
public:
       
    struct Path3 { uint32_t a, b, c; }; 

    vector<int>      local2eid;           
    vector<uint32_t> local2hostidx;       

    vector<Path3>    paths;               // size = total_paths (<= seg_len)
    vector<uint32_t> off;                 // size = K+1, CSR offsets
    vector<uint32_t> inc;                 
    // alive 
    vector<uint64_t> alive;
    inline bool is_alive(uint32_t pid) const {
        return (alive[pid>>6] >> (pid & 63)) & 1ull;
    }
    inline void kill(uint32_t pid) {
        alive[pid>>6] &= ~(1ull << (pid & 63));
    }

    //int       id{-1};
    //uint64_t  key{0};                 // (a,d)
    uint64_t  total_paths{0};

    int bloomNumber{0};
    int counter{0};

    vector<vector<int>>      memberEdge;
    vector<vector<ui>>       reverseIndexInMemberEdge;
    //std::vector<std::vector<ui>>       cnt;
    vector<int> nodtmemberEdge;
    vector<int>nodtreverseIndexInMemberEdge;
    ThreeBloom() = default;
    //ThreeBloom(int _id, uint64_t _key) : id(_id), key(_key) {}
    ThreeBloom(int /*_id*/, uint64_t /*_key*/) {};

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
        
            nodtmemberEdge.push_back((int)edgeID);
            nodtreverseIndexInMemberEdge.push_back(indexInMemberEdge);
            //if(edgeID == 8523) cout<<"ok1"<<endl;
            //cnt[0].push_back((ui)counter);
            return std::make_pair(-2, nodtmemberEdge.size()-1);
            
        
    }

    pair_t add_member_edge2(uint32_t bucket,ui edgeID, ui indexInMemberEdge, vector<Edge>&edge) {
        
        //if(edgeID == 8523) cout<<"ok2"<<endl;
        ui slackValue = edge[edgeID].get_slack_value();
        int edgeNumber = edge[edgeID].host3Cnt[indexInMemberEdge];
        if (slackValue > bloomNumber)
            return std::make_pair(-1, 0);
        //uint32_t ratio = (uint32_t)(slackValue / edgeNumber);
        //if (ratio == 0) ratio = 1; 
        //int bucket = log2_32(ratio);
        memberEdge[bucket].push_back((int)edgeID);
        //edge[edgeID].cnt3[indexInMemberEdge] = counter;
        reverseIndexInMemberEdge[bucket].push_back(indexInMemberEdge);
        return std::make_pair(bucket, (int)memberEdge[bucket].size() - 1);
    }

    affect_edge_t remove_member_by_index(pair_t index) {
        

        if (index.first == -1) {
            return std::make_pair(-1, 0);
        }
        int bucket = index.first;
        //ui length = memberEdge[bucket].size();
        //cout<<"bucket:"<<memberEdge[0].size()<<endl;
        if(bucket == -2){
            int length = nodtmemberEdge.size();
            if (index.second < length - 1) {
                ui affectEdgeID = nodtmemberEdge[length - 1];
                ui affectedIndex = nodtreverseIndexInMemberEdge[length - 1];
                nodtmemberEdge[index.second] = affectEdgeID;
                nodtreverseIndexInMemberEdge[index.second] = affectedIndex;
                nodtmemberEdge.pop_back();
                nodtreverseIndexInMemberEdge.pop_back();
                //cout<<index.first<<" "<<index.second<<endl;
                return std::make_pair(affectEdgeID, affectedIndex);
            } else {
                nodtmemberEdge.pop_back();
                nodtreverseIndexInMemberEdge.pop_back();
                //cout<<index.first<<" "<<index.second<<endl;
                return std::make_pair(-1, 0);
            }
        }
        else{
            ui length = memberEdge[bucket].size();
            //cout<<memberEdge[bucket].size()<<endl;
            if (index.second < length - 1) {
                ui affectEdgeID = memberEdge[bucket][length - 1];
                ui affectedIndex = reverseIndexInMemberEdge[bucket][length - 1];
                //cout<<"bucket:"<<bucket<<" size:"<< memberEdge[bucket].size()<<" index:"<<index.second<<endl;
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

    void send_value_to_member(vector<ui> &matureList, vector<ui> &peelList, vector<Edge>& edge) {
        int old_counter = counter;
        counter++;  // Increment counter

        // Cache memberEdge[0] for quick access
        //auto& bucket0 = memberEdge[0];  // Cache first bucket
        for (ui i = 0;i <nodtmemberEdge.size() ;i++) {
            ui edgeID = nodtmemberEdge[i];
            if(!edge[edgeID].isPeel){
                edge[edgeID].accumulate_value(edge[edgeID].host3Cnt[nodtreverseIndexInMemberEdge[i]]);
                //if(edgeID == 19559)cout<<"a7:"<<edge[edgeID].host3Cnt[nodtreverseIndexInMemberEdge[i]]<<endl;
                /*
                if(edgeID == 20){
                    cout<<"done4:"<<edge[edgeID].balance<<endl;
                }
                    */
                edge[edgeID].decrease_value(edge[edgeID].balance);
                //if(edgeID == 19559)cout<<"decrease:a7 "<<edge[edgeID].balance<<endl;
                edge[edgeID].balance = 0;

                if (edge[edgeID].check_maturity()) {
                    if(edge[edgeID].isPeel == false){
                    edge[edgeID].isPeel = true;
                    peelList.push_back(edgeID);
                    }
                }
            }
        }

        //if (counter < 16) return;

        int temp = log2_32((old_counter ^ counter)+1);
        int bucket = 0;

        // Cache memberEdge sizes to avoid multiple accesses
        size_t memberEdgeSize = memberEdge.size();

        // Iterate through buckets based on the diff
        while (bucket< temp) {
            if (bucket < memberEdgeSize) {
                auto& currentBucket = memberEdge[bucket];  // Cache current bucket
                for (ui i = 0; i< currentBucket.size();i++) {
                    ui edgeID = memberEdge[bucket][i];
                    if(!edge[edgeID].isPeel){
                    ui cnt3 = edge[edgeID].cnt3[reverseIndexInMemberEdge[bucket][i]];
                    edge[edgeID].accumulate_value((counter-cnt3)*edge[edgeID].host3Cnt[reverseIndexInMemberEdge[bucket][i]]);
                    //if(edgeID == 19559)cout<<"a8:"<<(counter-cnt3)*edge[edgeID].host3Cnt[reverseIndexInMemberEdge[bucket][i]]<<endl;
                    edge[edgeID].cnt3[reverseIndexInMemberEdge[bucket][i]] = counter;

                    if (edge[edgeID].check_maturity()) {
                        matureList.push_back(edgeID);
                    }
                }
                }
            }
            bucket++;
        }
    }
};
