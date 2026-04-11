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

struct PathInfo {
    ui e1; // edge (u, v)
    ui e2; // edge (v, w)
};

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

    struct AdjEdge {
        int to;
        ui eid;
    };

    struct Record {
        int u;
        int w;
        ui e1;
        ui e2;
        size_t seq;   // original generation order
    };

    auto higher_priority = [&](int x, int y) -> bool {
        if (degree[x] != degree[y]) return degree[x] > degree[y];
        return x > y;
    };

    // ------------------------------------------------------------
    // Step 1. Build adjacency with edge ids, while preserving the
    // exact neighbor order used by nbr[v].
    //
    // Original nbr[v] is sorted increasingly by vertex id.
    // We keep exactly the same order, and for each nbr[v][k] attach
    // the corresponding edge id.
    // ------------------------------------------------------------
    std::vector<std::vector<AdjEdge>> adjE(n);
    for (int v = 0; v < n; ++v) {
        adjE[v].reserve(degree[v]);
    }

    for (ui eid = 0; eid < static_cast<ui>(m); ++eid) {
        int a = uniqueEdgesCompressedInputOrder[eid].first;
        int b = uniqueEdgesCompressedInputOrder[eid].second;
        edge[eid].id = eid;

        adjE[a].push_back({b, eid});
        adjE[b].push_back({a, eid});
    }

    for (int v = 0; v < n; ++v) {
        std::sort(adjE[v].begin(), adjE[v].end(),
                  [](const AdjEdge &x, const AdjEdge &y) {
                      return x.to < y.to;
                  });

        if (static_cast<int>(adjE[v].size()) != degree[v]) {
            std::cerr << "Internal error: adjE[" << v << "].size() != degree[" << v << "]."
                      << std::endl;
            std::exit(EXIT_FAILURE);
        }

        // sanity check: must match nbr[v] exactly
        for (int i = 0; i < degree[v]; ++i) {
            if (adjE[v][i].to != nbr[v][i]) {
                std::cerr << "Internal error: adjacency order mismatch at vertex " << v
                          << ", position " << i << "." << std::endl;
                std::exit(EXIT_FAILURE);
            }
        }
    }

    // ------------------------------------------------------------
    // Step 2. Enumerate priority 2-paths in the EXACT SAME ORDER
    // as the original code:
    //
    //   for v
    //     for i over nbr[v]
    //       u = nbr[v][i]
    //       if (!higher_priority(u,v)) continue
    //       for j over nbr[v]
    //         w = nbr[v][j]
    //         if (w == u) continue
    //         if (!higher_priority(u,w)) continue
    //         append path (u,v,w) to bloom(u,w)
    //
    // We first record every generated path in the original order.
    // ------------------------------------------------------------
    std::vector<Record> records;
    records.reserve(static_cast<size_t>(m)); // will grow if needed

    size_t seq = 0;

    for (int v = 0; v < n; ++v) {
        for (int i = 0; i < degree[v]; ++i) {
            int u = adjE[v][i].to;
            if (!higher_priority(u, v)) continue;

            ui e_uv = adjE[v][i].eid;

            for (int j = 0; j < degree[v]; ++j) {
                int w = adjE[v][j].to;
                if (w == u) continue;
                if (!higher_priority(u, w)) continue;

                ui e_vw = adjE[v][j].eid;

                records.push_back({u, w, e_uv, e_vw, seq++});
            }
        }
    }

    // ------------------------------------------------------------
    // Step 3. Group records by bloom key (u,w), BUT preserve the
    // original path insertion order inside each bloom.
    //
    // We do this by stable_sort on (u,w). Since records were pushed
    // in the exact original order, stable_sort guarantees that within
    // the same (u,w), the relative order is unchanged.
    //
    // Note:
    //   the original code used unordered_map iteration for bloom order,
    //   which is not a semantic order to preserve.
    //   What must be preserved is the generation order of paths inside
    //   each bloom, and this version does preserve that exactly.
    // ------------------------------------------------------------
    std::stable_sort(records.begin(), records.end(),
                     [](const Record &a, const Record &b) {
                         if (a.u != b.u) return a.u < b.u;
                         return a.w < b.w;
                     });

    std::vector<int> bloomNumber;
    bloomNumber.reserve(records.size() / 2 + 1);

    bloomCount = 0;
    size_t ignoredSingletonBloom = 0;

    size_t l = 0;
    while (l < records.size()) {
        size_t r = l + 1;
        while (r < records.size() &&
               records[r].u == records[l].u &&
               records[r].w == records[l].w) {
            ++r;
        }

        const int K = static_cast<int>(r - l);

        if (K <= 1) {
            ++ignoredSingletonBloom;
            l = r;
            continue;
        }

        const int currentBloomID = bloomCount++;
        bloomNumber.push_back(K);

        for (size_t t = l; t < r; ++t) {
            ui e1 = records[t].e1;
            ui e2 = records[t].e2;

            ui index1 = edge[e1].add_host_bloom_and_twin_edge(currentBloomID, e2);
            ui index2 = edge[e2].add_host_bloom_and_twin_edge(currentBloomID, e1);

            edge[e1].add_host_bloom_index_of_twin_edge(index2);
            edge[e2].add_host_bloom_index_of_twin_edge(index1);

            edge[e1].add_butterfly_support(K - 1);
            edge[e2].add_butterfly_support(K - 1);
        }

        l = r;
    }

    bloom = new Bloom[bloomCount];
    for (int i = 0; i < bloomCount; i++) {
        bloom[i].id = i;
        bloom[i].bloomNumber = bloomNumber[i];
        bloom[i].initialize_space();
    }

    std::cout << std::fixed << std::setprecision(6)
              << "bloom construction time:\t" << get_current_time() - start
              << " sec\n";
    std::cout << "valid blooms = " << bloomCount
              << ", ignored singleton blooms = " << ignoredSingletonBloom
              << std::endl;

    double start1 = get_current_time();

    // ------------------------------------------------------------
    // Step 4. Build member-edge indices for all nonzero-support edges.
    // Save the initial support before decomposition starts.
    // ------------------------------------------------------------
    extraBloom.id = bloomCount;
    extraBloom.bloomNumber = static_cast<int>(m);
    extraBloom.initialize_space_member_edge_only();

    for (ui i = 0; i < m; i++) {
        auto &currentEdge = edge[i];
        int butterflySupport = currentEdge.get_butterfly_support();

        initialButterflySupport[i] = butterflySupport;

        if (butterflySupport == 0) {
            if (currentEdge.get_host_bloom_number() != 0) {
                std::cerr << "Internal error: edge " << i
                          << " has zero support but nonzero host bloom number."
                          << std::endl;
                std::exit(EXIT_FAILURE);
            }
            edgeToPeel--;
            continue;
        }

        currentEdge.compute_slack_value();

        const ui hostNum = currentEdge.get_host_bloom_number();
        for (ui j = 0; j < hostNum; j++) {
            int bloomID = currentEdge.get_host_bloom_id_by_index(j);
            pair_t index = bloom[bloomID].add_member_edge(i, j, edge);
            currentEdge.add_reverse_index_in_host_bloom(index);
        }

        if (currentEdge.reverseIndexInHostBloom.size() != currentEdge.hostBloom.size()) {
            std::cerr << "Internal error: reverseIndexInHostBloom.size() != hostBloom.size() "
                      << "for edge " << i << std::endl;
            std::exit(EXIT_FAILURE);
        }

        pair_t index = extraBloom.add_member_edge(i, edge);
        currentEdge.set_reverse_index_in_extra_bloom(index);
    }

    delete[] nbrAll;
    nbrAll = nullptr;

    delete[] nbr;
    nbr = nullptr;

    delete[] degree;
    degree = nullptr;

    std::vector<int>().swap(bloomNumber);
    std::vector<Record>().swap(records);
    std::vector<std::vector<AdjEdge>>().swap(adjE);

    std::cout << std::fixed << std::setprecision(6)
              << "index finalization time:\t" << get_current_time() - start1
              << " sec\n";
}
void Graph::remove_edge_from_bloom_by_index(int bloomID, pair_t index) {
    affect_edge_t affectEdgeInfo = bloom[bloomID].remove_member_by_index(index);
    if (affectEdgeInfo.first == static_cast<ui>(-1)) {
        return;
    } else {
        ui affectEdgeID = affectEdgeInfo.first;
        ui affectIndex = affectEdgeInfo.second;
        edge[affectEdgeID].set_reverse_index_by_index(affectIndex, index);
    }
}

void Graph::remove_edge_from_extra_bloom_by_index(pair_t index) {
    ui affectEdgeID = extraBloom.remove_member_by_index_id_only(index);
    if (affectEdgeID == static_cast<ui>(-1)) {
        return;
    } else {
        edge[affectEdgeID].set_reverse_index_in_extra_bloom(index);
    }
}

void Graph::remove_bloom_from_edge_by_index(ui edgeID, ui index) {
    affect_bloom_t affectBloomInfo = edge[edgeID].remove_host_bloom_by_index(index);
    if (std::get<0>(affectBloomInfo) == -1) {
        return;
    } else {
        if (std::get<1>(affectBloomInfo).first != -1) {
            bloom[std::get<0>(affectBloomInfo)].set_reverse_index_by_index(
                std::get<1>(affectBloomInfo), index);
        }
        edge[std::get<2>(affectBloomInfo)].set_twin_index_by_index(
            std::get<3>(affectBloomInfo), index);
    }
}

void Graph::bitruss_decomposition() {
    std::ifstream statm_file("/proc/self/statm");
    if (statm_file) {
        size_t size, resident, share, text, lib, data, dt;
        statm_file >> size >> resident >> share >> text >> lib >> data >> dt;
        std::cout << "Memory usage: "
                  << resident * sysconf(_SC_PAGESIZE) / 1024
                  << " KB" << std::endl;
    } else {
        std::cerr << "Failed to open /proc/self/statm" << std::endl;
    }

    ui visitedEdge = 0;
    std::queue<ui> peelList;
    std::vector<ui> matureList;

    double start = get_current_time();
    std::cout << "bitruss decomposing..." << std::endl;

    while (visitedEdge < edgeToPeel) {
        if (peelList.empty()) {
            extraBloom.send_value_to_member(matureList, peelList, edge);
            for (ui i = 0; i < matureList.size(); i++) {
                ui edgeID = matureList[i];
                check_mature_edge(edgeID, peelList);
            }
            matureList.clear();
        } else {
            while (!peelList.empty()) {
                ui edgeID = peelList.front();
                peelList.pop();

                if (bitrussNumber[edgeID] != 0)
                    continue;

                bitrussNumber[edgeID] = extraBloom.get_counter();
                peel_edge(edgeID, peelList);
                visitedEdge++;
            }
        }
    }

    std::cout << std::fixed << std::setprecision(6)
              << "bitruss decomposition time:\t" << get_current_time() - start
              << " sec\n";
}

void Graph::peel_edge(ui edgeID, std::queue<ui> &peelList) {
    auto *peelEdge = &edge[edgeID];
    pair_t index = peelEdge->get_reverse_index_in_extra_bloom();

    remove_edge_from_extra_bloom_by_index(index);

    const ui hostNum = peelEdge->get_host_bloom_number();

    for (ui i = 0; i < hostNum; i++) {
        int bloomID = peelEdge->get_host_bloom_id_by_index(i);
        TwinInfo twinEdgeInfo = peelEdge->get_twin_edge_info_by_index(i);
        pair_t reverseIndex = peelEdge->get_reverse_index_in_host_bloom_by_index(i);
        auto *currentBloom = &bloom[bloomID];
        int bloomNumber = currentBloom->bloomNumber;
        ui twinEdgeID = twinEdgeInfo.twinEdgeID;

        remove_edge_from_bloom_by_index(bloomID, reverseIndex);

        ui indexInTwinEdge = twinEdgeInfo.hostBloomIndex;
        pair_t indexInHostBloom =
            edge[twinEdgeID].get_reverse_index_in_host_bloom_by_index(indexInTwinEdge);

        if (bloomNumber <= 1) {
            remove_edge_from_bloom_by_index(bloomID, indexInHostBloom);
            remove_bloom_from_edge_by_index(twinEdgeID, indexInTwinEdge);
            edge[twinEdgeID].decrease_butterfly_support(currentBloom->get_counter());
            currentBloom->bloomNumber--;
            continue;
        }

        currentBloom->send_value_to_member(bloomNumber - 1, indexInHostBloom, edge);
        remove_edge_from_bloom_by_index(bloomID, indexInHostBloom);
        remove_bloom_from_edge_by_index(twinEdgeID, indexInTwinEdge);
        edge[twinEdgeID].decrease_butterfly_support(
            currentBloom->get_counter() + bloomNumber - 1);

        if (edge[twinEdgeID].check_maturity()) {
            check_mature_edge(twinEdgeID, peelList);
        }

        currentBloom->bloomNumber--;
        std::vector<ui> matureList;
        currentBloom->send_value_to_member(matureList, peelList, edge);
        for (ui j = 0; j < matureList.size(); j++) {
            ui currentEdgeID = matureList[j];
            check_mature_edge(currentEdgeID, peelList);
        }
    }
}

int Graph::collect_counter(ui edgeID) {
    int counter = 0;
    for (ui i = 0; i < edge[edgeID].get_host_bloom_number(); i++) {
        counter += bloom[edge[edgeID].get_host_bloom_id_by_index(i)].get_counter();
    }
    return counter;
}

void Graph::check_mature_edge(ui edgeID, std::queue<ui> &peelList) {
    if (edge[edgeID].isPeel)
        return;

    int counterSum = collect_counter(edgeID);
    int requiredSupport = edge[edgeID].get_butterfly_support();
    int extraCounter = extraBloom.get_counter();

    if (counterSum + extraCounter >= requiredSupport) {
        edge[edgeID].isPeel = true;
        peelList.push(edgeID);
    } else {
        int trackValue = requiredSupport - counterSum - extraCounter;
        int temp = edge[edgeID].get_slack_value();
        edge[edgeID].compute_slack_value(trackValue);

        if (temp != edge[edgeID].get_slack_value()) {
            for (ui i = 0; i < edge[edgeID].get_host_bloom_number(); i++) {
                int bloomID = edge[edgeID].get_host_bloom_id_by_index(i);
                pair_t reverseIndex = edge[edgeID].get_reverse_index_in_host_bloom_by_index(i);
                remove_edge_from_bloom_by_index(bloomID, reverseIndex);
                pair_t index = bloom[bloomID].add_member_edge(edgeID, i, edge);
                edge[edgeID].set_reverse_index_by_index(i, index);
            }

            pair_t reverseIndex = edge[edgeID].get_reverse_index_in_extra_bloom();
            remove_edge_from_extra_bloom_by_index(reverseIndex);
            pair_t index = extraBloom.add_member_edge(edgeID, edge);
            edge[edgeID].set_reverse_index_in_extra_bloom(index);
        }
    }
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