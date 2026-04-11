#include "bloom/bloom.h"
#include<iostream>
using  namespace  std;
Bloom::~Bloom() {

}
/*
void Bloom::initialize_bacth_space() {
    maxSize = log2_32(bloomNumber - 1) + 1;
    memberEdge.resize(maxSize);
    reverseIndexInMemberEdge.resize(maxSize);
    cnt.resize(maxSize);
}
*/
void Bloom::initialize_space() {
    int maxSize = log2_32(bloomNumber - 1) + 1;
    memberEdge.resize(maxSize);
    reverseIndexInMemberEdge.resize(maxSize);
    cnt.resize(maxSize);
}

void Bloom::initialize_space(int value) {
    int maxSize = log2_32(value) + 1;
    memberEdge.resize(maxSize);
    reverseIndexInMemberEdge.resize(maxSize);
    cnt.resize(maxSize);
}

void Bloom::initialize_space_member_edge_only() {
    int maxSize = log2_32(bloomNumber - 1) + 1;
    memberEdge.resize(maxSize);
    cnt.resize(maxSize);
}

void Bloom::initialize_space_member_edge_only(int value) {
    int maxSize = log2_32(value) + 1;
    memberEdge.resize(maxSize);
    cnt.resize(maxSize);
}

pair_t Bloom::add_member_edge(ui edgeID, ui indexInMemberEdge,
                              Edge *edge) {
    if(!edge[edgeID].isDT){
        memberEdge[0].push_back(edgeID);
        reverseIndexInMemberEdge[0].push_back(indexInMemberEdge);
        return std::make_pair(0,memberEdge[0].size()-1);
    }
    int slackValue = edge[edgeID].get_slack_value();
    if (slackValue >= bloomNumber)
        return std::make_pair(-1, 0);
    int bucket = log2_32(slackValue);// edge future value：slackValue+counter
    memberEdge[bucket].push_back(edgeID);
    cnt[bucket].push_back(counter);
    reverseIndexInMemberEdge[bucket].push_back(indexInMemberEdge);
    return std::make_pair(bucket, memberEdge[bucket].size() - 1);
}

pair_t Bloom::add_member_edge(ui edgeID, Edge *edge) {
    if(!edge[edgeID].isDT){
        memberEdge[0].push_back(edgeID);
        return std::make_pair(0, memberEdge[0].size()-1);
    }
    int slackValue = edge[edgeID].get_slack_value();
    if (slackValue >= bloomNumber)
        return std::make_pair(-1, 0);
    int bucket = log2_32(slackValue);
    memberEdge[bucket].push_back(edgeID);
    cnt[bucket].push_back(counter);
    return std::make_pair(bucket, memberEdge[bucket].size() - 1);
}

void Bloom::send_value_to_member(std::vector<ui> &matureList, std::queue<ui> &peelList, Edge *edge) {
    int old_counter = counter;
    counter++;  // Increment counter

    // Cache memberEdge[0] for quick access
    auto& bucket0 = memberEdge[0];  // Cache first bucket
    for (ui i = 0;i <bucket0.size() ;i++) {
        ui edgeID = bucket0[i];
        edge[edgeID].accumulate_value(1);
        if (edge[edgeID].check_maturity()) {
            edge[edgeID].isPeel = true;
            peelList.push(edgeID);
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
                    edge[edgeID].accumulate_value(counter-cnt[bucket][i]);
                    cnt[bucket][i] = counter;

                    if (edge[edgeID].check_maturity()) {
                        matureList.push_back(edgeID);
                    }
                }
            }
        bucket++;
    }
}

void Bloom::send_value_to_member1(
                                 std::vector<ui> &matureList, std::vector<ui> &peelList,Edge *edge,
                                 char *isAffected) {
    int old_counter = counter;
    counter++;  
    auto& bucket0 = memberEdge[0];  // Cache first bucket
    for (ui i = 0;i <bucket0.size() ;i++) {
        ui edgeID = bucket0[i];
        edge[edgeID].accumulate_value(1);
        if (edge[edgeID].check_maturity()) {
            edge[edgeID].isPeel = true;
            peelList.push_back(edgeID);
        }
    }

    if (counter < 16) return;

    int temp = log2_32((old_counter ^ counter)+1);
    int bucket = 4;
    size_t memberEdgeSize = memberEdge.size();

    while (bucket< temp) {
            if (bucket < memberEdgeSize) {
                auto& currentBucket = memberEdge[bucket];  // Cache current bucket
                for (ui i = 0; i< currentBucket.size();i++) {
                    ui edgeID = memberEdge[bucket][i];
                    edge[edgeID].accumulate_value(counter-cnt[bucket][i]);
                    cnt[bucket][i] = counter;

                    if (edge[edgeID].check_maturity()) {
                        matureList.push_back(edgeID);
                    }
                }
            }
        bucket++;
    }
}

void Bloom::send_value_to_member(int deltaValue,
                                 std::vector<ui> &matureList, std::vector<ui> &peelList,Edge *edge,
                                 char *isAffected){
    int old_counter = counter;
    counter+=deltaValue;
    auto& bucket0 = memberEdge[0];  // Cache first bucket
    for (ui i = 0;i <bucket0.size() ;i++) {
        ui edgeID = bucket0[i];
        edge[edgeID].accumulate_value(deltaValue);
        if (edge[edgeID].check_maturity() &&edge[edgeID].isPeel == false) {
            edge[edgeID].isPeel = true;
            peelList.push_back(edgeID);
        }
    }
    if (counter < 16) return;
    int temp = counter;
    size_t memberEdgeSize = memberEdge.size();
    int max_bit = 63 - __builtin_clzll(counter);
    for (int k = 4; k <= max_bit; ++k) {
        uint64_t bit = (old_counter >> k) & 1;
        uint64_t next_flip;
        
        if (bit == 0) {
            next_flip = (old_counter | ((1ULL << k) - 1)) + 1;
        } else {
            uint64_t mask = (1ULL << (k + 1)) - 1;
            next_flip = (old_counter & ~mask) + (1ULL << (k + 1));
        }
        
        if (next_flip <= counter) {
            if (k < memberEdgeSize) {
            auto& currentBucket = memberEdge[k];  // Cache current bucket
            for (ui i = 0; i< currentBucket.size();i++) {
                ui edgeID = memberEdge[k][i];
                edge[edgeID].accumulate_value(counter-cnt[k][i]);
                cnt[k][i] = counter;

                if (edge[edgeID].check_maturity() && !isAffected[edgeID]) {
                    matureList.push_back(edgeID);
                    isAffected[edgeID] = 1;
                }
            }
        }
        }
    }
}

void Bloom::send_value_to_member(int deltaValue, pair_t index, Edge *edge) {
    int bucket = index.first;

    if (bucket == -1)
        return;
    ui edgeID = memberEdge[bucket][index.second];
    if(bucket == 0){
        edge[edgeID].accumulate_value(deltaValue);
        return;
    }
    //int bucketValue = pow2[bucket];

    int sendValue = counter + deltaValue - cnt[bucket][index.second];
    edge[edgeID].accumulate_value(sendValue);
}

affect_edge_t Bloom::remove_member_by_index(pair_t index) {
    
    if (index.first == -1) {
        return std::make_pair(-1, 0);
    }
    int bucket = index.first;
    ui length = memberEdge[bucket].size();
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
            ui cb = cnt[bucket][length - 1];
            cnt[bucket][index.second] = cb;
            reverseIndexInMemberEdge[bucket][index.second] = affectedIndex;
            memberEdge[bucket].pop_back();
            cnt[bucket].pop_back();
            reverseIndexInMemberEdge[bucket].pop_back();
            return std::make_pair(affectEdgeID, affectedIndex);
        } else {
            memberEdge[bucket].pop_back();
            cnt[bucket].pop_back();
            reverseIndexInMemberEdge[bucket].pop_back();
            return std::make_pair(-1, 0);
        }
    }
}

ui Bloom::remove_member_by_index_id_only(pair_t index) {
    if (index.first == -1) {
        return -1;
    }
    int bucket = index.first;
    ui length = memberEdge[bucket].size();
    if(bucket == 0){
        if (index.second < length - 1) {
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
            ui cb = cnt[bucket][length-1];
            memberEdge[bucket][index.second] = affectEdgeID;
            cnt[bucket][index.second] = cb;
            memberEdge[bucket].pop_back();
            cnt[bucket].pop_back();
            return affectEdgeID;
        } else {
            memberEdge[bucket].pop_back();
            cnt[bucket].pop_back();
            return -1;
        }
    }
}

int log2_32(uint32_t value) {
    value |= value >> 1;
    value |= value >> 2;
    value |= value >> 4;
    value |= value >> 8;
    value |= value >> 16;
    return tab32[(uint32_t)(value * 0x07C4ACDD) >> 27];
}