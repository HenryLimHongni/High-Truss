#pragma once
#include <vector>
#include <string>
#include <fstream>
#include <sstream>
#include <iostream>
#include <utility>
#include <algorithm>
#include <cstdint>
#include <iomanip>
#include <unordered_map>
#include<limits>
#include <queue>
#include<unistd.h>
#include <fstream>



#include <unordered_set>
#include "extraBloom.h"
#include "current_time.h"
#include "edge.h"
#include "3-bloom.h"
#include "2-bloom.h"
#include "dt_utils.h"
using namespace std;
typedef std::tuple<int, pair_t, ui, ui> affect_bloom_t;
typedef std::tuple<int, pair_t, pair_t, pair_t> affect_3bloom_t;

// 选择更快的哈希可自行切换
template <class K, class V>

using FlatHashMap = std::unordered_map<K, V>;



class Graph {
public:
    void read_edges_from_file(const std::string& filename) {
        std::ifstream fin(filename);
        if (!fin) {
            std::cerr << "Cannot open file: " << filename << "\n";
            std::exit(1);
        }
        std::string line;
        edges_raw.reserve(1<<20);

        while (std::getline(fin, line)) {
            if (line.empty()) continue;
            for (char &c : line) if (c==',' || c=='\t') c=' ';
            int u, v;
            std::istringstream iss(line);
            if (!(iss >> u >> v)) continue;
            if (u == v) continue;                
            edges_raw.emplace_back(u, v);
        }
    }

    void remap_ids() {
        
        id2idx.reserve(edges_raw.size()*2 + 1024);
        idx2id.reserve(edges_raw.size());

        for (auto &e : edges_raw) {
            if (!id2idx.count(e.first)) {
                int idx = (int)id2idx.size();
                id2idx[e.first] = idx;
                idx2id.push_back(e.first);
            }
            if (!id2idx.count(e.second)) {
                int idx = (int)id2idx.size();
                id2idx[e.second] = idx;
                idx2id.push_back(e.second);
            }
        }
        n = (int)idx2id.size();
    }

    inline uint64_t pack_edge(uint32_t a, uint32_t b) const {
        uint32_t x = std::min(a, b);
        uint32_t y = std::max(a, b);
        return (uint64_t(x) << 32) | uint64_t(y);
    }

    template <class MapT>
    static inline void tune_hashmap_any(MapT& m, size_t expected_size, float lf=0.6f) {
        if (lf > 0.0f) m.max_load_factor(lf);
        if (expected_size > 0) m.reserve(expected_size);
    }

    void dedup_and_build_edges() {
        
        FlatHashMap<uint64_t,char> seen;
        tune_hashmap_any(seen, edges_raw.size()*2 + 1024, 0.6f);

        edges.reserve(edges_raw.size());
        edgeId.reserve(edges_raw.size()*2 + 1024);
        undirected_edges.reserve(edges_raw.size());

        for (auto &e : edges_raw) {
            
            int u = id2idx[e.first];
            int v = id2idx[e.second];
            if (u == v) continue; 

           
            uint64_t k = pack_edge((uint32_t)u, (uint32_t)v);
            if (seen.emplace(k, (char)1).second) {
                int id = (int)edges.size();           
                edges.emplace_back(id, u, v);         
                edgeId.emplace(k, id);                
                undirected_edges.emplace_back(u, v);  
            }
        }

        edges_raw.clear();
        edges_raw.shrink_to_fit();
    }


   
    void core_decomposition_bz() {
        adj.assign(n, {});
        for (const auto &e : edges) {
            adj[e.u].push_back(e.v);
            adj[e.v].push_back(e.u);
        }

        vector<int> deg(n, 0);
        int maxd = 0;
        for (int i=0;i<n;++i) { deg[i]=(int)adj[i].size(); if (deg[i]>maxd) maxd=deg[i]; }

        vector<int> bin(maxd+1, 0);
        for (int d:deg) ++bin[d];
        int start = 0;
        for (int d=0; d<=maxd; ++d) { int cnt = bin[d]; bin[d]=start; start+=cnt; }

        vector<int> pos(n), vert(n);
        for (int v=0; v<n; ++v) { int d=deg[v]; pos[v]=bin[d]++; vert[pos[v]]=v; }
        for (int d=maxd; d>0; --d) bin[d]=bin[d-1];
        bin[0]=0;

        core.assign(n, 0);
        peel_rank.assign(n, -1);
        int rank = 0;

        for (int i=0; i<n; ++i) {
            int v = vert[i];
            core[v] = deg[v];
            peel_rank[v] = rank++;
            for (int u : adj[v]) if (deg[u] > deg[v]) {
                int du=deg[u], pu=pos[u], pw=bin[du], w=vert[pw];
                if (u != w) { pos[u]=pw; vert[pw]=u; pos[w]=pu; vert[pu]=w; }
                ++bin[du]; --deg[u];
            }
        }
        
    }

   
    struct Next { int to; int eid; };
    void build_oriented_out() {
        out.assign(n, {});
        out.reserve(n);
        for (int i=0;i<n;++i) out[i].reserve(adj[i].size()/2 + 1);

        for (const auto &e : edges) {
            if (peel_rank[e.u] < peel_rank[e.v]) {
                out[e.u].push_back({e.v, e.id});
            } else {
                out[e.v].push_back({e.u, e.id});
            }
        }

        
        nbr2eid.clear();
        nbr2eid.resize(n);
        for (const auto &e : edges) {
            nbr2eid[e.u].emplace(e.v, e.id);
            nbr2eid[e.v].emplace(e.u, e.id);
        }
    }

void collect_2paths_grouped() {
    key2tmpid.clear(); tmpid2key.clear(); twopaths_by_key.clear();

    tmpid2key.reserve(std::max<size_t>(edges.size() / 8 + 1, 1024ull));

    for (int b = 0; b < n; ++b) {
        const auto &A = out[b];   // a ∈ out[b]
        if (A.empty()) continue;
        const auto &C = adj[b];   // c ∈ adj[b]

        for (const auto &an : A) {
            const int a    = an.to;
            const int e_ab = an.eid;

            for (int c : C) {
                if (c == b || c == a) continue;
                if (!(peel_rank[a] > peel_rank[c])) continue;

                const int e_bc = get_edge_id_fast(b, c);
                if (e_bc < 0) continue;

                const uint64_t key = pack_edge((uint32_t)a, (uint32_t)c);
                int tid = get_or_create_tmpid(key);
                twopaths_by_key[tid].emplace_back(e_ab, e_bc); // (a,b) 与 (b,c)
            }
        }
    }
}

void build_twopath_edge_membership() {
    const int T = (int)tmpid2key.size();
    twopath_edge_membership.clear();
    twopath_edge_membership.resize(T);
    for (int tid = 0; tid < T; ++tid) {
        auto &set = twopath_edge_membership[tid];
        const auto &vec = twopaths_by_key[tid];
        tune_hashmap_any(set, vec.size()*2 + 8, 0.6f);
        for (const auto &p : vec) {
            set.emplace(p.first, 1);   // e_ab
            set.emplace(p.second, 1);  // e_bc
        }
    }
}

void collect_3paths_for_existing_keys() {
    const int T = (int)tmpid2key.size();
    threepaths_by_tid.clear();
    threepaths_by_tid.assign(T, {});

    valid_tmp.assign(T, false);
    edge_dedup.assign(edges.size(), 0);

    
    auto decode_abc_from_two_edges = [&](int e_ab, int e_bc, int &a, int &b, int &c) -> bool {
        const int u1 = edges[e_ab].u, v1 = edges[e_ab].v;
        const int u2 = edges[e_bc].u, v2 = edges[e_bc].v;

        
        if (u1 == u2) { b = u1; a = v1; c = v2; return true; }
        if (u1 == v2) { b = u1; a = v1; c = u2; return true; }
        if (v1 == u2) { b = v1; a = u1; c = v2; return true; }
        if (v1 == v2) { b = v1; a = u1; c = u2; return true; }
        return false; 
    };

    
    for (int tid_ac = 0; tid_ac < T; ++tid_ac) {
        const auto &vec2 = twopaths_by_key[tid_ac];
        if (vec2.empty()) continue;

        for (const auto &pr : vec2) {
            const int e_ab = pr.first;
            const int e_bc = pr.second;

            int a, b, c;
            if (!decode_abc_from_two_edges(e_ab, e_bc, a, b, c)) continue;
            if (a == b || b == c || a == c) continue;

            
            if (!(peel_rank[a] > peel_rank[c])) continue;

            const auto &out_c = out[c];
            if (out_c.empty()) continue;

            for (const auto &dn : out_c) {
                const int d    = dn.to;
                const int e_cd = dn.eid;

                if (d == a || d == b || d == c) continue;

                
                if (!(peel_rank[a] > peel_rank[d])) continue;

               
                const uint64_t key_ad = pack_edge((uint32_t)a, (uint32_t)d);
                auto it = key2tmpid.find(key_ad);
                if (it == key2tmpid.end()) continue;

                const int tid_ad = it->second;

                threepaths_by_tid[tid_ad].push_back(
                    Path3E{ (uint32_t)e_ab, (uint32_t)e_bc, (uint32_t)e_cd }
                );
                valid_tmp[tid_ad] = true;

                
                const auto &mem = twopath_edge_membership[tid_ad];

                const bool hit_ab = (mem.find(e_ab) != mem.end());
                if (hit_ab) {
                    edge_dedup[e_ab] += 2;
                    edge_dedup[e_bc] += 1;
                    edge_dedup[e_cd] += 1;
                    const int e_bd = get_edge_id_fast(b, d);
                    if (e_bd >= 0 && mem.find(e_bd) != mem.end()) {
                        edge_dedup[e_bd] += 1;
                    }
                }

                const bool hit_cd = (mem.find(e_cd) != mem.end());
                if (hit_cd) {
                    edge_dedup[e_cd] += 2;
                    edge_dedup[e_ab] += 1;
                    edge_dedup[e_bc] += 1;
                    const int e_ac = get_edge_id_fast(a, c);
                    if (e_ac >= 0 && mem.find(e_ac) != mem.end()) {
                        edge_dedup[e_ac] += 1;
                    }
                }
            }
        }
    }

    std::size_t rss_bytes = getCurrentRSSBytes();
    double rss_gib = rss_bytes / (1024.0 * 1024.0 * 1024.0);
    double rss_gb  = rss_bytes / 1e9;
    cerr << "[MEM] After enumerating 3-path (2-path extend): "
         << fixed << setprecision(3)
         << rss_gib << " GiB  (~" << rss_gb << " GB)\n";
}

    

   
    void build_blooms_strict_matched() {
        double start = get_current_time();
    
       
        for (auto &E : edges) {
            E.host3Bloom.clear();
            E.host3Cnt.clear();
            E.local3idx.clear();
            E.host2Bloom.clear();
            E.reverseIndexInHost2Bloom.clear();
            E.twinEdge.clear();
            E.hostBloomIndexInTwin.clear();
        }
    
        key2bid.clear();
        bid2key.clear();
        three_blooms.clear();
        two_blooms.clear();
    
       
        std::vector<int> tid2bid(tmpid2key.size(), -1);
        bid2key.reserve(tmpid2key.size());
    
        for (int tid = 0; tid < (int)tmpid2key.size(); ++tid) {
            if (!valid_tmp[tid]) continue;
            int bid = (int)bid2key.size();
            tid2bid[tid] = bid;
            uint64_t key = tmpid2key[tid];
            bid2key.push_back(key);
            key2bid.emplace(key, bid);
        }
    
       
        three_blooms.clear();
        three_blooms.reserve(bid2key.size());
        for (int bid = 0; bid < (int)bid2key.size(); ++bid) {
            three_blooms.emplace_back(bid, bid2key[bid]);
        }
    
        
        two_blooms.assign(bid2key.size(), {});
        for (int tid = 0; tid < (int)tmpid2key.size(); ++tid) {
            int bid = tid2bid[tid];
            if (bid < 0) continue;
    
            TwoBloom TB(bid, bid2key[bid]);
            const auto &list = twopaths_by_key[tid];
            TB.total_paths = (uint64_t)list.size();
    
            for (const auto &pr : list) {
                const int e1 = pr.first, e2 = pr.second;
    
                edges[e1].host2Bloom.push_back(bid);
                edges[e1].reverseIndexInHost2Bloom.emplace_back(-1, -1);
                edges[e1].twinEdge.push_back(e2);
    
                edges[e2].host2Bloom.push_back(bid);
                edges[e2].reverseIndexInHost2Bloom.emplace_back(-1, -1);
                edges[e2].twinEdge.push_back(e1);
    
                const int index1 = (int)edges[e1].host2Bloom.size() - 1;
                const int index2 = (int)edges[e2].host2Bloom.size() - 1;
                edges[e1].hostBloomIndexInTwin.push_back(index2);
                edges[e2].hostBloomIndexInTwin.push_back(index1);
            }
    
            two_blooms[bid] = std::move(TB);
        }
    
        
        twopaths_by_key.clear();
        twopaths_by_key.shrink_to_fit();
        key2tmpid.clear();
        tmpid2key.clear();
        twopath_edge_membership.clear();
        twopath_edge_membership.shrink_to_fit();
    
        
        static std::vector<int> eid2idx; 
        if ((int)eid2idx.size() != (int)edges.size()) eid2idx.assign(edges.size(), -1);
    
        std::vector<uint32_t> local_deg;   
        std::vector<uint32_t> cursor;      
    
        for (int tid = 0; tid < (int)threepaths_by_tid.size(); ++tid) {
            int bid = (tid < (int)tid2bid.size() ? tid2bid[tid] : -1);
            if (bid < 0) continue;
    
            auto &pathsE = threepaths_by_tid[tid];
            const uint64_t seg_len64 = (uint64_t)pathsE.size();
            if (seg_len64 == 0) continue;
    
            if (seg_len64 > (uint64_t)std::numeric_limits<uint32_t>::max()) {
                std::cerr << "[FATAL] One key segment has seg_len > 2^32, cannot build in-memory CSR.\n";
                std::exit(1);
            }
            const uint32_t seg_len = (uint32_t)seg_len64;
    
            ThreeBloom &tb = three_blooms[bid];
            //tb.key = bid2key[bid];
            //tb.id  = bid;
    
            
            tb.local2eid.clear();
            local_deg.clear();
    
            reserve_capped(tb.local2eid, (size_t)seg_len * 3);
            reserve_capped(local_deg,    (size_t)seg_len * 3);
    
            auto touch = [&](int eid) -> uint32_t {
                int &ref = eid2idx[eid];
                if (ref != -1) return (uint32_t)ref;
                uint32_t idx = (uint32_t)tb.local2eid.size();
                ref = (int)idx;
                tb.local2eid.push_back(eid);
                local_deg.push_back(0);
                return idx;
            };
    
            
            tb.paths.clear();
            reserve_capped(tb.paths, seg_len);
    
            for (size_t t = 0; t < pathsE.size(); ++t) {
                const auto &r = pathsE[t];
    
                const uint32_t la = touch((int)r.e_ab);
                const uint32_t lb = touch((int)r.e_bc);
                const uint32_t lc = touch((int)r.e_cd);
    
                ++local_deg[la];
                ++local_deg[lb];
                ++local_deg[lc];
    
                tb.paths.push_back(ThreeBloom::Path3{la, lb, lc});
            }
    
            tb.total_paths = (uint64_t)seg_len;
            const uint32_t K = (uint32_t)tb.local2eid.size();
    
            // local->hostidx
            tb.local2hostidx.clear();
            tb.local2hostidx.resize(K);
    
            
            for (uint32_t lid = 0; lid < K; ++lid) {
                const int eid = tb.local2eid[lid];
    
                edges[eid].host3Bloom.push_back(bid);
                edges[eid].host3Cnt.push_back((int)local_deg[lid]);
    
                const uint32_t host_idx = (uint32_t)(edges[eid].host3Bloom.size() - 1);
                edges[eid].local3idx.push_back(lid);
    
                tb.local2hostidx[lid] = host_idx;
            }
    
            // CSR off/inc
            tb.off.assign((size_t)K + 1, 0u);
            for (uint32_t e = 0; e < K; ++e) {
                tb.off[(size_t)e + 1] = tb.off[(size_t)e] + local_deg[e];
            }
    
            const size_t inc_len = (size_t)tb.off[(size_t)K];
            tb.inc.assign(inc_len, 0u);
    
            cursor.assign(K, 0u);
            for (uint32_t e = 0; e < K; ++e) cursor[e] = tb.off[e];
    
            for (uint32_t pid = 0; pid < seg_len; ++pid) {
                const auto &p = tb.paths[pid];
                tb.inc[(size_t)cursor[p.a]++] = pid;
                tb.inc[(size_t)cursor[p.b]++] = pid;
                tb.inc[(size_t)cursor[p.c]++] = pid;
            }
    
            // alive bitset
            tb.alive.assign(((uint64_t)seg_len + 63ull) >> 6, ~0ull);
            if ((seg_len & 63u) != 0u) {
                const uint32_t r = (seg_len & 63u);
                tb.alive.back() = (r == 64u) ? ~0ull : ((1ull << r) - 1ull);
            }
    
            // reset eid2idx
            for (int eid : tb.local2eid) eid2idx[eid] = -1;
    
            
            pathsE.clear();
            pathsE.shrink_to_fit();
        }
    
        
        threepaths_by_tid.clear();
        threepaths_by_tid.shrink_to_fit();
    
        std::cout << std::fixed << std::setprecision(6)
                  << " Bloom construction time:\t"
                  << (get_current_time() - start) << "sec\n";
    
        std::size_t rss_bytes = getCurrentRSSBytes();
        double rss_gib = rss_bytes / (1024.0 * 1024.0 * 1024.0);
        double rss_gb  = rss_bytes / 1e9;
        cerr << "[MEM] After index build: "
             << fixed << setprecision(3)
             << rss_gib << " GiB  (~" << rss_gb << " GB)\n";
    
        
        build_samekey_cross_index(edges, (int)bid2key.size());
    }
    
    
    
        
    void build_samekey_cross_index(vector<Edge>& edges, int total_blooms) {
        
        static vector<int> idx3_by_bid;       
        static vector<int> idx2_by_bid;       // for host2Bloom
        static vector<uint32_t> mark3;        
        static vector<uint32_t> mark2;        
        static uint32_t epoch3 = 1, epoch2 = 1;    

       
        if ((int)idx3_by_bid.size() < total_blooms) {
            idx3_by_bid.resize(total_blooms, -1);
            mark3.resize(total_blooms, 0);
        }
        if ((int)idx2_by_bid.size() < total_blooms) {
            idx2_by_bid.resize(total_blooms, -1);
            mark2.resize(total_blooms, 0);
        }

        for (auto &E : edges) {
            const int n2 = (int)E.host2Bloom.size();
            const int n3 = (int)E.host3Bloom.size();


            E.samekey2to3check.assign(n2, false);
            E.samekey2to3index.assign(n2, -1);
            E.samekey3to2check.assign(n3, false);
            E.samekey3to2index.assign(n3, -1);

            
            ++epoch3; if (epoch3 == 0) { std::fill(mark3.begin(), mark3.end(), 0); epoch3 = 1; }
            for (int j = 0; j < n3; ++j) {
                const int bid = E.host3Bloom[j];
                mark3[bid] = epoch3;
                idx3_by_bid[bid] = j;               
            }

           
            for (int i = 0; i < n2; ++i) {
                const int bid = E.host2Bloom[i];
                if (mark3[bid] == epoch3) {         
                    const int j = idx3_by_bid[bid];
                    E.samekey2to3check[i] = true;
                    E.samekey2to3index[i] = j;
                    
                    E.samekey3to2check[j] = true;
                    E.samekey3to2index[j] = i;
                }
            }

            
        }
    }

   
    void free_before_dt(bool keep_id_mapping = true, bool keep_edge_query = false) {
        
        adj.clear(); adj.shrink_to_fit();
        out.clear(); out.shrink_to_fit();

       
        for (auto &mp : nbr2eid) {
            FlatHashMap<int,int>().swap(mp);   
        }
        nbr2eid.clear();
        nbr2eid.shrink_to_fit();

        // ---------- 2) core/orientation ----------
        core.clear(); core.shrink_to_fit();
        peel_rank.clear(); peel_rank.shrink_to_fit();

       
        FlatHashMap<uint64_t,int>().swap(key2bid);
        bid2key.clear(); bid2key.shrink_to_fit();

        
        tmp_edges.clear(); tmp_edges.shrink_to_fit();
        local_cnt.clear(); local_cnt.shrink_to_fit();
        FlatHashMap<int,int>().swap(local_index);

        
        twobloom_fast_check.clear(); twobloom_fast_check.shrink_to_fit();
        twobloom_drive.clear(); twobloom_drive.shrink_to_fit();
        undirected_edges.clear(); undirected_edges.shrink_to_fit();

        
        if (!keep_id_mapping) {
            FlatHashMap<int,int>().swap(id2idx);
            idx2id.clear(); idx2id.shrink_to_fit();
        } else {
            FlatHashMap<int,int>().swap(id2idx); 
        }

        if (!keep_edge_query) {
            FlatHashMap<uint64_t,int>().swap(edgeId); 
        }
    }



    



    /*====================== Five-cycle support ======================*/
    void compute_five_cycle_supports() {

        for(int i = 0;i < three_blooms.size();i++){
            three_blooms[i].ensure_buckets_by_paths(two_blooms[i].total_paths);
        }
        for(int i = 0;i < two_blooms.size();i++){
            two_blooms[i].ensure_buckets_by_paths(three_blooms[i].total_paths);
        }

        edge_5cycle_support.assign(edges.size(), 0);

        const int Bn = (int)bid2key.size();
        if (Bn == 0) return;

        
        {
            double t0 = get_current_time();
            for (size_t eid = 0; eid < edges.size(); ++eid) {
                uint64_t acc = 0;
                for (int bid : edges[eid].host2Bloom) {
                    acc += three_blooms[bid].total_paths;
                }
                edge_5cycle_support[eid] += acc;
                edges[eid].cnt2.assign(edges[eid].host2Bloom.size(),0);
            }
            std::cout << std::fixed << std::setprecision(6)
                      << " 2-path support computing time:\t" << (get_current_time() - t0)
                      << "sec\n";
        }

        
        {
            double t0 = get_current_time();
            for (size_t eid = 0; eid < edges.size(); ++eid) {
                uint64_t acc = 0;
                const auto &bids3 = edges[eid].host3Bloom;
                const auto &cnt3  = edges[eid].host3Cnt;
                const size_t L = bids3.size();
                for (size_t k = 0; k < L; ++k) {
                    const int bid = bids3[k];
                    const int cnt = cnt3[k];
                    const uint64_t t2 = two_blooms[bid].total_paths;
                    acc += (uint64_t)cnt * t2;
                }
                edge_5cycle_support[eid] += acc;
                //sum2 += edge_5cycle_support[eid];
                edges[eid].cnt3.assign(L,0);
                edges[eid].reverseIndexInHost3Bloom.assign(L,{-1,-1});
            }
            //cout <<"sum2:"<<sum2<<endl;
            std::cout << std::fixed << std::setprecision(6)
                      << " 3-path support computing time:\t" << (get_current_time() - t0)
                      << "sec\n";
        }

       
        uint64_t sum = 0;
        //int maxfive =  0;
        for (size_t eid = 0; eid < edges.size(); ++eid) {
            if(edge_dedup[eid] >= edge_5cycle_support[eid]) {
                edges[eid].five_cycle_support = 0;
                continue;
            }
            edges[eid].five_cycle_support = edge_5cycle_support[eid] - edge_dedup[eid];
            
            edge_5cycle_support[eid] = edges[eid].five_cycle_support;
            
            if (maxsupport < edge_5cycle_support[eid]) {
                maxsupport = edge_5cycle_support[eid];
            }
                
            sum +=  edge_5cycle_support[eid];
            
            /*
            if(edge_5cycle_support[eid] > maxfive){
                maxfive = edge_5cycle_support[eid];
            }
                */
        }
        cout<<"sum:"<<sum/5<<endl;
        std::cout<<"max sum:"<< maxsupport<<std::endl;
        edge_5cycle_support.clear();
        edge_5cycle_support.shrink_to_fit();
        edge_dedup.clear();
        edge_dedup.shrink_to_fit();
        //cout << " max five cycle support:"<<maxfive<<endl;
        /*
        for(int i = 0;i < 30;i++){
            cout<<"edgeid:"<<i<<"     "<<edges[i].five_cycle_support<<endl;
        }
            */
    }

    /*============================================*/
    void build_distributed_tracking() {
        
    

        
        for (auto &tb : two_blooms) tb.clear_buckets();
        for (auto &b3 : three_blooms) b3.clear_buckets();

        extraBloom.maxSupport = maxsupport;
        extraBloom.ensure_buckets_by_paths();
        //cout<<"zeroid:";
        for (size_t eid = 0; eid < edges.size(); ++eid) {
            Edge &E = edges[eid];
            
            
            if(E.five_cycle_support == 0){
                //cout<<eid<<" ";
                zeronode++;
                E.isPeel = true;
                continue;

            }
                
            E.hostbloomnumber = E.host2Bloom.size() + E.host3Bloom.size() + 1;
            //uint32_t old_bucket = log2_32( (ui)E.get_slack_value());
            E.compute_slack_value();
            
            if(!E.isDT){
                for (size_t k = 0; k < E.host2Bloom.size(); ++k) {
                    int bid    = E.host2Bloom[k];
                    
                    pair_t pos = two_blooms[bid].add_member_edge((ui)eid, k, edges);
                    E.reverseIndexInHost2Bloom[k] = pos;
                }
            }
            else{
                uint32_t new_bucket = log2_32( (ui)E.get_slack_value());
                for (size_t k = 0; k < E.host2Bloom.size(); ++k) {
                    int bid    = E.host2Bloom[k];
                    
                    pair_t pos = two_blooms[bid].add_member_edge2(new_bucket,(ui)eid, k, edges);
                    E.reverseIndexInHost2Bloom[k] = pos;
                }
            }

            
            
            

            if(!E.isDT){
                for (size_t k = 0; k < E.host3Bloom.size(); ++k) {
                    int bid    = E.host3Bloom[k];
                    //ui mindex  = tmp_edge_host3MemberIndex[eid][k];   
                    pair_t pos = three_blooms[bid].add_member_edge((ui)eid, k, edges);
                    E.reverseIndexInHost3Bloom[k] = pos;
                }
            }
            else{
                
                for (size_t k = 0; k < E.host3Bloom.size(); ++k) {
                    uint32_t ratio = (ui)E.get_slack_value()/E.host3Cnt[k];
                    if(ratio == 0) ratio = 1;
                    uint32_t new_bucket = log2_32( ratio);
                    int bid    = E.host3Bloom[k];
                    
                    pair_t pos = three_blooms[bid].add_member_edge2(new_bucket,(ui)eid, k, edges);
                    E.reverseIndexInHost3Bloom[k] = pos;
                }
            }
            
            pair_t index = extraBloom.add_member_edge(eid,edges);
            E.set_reverse_index_in_extra_bloom(index);
            
        }
       //cout<<endl;
        cout<<zeronode<<endl;
        
        
        //tmp_edge_host2MemberIndex.clear(); tmp_edge_host2MemberIndex.shrink_to_fit();
        //tmp_edge_host3MemberIndex.clear(); tmp_edge_host3MemberIndex.shrink_to_fit();
    }

    int collect_counter(ui edgeID) {
        uint64_t counter = 0;
        for (ui i = 0; i < edges[edgeID].host2Bloom.size(); i++) {
            int BiD = edges[edgeID].host2Bloom[i];
            if(BiD == -1) continue;
            counter += (two_blooms[BiD].counter-edges[edgeID].cnt2[i]);
        }
        for (ui i = 0; i < edges[edgeID].host3Bloom.size(); i++) {
            int BiD = edges[edgeID].host3Bloom[i];
            if(BiD == -1) continue;
            counter += (edges[edgeID].host3Cnt[i])*(three_blooms[BiD].counter-edges[edgeID].cnt3[i]);
        }

        //if(edgeID == 19559)cout<<"a9:"<<counter<<endl;
        counter -= edges[edgeID].balance;
        //if(edgeID == 19559)cout<<"decrease a9:"<< edges[edgeID].balance<<endl;
        
        edges[edgeID].balance = 0;
        return counter;
    }

    
    void check_mature_edge(ui edgeID, vector<ui> &peelList) {
        Edge &E = edges[edgeID];
        if (E.isPeel) return;
    
        
        if (!E.isDT) {
            E.isPeel = true;
            peelList.push_back(edgeID);
            return;
        }
    
        
        const uint64_t counterSum = collect_counter(edgeID);
        const uint64_t requireSupport = (uint64_t)E.five_cycle_support;
        const uint64_t extraCounter = (uint64_t)(extraBloom.counter - E.extraBloom_cnt);
        const uint64_t lhs = counterSum + extraCounter + (uint64_t)E.delta;
    
        if (lhs >= requireSupport) {
            E.isPeel = true;
            peelList.push_back(edgeID);
            return;
        }
    
       
        const uint64_t trackValue64 = requireSupport - lhs;
        E.five_cycle_support = trackValue64;
        E.extraBloom_cnt = extraBloom.counter;
    
        
        const ui old_slack = (ui)E.get_slack_value();
        const uint32_t old_bucket2 = log2_32(old_slack);
    
       
        E.compute_slack_value();
    
        
        if (E.isDT) {
            const ui new_slack = (ui)E.get_slack_value();
            const uint32_t new_bucket2 = log2_32(new_slack);
    
            if (old_bucket2 != new_bucket2) {
                const ui h2sz = (ui)E.host2Bloom.size();
                for (ui i = 0; i < h2sz; ++i) {
                    const int twobloomID = E.get_host_2bloom_id_by_index(i);
                    if (twobloomID == -1) continue;
    
                    const pair_t rev = E.get_reverse_index_in_host_2bloom_by_index(i);
                    remove_edge_from_2bloom_by_index(twobloomID, rev);
    
                    const pair_t idx = two_blooms[twobloomID].add_member_edge2(new_bucket2, edgeID, i, edges);
                    E.set_reverse_2bloom_index_by_index(i, idx);
                    E.cnt2[i] = two_blooms[twobloomID].counter;
                }
            } else {
                const ui h2sz = (ui)E.host2Bloom.size();
                for (ui i = 0; i < h2sz; ++i) {
                    const int twobloomID = E.get_host_2bloom_id_by_index(i);
                    if (twobloomID == -1) continue;
                    E.cnt2[i] = two_blooms[twobloomID].counter;
                }
            }
        } else {
            
            const ui h2sz = (ui)E.host2Bloom.size();
            for (ui i = 0; i < h2sz; ++i) {
                const int twobloomID = E.get_host_2bloom_id_by_index(i);
                if (twobloomID == -1) continue;
    
                const pair_t rev = E.get_reverse_index_in_host_2bloom_by_index(i);
                remove_edge_from_2bloom_by_index(twobloomID, rev);
    
                const pair_t idx = two_blooms[twobloomID].add_member_edge(edgeID, i, edges);
                E.set_reverse_2bloom_index_by_index(i, idx);
            }
        }
    
        // ----- update 3-bloom -----
        const ui h3sz = (ui)E.host3Bloom.size();
        if (!E.isDT) {
            for (ui i = 0; i < h3sz; ++i) {
                const int threebloomID = E.get_host_3bloom_id_by_index(i);
                if (threebloomID == -1) continue;
    
                const pair_t rev = E.get_reverse_index_in_host_3bloom_by_index(i);
                remove_edge_from_3bloom_by_index(threebloomID, rev);
    
                const pair_t idx = three_blooms[threebloomID].add_member_edge(edgeID, i, edges);
                E.set_reverse_3bloom_index_by_index(i, idx);
            }
        } else {
           
            for (ui i = 0; i < h3sz; ++i) {
                const int threebloomID = E.get_host_3bloom_id_by_index(i);
                if (threebloomID == -1) continue;
    
                const uint32_t old_bucket3 = E.reverseIndexInHost3Bloom[i].first;
    
                
                const int hostCnt = (int)E.host3Cnt[i];
                int ratio = (hostCnt > 0) ? (E.slackValue / hostCnt) : E.slackValue;
                if (ratio <= 0) ratio = 1;
    
                const uint32_t new_bucket3 = log2_32((ui)ratio);
    
                if (old_bucket3 != new_bucket3) {
                    const pair_t rev = E.get_reverse_index_in_host_3bloom_by_index(i);
                    remove_edge_from_3bloom_by_index(threebloomID, rev);
    
                    const pair_t idx = three_blooms[threebloomID].add_member_edge2(new_bucket3, edgeID, i, edges);
                    E.set_reverse_3bloom_index_by_index(i, idx);
                }
                E.cnt3[i] = three_blooms[threebloomID].counter;
            }
        }
    
        // ----- update extra bloom -----
        E.delta = 0;
        const pair_t revExtra = E.get_reverse_index_in_extra_bloom();
        remove_edge_from_extra_bloom_by_index(revExtra);
    
        const pair_t idxExtra = extraBloom.add_member_edge(edgeID, edges);
        E.set_reverse_index_in_extra_bloom(idxExtra);
    }
    
    std::size_t getCurrentRSSBytes() {
        long rss_pages = 0;
        FILE* fp = std::fopen("/proc/self/statm", "r");
        if (!fp) return 0;
        // statm: size resident share text lib data dt
        if (std::fscanf(fp, "%*s %ld", &rss_pages) != 1) {
            std::fclose(fp);
            return 0;
        }
        std::fclose(fp);
        long page_size = sysconf(_SC_PAGESIZE); 
        return (std::size_t)rss_pages * (std::size_t)page_size;
    }
    
    void five_cycle_decomposition() {
        
       
        std::vector<ui> peelList;
        std::vector<ui> peelListTmp;
        vector<ui> matureList;
        double start = get_current_time();
        std::cout << "bitruss decomposing..." << std::endl;
        
        edgeToPeel = edges.size()-zeronode;
        checknumber += zeronode;
        five_cycle_decomposition_number.resize(edges.size(),0);
        
        if (touched_epoch.size() != edges.size()) touched_epoch.assign(edges.size(), 0);
        if (checked_epoch.size() != edges.size()) checked_epoch.assign(edges.size(), 0);

        
        checkoutarray_buf.clear();
        mature_buf.clear();
        checkoutarray_buf.reserve(1 << 16);
        mature_buf.reserve(1 << 16);

       

        while (visitedEdge < edgeToPeel) {
            
            
            if (peelList.empty()) {
                extraBloom.send_value_to_member( matureList, peelList,edges);
                //extraBloom.increse_counter(1);
                for (ui i = 0; i < matureList.size(); i++) {
                    
                    ui edgeID = matureList[i];
                    //if(visitedEdge > 15670) cout << edgeID<<endl;
                    check_mature_edge(edgeID, peelList);
                }
                matureList.clear();
                //cout<<peelList.size()<<endl;
                //cout<<peelList.size()<<endl;
            } else {
                visitedEdge += peelList.size();
                peel_edge(peelList, peelListTmp);
            //visitedEdge += (int)peelList.size();
                peelList.swap(peelListTmp);
                peelListTmp.clear();
                
                
            }
        }
        //std::sort(five_cycle_decomposition_number.begin(), five_cycle_decomposition_number.end());

        std::cout << std::fixed << std::setprecision(6)
                  << "Bitruss decomposition time:\t" << get_current_time() - start
                  << "sec\n";
                  /*
                  std::ofstream fout;
                  fout.open("/Users/fengnianlin/Desktop/cycle decomposition/5cycle/result/dblp.txt", std::ios::out);
                  
                  for (ui i = 0; i < edges.size(); i++) {
                      fout << i << "\t" << five_cycle_decomposition_number[i] << std::endl;
                  }
                      
                  for(int edgeid : edgeid_result ){
                    fout <<edgeid<<endl;
                  }
                  fout.close();
                  */
                  
    }
                  
    

    void peel_edge(vector<ui>& edgeList, vector<ui>& peelList) {
        twobloom_drive.clear();
    
        // ====== epoch_touch：取代 fill(batch_edges...) 的 O(m) 清空 ======
        if (touched_epoch.size() != edges.size()) touched_epoch.assign(edges.size(), 0);
        ++epoch_touch;
        if (epoch_touch == 0) { // wrap
            std::fill(touched_epoch.begin(), touched_epoch.end(), 0);
            epoch_touch = 1;
        }
    
        
        checkoutarray_buf.clear();
    
        for (ui edgeID : edgeList) {
           
            Edge &peelEdge = edges[edgeID];
            /*
            checknumber++;
            
            if(checknumber % 10000 == 0){
                cout<<"checknumber:"<<checknumber<<" extracounter:"<<extraBloom.counter<<endl;
            }
            if(checknumber >6645000){
                cout<<"checknumber:"<<checknumber<<endl;
            }
            */
            five_cycle_decomposition_number[edgeID] = extraBloom.counter;
    
            // ---- remove from extra bloom ----
            pair_t index = peelEdge.get_reverse_index_in_extra_bloom();
            remove_edge_from_extra_bloom_by_index(index);
    
            // --------------------------------------------
            // Part 1) peelEdge in 2-blooms
            // --------------------------------------------
            for (ui i = 0; i < peelEdge.host2Bloom.size(); i++) {
                int twoBloomID = peelEdge.get_host_2bloom_id_by_index(i);
                if (twoBloomID == -1) continue;
    
                TwinInfo twinEdgeInfo = peelEdge.get_twin_edge_info_by_index(i);
                pair_t reverseIndex   = peelEdge.get_reverse_index_in_host_2bloom_by_index(i);
    
                TwoBloom   &current2Bloom = two_blooms[twoBloomID];
                ThreeBloom &current3Bloom = three_blooms[twoBloomID];
    
                int totalthreepath = (int)current3Bloom.total_paths;
                

                ui twinEdgeID = twinEdgeInfo.twinEdgeID;
                Edge &twinedge = edges[twinEdgeID];
    
                // remove peel edge from 2-bloom
                remove_edge_from_2bloom_by_index(twoBloomID, reverseIndex);
    
                // get twin's host2 index
                ui indexInTwinEdge = twinEdgeInfo.hostBloomIndex;
                pair_t twinedgeindexInHostBloom =
                twinedge.get_reverse_index_in_host_2bloom_by_index(indexInTwinEdge);
    
                // accumulate to twin edge
                if (!twinedge.isDT) {
                    twinedge.accumulate_value(totalthreepath);
                } else {
                    twinedge.accumulate_value(
                        totalthreepath + current2Bloom.counter - twinedge.cnt2[indexInTwinEdge]
                    );
                }
    
                // ---- samekey2->3 : balance bumps via alive incident 3-paths (do_kill=false)
                if (edges[edgeID].samekey2to3check[i]) {
                    int host3idx = edges[edgeID].samekey2to3index[i];
    
                    twinedge.decrease_value(edges[edgeID].host3Cnt[host3idx]);
    
                    const uint32_t lid_peel = edges[edgeID].local3idx[host3idx];
                    bump_balance_incident_alive_paths(current3Bloom, lid_peel, (int)edgeID);
                }
    
                // remove twin edge from 2-bloom and edge-side arrays
                remove_edge_from_2bloom_by_index(twoBloomID, twinedgeindexInHostBloom);
                remove_2bloom_from_edge_by_index(twinEdgeID, indexInTwinEdge);
    
                current2Bloom.total_paths--;
    
                // ---- twinEdge samekey2->3 : balance bumps via alive incident 3-paths (do_kill=false)
                if (twinedge.samekey2to3check[indexInTwinEdge]) {
                    int edgethreeindex = twinedge.samekey2to3index[indexInTwinEdge];
                    int host3Cnt       = twinedge.host3Cnt[edgethreeindex];
    
                    twinedge.decrease_value(host3Cnt);
                    twinedge.balance += host3Cnt;
    
                    const uint32_t lid_twin = twinedge.local3idx[edgethreeindex];
                    bump_balance_incident_alive_paths(current3Bloom, lid_twin, (int)twinEdgeID);
    
                    twinedge.samekey2to3check[indexInTwinEdge] = false;
                    twinedge.samekey3to2check[edgethreeindex]  = false;
                }
    
                // ---- current3Bloom DT sending ----
                mature_buf.clear();
                current3Bloom.send_value_to_member(mature_buf, peelList, edges);
    
               
                bump_epoch_check();
                for (ui tmpedgeID : mature_buf) {
                    check_mature_edge_list_dedup(tmpedgeID, peelList);
                }
    
                if (twinedge.check_maturity()) {
                    check_mature_edge(twinEdgeID, peelList);
                }
            }
    
            // --------------------------------------------
            // Part 2) peelEdge in 3-blooms
            // kill alive 3-paths incident to peelEdge
            // --------------------------------------------
            for (ui i = 0; i < peelEdge.host3Bloom.size(); i++) {
                int threeBloomID = peelEdge.get_host_3bloom_id_by_index(i);
                if (threeBloomID == -1) continue;
    
                ThreeBloom &current3Bloom = three_blooms[threeBloomID];
                TwoBloom   &current2Bloom = two_blooms[threeBloomID];
    
                pair_t reverseIndex = peelEdge.get_reverse_index_in_host_3bloom_by_index(i);
                remove_edge_from_3bloom_by_index(threeBloomID, reverseIndex);
    
                int currentcnt3 = peelEdge.host3Cnt[i];
                const uint32_t lid_peel = peelEdge.local3idx[i];
    
                kill_incident_alive_paths_and_update(
                    current3Bloom,
                    current2Bloom,
                    (int)edgeID,
                    threeBloomID,
                    lid_peel,
                    checkoutarray_buf
                );
    
                current3Bloom.total_paths -= currentcnt3;
    
                mature_buf.clear();
                current2Bloom.send_value_to_member(currentcnt3, mature_buf, peelList, edges);
    
                bump_epoch_check();
                for (ui tmpedgeID : mature_buf) {
                    check_mature_edge_list_dedup(tmpedgeID, peelList);
                }
            }
        }
    
       
        bump_epoch_check(); 
        for (int tempEdgeID : checkoutarray_buf) {
            if (tempEdgeID < 0) continue;
            ui eid = (ui)tempEdgeID;
            if (edges[eid].check_maturity()) {
                check_mature_edge_list_dedup(eid, peelList);
            }
        }
    }
    
    

   
    void bump_balance_incident_alive_paths(ThreeBloom &B, uint32_t lid, int excludeE) {
        if (B.off.empty()) return;
        if (lid + 1 >= (uint32_t)B.off.size()) return;

        const size_t L = (size_t)B.off[lid];
        const size_t R = (size_t)B.off[lid + 1];

        for (size_t pos = L; pos < R; ++pos) {
            const uint32_t pid = B.inc[pos];
            const uint32_t w   = (pid >> 6);
            const uint32_t bit = (pid & 63);
            const uint64_t mask = (1ull << bit);
            if ((B.alive[w] & mask) == 0) continue;

            const ThreeBloom::Path3 &p = B.paths[pid];
            const int e1 = B.local2eid[p.a];
            const int e2 = B.local2eid[p.b];
            const int e3 = B.local2eid[p.c];

            if (e1 != excludeE) edges[e1].balance++;
            if (e2 != excludeE) edges[e2].balance++;
            if (e3 != excludeE) edges[e3].balance++;
        }
    }

   
    void kill_incident_alive_paths_and_update(
        ThreeBloom &B,
        TwoBloom   &TB,
        int globalPeelEdgeID,
        int threeBloomID,
        uint32_t lid_peel,
        std::vector<int> &checkoutarray
    ) {
        if (B.off.empty()) return;
        if (lid_peel + 1 >= (uint32_t)B.off.size()) return;

        const size_t L = (size_t)B.off[lid_peel];
        const size_t R = (size_t)B.off[lid_peel + 1];

        for (size_t pos = L; pos < R; ++pos) {
            const uint32_t pid = B.inc[pos];
            const uint32_t w   = (pid >> 6);
            const uint32_t bit = (pid & 63);
            const uint64_t mask = (1ull << bit);

            if ((B.alive[w] & mask) == 0) continue;

            // kill
            B.alive[w] &= ~mask;

            const ThreeBloom::Path3 &p = B.paths[pid];

            // mimic your pair_a / pair_b / pair_c blocks
            process_one_local_edge_after_kill(B, TB, globalPeelEdgeID, threeBloomID,
                                            p.a, p.b, p.c, checkoutarray);
            process_one_local_edge_after_kill(B, TB, globalPeelEdgeID, threeBloomID,
                                            p.b, p.a, p.c, checkoutarray);
            process_one_local_edge_after_kill(B, TB, globalPeelEdgeID, threeBloomID,
                                            p.c, p.a, p.b, checkoutarray);
        }
    }

   
    void process_one_local_edge_after_kill(
        ThreeBloom &B,
        TwoBloom   &TB,
        int globalPeelEdgeID,
        int threeBloomID,
        uint32_t lcur,
        uint32_t lX,
        uint32_t lY,
        std::vector<int> &checkoutarray
    ) {
        const int tempedgeid = B.local2eid[lcur];
        if (tempedgeid == globalPeelEdgeID) return;
        if (tempedgeid < 0) return;
    
        if ((size_t)tempedgeid < touched_epoch.size() && touched_epoch[tempedgeid] != epoch_touch) {
            touched_epoch[tempedgeid] = epoch_touch;
            checkoutarray.push_back(tempedgeid);
        }
    
        const uint32_t tempthreebloomindex = B.local2hostidx[lcur];
        const int host3cnt = edges[tempedgeid].host3Cnt[tempthreebloomindex];
    
        if (edges[tempedgeid].isDT == false) {
            edges[tempedgeid].accumulate_value((int)TB.total_paths);
            edges[tempedgeid].host3Cnt[tempthreebloomindex]--;
    
            if (edges[tempedgeid].host3Cnt[tempthreebloomindex] <= 0) {
                pair_t threeEdgeindexInHostBloom =
                    edges[tempedgeid].reverseIndexInHost3Bloom[tempthreebloomindex];
                remove_edge_from_3bloom_by_index(threeBloomID, threeEdgeindexInHostBloom);
                remove_3bloom_from_edge_by_index((ui)tempedgeid, tempthreebloomindex);
            }
        } else {
            edges[tempedgeid].accumulate_value(
                (int)TB.total_paths +
                (int64_t)host3cnt * (int64_t)(B.counter - edges[tempedgeid].cnt3[tempthreebloomindex])
            );
            edges[tempedgeid].cnt3[tempthreebloomindex] = B.counter;
    
            edges[tempedgeid].host3Cnt[tempthreebloomindex]--;
            if (edges[tempedgeid].host3Cnt[tempthreebloomindex] <= 0) {
                pair_t threeEdgeindexInHostBloom =
                    edges[tempedgeid].reverseIndexInHost3Bloom[tempthreebloomindex];
                remove_edge_from_3bloom_by_index(threeBloomID, threeEdgeindexInHostBloom);
                remove_3bloom_from_edge_by_index((ui)tempedgeid, tempthreebloomindex);
            }
        }
    
       
        if (edges[tempedgeid].samekey3to2check[tempthreebloomindex] == true) {
            int edgetwoindex = edges[tempedgeid].samekey3to2index[tempthreebloomindex];
    
            edges[tempedgeid].decrease_value(1);
    
            const int eX = B.local2eid[lX];
            const int eY = B.local2eid[lY];
    
            if (eX != globalPeelEdgeID && eX != tempedgeid) edges[eX].decrease_value(1);
            if (eY != globalPeelEdgeID && eY != tempedgeid) edges[eY].decrease_value(1);
    
            edges[tempedgeid].balance += 1;
    
            if (edges[tempedgeid].host3Bloom[tempthreebloomindex] == -1) {
                edges[tempedgeid].samekey3to2check[tempthreebloomindex] = false;
                edges[tempedgeid].samekey2to3check[edgetwoindex] = false;
            }
    
            int twinedgeid = edges[tempedgeid].twinEdge[edgetwoindex];
            edges[twinedgeid].balance += 1;
        }
    }
    
    

    
    void remove_edge_from_2bloom_by_index(int bloomID, pair_t index) {
        affect_edge_t affectEdgeInfo = two_blooms[bloomID].remove_member_by_index(index);
        if (affectEdgeInfo.first == -1) {
            return;
        } else {
            ui affectEdgeID = affectEdgeInfo.first;
            ui affectIndex = affectEdgeInfo.second;
            edges[affectEdgeID].set_reverse_2bloom_index_by_index(affectIndex, index);
        }
    }

    void remove_edge_from_3bloom_by_index(int bloomID, pair_t index) {
        affect_edge_t affectEdgeInfo = three_blooms[bloomID].remove_member_by_index(index);
        if (affectEdgeInfo.first == -1) {
            return;
        } else {
            ui affectEdgeID = affectEdgeInfo.first;
            ui affectIndex = affectEdgeInfo.second;
            edges[affectEdgeID].set_reverse_3bloom_index_by_index(affectIndex, index);
        }
    }

    void remove_edge_from_extra_bloom_by_index(pair_t index) {
        ui affectEdgeID = extraBloom.remove_member_by_index_id_only(index);
        if (affectEdgeID == -1) {
            return;
        } else {
            edges[affectEdgeID].set_reverse_index_in_extra_bloom(index);
        }
    }

    void remove_2bloom_from_edge_by_index(ui edgeID, ui index) {
        edges[edgeID].remove_host_2bloom_by_index(index);
        edges[edgeID].hostbloomnumber--;
    }
    
    void remove_3bloom_from_edge_by_index(ui edgeID, ui index) {
        edges[edgeID].remove_host_3bloom_by_index(index);
        edges[edgeID].hostbloomnumber--;
    }
    


    
    void build_blooms_strict_pipeline_with_DT() {
        double start = get_current_time();

        collect_2paths_grouped();
        std::cout << std::fixed << std::setprecision(6)
                  << " finish 2-path construction time:\t" << (get_current_time() - start)
                  << "sec\n";

        build_twopath_edge_membership();
        std::cout << std::fixed << std::setprecision(6)
                  << " finish 2-path membership time:\t" << (get_current_time() - start)
                  << "sec\n";

        collect_3paths_for_existing_keys();
        std::cout << std::fixed << std::setprecision(6)
                  << " finish 3-path collection time:\t" << (get_current_time() - start)
                  << "sec\n";
                  twopath_edge_membership.clear();
                  twopath_edge_membership.shrink_to_fit();

        build_blooms_strict_matched();
        std::cout << std::fixed << std::setprecision(6)
                  << " finish Bloom construction time:\t" << (get_current_time() - start)
                  << "sec\n";

        compute_five_cycle_supports();
        std::cout << std::fixed << std::setprecision(6)
                  << " finish 5-cycle support time:\t" << (get_current_time() - start)
                  << "sec\n";
        free_before_dt(/*keep_id_mapping=*/true, /*keep_edge_query=*/false);
        build_distributed_tracking();
        std::cout << std::fixed << std::setprecision(6)
                  << " finish distributed tracking time:\t" << (get_current_time() - start)
                  << "sec\n";
        std::size_t rss_bytes = getCurrentRSSBytes();
            double rss_gib = rss_bytes / (1024.0 * 1024.0 * 1024.0);  // GiB
            double rss_gb  = rss_bytes / 1e9;                        
                  
            cerr << "[MEM] After index build: "
                       << fixed << setprecision(3)
                       << rss_gib << " GiB  (~" << rss_gb << " GB)"
                       << "\n";
        
        five_cycle_decomposition();
        std::cout << std::fixed << std::setprecision(6)
                  << " finish cycle decomposition time:\t" << (get_current_time() - start)
                  << "sec\n";
                  /*
                  std::ofstream fout;
                  fout.open("/home/fengnianl/decomposition/five-cycle/5cycle/result/email-Eu-core.txt", std::ios::out);
                            
                  for (ui i = 0; i < edges.size(); i++) {
                      fout << i << "\t" << five_cycle_decomposition_number[i] << std::endl;
                  }
                  
                                
                  for(int edgeid : edgeid_result ){
                      fout <<edgeid<<endl;
                  }
                      
                  fout.close();
                  */
        
    }

    inline int get_edge_id(int u, int v) const {
        uint64_t k = pack_edge((uint32_t)u, (uint32_t)v);
        auto it = edgeId.find(k);
        return (it==edgeId.end()) ? -1 : it->second;
    }

    inline int get_edge_id_fast(int u, int v) const {
        const auto &mp = nbr2eid[u];
        auto it = mp.find(v);
        return (it == mp.end()) ? -1 : it->second;
    }

    inline int bloom_count_2() const { return (int)two_blooms.size(); }
    inline int bloom_count_3() const { return (int)three_blooms.size(); }

    inline const ThreeBloom&     get_bloom(int bid)   const { return three_blooms[bid]; }
    inline const TwoBloom&  get_tbloom(int bid)  const { return two_blooms[bid]; }
    inline uint64_t bloom_key (int bid) const { return bid2key[bid]; }

    inline int try_get_bid(int x, int y) const {
        uint64_t key = pack_edge((uint32_t)x, (uint32_t)y);
        auto it = key2bid.find(key);
        return (it == key2bid.end()) ? -1 : it->second;
    }

    inline uint64_t edge_5cycle(int eid) const {
        return (eid < 0 || (size_t)eid >= edge_5cycle_support.size()) ? 0ull
                                                                      : edge_5cycle_support[eid];
    }



public:
   
    int n{0};
    ui visitedEdge {0};
    int zeronode{0};
    ExtraBloom extraBloom;
    vector<std::pair<int,int>> edges_raw;
    FlatHashMap<int,int>            id2idx;    
    vector<int>                idx2id;
    uint64_t maxsupport{0};
    vector<Edge>               edges;     
    vector<int> twobloom_fast_check;
    //vector<int> threebloom_fastcheck;
    vector<int> twobloom_drive;
    //vector<int>threebloom_drive;
    int checknumber{0};

    FlatHashMap<uint64_t,int>       edgeId;    
    vector<std::pair<int,int>> undirected_edges;
    //ui five_cycle_decomposition_number;
    vector<vector<int>>   adj;       
    vector<int>                core;
    vector<int>                peel_rank;
    unsigned int edgeToPeel{0};
    vector<int>five_cycle_decomposition_number;
   
std::vector<uint32_t> touched_epoch;   // size = edges.size()
uint32_t epoch_touch{1};             


std::vector<int> checkoutarray_buf;
std::vector<ui>  mature_buf;


std::vector<uint32_t> checked_epoch;  // size = edges.size()
uint32_t epoch_check{1};

inline void bump_epoch_check() {
    ++epoch_check;
    if (epoch_check == 0) { // wrap
        std::fill(checked_epoch.begin(), checked_epoch.end(), 0);
        epoch_check = 1;
    }
}


inline void check_mature_edge_list_dedup(ui edgeID, std::vector<ui> &peelList) {
    if (edges[edgeID].isPeel) return;
    if (checked_epoch[edgeID] == epoch_check) return;
    checked_epoch[edgeID] = epoch_check;
    check_mature_edge(edgeID, peelList);
}


    vector<vector<Next>>  out;       
    vector<FlatHashMap<int,int>> nbr2eid;

   
    FlatHashMap<uint64_t,int>       key2bid;   
    vector<uint64_t>           bid2key;  

    vector<TwoBloom>           two_blooms;       
    vector<ThreeBloom>              three_blooms;     

    vector<uint64_t>           edge_dedup;            
    vector<uint64_t>           edge_5cycle_support;   

private:
    FlatHashMap<uint64_t,int>                   key2tmpid;         // (a,c) -> tmp id
    vector<uint64_t>                       tmpid2key;         // tmp id -> (a,c)
    vector<vector<std::pair<int,int>>> twopaths_by_key;  // tmp id -> [(e_ab,e_bc)...]
    vector<char>                           valid_tmp;         
    vector<FlatHashMap<int,char>>          twopath_edge_membership;

    inline int get_or_create_tmpid(uint64_t key) {
        auto it = key2tmpid.find(key);
        if (it != key2tmpid.end()) return it->second;
        int tid = (int)tmpid2key.size();
        key2tmpid.emplace(key, tid);
        tmpid2key.push_back(key);
        twopaths_by_key.emplace_back();
        return tid;
    }

   
   struct Path3E {
    uint32_t e_ab, e_bc, e_cd;
};
   vector<vector<Path3E>> threepaths_by_tid;
    //vector <PathMeta> path_meta;
    //vector<bool> path_exist;
    static constexpr size_t SCRATCH_CAP_BYTES = (256ull << 20); 

    template <class T>
    static inline void reserve_capped(std::vector<T>& v,
                                    size_t want_elems,
                                    size_t cap_bytes = SCRATCH_CAP_BYTES) {
        size_t cap_elems = cap_bytes / sizeof(T);
        size_t req = std::min(want_elems, cap_elems);
        if (v.capacity() < req) v.reserve(req);
    }


    size_t estimate_3path_upper() const {
        const size_t m = edges.size();
        if (n == 0) return 0;
        double sum_out = 0.0;
        for (const auto& v : out) sum_out += (double)v.size();
        double avg_out = (n ? sum_out / n : 0.0);
        size_t est = (size_t)(m * (avg_out * avg_out));
        return est + (est >> 1) + 1024;
    }
        
    /*
    static void radix_sort_by_key(vector<PathRec>& a) {
        const size_t N = a.size();
        if (N <= 1) return;
        vector<PathRec> tmp(N);

        constexpr int BYTES = 8; // 64-bit
        constexpr int B = 256;
        uint32_t cnt[B];

        for (int pass = 0; pass < BYTES; ++pass) {
            std::fill(std::begin(cnt), std::end(cnt), 0u);
            const int shift = pass * 8;
            for (size_t i = 0; i < N; ++i) {
                uint8_t bucket = (uint8_t)((a[i].key >> shift) & 0xffu);
                ++cnt[bucket];
            }
            uint32_t sum = 0;
            for (int b = 0; b < B; ++b) { uint32_t c = cnt[b]; cnt[b] = sum; sum += c; }
            for (size_t i = 0; i < N; ++i) {
                uint8_t bucket = (uint8_t)((a[i].key >> shift) & 0xffu);
                tmp[cnt[bucket]++] = a[i];
            }
            a.swap(tmp);
        }
    }
        */

    /*------------------------------------------*/
    //std::vector<std::vector<ui>>    tmp_edge_host2MemberIndex;  // [eid] -> [idx in 2-bloom member set]
    //std::vector<std::vector<ui>>    tmp_edge_host3MemberIndex;  // [eid] -> [idx in 3-bloom member set]

    /*------------------------------------------*/
    vector<int>          tmp_edges;
    vector<int>          local_cnt;
    FlatHashMap<int,int>      local_index;
    
};