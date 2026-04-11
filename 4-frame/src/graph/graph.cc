#include "graph/graph.h"

#include <queue>
#include <fstream>
#include <unistd.h>
#include <iostream>
#include <iomanip>
#include <vector>
#include <string>
#include <sstream>
#include <unordered_map>
#include <unordered_set>
#include <algorithm>
#include <cstring>
#include <utility>
#include <cstdint>
#include <cstdlib>

namespace {

struct PairHash {
    size_t operator()(const std::pair<int, int>& p) const noexcept {
        uint64_t x = static_cast<uint64_t>(static_cast<uint32_t>(p.first));
        uint64_t y = static_cast<uint64_t>(static_cast<uint32_t>(p.second));
        x ^= x >> 33; x *= 0xff51afd7ed558ccdULL; x ^= x >> 33; x *= 0xc4ceb9fe1a85ec53ULL; x ^= x >> 33;
        y ^= y >> 33; y *= 0xff51afd7ed558ccdULL; y ^= y >> 33; y *= 0xc4ceb9fe1a85ec53ULL; y ^= y >> 33;
        return static_cast<size_t>(x ^ (y + 0x9e3779b97f4a7c15ULL + (x << 6) + (x >> 2)));
    }
};

struct PairHashLL {
    size_t operator()(const std::pair<long long, long long>& p) const noexcept {
        uint64_t x = static_cast<uint64_t>(p.first);
        uint64_t y = static_cast<uint64_t>(p.second);
        x ^= x >> 33; x *= 0xff51afd7ed558ccdULL; x ^= x >> 33; x *= 0xc4ceb9fe1a85ec53ULL; x ^= x >> 33;
        y ^= y >> 33; y *= 0xff51afd7ed558ccdULL; y ^= y >> 33; y *= 0xc4ceb9fe1a85ec53ULL; y ^= y >> 33;
        return static_cast<size_t>(x ^ (y + 0x9e3779b97f4a7c15ULL + (x << 6) + (x >> 2)));
    }
};

// one 2-path in a bloom
struct PathInfo {
    ui e1;   // edge (u, v)
    ui e2;   // edge (v, w)
    bool alive;
};

struct LocalBloomState {
    std::vector<PathInfo> paths;                 // all 2-paths in this bloom
    int alivePathCount = 0;                      // number of alive 2-paths
    std::unordered_map<ui, int> edgeToPathIdx;   // in one bloom, an edge appears in at most one 2-path
};

struct LocalState {
    std::vector<int> support;                    // current support
    std::vector<char> peeled;                    // whether edge has been peeled
    std::vector<std::vector<int>> edgeToBlooms;  // blooms containing each edge
    std::vector<LocalBloomState> blooms;         // local bloom states for direct support update
};

static std::unordered_map<Graph*, LocalState> gLocalState;

} // namespace

Graph::Graph(const std::string path) {
    std::cout << "path: " << path << std::endl;

    inputGraphPath = path;

    bloom = nullptr;
    degree = nullptr;
    nbrAll = nullptr;
    nbr = nullptr;
    edge = nullptr;
    bitrussNumber = nullptr;
    bloomCount = 0;
    edgeToPeel = 0;

    uniqueEdgesCompressedInputOrder.clear();
    uniqueEdgesOriginalInputOrder.clear();
    uniqueEdgeFirstLineNo.clear();
    initialButterflySupport.clear();

    std::ifstream fin(path);
    if (!fin.is_open()) {
        std::cerr << "Failed to open input graph file: " << path << std::endl;
        std::exit(EXIT_FAILURE);
    }

    // Read general graph txt: one undirected edge u v per line.
    // 1) ignore self-loops
    // 2) deduplicate
    // 3) preserve the first-appearance order of unique edges in the input
    std::unordered_map<long long, int> vid;

    std::unordered_set<std::pair<long long, long long>, PairHashLL> dedupRaw;
    dedupRaw.reserve(1 << 20);

    auto get_vid = [&](long long x) -> int {
        auto it = vid.find(x);
        if (it != vid.end()) return it->second;
        int id = static_cast<int>(vid.size());
        vid.emplace(x, id);
        return id;
    };

    long long a_raw, b_raw;
    long long lineNo = 0;

    while (fin >> a_raw >> b_raw) {
        ++lineNo;

        if (a_raw == b_raw) continue; // ignore self-loop

        long long x_raw = a_raw;
        long long y_raw = b_raw;
        if (x_raw > y_raw) std::swap(x_raw, y_raw);

        std::pair<long long, long long> rawEdge = {x_raw, y_raw};

        if (dedupRaw.find(rawEdge) != dedupRaw.end()) {
            continue; // duplicate edge: keep only the first occurrence
        }

        dedupRaw.insert(rawEdge);

        int a = get_vid(x_raw);
        int b = get_vid(y_raw);
        if (a > b) std::swap(a, b);

        uniqueEdgesCompressedInputOrder.push_back({a, b});
        uniqueEdgesOriginalInputOrder.push_back(rawEdge);
        uniqueEdgeFirstLineNo.push_back(lineNo);
    }
    fin.close();

    n = static_cast<int>(vid.size());
    m = static_cast<long long>(uniqueEdgesCompressedInputOrder.size());

    std::vector<std::vector<int>> adj(n);
    for (const auto &e0 : uniqueEdgesCompressedInputOrder) {
        int u = e0.first;
        int v = e0.second;
        adj[u].push_back(v);
        adj[v].push_back(u);
    }

    degree = new int[n];
    long long totalNbr = 0;
    for (int u = 0; u < n; ++u) {
        std::sort(adj[u].begin(), adj[u].end());
        adj[u].erase(std::unique(adj[u].begin(), adj[u].end()), adj[u].end());
        degree[u] = static_cast<int>(adj[u].size());
        totalNbr += degree[u];
    }

    nbrAll = new int[totalNbr];
    nbr = new int*[n];

    long long ptr = 0;
    for (int u = 0; u < n; ++u) {
        nbr[u] = nbrAll + ptr;
        for (int v : adj[u]) nbrAll[ptr++] = v;
    }

    edge = new Edge[m];
    bitrussNumber = new int[m];
    std::memset(bitrussNumber, 0, sizeof(int) * m);

    initialButterflySupport.resize(m, 0);

    edgeToPeel = static_cast<ui>(m);
    for (ui i = 0; i < m; i++) {
        edge[i].id = i; // edgeID matches deduplicated input order
    }

    std::cout << "n = " << n << ", m = " << m << std::endl;
}

Graph::~Graph() {
    gLocalState.erase(this);

    delete[] degree;
    degree = nullptr;

    delete[] nbrAll;
    nbrAll = nullptr;

    delete[] nbr;
    nbr = nullptr;

    delete[] edge;
    edge = nullptr;

    delete[] bitrussNumber;
    bitrussNumber = nullptr;

    delete[] bloom;
    bloom = nullptr;
}

void Graph::construct_index() {
    double start = get_current_time();

    auto higher_priority = [&](int x, int y) -> bool {
        if (degree[x] != degree[y]) return degree[x] > degree[y];
        return x > y;
    };

    auto make_key = [&](int a, int b) -> unsigned long long {
        if (a > b) std::swap(a, b);
        return (static_cast<unsigned long long>(static_cast<unsigned int>(a)) << 32) |
               static_cast<unsigned int>(b);
    };

    struct AdjItem {
        int to;
        ui eid;
    };

    struct TwoPathRec {
        unsigned long long key; // encoded (u,w), with u < w
        ui e1;
        ui e2;
    };

    // ------------------------------------------------------------
    // Step 1. Build adjacency lists that directly store edge IDs.
    // This avoids any (u,v)->eid hash lookup during 2-path enumeration.
    // ------------------------------------------------------------
    std::vector<std::vector<AdjItem>> adjE(n);
    adjE.reserve(n);
    for (int u = 0; u < n; ++u) {
        adjE[u].reserve(static_cast<size_t>(degree[u]));
    }

    for (ui eid = 0; eid < static_cast<ui>(m); ++eid) {
        int u = uniqueEdgesCompressedInputOrder[eid].first;
        int v = uniqueEdgesCompressedInputOrder[eid].second;
        edge[eid].id = eid;

        adjE[u].push_back({v, eid});
        adjE[v].push_back({u, eid});
    }

    for (int u = 0; u < n; ++u) {
        std::sort(adjE[u].begin(), adjE[u].end(),
                  [](const AdjItem &a, const AdjItem &b) {
                      return a.to < b.to;
                  });
    }

    // ------------------------------------------------------------
    // Step 2. Enumerate all priority 2-paths u-v-w.
    // Conditions:
    //   priority(u) > priority(v)
    //   priority(u) > priority(w)
    // Then group later by key=(u,w).
    // ------------------------------------------------------------
    std::vector<TwoPathRec> allPaths;
    allPaths.reserve(static_cast<size_t>(m) * 2);

    for (int v = 0; v < n; ++v) {
        const auto &adjV = adjE[v];
        const int dv = static_cast<int>(adjV.size());

        for (int i = 0; i < dv; ++i) {
            const int u = adjV[i].to;
            if (!higher_priority(u, v)) continue;

            const ui e_uv = adjV[i].eid;

            for (int j = 0; j < dv; ++j) {
                if (j == i) continue;

                const int w = adjV[j].to;
                if (!higher_priority(u, w)) continue;

                const ui e_vw = adjV[j].eid;
                allPaths.push_back({make_key(u, w), e_uv, e_vw});
            }
        }
    }

    // ------------------------------------------------------------
    // Step 3. Sort by bloom key=(u,w), then scan each group.
    // A group with K>=2 paths forms one valid bloom.
    // Each path contributes K-1 butterflies to both its edges.
    // ------------------------------------------------------------
    std::sort(allPaths.begin(), allPaths.end(),
              [](const TwoPathRec &a, const TwoPathRec &b) {
                  if (a.key != b.key) return a.key < b.key;
                  if (a.e1 != b.e1) return a.e1 < b.e1;
                  return a.e2 < b.e2;
              });

    LocalState &state = gLocalState[this];
    state = LocalState();
    state.support.assign(static_cast<size_t>(m), 0);
    state.peeled.assign(static_cast<size_t>(m), 0);
    state.edgeToBlooms.assign(static_cast<size_t>(m), {});

    bloomCount = 0;
    size_t ignoredSingletonBloom = 0;

    size_t L = 0;
    while (L < allPaths.size()) {
        size_t R = L + 1;
        while (R < allPaths.size() && allPaths[R].key == allPaths[L].key) ++R;

        const int K = static_cast<int>(R - L);
        if (K <= 1) {
            ++ignoredSingletonBloom;
            L = R;
            continue;
        }

        LocalBloomState lb;
        lb.paths.reserve(static_cast<size_t>(K));
        lb.alivePathCount = K;
        lb.edgeToPathIdx.reserve(static_cast<size_t>(K) * 2);

        const int bloomID = bloomCount++;

        for (int idx = 0; idx < K; ++idx) {
            const ui e1 = allPaths[L + idx].e1;
            const ui e2 = allPaths[L + idx].e2;

            lb.paths.push_back({e1, e2, true});

            // In one bloom, an edge appears in at most one 2-path.
            lb.edgeToPathIdx.emplace(e1, idx);
            lb.edgeToPathIdx.emplace(e2, idx);

            state.edgeToBlooms[e1].push_back(bloomID);
            state.edgeToBlooms[e2].push_back(bloomID);

            state.support[e1] += (K - 1);
            state.support[e2] += (K - 1);
        }

        state.blooms.push_back(std::move(lb));
        L = R;
    }

    for (ui i = 0; i < static_cast<ui>(m); ++i) {
        initialButterflySupport[i] = state.support[i];
    }

    bloom = nullptr;
    /*
    std::cout << std::fixed << std::setprecision(6)
              << "bloom construction time:\t" << get_current_time() - start
              << "sec\n";
              */
    std::cout << "valid blooms = " << bloomCount
              << ", ignored singleton blooms = " << ignoredSingletonBloom
              << std::endl;

    // adjacency no longer needed after index construction
    delete[] nbrAll;
    nbrAll = nullptr;

    delete[] nbr;
    nbr = nullptr;

    delete[] degree;
    degree = nullptr;


    //std::cout << std::fixed << std::setprecision(6)<< "Index construction time:\t" << get_current_time() - start<< "sec\n";
}

// ------------------------------------------------------------------
// The following functions are kept only to satisfy the class interface.
// They are not used in the new support-driven decomposition.
// ------------------------------------------------------------------
void Graph::remove_edge_from_bloom_by_index(int bloomID, pair_t index) {
    (void)bloomID;
    (void)index;
}

void Graph::remove_edge_from_extra_bloom_by_index(pair_t index) {
    (void)index;
}

void Graph::remove_bloom_from_edge_by_index(ui edgeID, ui index) {
    (void)edgeID;
    (void)index;
}

int Graph::collect_counter(ui edgeID) {
    (void)edgeID;
    return 0;
}

void Graph::check_mature_edge(ui edgeID, std::queue<ui> &peelList) {
    (void)edgeID;
    (void)peelList;
}

void Graph::peel_edge(ui edgeID, std::queue<ui> &peelList) {
    (void)edgeID;
    (void)peelList;
}

void Graph::bitruss_decomposition() {
    double start = get_current_time();
    std::cout << "bitruss decomposing..." << std::endl;

    LocalState &state = gLocalState[this];
    if (state.support.size() != static_cast<size_t>(m)) {
        std::cerr << "Internal error: local support array is not initialized." << std::endl;
        std::exit(EXIT_FAILURE);
    }

    int maxSupport = 0;
    for (ui i = 0; i < m; ++i) {
        if (state.support[i] > maxSupport) maxSupport = state.support[i];
    }

    // bucket-based peeling
    std::vector<std::vector<ui>> buckets(static_cast<size_t>(maxSupport + 1));
    for (ui i = 0; i < m; ++i) {
        buckets[state.support[i]].push_back(i);
    }

    auto push_to_bucket = [&](ui eid) {
        int s = state.support[eid];
        if (s < 0) s = 0;
        if (s >= static_cast<int>(buckets.size())) {
            buckets.resize(static_cast<size_t>(s + 1));
        }
        buckets[s].push_back(eid);
    };

    auto dec_support = [&](ui eid, int delta, int currentK) {
        if (delta <= 0) return;
        if (state.peeled[eid]) return;

        int oldSup = state.support[eid];
        int newSup = oldSup - delta;
        if (newSup < currentK) newSup = currentK;
        if (newSup == oldSup) return;

        state.support[eid] = newSup;
        push_to_bucket(eid);
    };

    ui visitedEdge = 0;

    for (int k = 0; k < static_cast<int>(buckets.size()); ++k) {
        size_t ptr = 0;
        while (ptr < buckets[k].size()) {
            ui edgeID = buckets[k][ptr++];
            if (state.peeled[edgeID]) continue;
            if (state.support[edgeID] != k) continue;

            state.peeled[edgeID] = 1;
            bitrussNumber[edgeID] = k;
            ++visitedEdge;

            const std::vector<int> hostBlooms = state.edgeToBlooms[edgeID];

            for (int bloomID : hostBlooms) {
                LocalBloomState &lb = state.blooms[bloomID];
                if (lb.alivePathCount <= 0) continue;

                auto it = lb.edgeToPathIdx.find(edgeID);
                if (it == lb.edgeToPathIdx.end()) continue;

                int pathIdx = it->second;
                if (pathIdx < 0 || pathIdx >= static_cast<int>(lb.paths.size())) continue;

                PathInfo &deadPath = lb.paths[pathIdx];
                if (!deadPath.alive) continue;

                ui twinEdgeID = (deadPath.e1 == edgeID ? deadPath.e2 : deadPath.e1);

                int otherPathCount = lb.alivePathCount - 1;

                dec_support(twinEdgeID, otherPathCount, k);

                for (int q = 0; q < static_cast<int>(lb.paths.size()); ++q) {
                    if (q == pathIdx) continue;
                    PathInfo &p = lb.paths[q];
                    if (!p.alive) continue;

                    dec_support(p.e1, 1, k);
                    dec_support(p.e2, 1, k);
                }

                deadPath.alive = false;
                lb.alivePathCount--;

                lb.edgeToPathIdx.erase(deadPath.e1);
                lb.edgeToPathIdx.erase(deadPath.e2);
            }
        }

        if (k + 1 >= static_cast<int>(buckets.size()) && visitedEdge < static_cast<ui>(m)) {
            buckets.resize(static_cast<size_t>(k + 2));
        }
    }

    if (visitedEdge != static_cast<ui>(m)) {
        for (ui i = 0; i < m; ++i) {
            if (!state.peeled[i]) {
                state.peeled[i] = 1;
                bitrussNumber[i] = state.support[i];
                ++visitedEdge;
            }
        }
    }
    /*

    std::cout << std::fixed << std::setprecision(6)
              << "Bitruss decomposition time:\t" << get_current_time() - start
              << "sec\n";
    */
}

void Graph::output_bitruss_number(std::string outputPath) {
    // This function is called after timing has already been printed.
    // So mapping back and outputting results do NOT affect the measured time.

    std::ofstream fout;
    std::string outFile = outputPath + ".bn.txt";
    fout.open(outFile, std::ios::out);

    if (!fout.is_open()) {
        std::cerr << "Failed to open output file: " << outFile << std::endl;
        return;
    }

    fout << "#edge_id\tfirst_occurrence_line\toriginal_u\toriginal_v\tinitial_support\tbitruss_number\n";
    for (ui i = 0; i < m; i++) {
        long long rawU = uniqueEdgesOriginalInputOrder[i].first;
        long long rawV = uniqueEdgesOriginalInputOrder[i].second;
        long long lineNo = uniqueEdgeFirstLineNo[i];
        fout << i << "\t"
             << lineNo << "\t"
             << rawU << "\t"
             << rawV << "\t"
             << initialButterflySupport[i] << "\t"
             << bitrussNumber[i] << "\n";
    }
    fout.close();

    std::cout << "Output written to: " << outFile << std::endl;
}