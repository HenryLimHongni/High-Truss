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
#include <unordered_set>
#include <queue>
#include <limits>
#include <unistd.h>

#include "current_time.h"
#include "edge.h"
#include "3-bloom.h"
#include "2-bloom.h"
#include "dt_utils.h"

using namespace std;

typedef std::tuple<int, pair_t, ui, ui> affect_bloom_t;
typedef std::tuple<int, pair_t, pair_t, pair_t> affect_3bloom_t;


template <class K, class V>
using FlatHashMap = std::unordered_map<K, V>;

class Graph {
public:
    // ====================== IO ======================
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

    // ====================== Dedup ======================
    inline uint64_t pack_edge_u32(uint32_t a, uint32_t b) const {
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

            uint64_t k = pack_edge_u32((uint32_t)u, (uint32_t)v);
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

    // ====================== Core decomposition ======================
    void core_decomposition_bz() {
        adj.assign(n, {});
        for (const auto &e : edges) {
            adj[e.u].push_back(e.v);
            adj[e.v].push_back(e.u);
        }

        std::vector<int> deg(n, 0);
        int maxd = 0;
        for (int i=0;i<n;++i) { deg[i]=(int)adj[i].size(); if (deg[i]>maxd) maxd=deg[i]; }

        std::vector<int> bin(maxd+1, 0);
        for (int d:deg) ++bin[d];
        int start = 0;
        for (int d=0; d<=maxd; ++d) { int cnt = bin[d]; bin[d]=start; start+=cnt; }

        std::vector<int> pos(n), vert(n);
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
        int max_core = 0;
        long long sum_core = 0;
        for (int v = 0; v < n; ++v) {
            if (core[v] > max_core) max_core = core[v];
            sum_core += core[v];
        }
        double avg_core = (n > 0) ? (double)sum_core / (double)n : 0.0;

        std::cout << "max core number: " << max_core << "\n";
        std::cout << "avg core number: " << avg_core << "\n";
    }

    // ====================== Orientation ======================
    struct Next { int to; int eid; };

    void build_oriented_out() {
        out.assign(n, {});
        out.reserve(n);
        for (int i=0;i<n;++i) out[i].reserve(adj[i].size()/2 + 1);

        for (const auto &e : edges) {
            if (peel_rank[e.u] < peel_rank[e.v]) out[e.u].push_back({e.v, e.id});
            else                                 out[e.v].push_back({e.u, e.id});
        }

        nbr2eid.clear();
        nbr2eid.resize(n);
        for (const auto &e : edges) {
            nbr2eid[e.u].emplace(e.v, e.id);
            nbr2eid[e.v].emplace(e.u, e.id);
        }
    }

    // ====================== 2-path group ======================
    void collect_2paths_grouped() {
        key2tmpid.clear(); tmpid2key.clear(); twopaths_by_key.clear();

        tmpid2key.reserve(std::max<size_t>(edges.size() / 8 + 1, 1024ull));
        uint64_t twopathnum = 0;

        for (int b = 0; b < n; ++b) {
            const auto &A = out[b];
            if (A.empty()) continue;
            const auto &C = adj[b];

            for (const auto &an : A) {
                const int a    = an.to;
                const int e_ab = an.eid;

                for (int c : C) {
                    if (c == b || c == a) continue;
                    if (!(peel_rank[a] > peel_rank[c])) continue;

                    const int e_bc = get_edge_id_fast(b, c);
                    if (e_bc < 0) continue;

                    const uint64_t key = pack_edge_u32((uint32_t)a, (uint32_t)c);
                    int tid = get_or_create_tmpid(key);
                    twopaths_by_key[tid].emplace_back(e_ab, e_bc);
                    twopathnum++;
                }
            }
        }
        cout<<"number of 2-path:"<<twopathnum<<endl;
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
                set.emplace(p.first, 1);
                set.emplace(p.second, 1);
            }
        }
    }

    // ====================== 3-path collection (by tid) ======================
    struct ThreePathRaw { int e_ab, e_bc, e_cd; };

    void collect_3paths_for_existing_keys() {
        const int T = (int)tmpid2key.size();
        threepaths_by_tid.clear();
        threepaths_by_tid.resize(T);

        valid_tmp.assign(T, false);
        edge_dedup.assign(edges.size(), 0);
        uint64_t threepathnum = 0;

        const size_t M = edges.size();
        for (size_t eid_bc = 0; eid_bc < M; ++eid_bc) {
            const int b = edges[eid_bc].u;
            const int c = edges[eid_bc].v;

            const auto& out_b = out[b];
            const auto& out_c = out[c];

            for (const auto &an : out_b) {
                const int a = an.to;
                if (a == c) continue;
                const int e_ab = an.eid;
                const int e_bc = (int)eid_bc;

                for (const auto &dn : out_c) {
                    const int d = dn.to;
                    if (d == b || d == a) continue;
                    const int e_cd = dn.eid;

                    const uint64_t key_ad = pack_edge_u32((uint32_t)a, (uint32_t)d);
                    auto it = key2tmpid.find(key_ad);
                    if (it == key2tmpid.end()) continue;
                    const int tid = it->second;

                    threepaths_by_tid[tid].push_back({e_ab, e_bc, e_cd});
                    threepathnum++;
                    valid_tmp[tid] = true;

                    const auto &mem = twopath_edge_membership[tid];

                    const bool hit_ab = (mem.find(e_ab) != mem.end());
                    if (hit_ab) {
                        edge_dedup[e_ab] += 2;
                        edge_dedup[e_bc] += 1;
                        edge_dedup[e_cd] += 1;
                        const int e_bd = get_edge_id_fast(b, d);
                        if (e_bd >= 0 && mem.find(e_bd) != mem.end()) edge_dedup[e_bd] += 1;
                    }

                    const bool hit_cd = (mem.find(e_cd) != mem.end());
                    if (hit_cd) {
                        edge_dedup[e_cd] += 2;
                        edge_dedup[e_ab] += 1;
                        edge_dedup[e_bc] += 1;
                        const int e_ac = get_edge_id_fast(a, c);
                        if (e_ac >= 0 && mem.find(e_ac) != mem.end()) edge_dedup[e_ac] += 1;
                    }
                }
            }
        }
        cout<<"number of 3-path:"<<threepathnum<<endl;
    }

    // ====================== Bloom build (strict matched, CSR+alive) ======================
    template <class Vec>
    static inline void reserve_capped(Vec& v, size_t need) {
        
        const size_t CAP = (size_t)1e9; 
        v.reserve(std::min(need, CAP));
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

            E.samekey2to3check.clear(); E.samekey2to3index.clear();
            E.samekey3to2check.clear(); E.samekey3to2index.clear();
        }

        key2bid.clear();
        bid2key.clear();
        three_blooms.clear();
        two_blooms.clear();

        // 0) tid -> bid（仅 valid_tmp）
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

        
        three_blooms.reserve(bid2key.size());
        for (int bid = 0; bid < (int)bid2key.size(); ++bid) {
            three_blooms.emplace_back(bid, bid2key[bid]);
        }

        
        two_blooms.assign(bid2key.size(), TwoBloom{});

        for (int tid = 0; tid < (int)tid2bid.size(); ++tid) {
            int bid = tid2bid[tid];
            if (bid < 0) continue;

            TwoBloom TB(bid, bid2key[bid]);
            auto &list = twopaths_by_key[tid];
            TB.total_paths = (uint64_t)list.size();

            for (const auto &pr : list) {
                const int e1 = pr.first, e2 = pr.second;

                edges[e1].host2Bloom.push_back(bid);
                edges[e1].reverseIndexInHost2Bloom.push_back(-1);
                edges[e1].twinEdge.push_back(e2);

                edges[e2].host2Bloom.push_back(bid);
                edges[e2].reverseIndexInHost2Bloom.push_back(-1);
                edges[e2].twinEdge.push_back(e1);

                const int index1 = (int)edges[e1].host2Bloom.size() - 1;
                const int index2 = (int)edges[e2].host2Bloom.size() - 1;
                edges[e1].hostBloomIndexInTwin.push_back(index2);
                edges[e2].hostBloomIndexInTwin.push_back(index1);
            }

            two_blooms[bid] = std::move(TB);
            list.clear();
            list.shrink_to_fit();
        }

        twopaths_by_key.clear(); twopaths_by_key.shrink_to_fit();
        key2tmpid.clear();
        tmpid2key.clear();
        twopath_edge_membership.clear(); twopath_edge_membership.shrink_to_fit();

        // 3) build 3-bloom CSR+alive
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
                std::cerr << "[FATAL] seg_len > 2^32, cannot build CSR in-memory.\n";
                std::exit(1);
            }
            const uint32_t seg_len = (uint32_t)seg_len64;

            ThreeBloom &B = three_blooms[bid];
            B.id = bid;
            B.key = bid2key[bid];

            B.local2eid.clear();
            local_deg.clear();
            reserve_capped(B.local2eid, (size_t)seg_len * 3);
            reserve_capped(local_deg,    (size_t)seg_len * 3);

            auto touch = [&](int eid) -> uint32_t {
                int &ref = eid2idx[(size_t)eid];
                if (ref != -1) return (uint32_t)ref;
                uint32_t idx = (uint32_t)B.local2eid.size();
                ref = (int)idx;
                B.local2eid.push_back(eid);
                local_deg.push_back(0);
                return idx;
            };

            B.paths.clear();
            reserve_capped(B.paths, seg_len);

            for (size_t t = 0; t < pathsE.size(); ++t) {
                const auto &r = pathsE[t];
                const uint32_t la = touch(r.e_ab);
                const uint32_t lb = touch(r.e_bc);
                const uint32_t lc = touch(r.e_cd);

                ++local_deg[la];
                ++local_deg[lb];
                ++local_deg[lc];

                B.paths.push_back(ThreeBloom::Path3{la, lb, lc});
            }

            B.total_paths = (uint64_t)seg_len;
            const uint32_t K = (uint32_t)B.local2eid.size();

            B.local2hostidx.assign((size_t)K, 0u);

            
            for (uint32_t lid = 0; lid < K; ++lid) {
                const int eid = B.local2eid[lid];
                edges[(size_t)eid].host3Bloom.push_back(bid);
                edges[(size_t)eid].host3Cnt.push_back((int)local_deg[lid]);

                const uint32_t host_idx = (uint32_t)(edges[(size_t)eid].host3Bloom.size() - 1);
                edges[(size_t)eid].local3idx.push_back(lid);
                B.local2hostidx[lid] = host_idx;
            }

            // CSR off/inc
            B.off.assign((size_t)K + 1, 0u);
            for (uint32_t e = 0; e < K; ++e) B.off[(size_t)e + 1] = B.off[(size_t)e] + local_deg[e];

            const size_t inc_len = (size_t)B.off[(size_t)K];
            B.inc.assign(inc_len, 0u);

            cursor.resize(K);
            for (uint32_t e = 0; e < K; ++e) cursor[e] = B.off[e];

            for (uint32_t pid = 0; pid < seg_len; ++pid) {
                const auto &p = B.paths[pid];
                B.inc[(size_t)cursor[p.a]++] = pid;
                B.inc[(size_t)cursor[p.b]++] = pid;
                B.inc[(size_t)cursor[p.c]++] = pid;
            }

            // alive bitset
            B.alive.assign(((uint64_t)seg_len + 63ull) >> 6, ~0ull);
            const uint32_t rem = (seg_len & 63u);
            if (rem != 0u) B.alive.back() = ((1ull << rem) - 1ull);

            // reset touched
            for (int eid : B.local2eid) eid2idx[(size_t)eid] = -1;

            pathsE.clear();
            pathsE.shrink_to_fit();
        }

        threepaths_by_tid.clear();
        threepaths_by_tid.shrink_to_fit();

        std::cout << std::fixed << std::setprecision(6)
                  << " Bloom construction time:\t"
                  << (get_current_time() - start) << "sec\n";

        build_samekey_cross_index(edges, (int)bid2key.size());
    }

    // ====================== samekey cross index ======================
    void build_samekey_cross_index(std::vector<Edge>& edges, int total_blooms) {
        static std::vector<int> idx3_by_bid;
        static std::vector<int> idx2_by_bid;
        static std::vector<uint32_t> mark3;
        static std::vector<uint32_t> mark2;
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

            ++epoch3;
            if (epoch3 == 0) { std::fill(mark3.begin(), mark3.end(), 0); epoch3 = 1; }

            for (int j = 0; j < n3; ++j) {
                const int bid = E.host3Bloom[j];
                if (bid < 0) continue;
                mark3[bid] = epoch3;
                idx3_by_bid[bid] = j;
            }

            for (int i = 0; i < n2; ++i) {
                const int bid = E.host2Bloom[i];
                if (bid < 0) continue;
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

    // ====================== support compute ======================
    void compute_five_cycle_supports() {
        edge_5cycle_support.assign(edges.size(), 0);
        const int Bn = (int)bid2key.size();
        if (Bn == 0) return;

        
        {
            double t0 = get_current_time();
            for (size_t eid = 0; eid < edges.size(); ++eid) {
                uint64_t acc = 0;
                for (int bid : edges[eid].host2Bloom) {
                    if (bid < 0) continue;
                    acc += three_blooms[bid].total_paths;
                }
                edge_5cycle_support[eid] += acc;
                //edges[eid].cnt2.assign(edges[eid].host2Bloom.size(), 0);
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
                    if (bid < 0) continue;
                    const int cnt = cnt3[k];
                    const uint64_t t2 = two_blooms[bid].total_paths;
                    acc += (uint64_t)cnt * t2;
                }
                edge_5cycle_support[eid] += acc;
                //edges[eid].cnt3.assign(L, 0);
                edges[eid].reverseIndexInHost3Bloom.assign(L, -1);
            }
            std::cout << std::fixed << std::setprecision(6)
                      << " 3-path support computing time:\t" << (get_current_time() - t0)
                      << "sec\n";
        }

        
        uint64_t sum = 0;
        for (size_t eid = 0; eid < edges.size(); ++eid) {
            edges[eid].five_cycle_support = edge_5cycle_support[eid] - edge_dedup[eid];
            edge_5cycle_support[eid] = edges[eid].five_cycle_support;
            maxsupport = std::max<int>(maxsupport, (int)edge_5cycle_support[eid]);
            sum += edge_5cycle_support[eid];
        }
        std::cout << "max sum:" << sum << std::endl;
    }

    // ====================== DT build (NODT container) ======================
    void build_distributed_tracking() {
        for (auto &tb : two_blooms) tb.clear_buckets();
        for (auto &b3 : three_blooms) b3.clear_buckets();

        for (size_t eid = 0; eid < edges.size(); ++eid) {
            Edge &E = edges[eid];

            if (E.five_cycle_support == 0) {
                zeronode++;
                E.isPeel = true;
                continue;
            }

            for (size_t k = 0; k < E.host2Bloom.size(); ++k) {
                int bid = E.host2Bloom[k];
                if (bid < 0) continue;
                int pos = two_blooms[bid].add_member_edge((ui)eid, (ui)k, edges);
                E.reverseIndexInHost2Bloom[k] = pos;
            }

            for (size_t k = 0; k < E.host3Bloom.size(); ++k) {
                int bid = E.host3Bloom[k];
                if (bid < 0) continue;
                int pos = three_blooms[bid].add_member_edge((ui)eid, (ui)k, edges);
                E.reverseIndexInHost3Bloom[k] = pos;
            }
        }
        std::cout << zeronode << std::endl;
    }

    // ====================== helper: enumerate alive paths, bump balance ======================
    inline void bump_balance_incident_alive_paths(ThreeBloom &B, uint32_t lid, int excludeEid) {
        if (B.off.empty()) return;
        if (lid + 1 >= (uint32_t)B.off.size()) return;

        const uint32_t beg = B.incident_begin(lid);
        const uint32_t end = B.incident_end(lid);

        for (uint32_t t = beg; t < end; ++t) {
            const uint32_t pid = B.inc[(size_t)t];
            if (!B.alive_path(pid)) continue;

            const auto &p = B.paths[pid];
            const int e1 = B.local2eid[p.a];
            const int e2 = B.local2eid[p.b];
            const int e3 = B.local2eid[p.c];

            if (e1 != excludeEid) edges[(size_t)e1].balance++;
            if (e2 != excludeEid) edges[(size_t)e2].balance++;
            if (e3 != excludeEid) edges[(size_t)e3].balance++;
        }
    }

    // ====================== peeling core ======================
    void five_cycle_decomposition() {
        std::queue<ui> peelList;
        double start = get_current_time();
        std::cout << "bitruss decomposing..." << std::endl;

        edgeToPeel = (unsigned int)edges.size() - (unsigned int)zeronode;
        five_cycle_decomposition_number.assign(edges.size(), 0);
        initial_store_edge();
        cout<< "edge to peel:"<<edgeToPeel<<endl;
        while (visitedEdge < edgeToPeel) {
            if (peelList.empty()) {
                globalCounter++;
                for (int i = 0; i < (int)storeEdge.size(); ++i) {
                    int EdgeID = storeEdge[i];
                    if (!edges[(size_t)EdgeID].isPeel &&
                        edges[(size_t)EdgeID].five_cycle_support <= (uint64_t)globalCounter) {
                        edges[(size_t)EdgeID].isPeel = true;
                        peelList.push((ui)EdgeID);
                    }
                }
            } else {
                while (!peelList.empty()) {
                    ui edgeID = peelList.front();
                    peelList.pop();

                    if (five_cycle_decomposition_number[(size_t)edgeID] != 0) continue;

                    five_cycle_decomposition_number[(size_t)edgeID] = globalCounter;
                    peel_edge(edgeID, peelList);
                    visitedEdge++;
                    if(visitedEdge % 5000 == 0) cout<<"visitededge:"<<visitedEdge<<endl;
                }
            }   
        }

        std::cout << std::fixed << std::setprecision(6)
                  << "Bitruss decomposition time:\t" << get_current_time() - start
                  << "sec\n";
    }

    void peel_edge(ui edgeID, queue<ui>& peelList) {
        
        Edge peelEdge = edges[(size_t)edgeID];   
        remove_edge_from_storage_list((int)edgeID);

        
        for (ui i = 0; i < (ui)peelEdge.host2Bloom.size(); ++i) {
            int twoBloomID = peelEdge.get_host_2bloom_id_by_index(i);
            if (twoBloomID < 0) continue;

            TwinInfo twinEdgeInfo = peelEdge.get_twin_edge_info_by_index(i);
            int reverseIndex = peelEdge.get_reverse_index_in_host_2bloom_by_index(i);

            TwoBloom  &current2Bloom = two_blooms[twoBloomID];
            ThreeBloom &current3Bloom = three_blooms[twoBloomID];
            int totalthreepath = (int)current3Bloom.total_paths;

            ui twinEdgeID = twinEdgeInfo.twinEdgeID;

            remove_edge_from_2bloom_by_index(twoBloomID, reverseIndex);

            ui indexInTwinEdge = twinEdgeInfo.hostBloomIndex;
            int twinedgeindexInHostBloom =
                edges[(size_t)twinEdgeID].get_reverse_index_in_host_2bloom_by_index(indexInTwinEdge);

            
            edges[(size_t)twinEdgeID].five_cycle_support -= (uint64_t)totalthreepath;

            
            if (edges[(size_t)edgeID].samekey2to3check[i]) {
                int host3idx = edges[(size_t)edgeID].samekey2to3index[i];
                if (host3idx >= 0 && host3idx < (int)edges[(size_t)edgeID].local3idx.size()) {
                    uint32_t lid = edges[(size_t)edgeID].local3idx[(size_t)host3idx];
                    edges[(size_t)twinEdgeID].five_cycle_support += (uint64_t)edges[(size_t)edgeID].host3Cnt[(size_t)host3idx];

                    bump_balance_incident_alive_paths(current3Bloom, lid, (int)edgeID);
                }
            }

            
            remove_edge_from_2bloom_by_index(twoBloomID, twinedgeindexInHostBloom);
            remove_2bloom_from_edge_by_index(twinEdgeID, indexInTwinEdge);

            current2Bloom.total_paths--;

            
            if (indexInTwinEdge < edges[(size_t)twinEdgeID].samekey2to3check.size() &&
                edges[(size_t)twinEdgeID].samekey2to3check[indexInTwinEdge]) {

                int host3idx = edges[(size_t)twinEdgeID].samekey2to3index[indexInTwinEdge];
                if (host3idx >= 0 && host3idx < (int)edges[(size_t)twinEdgeID].local3idx.size()) {
                    uint32_t lid = edges[(size_t)twinEdgeID].local3idx[(size_t)host3idx];
                    int host3Cnt = edges[(size_t)twinEdgeID].host3Cnt[(size_t)host3idx];

                    edges[(size_t)twinEdgeID].five_cycle_support += (uint64_t)host3Cnt;
                    edges[(size_t)twinEdgeID].balance += host3Cnt;

                    bump_balance_incident_alive_paths(current3Bloom, lid, (int)twinEdgeID);

                    edges[(size_t)twinEdgeID].samekey2to3check[indexInTwinEdge] = false;
                    edges[(size_t)twinEdgeID].samekey3to2check[(size_t)host3idx] = false;
                }
            }

            
            current3Bloom.send_value_to_member(peelList, edges, globalCounter);

            if (edges[(size_t)twinEdgeID].check_maturity(globalCounter)) {
                if (!edges[(size_t)twinEdgeID].isPeel) {
                    edges[(size_t)twinEdgeID].isPeel = true;
                    peelList.push(twinEdgeID);
                }
            }
        }

        
        static std::vector<uint32_t> markEdge;
        static uint32_t epochEdge = 1;
        if (markEdge.size() < edges.size()) markEdge.assign(edges.size(), 0);

        for (ui i = 0; i < (ui)peelEdge.host3Bloom.size(); ++i) {
            int threeBloomID = peelEdge.get_host_3bloom_id_by_index(i);
            if (threeBloomID < 0) continue;

            ThreeBloom &B = three_blooms[threeBloomID];
            TwoBloom   &T = two_blooms[threeBloomID];

            int reverseIndex = peelEdge.get_reverse_index_in_host_3bloom_by_index(i);
            remove_edge_from_3bloom_by_index(threeBloomID, reverseIndex);

            
            int currentcnt3 = peelEdge.host3Cnt[i];
            if (currentcnt3 <= 0) {
                
                continue;
            }

            
            if (i >= peelEdge.local3idx.size()) continue;
            const uint32_t lid_peel = peelEdge.local3idx[i];

            
            std::vector<int> checkoutarray;
            checkoutarray.reserve((size_t)currentcnt3 * 2);

            ++epochEdge;
            if (epochEdge == 0) { std::fill(markEdge.begin(), markEdge.end(), 0); epochEdge = 1; }

            auto push_unique = [&](int eid) {
                if (eid < 0) return;
                if ((size_t)eid >= markEdge.size()) return;
                if (markEdge[(size_t)eid] == epochEdge) return;
                markEdge[(size_t)eid] = epochEdge;
                checkoutarray.push_back(eid);
            };

            
            const uint32_t beg = B.incident_begin(lid_peel);
            const uint32_t end = B.incident_end(lid_peel);

            int removed = 0;

            for (uint32_t t = beg; t < end; ++t) {
                const uint32_t pid = B.inc[(size_t)t];
                if (!B.alive_path(pid)) continue;

                B.kill_path(pid);
                removed++;

                const auto &p = B.paths[pid];

                const uint32_t lids[3] = {p.a, p.b, p.c};
                const int eids[3] = { B.local2eid[p.a], B.local2eid[p.b], B.local2eid[p.c] };

                
                for (int tEdge = 0; tEdge < 3; ++tEdge) {
                    const int eid = eids[tEdge];
                    const uint32_t lid = lids[tEdge];
                    if (eid == (int)edgeID) continue; 

                    push_unique(eid);

                    
                    const uint32_t host_idx = B.local2hostidx[lid];

                    
                    edges[(size_t)eid].five_cycle_support -= (uint64_t)T.total_paths;

                    // 2) host3Cnt--
                    if (host_idx < edges[(size_t)eid].host3Cnt.size()) {
                        edges[(size_t)eid].host3Cnt[(size_t)host_idx]--;
                        if (edges[(size_t)eid].host3Cnt[(size_t)host_idx] <= 0) {
                            int threeEdgeIndexInBloom = edges[(size_t)eid].reverseIndexInHost3Bloom[(size_t)host_idx];
                            remove_edge_from_3bloom_by_index(threeBloomID, threeEdgeIndexInBloom);
                            remove_3bloom_from_edge_by_index((ui)eid, (ui)host_idx);
                        }
                    }

                    
                    if (host_idx < edges[(size_t)eid].samekey3to2check.size() &&
                        edges[(size_t)eid].samekey3to2check[(size_t)host_idx]) {

                        int edgetwoindex = edges[(size_t)eid].samekey3to2index[(size_t)host_idx];

                        edges[(size_t)eid].five_cycle_support += 1;

                        for (int tOther = 0; tOther < 3; ++tOther) {
                            if (tOther == tEdge) continue;
                            int e_other = eids[tOther];
                            if (e_other != (int)edgeID) edges[(size_t)e_other].five_cycle_support += 1;
                        }

                        edges[(size_t)eid].balance += 1;

                        if (host_idx < edges[(size_t)eid].host3Bloom.size() &&
                            edges[(size_t)eid].host3Bloom[(size_t)host_idx] == -1) {
                            edges[(size_t)eid].samekey3to2check[(size_t)host_idx] = false;
                            if (edgetwoindex >= 0 && (size_t)edgetwoindex < edges[(size_t)eid].samekey2to3check.size())
                                edges[(size_t)eid].samekey2to3check[(size_t)edgetwoindex] = false;
                        }

                        if (edgetwoindex >= 0 && (size_t)edgetwoindex < edges[(size_t)eid].twinEdge.size()) {
                            int twinedgeid = edges[(size_t)eid].twinEdge[(size_t)edgetwoindex];
                            if (twinedgeid >= 0) edges[(size_t)twinedgeid].balance += 1;
                        }
                    }
                }
            }

            
            B.total_paths -= (uint64_t)removed;

           
            T.send_value_to_member(removed, peelList, edges, globalCounter);

           
            for (int eid : checkoutarray) {
                if (!edges[(size_t)eid].isPeel && edges[(size_t)eid].check_maturity(globalCounter)) {
                    edges[(size_t)eid].isPeel = true;
                    peelList.push((ui)eid);
                }
            }
        }
    }

    // ====================== remove helpers ======================
    void remove_edge_from_2bloom_by_index(int bloomID, int index) {
        if (index < 0) return;
        affect_edge_t affectEdgeInfo = two_blooms[bloomID].remove_member_by_index(index);
        if ((int)affectEdgeInfo.first == -1) return;
        ui affectEdgeID = affectEdgeInfo.first;
        ui affectIndex = affectEdgeInfo.second;
        edges[(size_t)affectEdgeID].set_reverse_2bloom_index_by_index(affectIndex, index);
    }

    void remove_edge_from_3bloom_by_index(int bloomID, int index) {
        if (index < 0) return;
        affect_edge_t affectEdgeInfo = three_blooms[bloomID].remove_member_by_index(index);
        if ((int)affectEdgeInfo.first == -1) return;
        ui affectEdgeID = affectEdgeInfo.first;
        ui affectIndex = affectEdgeInfo.second;
        edges[(size_t)affectEdgeID].set_reverse_3bloom_index_by_index(affectIndex, index);
    }

    void remove_edge_from_storage_list(int EdgeID) {
        int length = (int)storeEdge.size();
        int storageindex = edges[(size_t)EdgeID].storageIndex;
        if (storageindex < length - 1) {
            int affectEdgeID = storeEdge[length - 1];
            storeEdge[storageindex] = affectEdgeID;
            edges[(size_t)affectEdgeID].storageIndex = storageindex;
            storeEdge.pop_back();
        } else {
            storeEdge.pop_back();
        }
    }

    void remove_2bloom_from_edge_by_index(ui edgeID, ui index) {
        edges[(size_t)edgeID].remove_host_2bloom_by_index(index);
    }

    void remove_3bloom_from_edge_by_index(ui edgeID, ui index) {
        edges[(size_t)edgeID].remove_host_3bloom_by_index(index);
    }

    // ====================== pipeline ======================
    void build_blooms_strict_pipeline_with_DT() {
        double start = get_current_time();

        collect_2paths_grouped();
        std::cout << std::fixed << std::setprecision(6)
                  << " 2-path construction time:\t" << (get_current_time() - start)
                  << "sec\n";

        build_twopath_edge_membership();
        std::cout << std::fixed << std::setprecision(6)
                  << " 2-path membership time:\t" << (get_current_time() - start)
                  << "sec\n";

        collect_3paths_for_existing_keys();
        std::cout << std::fixed << std::setprecision(6)
                  << " 3-path collection time:\t" << (get_current_time() - start)
                  << "sec\n";

        build_blooms_strict_matched();
        std::cout << std::fixed << std::setprecision(6)
                  << " Bloom construction time:\t" << (get_current_time() - start)
                  << "sec\n";

        compute_five_cycle_supports();
        std::cout << std::fixed << std::setprecision(6)
                  << " 5-cycle support time:\t" << (get_current_time() - start)
                  << "sec\n";

        build_distributed_tracking();
        
        std::cout << std::fixed << std::setprecision(6)
                  << " distributed tracking time:\t" << (get_current_time() - start)
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
                  << " cycle decomposition time:\t" << (get_current_time() - start)
                  << "sec\n";
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

    // ====================== query ======================
    inline int get_edge_id(int u, int v) const {
        uint64_t k = pack_edge_u32((uint32_t)u, (uint32_t)v);
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

    inline const ThreeBloom& get_bloom(int bid) const { return three_blooms[bid]; }
    inline const TwoBloom&   get_tbloom(int bid) const { return two_blooms[bid]; }
    inline uint64_t bloom_key(int bid) const { return bid2key[bid]; }

    inline int try_get_bid(int x, int y) const {
        uint64_t key = pack_edge_u32((uint32_t)x, (uint32_t)y);
        auto it = key2bid.find(key);
        return (it == key2bid.end()) ? -1 : it->second;
    }

    inline uint64_t edge_5cycle(int eid) const {
        return (eid < 0 || (size_t)eid >= edge_5cycle_support.size()) ? 0ull
                                                                      : edge_5cycle_support[(size_t)eid];
    }

    inline void initial_store_edge() {
        storeEdge.clear();
        storeEdge.reserve(edges.size());
        for (int i = 0; i < (int)edges.size(); ++i) {
            if (!edges[(size_t)i].isPeel) {
                storeEdge.push_back(i);
                edges[(size_t)i].storageIndex = (int)storeEdge.size() - 1;
            }
        }
    }

public:
    // ====================== data ======================
    int n{0};
    ui visitedEdge{0};
    int zeronode{0};
    int globalCounter{0};
    int maxsupport{0};
    unsigned int edgeToPeel{0};

    std::vector<std::pair<int,int>> edges_raw;
    FlatHashMap<int,int>            id2idx;
    std::vector<int>                idx2id;

    std::vector<Edge>               edges;
    std::vector<int>                storeEdge;

    FlatHashMap<uint64_t,int>       edgeId;
    std::vector<std::pair<int,int>> undirected_edges;

    std::vector<std::vector<int>>   adj;
    std::vector<int>                core;
    std::vector<int>                peel_rank;

    std::vector<std::vector<Next>>  out;
    std::vector<FlatHashMap<int,int>> nbr2eid;

    FlatHashMap<uint64_t,int>       key2bid;
    std::vector<uint64_t>           bid2key;

    std::vector<TwoBloom>           two_blooms;
    std::vector<ThreeBloom>         three_blooms;

    std::vector<uint64_t>           edge_dedup;
    std::vector<uint64_t>           edge_5cycle_support;
    std::vector<int>                five_cycle_decomposition_number;

private:
    // 2-path group
    FlatHashMap<uint64_t,int>                    key2tmpid;
    std::vector<uint64_t>                        tmpid2key;
    std::vector<std::vector<std::pair<int,int>>> twopaths_by_key;
    std::vector<char>                            valid_tmp;
    std::vector<FlatHashMap<int,char>>           twopath_edge_membership;

    // 3-paths by tid
    std::vector<std::vector<ThreePathRaw>>       threepaths_by_tid;

    inline int get_or_create_tmpid(uint64_t key) {
        auto it = key2tmpid.find(key);
        if (it != key2tmpid.end()) return it->second;
        int tid = (int)tmpid2key.size();
        key2tmpid.emplace(key, tid);
        tmpid2key.push_back(key);
        twopaths_by_key.emplace_back();
        return tid;
    }
};
