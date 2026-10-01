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
#include <limits>
#include <queue>
#include <unistd.h>
#include <cstdio>
#include <cstdlib>
#include <tuple>
#include <cmath>

#include "extraBloom.h"
#include "current_time.h"
#include "edge.h"
#include "3-bloom.h"
#include "2-bloom.h"
#include "dt_utils.h"
#include "flat_u64_u32_map.h"
#include "flat_u64_u64_map.h"
#include "flat_u64_hybrid_count_map.h"

using namespace std;

struct TwoPathPair {
    uint32_t first;
    uint32_t second;
    TwoPathPair() = default;
    TwoPathPair(uint32_t a, uint32_t b) : first(a), second(b) {}
};

class Graph {
public:
    void read_edges_from_file(const std::string& filename) {
        FILE* fin = std::fopen(filename.c_str(), "r");
        if (!fin) {
            std::cerr << "Cannot open file: " << filename << "\n";
            std::exit(1);
        }

        constexpr std::size_t LINE_CAP = 1u << 16;
        char line[LINE_CAP];
        edges_raw.reserve(1u << 20);
        while (std::fgets(line, static_cast<int>(LINE_CAP), fin)) {
            char* cur = line;
            auto skip = [](char*& x) {
                while (*x == ' ' || *x == '\t' || *x == '\r' || *x == '\n' || *x == ',') ++x;
            };
            skip(cur);
            if (!((*cur >= '0' && *cur <= '9') || *cur == '-' || *cur == '+')) continue;
            char* end = nullptr;
            const long lu = std::strtol(cur, &end, 10);
            if (end == cur) continue;
            cur = end;
            skip(cur);
            if (!((*cur >= '0' && *cur <= '9') || *cur == '-' || *cur == '+')) continue;
            const long lv = std::strtol(cur, &end, 10);
            if (end == cur) continue;
            const int u = static_cast<int>(lu);
            const int v = static_cast<int>(lv);
            if (u != v) edges_raw.emplace_back(u, v);
        }
        std::fclose(fin);
    }

    void remap_ids() {
        id2idx.release();
        id2idx.reserve(edges_raw.size() * 2u + 1024u);
        idx2id.clear();
        idx2id.reserve(edges_raw.size());

        for (const auto &e : edges_raw) {
            bool inserted = false;
            const uint64_t ku = uint64_t(uint32_t(e.first));
            const uint32_t iu = static_cast<uint32_t>(idx2id.size());
            id2idx.get_or_insert(ku, iu, inserted);
            if (inserted) idx2id.push_back(e.first);

            const uint64_t kv = uint64_t(uint32_t(e.second));
            const uint32_t iv = static_cast<uint32_t>(idx2id.size());
            id2idx.get_or_insert(kv, iv, inserted);
            if (inserted) idx2id.push_back(e.second);
        }
        n = static_cast<int>(idx2id.size());
    }

    inline uint64_t pack_edge(uint32_t a, uint32_t b) const {
        uint32_t x = std::min(a, b);
        uint32_t y = std::max(a, b);
        return (uint64_t(x) << 32) | uint64_t(y);
    }

    void dedup_and_build_edges() {
        edges.clear();
        edges.reserve(edges_raw.size());
        edgeId.release();
        edgeId.reserve(edges_raw.size() + 1024u);

        for (const auto &e : edges_raw) {
            const int u = static_cast<int>(id2idx.get(uint64_t(uint32_t(e.first))));
            const int v = static_cast<int>(id2idx.get(uint64_t(uint32_t(e.second))));
            if (u == v) continue;

            const uint64_t key = pack_edge((uint32_t)u, (uint32_t)v);
            const uint32_t id = static_cast<uint32_t>(edges.size());
            bool inserted = false;
            edgeId.get_or_insert(key, id, inserted);
            if (inserted) edges.emplace_back((int)id, u, v);
        }

        vector<pair<int,int>>().swap(edges_raw);
        id2idx.release();
        vector<int>().swap(idx2id);
    }

    void core_decomposition_bz() {
        adj.assign(n, {});
        for (const auto &e : edges) {
            adj[e.u].push_back(e.v);
            adj[e.v].push_back(e.u);
        }

        vector<int> deg(n, 0);
        int maxd = 0;
        for (int i = 0; i < n; ++i) {
            deg[i] = (int)adj[i].size();
            if (deg[i] > maxd) maxd = deg[i];
        }

        vector<int> bin(maxd + 1, 0);
        for (int d : deg) ++bin[d];
        int start = 0;
        for (int d = 0; d <= maxd; ++d) {
            int cnt = bin[d];
            bin[d] = start;
            start += cnt;
        }

        vector<int> pos(n), vert(n);
        for (int v = 0; v < n; ++v) {
            int d = deg[v];
            pos[v] = bin[d]++;
            vert[pos[v]] = v;
        }
        for (int d = maxd; d > 0; --d) bin[d] = bin[d - 1];
        bin[0] = 0;

        core.assign(n, 0);
        peel_rank.assign(n, -1);
        int rank = 0;

        for (int i = 0; i < n; ++i) {
            int v = vert[i];
            core[v] = deg[v];
            peel_rank[v] = rank++;
            for (int u : adj[v]) if (deg[u] > deg[v]) {
                int du = deg[u], pu = pos[u], pw = bin[du], w = vert[pw];
                if (u != w) {
                    pos[u] = pw; vert[pw] = u;
                    pos[w] = pu; vert[pu] = w;
                }
                ++bin[du];
                --deg[u];
            }
        }
    }

    struct Next { uint32_t to; uint32_t eid; };

    void build_oriented_out() {
        if (edges.size() > (size_t)std::numeric_limits<uint32_t>::max()) {
            std::cerr << "[FATAL] edge ids exceed uint32_t.\n";
            std::exit(1);
        }

        std::vector<uint32_t> out_count((size_t)n, 0u);
        std::vector<uint32_t> adj_count((size_t)n, 0u);
        std::vector<uint32_t> raw_out_count((size_t)n, 0u);
        std::vector<uint32_t> raw_in_count((size_t)n, 0u);

        for (uint32_t eid = 0; eid < (uint32_t)edges.size(); ++eid) {
            const Edge &e = edges[eid];
            const uint32_t tail = peel_rank[e.u] < peel_rank[e.v] ? (uint32_t)e.u : (uint32_t)e.v;
            ++out_count[tail];
            ++adj_count[(size_t)e.u];
            ++adj_count[(size_t)e.v];
            ++raw_out_count[(size_t)e.u];
            ++raw_in_count[(size_t)e.v];
        }

        auto make_offsets = [this](const std::vector<uint32_t>& count,
                                   std::vector<uint32_t>& off) {
            off.assign((size_t)n + 1u, 0u);
            for (int v = 0; v < n; ++v) off[(size_t)v + 1u] = off[(size_t)v] + count[(size_t)v];
        };
        make_offsets(out_count, out_off);
        make_offsets(adj_count, adj_eid_off);
        make_offsets(raw_out_count, raw_out_off);
        make_offsets(raw_in_count, raw_in_off);

        out_data.assign(edges.size(), {});
        adj_eid_data.assign(edges.size() * 2u, {});
        raw_out_eid.assign(edges.size(), 0u);
        raw_in_eid.assign(edges.size(), 0u);

        std::vector<uint32_t> out_cursor(out_off.begin(), out_off.end() - 1);
        std::vector<uint32_t> adj_cursor(adj_eid_off.begin(), adj_eid_off.end() - 1);
        std::vector<uint32_t> raw_out_cursor(raw_out_off.begin(), raw_out_off.end() - 1);
        std::vector<uint32_t> raw_in_cursor(raw_in_off.begin(), raw_in_off.end() - 1);

        for (uint32_t eid = 0; eid < (uint32_t)edges.size(); ++eid) {
            const Edge &e = edges[eid];
            if (peel_rank[e.u] < peel_rank[e.v]) {
                out_data[out_cursor[(size_t)e.u]++] = {(uint32_t)e.v, eid};
            } else {
                out_data[out_cursor[(size_t)e.v]++] = {(uint32_t)e.u, eid};
            }
            adj_eid_data[adj_cursor[(size_t)e.u]++] = {(uint32_t)e.v, eid};
            adj_eid_data[adj_cursor[(size_t)e.v]++] = {(uint32_t)e.u, eid};
            raw_out_eid[raw_out_cursor[(size_t)e.u]++] = eid;
            raw_in_eid[raw_in_cursor[(size_t)e.v]++] = eid;
        }

        vector<vector<int>>().swap(adj);
        vector<int>().swap(core);
    }

    void collect_2paths_grouped() {
        key2tmpid.release();
        tmpid2key.clear();
        twopaths_by_key.clear();
        key2tmpid.reserve(edges.size() / 4u + 1024u);
        tmpid2key.reserve(std::max<size_t>(edges.size() / 8u + 1u, 1024ull));

        for (int b = 0; b < n; ++b) {
            const uint32_t ab = out_off[(size_t)b];
            const uint32_t ae = out_off[(size_t)b + 1u];
            if (ab == ae) continue;
            const uint32_t cb = adj_eid_off[(size_t)b];
            const uint32_t ce = adj_eid_off[(size_t)b + 1u];

            for (uint32_t pa = ab; pa < ae; ++pa) {
                const Next &an = out_data[pa];
                const int a = (int)an.to;
                const int e_ab = (int)an.eid;

                for (uint32_t pc = cb; pc < ce; ++pc) {
                    const Next &cn = adj_eid_data[pc];
                    const int c = (int)cn.to;
                    const int e_bc = (int)cn.eid;
                    if (c == b || c == a) continue;
                    if (!(peel_rank[a] > peel_rank[c])) continue;

                    const uint64_t key = pack_edge((uint32_t)a, (uint32_t)c);
                    const int tid = get_or_create_tmpid(key);
                    twopaths_by_key[(size_t)tid].emplace_back((uint32_t)e_ab, (uint32_t)e_bc);
                }
            }
        }
    }

    void release_after_2path_collection() {
        std::vector<uint32_t>().swap(adj_eid_off);
        std::vector<Next>().swap(adj_eid_data);
    }

    void build_twopath_edge_membership() {
        const int T = (int)tmpid2key.size();
        twopath_edge_membership.clear();
        twopath_edge_membership.resize(T);
        for (int tid = 0; tid < T; ++tid) {
            auto &mem = twopath_edge_membership[tid];
            const auto &vec = twopaths_by_key[tid];
            mem.reserve(vec.size() * 2);
            for (const auto &p : vec) {
                mem.push_back(p.first);
                mem.push_back(p.second);
            }
            std::sort(mem.begin(), mem.end());
            mem.erase(std::unique(mem.begin(), mem.end()), mem.end());
            mem.shrink_to_fit();
        }
    }

    static inline bool membership_contains(const vector<uint32_t>& mem, int eid) {
        return std::binary_search(mem.begin(), mem.end(), (uint32_t)eid);
    }

    void collect_3paths_count_only() {
        threepath_count_by_tid.assign(tmpid2key.size(), 0ull);
        valid_tmp.assign(tmpid2key.size(), false);
        edge_dedup.assign(edges.size(), 0ull);

        uint64_t total_paths = 0;
        const uint32_t MISS = std::numeric_limits<uint32_t>::max();
        for (uint32_t eid_bc = 0; eid_bc < (uint32_t)edges.size(); ++eid_bc) {
            const int b = edges[eid_bc].u;
            const int c = edges[eid_bc].v;
            const uint32_t bb = out_off[(size_t)b], be = out_off[(size_t)b + 1u];
            const uint32_t cb = out_off[(size_t)c], ce = out_off[(size_t)c + 1u];

            for (uint32_t pa = bb; pa < be; ++pa) {
                const Next &an = out_data[pa];
                const int a = (int)an.to;
                if (a == c) continue;
                const int e_ab = (int)an.eid;

                for (uint32_t pd = cb; pd < ce; ++pd) {
                    const Next &dn = out_data[pd];
                    const int d = (int)dn.to;
                    if (d == b || d == a) continue;
                    const int e_cd = (int)dn.eid;

                    const uint64_t key_ad = pack_edge((uint32_t)a, (uint32_t)d);
                    const uint32_t tid_u32 = key2tmpid.get(key_ad, MISS);
                    if (tid_u32 == MISS) continue;

                    const int tid = (int)tid_u32;
                    ++threepath_count_by_tid[(size_t)tid];
                    ++total_paths;
                    valid_tmp[(size_t)tid] = true;

                    const auto &mem = twopath_edge_membership[(size_t)tid];
                    if (membership_contains(mem, e_ab)) {
                        edge_dedup[(size_t)e_ab] += 2;
                        edge_dedup[(size_t)eid_bc] += 1;
                        edge_dedup[(size_t)e_cd] += 1;
                        const int e_bd = get_edge_id_fast(b, d);
                        if (e_bd >= 0 && membership_contains(mem, e_bd)) edge_dedup[(size_t)e_bd] += 1;
                    }

                    if (membership_contains(mem, e_cd)) {
                        edge_dedup[(size_t)e_cd] += 2;
                        edge_dedup[(size_t)e_ab] += 1;
                        edge_dedup[(size_t)eid_bc] += 1;
                        const int e_ac = get_edge_id_fast(a, c);
                        if (e_ac >= 0 && membership_contains(mem, e_ac)) edge_dedup[(size_t)e_ac] += 1;
                    }
                }
            }
        }

        const std::size_t rss_bytes = getCurrentRSSBytes();
        cerr << "[MEM] After counting 3-paths, no 3-path cache: "
             << fixed << setprecision(3)
             << rss_bytes / (1024.0 * 1024.0 * 1024.0) << " GiB  (~"
             << rss_bytes / 1e9 << " GB), total 3-paths=" << total_paths << "\n";
    }

    void build_blooms_strict_matched() {
        double start = get_current_time();

        for (auto &E : edges) {
            E.isPeel = false;
            E.reverseIndexInExtraBloom = {-1, 0};
            E.targetValue = 0;
            E.extraBloom_cnt = 0;
            E.balance = 0;
            E.hostbloomnumber = 0;
            E.h2_off = E.h2_len = E.h3_off = E.h3_len = 0;
            E.accumulatedValue = 0;
            E.delta = 0;
            E.isDT = true;
            E.slackValue = 0;
            E.five_cycle_support = 0;
        }

        key2bid.release();
        bid2key.clear();
        three_blooms.clear();
        two_blooms.clear();

        vector<int> tid2bid(tmpid2key.size(), -1);
        bid2key.reserve(tmpid2key.size());
        uint64_t total3paths = 0;

        for (int tid = 0; tid < (int)tmpid2key.size(); ++tid) {
            if (!valid_tmp[(size_t)tid] || threepath_count_by_tid[(size_t)tid] == 0) continue;
            const int bid = (int)bid2key.size();
            tid2bid[(size_t)tid] = bid;
            const uint64_t key = tmpid2key[(size_t)tid];
            bid2key.push_back(key);
            bool inserted = false;
            key2bid.get_or_insert(key, (uint32_t)bid, inserted);
            total3paths += threepath_count_by_tid[(size_t)tid];
        }

        three_blooms.reserve(bid2key.size());
        for (int bid = 0; bid < (int)bid2key.size(); ++bid) three_blooms.emplace_back(bid, bid2key[(size_t)bid]);
        two_blooms.assign(bid2key.size(), {});

        vector<uint32_t> h2reserve(edges.size(), 0u);
        uint64_t h2total64 = 0;
        for (int tid = 0; tid < (int)tmpid2key.size(); ++tid) {
            const int bid = tid2bid[(size_t)tid];
            if (bid < 0) continue;
            for (const auto &pr : twopaths_by_key[(size_t)tid]) {
                ++h2reserve[(size_t)pr.first];
                ++h2reserve[(size_t)pr.second];
                h2total64 += 2;
            }
        }
        if (h2total64 > (uint64_t)std::numeric_limits<uint32_t>::max()) {
            std::cerr << "[FATAL] total 2-bloom memberships exceed uint32_t offsets.\n";
            std::exit(1);
        }

        uint32_t h2total = 0;
        for (size_t eid = 0; eid < edges.size(); ++eid) {
            edges[eid].h2_off = h2total;
            edges[eid].h2_len = h2reserve[eid];
            h2total += h2reserve[eid];
        }

        // Allocate only fields needed to construct H2. Reverse positions and DT
        // counters are delayed until the large H3 aggregation map is released.
        h2_bloom.assign(h2total, -1);
        h2_twin.assign(h2total, -1);
        h2_twin_index.assign(h2total, -1);
        h2_rev.clear();
        h2_cnt32.clear(); h2_cnt64.clear(); h2_cnt_use64 = false;
        h2_same3.clear();

        vector<uint32_t> h2cursor(edges.size(), 0u);
        for (int tid = 0; tid < (int)tmpid2key.size(); ++tid) {
            const int bid = tid2bid[(size_t)tid];
            if (bid < 0) continue;

            TwoBloom TB(bid, bid2key[(size_t)bid]);
            const auto &list = twopaths_by_key[(size_t)tid];
            TB.total_paths = (uint64_t)list.size();

            for (const auto &pr : list) {
                const ui e1 = (ui)pr.first;
                const ui e2 = (ui)pr.second;
                const ui idx1 = h2cursor[(size_t)e1]++;
                const ui idx2 = h2cursor[(size_t)e2]++;

                H2_bid(e1, idx1) = bid;
                H2_twin(e1, idx1) = (int)e2;
                H2_twinidx(e1, idx1) = (int)idx2;
                H2_bid(e2, idx2) = bid;
                H2_twin(e2, idx2) = (int)e1;
                H2_twinidx(e2, idx2) = (int)idx1;
            }
            two_blooms[(size_t)bid] = std::move(TB);
        }
        vector<uint32_t>().swap(h2reserve);
        vector<uint32_t>().swap(h2cursor);
        vector<vector<TwoPathPair>>().swap(twopaths_by_key);
        vector<vector<uint32_t>>().swap(twopath_edge_membership);

        // The second 3-path pass can now read a final Bloom id directly.
        key2tmpid.remap_values(tid2bid, std::numeric_limits<uint32_t>::max());
        build_three_blooms_direct(tid2bid, total3paths);

        // The construction orientation index is no longer needed. Keep the
        // compact raw CSR and the O(m) edge hash for fast on-demand regeneration.
        release_graph_lookup_after_bloom_build();

        std::cout << std::fixed << std::setprecision(6)
                  << " Bloom construction time:\t" << (get_current_time() - start) << "sec\n";

        const std::size_t rss_bytes = getCurrentRSSBytes();
        cerr << "[MEM] After direct index build: " << fixed << setprecision(3)
             << rss_bytes / (1024.0 * 1024.0 * 1024.0) << " GiB  (~"
             << rss_bytes / 1e9 << " GB)\n";

        build_samekey_cross_index(edges, (int)bid2key.size());

        key2tmpid.release();
        vector<uint64_t>().swap(tmpid2key);
        vector<char>().swap(valid_tmp);
        vector<uint64_t>().swap(threepath_count_by_tid);
    }

    inline uint64_t make_h3_key(int bid, int eid) const {
        return (uint64_t((uint32_t)bid) << 32) | uint64_t((uint32_t)eid);
    }

    inline void unpack_h3_key(uint64_t key, int &bid, int &eid) const {
        bid = (int)(key >> 32);
        eid = (int)(key & 0xffffffffull);
    }



    uint64_t get_counter_slot(int kind, size_t pos) const {
        switch (kind) {
            case 0: return h2_cnt_use64 ? h2_cnt64[pos] : (uint64_t)h2_cnt32[pos];
            case 1: return h3_count_use64 ? h3_count64[pos] : (uint64_t)h3_count32[pos];
            default: return h3_cnt_use64 ? h3_cnt64[pos] : (uint64_t)h3_cnt32[pos];
        }
    }

    void promote_counter_slot(int kind) {
        if (kind == 0 && !h2_cnt_use64) {
            h2_cnt64.assign(h2_cnt32.begin(), h2_cnt32.end());
            vector<uint32_t>().swap(h2_cnt32);
            h2_cnt_use64 = true;
        } else if (kind == 1 && !h3_count_use64) {
            h3_count64.assign(h3_count32.begin(), h3_count32.end());
            vector<uint32_t>().swap(h3_count32);
            h3_count_use64 = true;
        } else if (kind == 2 && !h3_cnt_use64) {
            h3_cnt64.assign(h3_cnt32.begin(), h3_cnt32.end());
            vector<uint32_t>().swap(h3_cnt32);
            h3_cnt_use64 = true;
        }
    }

    void set_counter_slot(int kind, size_t pos, uint64_t value) {
        if (value > (uint64_t)std::numeric_limits<uint32_t>::max()) promote_counter_slot(kind);
        switch (kind) {
            case 0:
                if (h2_cnt_use64) h2_cnt64[pos] = value;
                else h2_cnt32[pos] = (uint32_t)value;
                break;
            case 1:
                if (h3_count_use64) h3_count64[pos] = value;
                else h3_count32[pos] = (uint32_t)value;
                break;
            default:
                if (h3_cnt_use64) h3_cnt64[pos] = value;
                else h3_cnt32[pos] = (uint32_t)value;
                break;
        }
    }

    struct CounterRef {
        Graph* g{nullptr};
        int kind{0};
        size_t pos{0};
        operator uint64_t() const { return g->get_counter_slot(kind, pos); }
        CounterRef& operator=(uint64_t value) { g->set_counter_slot(kind, pos, value); return *this; }
        CounterRef& operator--() { uint64_t v = g->get_counter_slot(kind, pos); g->set_counter_slot(kind, pos, v ? v - 1 : 0); return *this; }
        uint64_t operator--(int) { uint64_t v = g->get_counter_slot(kind, pos); g->set_counter_slot(kind, pos, v ? v - 1 : 0); return v; }
        CounterRef& operator+=(uint64_t delta) { g->set_counter_slot(kind, pos, g->get_counter_slot(kind, pos) + delta); return *this; }
    };

    inline uint32_t h2_size(ui eid) const { return edges[(size_t)eid].h2_len; }
    inline uint32_t h3_size(ui eid) const { return edges[(size_t)eid].h3_len; }
    inline size_t h2_pos(ui eid, ui idx) const { return (size_t)edges[(size_t)eid].h2_off + (size_t)idx; }
    inline size_t h3_pos(ui eid, ui idx) const { return (size_t)edges[(size_t)eid].h3_off + (size_t)idx; }

    inline int& H2_bid(ui eid, ui idx) { return h2_bloom[h2_pos(eid, idx)]; }
    inline const int& H2_bid(ui eid, ui idx) const { return h2_bloom[h2_pos(eid, idx)]; }
    inline pair_t& H2_rev(ui eid, ui idx) { return h2_rev[h2_pos(eid, idx)]; }
    inline const pair_t& H2_rev(ui eid, ui idx) const { return h2_rev[h2_pos(eid, idx)]; }
    inline int& H2_twin(ui eid, ui idx) { return h2_twin[h2_pos(eid, idx)]; }
    inline const int& H2_twin(ui eid, ui idx) const { return h2_twin[h2_pos(eid, idx)]; }
    inline int& H2_twinidx(ui eid, ui idx) { return h2_twin_index[h2_pos(eid, idx)]; }
    inline const int& H2_twinidx(ui eid, ui idx) const { return h2_twin_index[h2_pos(eid, idx)]; }
    inline CounterRef H2_cnt(ui eid, ui idx) { return CounterRef{this, 0, h2_pos(eid, idx)}; }
    inline uint64_t H2_cnt(ui eid, ui idx) const { return get_counter_slot(0, h2_pos(eid, idx)); }
    inline int& H2_same3(ui eid, ui idx) { return h2_same3[h2_pos(eid, idx)]; }
    inline const int& H2_same3(ui eid, ui idx) const { return h2_same3[h2_pos(eid, idx)]; }
    inline bool H2_has_same3(ui eid, ui idx) const { return H2_same3(eid, idx) >= 0; }

    inline int& H3_bid(ui eid, ui idx) { return h3_bloom[h3_pos(eid, idx)]; }
    inline const int& H3_bid(ui eid, ui idx) const { return h3_bloom[h3_pos(eid, idx)]; }
    inline pair_t& H3_rev(ui eid, ui idx) { return h3_rev[h3_pos(eid, idx)]; }
    inline const pair_t& H3_rev(ui eid, ui idx) const { return h3_rev[h3_pos(eid, idx)]; }
    inline CounterRef H3_count(ui eid, ui idx) { return CounterRef{this, 1, h3_pos(eid, idx)}; }
    inline uint64_t H3_count(ui eid, ui idx) const { return get_counter_slot(1, h3_pos(eid, idx)); }
    inline CounterRef H3_cnt(ui eid, ui idx) { return CounterRef{this, 2, h3_pos(eid, idx)}; }
    inline uint64_t H3_cnt(ui eid, ui idx) const { return get_counter_slot(2, h3_pos(eid, idx)); }
    inline int& H3_same2(ui eid, ui idx) { return h3_same2[h3_pos(eid, idx)]; }
    inline const int& H3_same2(ui eid, ui idx) const { return h3_same2[h3_pos(eid, idx)]; }
    inline bool H3_has_same2(ui eid, ui idx) const { return H3_same2(eid, idx) >= 0; }

    void build_three_blooms_direct(const vector<int>& tid2bid, uint64_t total3paths) {
        for (int tid = 0; tid < (int)tmpid2key.size(); ++tid) {
            const int bid = tid2bid[(size_t)tid];
            if (bid < 0) continue;
            ThreeBloom &tb = three_blooms[(size_t)bid];
            tb.key = bid2key[(size_t)bid];
            tb.id = bid;
            tb.total_paths = threepath_count_by_tid[(size_t)tid];
        }

        FlatU64HybridCountMap h3cnt;
        uint64_t expected = 0;
        for (int tid = 0; tid < (int)threepath_count_by_tid.size(); ++tid) {
            if (tid2bid[(size_t)tid] < 0) continue;
            const uint64_t t = threepath_count_by_tid[(size_t)tid];
            const uint64_t shared_est = (uint64_t)(28.0L * std::sqrt((long double)t)) + 4ull;
            expected += std::min<uint64_t>(3ull * t, shared_est);
        }
        expected = std::min<uint64_t>(expected, 3ull * total3paths);
        h3cnt.reserve((size_t)expected);

        const uint32_t MISS = std::numeric_limits<uint32_t>::max();
        for (uint32_t eid_bc = 0; eid_bc < (uint32_t)edges.size(); ++eid_bc) {
            const int b = edges[eid_bc].u;
            const int c = edges[eid_bc].v;
            const uint32_t bb = out_off[(size_t)b], be = out_off[(size_t)b + 1u];
            const uint32_t cb = out_off[(size_t)c], ce = out_off[(size_t)c + 1u];

            for (uint32_t pa = bb; pa < be; ++pa) {
                const Next &an = out_data[pa];
                const int a = (int)an.to;
                if (a == c) continue;
                const int e_ab = (int)an.eid;

                for (uint32_t pd = cb; pd < ce; ++pd) {
                    const Next &dn = out_data[pd];
                    const int d = (int)dn.to;
                    if (d == b || d == a) continue;
                    const int e_cd = (int)dn.eid;

                    const uint32_t bid_u32 = key2tmpid.get(pack_edge((uint32_t)a, (uint32_t)d), MISS);
                    if (bid_u32 == MISS) continue;
                    const int bid = (int)bid_u32;
                    h3cnt.increment(make_h3_key(bid, e_ab));
                    h3cnt.increment(make_h3_key(bid, (int)eid_bc));
                    h3cnt.increment(make_h3_key(bid, e_cd));
                }
            }
        }

        std::cerr << "[INFO] H3 aggregate entries=" << h3cnt.size()
                  << ", hash capacity=" << h3cnt.capacity()
                  << ", reserve estimate=" << expected << "\n";

        vector<uint32_t> h3reserve(edges.size(), 0u);
        uint64_t max_h3_membership_count = 0;
        h3cnt.for_each([&](uint64_t key, uint64_t count) {
            int bid, eid;
            unpack_h3_key(key, bid, eid);
            if (eid >= 0 && (size_t)eid < h3reserve.size()) ++h3reserve[(size_t)eid];
            max_h3_membership_count = std::max(max_h3_membership_count, count);
        });

        uint64_t h3total64 = 0;
        for (uint32_t x : h3reserve) h3total64 += x;
        if (h3total64 > (uint64_t)std::numeric_limits<uint32_t>::max()) {
            std::cerr << "[FATAL] total 3-bloom memberships exceed uint32_t offsets.\n";
            std::exit(1);
        }

        uint32_t h3total = 0;
        for (size_t eid = 0; eid < edges.size(); ++eid) {
            edges[eid].h3_off = h3total;
            edges[eid].h3_len = h3reserve[eid];
            h3total += h3reserve[eid];
        }

        h3_bloom.assign(h3total, -1);
        h3_count_use64 = max_h3_membership_count > (uint64_t)std::numeric_limits<uint32_t>::max();
        if (h3_count_use64) {
            h3_count64.assign(h3total, 0ull);
            h3_count32.clear();
        } else {
            h3_count32.assign(h3total, 0u);
            h3_count64.clear();
        }
        h3_rev.clear();
        h3_cnt32.clear(); h3_cnt64.clear(); h3_cnt_use64 = false;
        h3_same2.clear();

        vector<uint32_t> h3cursor(edges.size(), 0u);
        h3cnt.for_each([&](uint64_t key, uint64_t count) {
            int bid, eid;
            unpack_h3_key(key, bid, eid);
            if (bid < 0 || eid < 0 || (size_t)eid >= edges.size()) return;
            const ui idx = h3cursor[(size_t)eid]++;
            H3_bid((ui)eid, idx) = bid;
            set_counter_slot(1, h3_pos((ui)eid, idx), count);
        });

        h3cnt.release();

        if (!h3_count_use64) {
            std::vector<uint64_t> sort_buffer;
            for (ui eid = 0; eid < (ui)edges.size(); ++eid) {
                const uint32_t L = edges[eid].h3_len;
                if (L <= 1) continue;
                const uint32_t off = edges[eid].h3_off;
                sort_buffer.clear();
                if (sort_buffer.capacity() < L) sort_buffer.reserve(L);
                for (uint32_t i = 0; i < L; ++i) {
                    sort_buffer.push_back((uint64_t((uint32_t)h3_bloom[(size_t)off + i]) << 32)
                                          | uint64_t(h3_count32[(size_t)off + i]));
                }
                std::sort(sort_buffer.begin(), sort_buffer.end());
                for (uint32_t i = 0; i < L; ++i) {
                    h3_bloom[(size_t)off + i] = (int)(sort_buffer[i] >> 32);
                    h3_count32[(size_t)off + i] = (uint32_t)sort_buffer[i];
                }
            }
        } else {
            struct Rec { int bid; uint64_t count; };
            std::vector<Rec> sort_buffer;
            for (ui eid = 0; eid < (ui)edges.size(); ++eid) {
                const uint32_t L = edges[eid].h3_len;
                if (L <= 1) continue;
                const uint32_t off = edges[eid].h3_off;
                sort_buffer.clear();
                if (sort_buffer.capacity() < L) sort_buffer.reserve(L);
                for (uint32_t i = 0; i < L; ++i)
                    sort_buffer.push_back({h3_bloom[(size_t)off + i], h3_count64[(size_t)off + i]});
                std::sort(sort_buffer.begin(), sort_buffer.end(),
                          [](const Rec &x, const Rec &y) { return x.bid < y.bid; });
                for (uint32_t i = 0; i < L; ++i) {
                    h3_bloom[(size_t)off + i] = sort_buffer[i].bid;
                    h3_count64[(size_t)off + i] = sort_buffer[i].count;
                }
            }
        }

        // Allocate DT-only arrays after the large construction hash has gone.
        h2_rev.assign(h2_bloom.size(), {-1, -1});
        uint64_t max_h2_counter = 0;
        for (const ThreeBloom &tb : three_blooms) max_h2_counter = std::max(max_h2_counter, tb.total_paths);
        h2_cnt_use64 = max_h2_counter > (uint64_t)std::numeric_limits<uint32_t>::max();
        if (h2_cnt_use64) { h2_cnt64.assign(h2_bloom.size(), 0ull); h2_cnt32.clear(); }
        else { h2_cnt32.assign(h2_bloom.size(), 0u); h2_cnt64.clear(); }
        h2_same3.assign(h2_bloom.size(), -1);

        h3_rev.assign(h3_bloom.size(), {-1, -1});
        uint64_t max_h3_counter = 0;
        for (const TwoBloom &tb : two_blooms) max_h3_counter = std::max(max_h3_counter, tb.total_paths);
        h3_cnt_use64 = max_h3_counter > (uint64_t)std::numeric_limits<uint32_t>::max();
        if (h3_cnt_use64) { h3_cnt64.assign(h3_bloom.size(), 0ull); h3_cnt32.clear(); }
        else { h3_cnt32.assign(h3_bloom.size(), 0u); h3_cnt64.clear(); }
        h3_same2.assign(h3_bloom.size(), -1);

        vector<uint32_t>().swap(h3reserve);
        vector<uint32_t>().swap(h3cursor);

        const std::size_t rss_bytes = getCurrentRSSBytes();
        cerr << "[MEM] After streaming 3-Bloom membership build: " << fixed << setprecision(3)
             << rss_bytes / (1024.0 * 1024.0 * 1024.0) << " GiB  (~"
             << rss_bytes / 1e9 << " GB), host3 memberships=" << h3_bloom.size() << "\n";
    }

    void release_graph_lookup_after_bloom_build() {
        std::vector<uint32_t>().swap(out_off);
        std::vector<Next>().swap(out_data);
        // edgeId is deliberately kept: on-demand 3-path generation performs a
        // very large number of endpoint-edge queries, and expected O(1) lookup
        // is substantially faster than repeated adjacency searches on large graphs.
    }

    void build_samekey_cross_index(vector<Edge>& edges, int total_blooms) {
        static vector<int> idx3_by_bid;
        static vector<uint32_t> mark3;
        static uint32_t epoch3 = 1;

        if ((int)idx3_by_bid.size() < total_blooms) {
            idx3_by_bid.resize(total_blooms, -1);
            mark3.resize(total_blooms, 0);
        }

        std::fill(h2_same3.begin(), h2_same3.end(), -1);
        std::fill(h3_same2.begin(), h3_same2.end(), -1);

        for (ui eid = 0; eid < (ui)edges.size(); ++eid) {
            const ui n2 = h2_size(eid);
            const ui n3 = h3_size(eid);

            ++epoch3;
            if (epoch3 == 0) {
                std::fill(mark3.begin(), mark3.end(), 0);
                epoch3 = 1;
            }

            for (ui j = 0; j < n3; ++j) {
                const int bid = H3_bid(eid, j);
                if (bid < 0) continue;
                mark3[bid] = epoch3;
                idx3_by_bid[bid] = (int)j;
            }

            for (ui i = 0; i < n2; ++i) {
                const int bid = H2_bid(eid, i);
                if (bid < 0) continue;
                if (mark3[bid] == epoch3) {
                    const int j = idx3_by_bid[bid];
                    H2_same3(eid, i) = j;
                    H3_same2(eid, (ui)j) = (int)i;
                }
            }
        }
    }

    void compute_five_cycle_supports() {
        for (int i = 0; i < (int)three_blooms.size(); i++) {
            three_blooms[i].ensure_buckets_by_paths(two_blooms[i].total_paths);
        }
        for (int i = 0; i < (int)two_blooms.size(); i++) {
            two_blooms[i].ensure_buckets_by_paths(three_blooms[i].total_paths);
        }

        edge_5cycle_support.assign(edges.size(), 0ull);
        const int Bn = (int)bid2key.size();
        if (Bn == 0) return;

        {
            double t0 = get_current_time();
            for (ui eid = 0; eid < (ui)edges.size(); ++eid) {
                uint64_t acc = 0;
                const ui L = h2_size(eid);
                for (ui k = 0; k < L; ++k) {
                    int bid = H2_bid(eid, k);
                    if (bid >= 0) acc += three_blooms[bid].total_paths;
                }
                edge_5cycle_support[eid] += acc;
            }
            std::cout << std::fixed << std::setprecision(6)
                      << " 2-path support computing time:\t" << (get_current_time() - t0)
                      << "sec\n";
        }

        {
            double t0 = get_current_time();
            for (ui eid = 0; eid < (ui)edges.size(); ++eid) {
                uint64_t acc = 0;
                const ui L = h3_size(eid);
                for (ui k = 0; k < L; ++k) {
                    int bid = H3_bid(eid, k);
                    if (bid < 0) continue;
                    acc += H3_count(eid, k) * two_blooms[bid].total_paths;
                }
                edge_5cycle_support[eid] += acc;
            }
            std::cout << std::fixed << std::setprecision(6)
                      << " 3-path support computing time:\t" << (get_current_time() - t0)
                      << "sec\n";
        }

        uint64_t sum = 0;
        maxsupport = 0;
        for (size_t eid = 0; eid < edges.size(); ++eid) {
            if (edge_dedup[eid] >= edge_5cycle_support[eid]) {
                edges[eid].five_cycle_support = 0;
                edge_5cycle_support[eid] = 0;
                continue;
            }
            edges[eid].five_cycle_support = edge_5cycle_support[eid] - edge_dedup[eid];
            edge_5cycle_support[eid] = edges[eid].five_cycle_support;
            if (maxsupport < edge_5cycle_support[eid]) maxsupport = edge_5cycle_support[eid];
            sum += edge_5cycle_support[eid];
        }
        cout << "sum:" << sum / 5 << endl;
        std::cout << "max sum:" << maxsupport << std::endl;

        vector<uint64_t>().swap(edge_5cycle_support);
        vector<uint64_t>().swap(edge_dedup);
    }

    void build_distributed_tracking() {
        for (auto &tb : two_blooms) tb.clear_buckets();
        for (auto &b3 : three_blooms) b3.clear_buckets();

        extraBloom.maxSupport = maxsupport;
        extraBloom.ensure_buckets_by_paths();
        zeronode = 0;

        // Count the initial records first, then reserve exact bucket capacities.
        // This avoids geometric vector over-allocation and repeated reallocations.
        for (ui eid = 0; eid < (ui)edges.size(); ++eid) {
            Edge &E = edges[eid];
            if (E.five_cycle_support == 0) {
                ++zeronode;
                E.isPeel = true;
                continue;
            }

            E.hostbloomnumber = (int)h2_size(eid) + (int)h3_size(eid) + 1;
            E.compute_slack_value();
            const uint64_t slack = E.get_slack_value();

            if (!E.isDT) {
                for (ui k = 0; k < h2_size(eid); ++k) two_blooms[(size_t)H2_bid(eid, k)].count_member(0);
                for (ui k = 0; k < h3_size(eid); ++k) three_blooms[(size_t)H3_bid(eid, k)].count_member(-2);
            } else {
                const int b2 = (int)log2_32(slack);
                for (ui k = 0; k < h2_size(eid); ++k) {
                    const int bid = H2_bid(eid, k);
                    if (slack < two_blooms[(size_t)bid].bloomNumber) two_blooms[(size_t)bid].count_member(b2);
                }
                for (ui k = 0; k < h3_size(eid); ++k) {
                    const int bid = H3_bid(eid, k);
                    if (slack > three_blooms[(size_t)bid].bloomNumber) continue;
                    uint64_t ratio = slack / std::max<uint64_t>(1ull, H3_count(eid, k));
                    if (ratio == 0) ratio = 1;
                    three_blooms[(size_t)bid].count_member((int)log2_32(ratio));
                }
            }
            extraBloom.count_member(E);
        }

        for (auto &tb : two_blooms) tb.reserve_counted();
        for (auto &b3 : three_blooms) b3.reserve_counted();
        extraBloom.reserve_counted();

        for (ui eid = 0; eid < (ui)edges.size(); ++eid) {
            Edge &E = edges[eid];
            if (E.five_cycle_support == 0) continue;
            const uint64_t slack = E.get_slack_value();

            if (!E.isDT) {
                for (ui k = 0; k < h2_size(eid); ++k) {
                    const int bid = H2_bid(eid, k);
                    H2_rev(eid, k) = two_blooms[(size_t)bid].add_member_edge(eid, k, edges);
                }
            } else {
                const uint32_t bucket = log2_32(slack);
                for (ui k = 0; k < h2_size(eid); ++k) {
                    const int bid = H2_bid(eid, k);
                    H2_rev(eid, k) = two_blooms[(size_t)bid].add_member_edge2(bucket, eid, k, edges);
                }
            }

            if (!E.isDT) {
                for (ui k = 0; k < h3_size(eid); ++k) {
                    const int bid = H3_bid(eid, k);
                    H3_rev(eid, k) = three_blooms[(size_t)bid].add_member_edge(eid, k, edges);
                }
            } else {
                for (ui k = 0; k < h3_size(eid); ++k) {
                    uint64_t ratio = slack / std::max<uint64_t>(1ull, H3_count(eid, k));
                    if (ratio == 0) ratio = 1;
                    const uint32_t bucket = log2_32(ratio);
                    const int bid = H3_bid(eid, k);
                    H3_rev(eid, k) = three_blooms[(size_t)bid].add_member_edge2(bucket, eid, k, edges);
                }
            }

            E.set_reverse_index_in_extra_bloom(extraBloom.add_member_edge(eid, edges));
        }
        cout << zeronode << endl;
    }

    void send_value_to_2bloom(int bloomID,
                              uint64_t increasenum,
                              std::vector<ui> &matureList,
                              std::vector<ui> &peelList) {
        TwoBloom &B = two_blooms[(size_t)bloomID];
        const uint64_t old_counter = B.counter;
        B.counter += increasenum;

        for (const DTBucketMember &rec : B.zero_members) {
            const ui edgeID = rec.edge;
            if (edges[edgeID].isPeel) continue;
            edges[edgeID].accumulate_value(increasenum);
            edges[edgeID].decrease_value(edges[edgeID].balance);
            edges[edgeID].balance = 0;
            if (edges[edgeID].check_maturity() && !edges[edgeID].isPeel) {
                edges[edgeID].isPeel = true;
                peelList.push_back(edgeID);
            }
        }

        if (B.counter < 16) return;
        const int highest = (int)log2_32(old_counter ^ B.counter);
        for (const SparseDTBucket &slot : B.dt_buckets) {
            const int bucket = (int)slot.id;
            if (bucket < 4 || bucket > highest) continue;
            for (const DTBucketMember &rec : slot.members) {
                const ui edgeID = rec.edge;
                if (edges[edgeID].isPeel) continue;
                const uint64_t cnt = H2_cnt(edgeID, rec.host);
                edges[edgeID].accumulate_value(B.counter - cnt);
                H2_cnt(edgeID, rec.host) = B.counter;
                if (edges[edgeID].check_maturity()) matureList.push_back(edgeID);
            }
        }
    }

    void send_value_to_3bloom(int bloomID,
                              std::vector<ui> &matureList,
                              std::vector<ui> &peelList) {
        ThreeBloom &B = three_blooms[(size_t)bloomID];
        const uint64_t old_counter = B.counter;
        ++B.counter;

        for (const DTBucketMember &rec : B.nodt_members) {
            const ui edgeID = rec.edge;
            if (edges[edgeID].isPeel) continue;
            edges[edgeID].accumulate_value(H3_count(edgeID, rec.host));
            edges[edgeID].decrease_value(edges[edgeID].balance);
            edges[edgeID].balance = 0;
            if (edges[edgeID].check_maturity() && !edges[edgeID].isPeel) {
                edges[edgeID].isPeel = true;
                peelList.push_back(edgeID);
            }
        }

        const int highest_exclusive = (int)log2_32((old_counter ^ B.counter) + 1);
        for (const SparseDTBucket &slot : B.dt_buckets) {
            if ((int)slot.id >= highest_exclusive) continue;
            for (const DTBucketMember &rec : slot.members) {
                const ui edgeID = rec.edge;
                if (edges[edgeID].isPeel) continue;
                const uint64_t cnt3v = H3_cnt(edgeID, rec.host);
                const uint64_t hostCnt = H3_count(edgeID, rec.host);
                edges[edgeID].accumulate_value((B.counter - cnt3v) * hostCnt);
                H3_cnt(edgeID, rec.host) = B.counter;
                if (edges[edgeID].check_maturity()) matureList.push_back(edgeID);
            }
        }
    }

    uint64_t collect_counter(ui edgeID) {
        uint64_t counter = 0;
        for (ui i = 0; i < h2_size(edgeID); i++) {
            int BiD = H2_bid(edgeID, i);
            if (BiD == -1) continue;
            counter += (two_blooms[BiD].counter - H2_cnt(edgeID, i));
        }
        for (ui i = 0; i < h3_size(edgeID); i++) {
            if (H3_count(edgeID, i) == 0) continue;
            int BiD = H3_bid(edgeID, i);
            counter += H3_count(edgeID, i) * (three_blooms[BiD].counter - H3_cnt(edgeID, i));
        }

        counter -= edges[edgeID].balance;
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
        const uint64_t requireSupport = E.five_cycle_support;
        const uint64_t extraCounter = extraBloom.counter - E.extraBloom_cnt;
        const uint64_t lhs = counterSum + extraCounter + E.delta;

        if (lhs >= requireSupport) {
            E.isPeel = true;
            peelList.push_back(edgeID);
            return;
        }

        const uint64_t trackValue64 = requireSupport - lhs;
        E.five_cycle_support = trackValue64;
        E.extraBloom_cnt = extraBloom.counter;

        const uint64_t old_slack = E.get_slack_value();
        const uint32_t old_bucket2 = log2_32(old_slack);

        E.compute_slack_value();

        if (E.isDT) {
            const uint64_t new_slack = E.get_slack_value();
            const uint32_t new_bucket2 = log2_32(new_slack);

            if (old_bucket2 != new_bucket2) {
                const ui h2sz = h2_size(edgeID);
                for (ui i = 0; i < h2sz; ++i) {
                    const int twobloomID = H2_bid(edgeID, i);
                    if (twobloomID == -1) continue;

                    const pair_t rev = H2_rev(edgeID, i);
                    remove_edge_from_2bloom_by_index(twobloomID, rev);

                    const pair_t idx = two_blooms[twobloomID].add_member_edge2(new_bucket2, edgeID, i, edges);
                    H2_rev(edgeID, i) = idx;
                    H2_cnt(edgeID, i) = two_blooms[twobloomID].counter;
                }
            } else {
                const ui h2sz = h2_size(edgeID);
                for (ui i = 0; i < h2sz; ++i) {
                    const int twobloomID = H2_bid(edgeID, i);
                    if (twobloomID == -1) continue;
                    H2_cnt(edgeID, i) = two_blooms[twobloomID].counter;
                }
            }
        } else {
            const ui h2sz = h2_size(edgeID);
            for (ui i = 0; i < h2sz; ++i) {
                const int twobloomID = H2_bid(edgeID, i);
                if (twobloomID == -1) continue;

                const pair_t rev = H2_rev(edgeID, i);
                remove_edge_from_2bloom_by_index(twobloomID, rev);

                const pair_t idx = two_blooms[twobloomID].add_member_edge(edgeID, i, edges);
                H2_rev(edgeID, i) = idx;
            }
        }

        const ui h3sz = h3_size(edgeID);
        if (!E.isDT) {
            for (ui i = 0; i < h3sz; ++i) {
                if (H3_count(edgeID, i) == 0) continue;
                const int threebloomID = H3_bid(edgeID, i);

                const pair_t rev = H3_rev(edgeID, i);
                remove_edge_from_3bloom_by_index(threebloomID, rev);

                const pair_t idx = three_blooms[threebloomID].add_member_edge(edgeID, i, edges);
                H3_rev(edgeID, i) = idx;
            }
        } else {
            for (ui i = 0; i < h3sz; ++i) {
                if (H3_count(edgeID, i) == 0) continue;
                const int threebloomID = H3_bid(edgeID, i);

                const int old_bucket3 = H3_rev(edgeID, i).first;
                const uint64_t hostCnt = H3_count(edgeID, i);
                uint64_t ratio = (hostCnt > 0) ? (E.slackValue / hostCnt) : E.slackValue;
                if (ratio == 0) ratio = 1;
                const uint32_t new_bucket3 = log2_32(ratio);

                if (old_bucket3 != (int)new_bucket3) {
                    const pair_t rev = H3_rev(edgeID, i);
                    remove_edge_from_3bloom_by_index(threebloomID, rev);

                    const pair_t idx = three_blooms[threebloomID].add_member_edge2(new_bucket3, edgeID, i, edges);
                    H3_rev(edgeID, i) = idx;
                }
                H3_cnt(edgeID, i) = three_blooms[threebloomID].counter;
            }
        }

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

        visitedEdge = 0;
        edgeToPeel = (unsigned int)(edges.size() - zeronode);
        checknumber += zeronode;
        five_cycle_decomposition_number.assign(edges.size(), 0);

        if (touched_epoch.size() != edges.size()) touched_epoch.assign(edges.size(), 0);
        if (checked_epoch.size() != edges.size()) checked_epoch.assign(edges.size(), 0);
        edge_processed.assign(edges.size(), 0);

        checkoutarray_buf.clear();
        mature_buf.clear();
        checkoutarray_buf.reserve(1 << 16);
        mature_buf.reserve(1 << 16);

        while (visitedEdge < edgeToPeel) {
            if (peelList.empty()) {
                extraBloom.send_value_to_member(matureList, peelList, edges);
                for (ui edgeID : matureList) {
                    check_mature_edge(edgeID, peelList);
                }
                matureList.clear();
            } else {
                visitedEdge += (ui)peelList.size();
                peel_edge(peelList, peelListTmp);
                peelList.swap(peelListTmp);
                peelListTmp.clear();
            }
        }

        std::cout << std::fixed << std::setprecision(6)
                  << "Bitruss decomposition time:\t" << get_current_time() - start
                  << "sec\n";
    }

    void peel_edge(vector<ui>& edgeList, vector<ui>& peelList) {
        twobloom_drive.clear();

        if (touched_epoch.size() != edges.size()) touched_epoch.assign(edges.size(), 0);
        ++epoch_touch;
        if (epoch_touch == 0) {
            std::fill(touched_epoch.begin(), touched_epoch.end(), 0);
            epoch_touch = 1;
        }

        checkoutarray_buf.clear();

        for (ui edgeID : edgeList) {
            Edge &peelEdge = edges[edgeID];
            five_cycle_decomposition_number[edgeID] = (int)extraBloom.counter;

            pair_t index = peelEdge.get_reverse_index_in_extra_bloom();
            remove_edge_from_extra_bloom_by_index(index);

            for (ui i = 0; i < h2_size(edgeID); i++) {
                int twoBloomID = H2_bid(edgeID, i);
                if (twoBloomID == -1) continue;

                const ui twinEdgeID = (ui)H2_twin(edgeID, i);
                const ui indexInTwinEdge = (ui)H2_twinidx(edgeID, i);
                pair_t reverseIndex = H2_rev(edgeID, i);

                TwoBloom   &current2Bloom = two_blooms[twoBloomID];
                ThreeBloom &current3Bloom = three_blooms[twoBloomID];
                const uint64_t totalthreepath = current3Bloom.total_paths;

                Edge &twinedge = edges[twinEdgeID];

                remove_edge_from_2bloom_by_index(twoBloomID, reverseIndex);

                pair_t twinedgeindexInHostBloom = H2_rev(twinEdgeID, indexInTwinEdge);

                if (!twinedge.isDT) {
                    twinedge.accumulate_value(totalthreepath);
                } else {
                    twinedge.accumulate_value(
                        totalthreepath + current2Bloom.counter - H2_cnt(twinEdgeID, indexInTwinEdge)
                    );
                }

                if (H2_has_same3(edgeID, i)) {
                    int host3idx = H2_same3(edgeID, i);
                    twinedge.decrease_value(H3_count(edgeID, (ui)host3idx));
                    (void)current3Bloom;
                    bump_balance_incident_alive_paths(twoBloomID, (int)edgeID, (int)edgeID);
                }

                remove_edge_from_2bloom_by_index(twoBloomID, twinedgeindexInHostBloom);
                remove_2bloom_from_edge_by_index(twinEdgeID, indexInTwinEdge);

                current2Bloom.total_paths--;

                if (H2_has_same3(twinEdgeID, indexInTwinEdge)) {
                    int edgethreeindex = H2_same3(twinEdgeID, indexInTwinEdge);
                    uint64_t host3Cnt = H3_count(twinEdgeID, (ui)edgethreeindex);

                    twinedge.decrease_value(host3Cnt);
                    twinedge.balance += host3Cnt;

                    (void)current3Bloom;
                    bump_balance_incident_alive_paths(twoBloomID, (int)twinEdgeID, (int)twinEdgeID);

                    H2_same3(twinEdgeID, indexInTwinEdge) = -1;
                    H3_same2(twinEdgeID, (ui)edgethreeindex) = -1;
                }

                mature_buf.clear();
                send_value_to_3bloom(twoBloomID, mature_buf, peelList);

                bump_epoch_check();
                for (ui tmpedgeID : mature_buf) {
                    check_mature_edge_list_dedup(tmpedgeID, peelList);
                }

                if (twinedge.check_maturity()) {
                    check_mature_edge(twinEdgeID, peelList);
                }
            }

            for (ui i = 0; i < h3_size(edgeID); i++) {
                if (H3_count(edgeID, i) == 0) continue;
                int threeBloomID = H3_bid(edgeID, i);

                ThreeBloom &current3Bloom = three_blooms[threeBloomID];
                TwoBloom   &current2Bloom = two_blooms[threeBloomID];

                pair_t reverseIndex = H3_rev(edgeID, i);
                remove_edge_from_3bloom_by_index(threeBloomID, reverseIndex);

                uint64_t currentcnt3 = H3_count(edgeID, i);

                kill_incident_alive_paths_and_update(
                    current3Bloom,
                    current2Bloom,
                    (int)edgeID,
                    threeBloomID,
                    checkoutarray_buf
                );

                current3Bloom.total_paths -= currentcnt3;

                mature_buf.clear();
                send_value_to_2bloom(threeBloomID, currentcnt3, mature_buf, peelList);

                bump_epoch_check();
                for (ui tmpedgeID : mature_buf) {
                    check_mature_edge_list_dedup(tmpedgeID, peelList);
                }
            }

            edge_processed[(size_t)edgeID] = 1;
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

    inline void unpack_bid_key(int bid, int &x, int &y) const {
        const uint64_t key = bid2key[(size_t)bid];
        x = (int)(key >> 32);
        y = (int)(key & 0xffffffffull);
    }

    inline int get_oriented_out_eid(int tail, int head) const {
        if (tail < 0 || head < 0 || tail >= n || head >= n) return -1;
        if (!(peel_rank[tail] < peel_rank[head])) return -1;
        return get_edge_id_fast(tail, head);
    }

    inline uint32_t get_h3_index(int eid, int bid) const {
        if (eid < 0 || bid < 0 || (size_t)eid >= edges.size()) return std::numeric_limits<uint32_t>::max();
        const uint32_t off = edges[(size_t)eid].h3_off;
        uint32_t l = 0, r = edges[(size_t)eid].h3_len;
        while (l < r) {
            uint32_t mid = l + ((r - l) >> 1);
            int mbid = h3_bloom[(size_t)off + mid];
            if (mbid < bid) l = mid + 1;
            else r = mid;
        }
        if (l < edges[(size_t)eid].h3_len && h3_bloom[(size_t)off + l] == bid) return l;
        return std::numeric_limits<uint32_t>::max();
    }




    inline bool path_alive_except_query(int queryEdgeID, int e1, int e2, int e3) const {
        if (e1 < 0 || e2 < 0 || e3 < 0) return false;
        if (e1 != queryEdgeID && edge_processed[(size_t)e1]) return false;
        if (e2 != queryEdgeID && edge_processed[(size_t)e2]) return false;
        if (e3 != queryEdgeID && edge_processed[(size_t)e3]) return false;
        return true;
    }

    template <class Fn>
    inline bool emit_incident_path_if_alive(int queryEdgeID,
                                            int e1,
                                            int e2,
                                            int e3,
                                            uint64_t &emitted,
                                            uint64_t limit,
                                            Fn&& fn) {
        if (e1 == e2 || e1 == e3 || e2 == e3) return false;
        if (queryEdgeID != e1 && queryEdgeID != e2 && queryEdgeID != e3) return false;
        if (!path_alive_except_query(queryEdgeID, e1, e2, e3)) return false;
        fn(e1, e2, e3);
        ++emitted;
        return emitted >= limit;
    }

    template <class Fn>
    void enumerate_alive_incident_3paths(int edgeID, int bid, Fn&& fn) {
        if (edgeID < 0 || bid < 0) return;
        const uint32_t hidx = get_h3_index(edgeID, bid);
        if (hidx == std::numeric_limits<uint32_t>::max()) return;
        const uint64_t limit = H3_count((ui)edgeID, hidx);
        if (limit == 0) return;
        uint64_t emitted = 0;

        int x, y;
        unpack_bid_key(bid, x, y);
        const Edge &E = edges[(size_t)edgeID];
        const int u = E.u;
        const int v = E.v;

        // Role 1: the query edge is the raw middle edge b->c.
        {
            const int b = u, c = v, e_bc = edgeID;
            int a = x, d = y;
            if (a != c && d != b && d != a) {
                const int e_ab = get_oriented_out_eid(b, a);
                const int e_cd = get_oriented_out_eid(c, d);
                if (e_ab >= 0 && e_cd >= 0 &&
                    emit_incident_path_if_alive(edgeID, e_ab, e_bc, e_cd, emitted, limit, fn)) return;
            }

            a = y; d = x;
            if (a != c && d != b && d != a) {
                const int e_ab = get_oriented_out_eid(b, a);
                const int e_cd = get_oriented_out_eid(c, d);
                if (e_ab >= 0 && e_cd >= 0 &&
                    emit_incident_path_if_alive(edgeID, e_ab, e_bc, e_cd, emitted, limit, fn)) return;
            }
        }

        int low = u, high = v;
        if (peel_rank[(size_t)low] > peel_rank[(size_t)high]) std::swap(low, high);

        // Role 2: the query edge is e_ab. Enumerate raw middle edges b->c.
        {
            const int b = low, a = high;
            int d = -1;
            if (x == a) d = y;
            else if (y == a) d = x;

            if (d >= 0 && d != b && d != a) {
                const uint32_t begin = raw_out_off[(size_t)b];
                const uint32_t end = raw_out_off[(size_t)b + 1u];
                for (uint32_t pos = begin; pos < end; ++pos) {
                    const int e_bc = (int)raw_out_eid[pos];
                    const int c = edges[(size_t)e_bc].v;
                    if (c == a) continue;
                    const int e_cd = get_oriented_out_eid(c, d);
                    if (e_cd < 0) continue;
                    if (emit_incident_path_if_alive(edgeID, edgeID, e_bc, e_cd, emitted, limit, fn)) return;
                }
            }
        }

        // Role 3: the query edge is e_cd. Enumerate raw middle edges b->c.
        {
            const int c = low, d = high;
            int a = -1;
            if (x == d) a = y;
            else if (y == d) a = x;

            if (a >= 0 && a != c && a != d) {
                const uint32_t begin = raw_in_off[(size_t)c];
                const uint32_t end = raw_in_off[(size_t)c + 1u];
                for (uint32_t pos = begin; pos < end; ++pos) {
                    const int e_bc = (int)raw_in_eid[pos];
                    const int b = edges[(size_t)e_bc].u;
                    if (b == d || b == a) continue;
                    const int e_ab = get_oriented_out_eid(b, a);
                    if (e_ab < 0) continue;
                    if (emit_incident_path_if_alive(edgeID, e_ab, e_bc, edgeID, emitted, limit, fn)) return;
                }
            }
        }
    }

    void bump_balance_incident_alive_paths(int bid, int edgeID, int excludeE) {
        enumerate_alive_incident_3paths(edgeID, bid, [&](int e1, int e2, int e3) {
            if (e1 != excludeE) edges[(size_t)e1].balance++;
            if (e2 != excludeE) edges[(size_t)e2].balance++;
            if (e3 != excludeE) edges[(size_t)e3].balance++;
        });
    }

    void kill_incident_alive_paths_and_update(
        ThreeBloom &B,
        TwoBloom   &TB,
        int globalPeelEdgeID,
        int threeBloomID,
        std::vector<int> &checkoutarray
    ) {
        enumerate_alive_incident_3paths(globalPeelEdgeID, threeBloomID, [&](int e1, int e2, int e3) {
            process_one_edge_after_kill(B, TB, globalPeelEdgeID, threeBloomID,
                                        e1, e2, e3, checkoutarray);
            process_one_edge_after_kill(B, TB, globalPeelEdgeID, threeBloomID,
                                        e2, e1, e3, checkoutarray);
            process_one_edge_after_kill(B, TB, globalPeelEdgeID, threeBloomID,
                                        e3, e1, e2, checkoutarray);
        });
    }

    void process_one_edge_after_kill(
        ThreeBloom &B,
        TwoBloom   &TB,
        int globalPeelEdgeID,
        int threeBloomID,
        int tempedgeid,
        int eX,
        int eY,
        std::vector<int> &checkoutarray
    ) {
        if (tempedgeid == globalPeelEdgeID || tempedgeid < 0) return;

        if (touched_epoch[(size_t)tempedgeid] != epoch_touch) {
            touched_epoch[(size_t)tempedgeid] = epoch_touch;
            checkoutarray.push_back(tempedgeid);
        }

        const uint32_t local_idx = get_h3_index(tempedgeid, threeBloomID);
        if (local_idx == std::numeric_limits<uint32_t>::max()) return;
        Edge &te = edges[(size_t)tempedgeid];
        if (local_idx >= te.h3_len) return;
        const size_t pos = (size_t)te.h3_off + local_idx;
        const uint64_t old_count = get_counter_slot(1, pos);
        if (old_count == 0) return;

        if (!te.isDT) {
            te.accumulate_value(TB.total_paths);
        } else {
            const uint64_t last = get_counter_slot(2, pos);
            te.accumulate_value(TB.total_paths + old_count * (B.counter - last));
            set_counter_slot(2, pos, B.counter);
        }

        const uint64_t new_count = old_count - 1ull;
        set_counter_slot(1, pos, new_count);
        if (new_count == 0) {
            const pair_t rev = h3_rev[pos];
            remove_edge_from_3bloom_by_index(threeBloomID, rev);
            remove_3bloom_from_edge_by_index((ui)tempedgeid, local_idx);
        }

        if (h3_same2[pos] >= 0) {
            const int edgetwoindex = h3_same2[pos];
            te.decrease_value(1);
            if (eX != globalPeelEdgeID && eX != tempedgeid) edges[(size_t)eX].decrease_value(1);
            if (eY != globalPeelEdgeID && eY != tempedgeid) edges[(size_t)eY].decrease_value(1);
            te.balance += 1;

            if (new_count == 0) {
                h3_same2[pos] = -1;
                if (edgetwoindex >= 0 && edgetwoindex < (int)te.h2_len)
                    h2_same3[(size_t)te.h2_off + (ui)edgetwoindex] = -1;
            }

            if (edgetwoindex >= 0 && edgetwoindex < (int)te.h2_len) {
                const int twinedgeid = h2_twin[(size_t)te.h2_off + (ui)edgetwoindex];
                edges[(size_t)twinedgeid].balance += 1;
            }
        }
    }

    void remove_edge_from_2bloom_by_index(int bloomID, pair_t index) {
        if (bloomID < 0) return;
        affect_edge_t affectEdgeInfo = two_blooms[bloomID].remove_member_by_index(index);
        if (affectEdgeInfo.first == -1) return;
        ui affectEdgeID = (ui)affectEdgeInfo.first;
        ui affectIndex = affectEdgeInfo.second;
        H2_rev(affectEdgeID, affectIndex) = index;
    }

    void remove_edge_from_3bloom_by_index(int bloomID, pair_t index) {
        if (bloomID < 0) return;
        affect_edge_t affectEdgeInfo = three_blooms[bloomID].remove_member_by_index(index);
        if (affectEdgeInfo.first == -1) return;
        ui affectEdgeID = (ui)affectEdgeInfo.first;
        ui affectIndex = affectEdgeInfo.second;
        H3_rev(affectEdgeID, affectIndex) = index;
    }

    void remove_edge_from_extra_bloom_by_index(pair_t index) {
        int affectEdgeID = extraBloom.remove_member_by_index_id_only(index);
        if (affectEdgeID == -1) return;
        edges[(ui)affectEdgeID].set_reverse_index_in_extra_bloom(index);
    }

    void remove_2bloom_from_edge_by_index(ui edgeID, ui index) {
        if (index >= h2_size(edgeID)) return;
        H2_bid(edgeID, index) = -1;
        edges[edgeID].hostbloomnumber--;
    }

    void remove_3bloom_from_edge_by_index(ui edgeID, ui index) {
        if (index >= h3_size(edgeID)) return;
        H3_count(edgeID, index) = 0;
        H3_rev(edgeID, index) = {-1, -1};
        edges[edgeID].hostbloomnumber--;
    }

    void build_blooms_strict_pipeline_with_DT() {
        double start = get_current_time();

        collect_2paths_grouped();
        std::cout << std::fixed << std::setprecision(6)
                  << " finish 2-path construction time:\t" << (get_current_time() - start)
                  << "sec\n";

        release_after_2path_collection();

        build_twopath_edge_membership();
        std::cout << std::fixed << std::setprecision(6)
                  << " finish 2-path membership time:\t" << (get_current_time() - start)
                  << "sec\n";

        collect_3paths_count_only();
        std::cout << std::fixed << std::setprecision(6)
                  << " finish 3-path count/dedup time:\t" << (get_current_time() - start)
                  << "sec\n";

        build_blooms_strict_matched();
        std::cout << std::fixed << std::setprecision(6)
                  << " finish Bloom construction time:\t" << (get_current_time() - start)
                  << "sec\n";

        compute_five_cycle_supports();
        std::cout << std::fixed << std::setprecision(6)
                  << " finish 5-cycle support time:\t" << (get_current_time() - start)
                  << "sec\n";

        build_distributed_tracking();
        std::cout << std::fixed << std::setprecision(6)
                  << " finish distributed tracking time:\t" << (get_current_time() - start)
                  << "sec\n";

        std::size_t rss_bytes = getCurrentRSSBytes();
        double rss_gib = rss_bytes / (1024.0 * 1024.0 * 1024.0);
        double rss_gb  = rss_bytes / 1e9;
        cerr << "[MEM] Before decomposition: "
             << fixed << setprecision(3)
             << rss_gib << " GiB  (~" << rss_gb << " GB)\n";

        five_cycle_decomposition();
        std::cout << std::fixed << std::setprecision(6)
                  << " finish cycle decomposition time:\t" << (get_current_time() - start)
                  << "sec\n";
    }

    inline int get_edge_id(int u, int v) const {
        uint64_t k = pack_edge((uint32_t)u, (uint32_t)v);
        const uint32_t x = edgeId.get(k, std::numeric_limits<uint32_t>::max());
        return (x == std::numeric_limits<uint32_t>::max()) ? -1 : (int)x;
    }

    inline int get_edge_id_fast(int u, int v) const {
        uint64_t k = pack_edge((uint32_t)u, (uint32_t)v);
        const uint32_t x = edgeId.get(k, std::numeric_limits<uint32_t>::max());
        return (x == std::numeric_limits<uint32_t>::max()) ? -1 : (int)x;
    }

    inline int bloom_count_2() const { return (int)two_blooms.size(); }
    inline int bloom_count_3() const { return (int)three_blooms.size(); }

    inline const ThreeBloom& get_bloom(int bid) const { return three_blooms[bid]; }
    inline const TwoBloom& get_tbloom(int bid) const { return two_blooms[bid]; }
    inline uint64_t bloom_key(int bid) const { return bid2key[bid]; }

    inline int try_get_bid(int x, int y) const {
        uint64_t key = pack_edge((uint32_t)x, (uint32_t)y);
        const uint32_t bid = key2bid.get(key, std::numeric_limits<uint32_t>::max());
        return (bid == std::numeric_limits<uint32_t>::max()) ? -1 : (int)bid;
    }

    inline uint64_t edge_5cycle(int eid) const {
        return (eid < 0 || (size_t)eid >= edge_5cycle_support.size()) ? 0ull
                                                                      : edge_5cycle_support[eid];
    }

public:
    int n{0};
    ui visitedEdge{0};
    int zeronode{0};
    ExtraBloom extraBloom;

    vector<pair<int,int>> edges_raw;
    FlatU64U32Map id2idx;
    vector<int>           idx2id;

    uint64_t maxsupport{0};
    vector<Edge> edges;
    vector<int> twobloom_fast_check;
    vector<int> twobloom_drive;
    int checknumber{0};

    FlatU64U32Map edgeId;

    vector<vector<int>> adj;
    vector<uint32_t> out_off;
    vector<Next> out_data;
    vector<uint32_t> adj_eid_off;
    vector<Next> adj_eid_data;
    vector<uint32_t> raw_out_off;
    vector<uint32_t> raw_out_eid;
    vector<uint32_t> raw_in_off;
    vector<uint32_t> raw_in_eid;
    vector<int> core;
    vector<int> peel_rank;

    unsigned int edgeToPeel{0};
    vector<int> five_cycle_decomposition_number;

    std::vector<uint32_t> touched_epoch;
    uint32_t epoch_touch{1};
    std::vector<int> checkoutarray_buf;
    std::vector<ui>  mature_buf;
    std::vector<uint32_t> checked_epoch;
    uint32_t epoch_check{1};
    std::vector<uint8_t> edge_processed; // true only after peel_edge has processed this edge's incident 3-paths

    inline void bump_epoch_check() {
        ++epoch_check;
        if (epoch_check == 0) {
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

    FlatU64U32Map key2bid;
    vector<uint64_t> bid2key;

    vector<TwoBloom> two_blooms;
    vector<ThreeBloom> three_blooms;
    // Compact edge-side CSR storage.  This removes 10+ std::vector objects from every Edge.
    vector<int>      h2_bloom;
    vector<pair_t>   h2_rev;
    vector<int>      h2_twin;
    vector<int>      h2_twin_index;
    bool h2_cnt_use64{false};
    vector<uint32_t> h2_cnt32;
    vector<uint64_t> h2_cnt64;
    vector<int>      h2_same3;

    vector<int>      h3_bloom;
    vector<pair_t>   h3_rev;
    bool h3_count_use64{false};
    vector<uint32_t> h3_count32;
    vector<uint64_t> h3_count64;
    bool h3_cnt_use64{false};
    vector<uint32_t> h3_cnt32;
    vector<uint64_t> h3_cnt64;
    vector<int>      h3_same2;

    vector<uint64_t> edge_dedup;
    vector<uint64_t> edge_5cycle_support;

private:
    FlatU64U32Map key2tmpid;
    vector<uint64_t> tmpid2key;
    vector<vector<TwoPathPair>> twopaths_by_key;
    vector<char> valid_tmp;
    vector<vector<uint32_t>> twopath_edge_membership;
    vector<uint64_t> threepath_count_by_tid;

    inline int get_or_create_tmpid(uint64_t key) {
        const uint32_t MISS = std::numeric_limits<uint32_t>::max();
        uint32_t old = key2tmpid.get(key, MISS);
        if (old != MISS) return (int)old;
        uint32_t tid = (uint32_t)tmpid2key.size();
        bool inserted = false;
        key2tmpid.get_or_insert(key, tid, inserted);
        tmpid2key.push_back(key);
        twopaths_by_key.emplace_back();
        return (int)tid;
    }

    static constexpr size_t SCRATCH_CAP_BYTES = (256ull << 20);

    template <class T>
    static inline void reserve_capped(std::vector<T>& v,
                                      size_t want_elems,
                                      size_t cap_bytes = SCRATCH_CAP_BYTES) {
        size_t cap_elems = cap_bytes / sizeof(T);
        size_t req = std::min(want_elems, cap_elems);
        if (v.capacity() < req) v.reserve(req);
    }
};
