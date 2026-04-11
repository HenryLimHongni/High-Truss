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
#include <stdexcept>
#include <cstdint>

namespace {

static inline uint64_t make_pair_key(uint32_t x, uint32_t y) {
    return (static_cast<uint64_t>(x) << 32) | static_cast<uint64_t>(y);
}

} // namespace

Graph::Graph(const std::string path) {
    degree = nullptr;
    nbrAll = nullptr;
    nbr = nullptr;
    edge = nullptr;
    bitrussNumber = nullptr;
    bloom = nullptr;

    bloomCount = 0;
    edgeToPeel = 0;

    originalVertexCount = 0;
    originalEdgeCount = 0;

    std::ifstream fin(path);
    if (!fin.is_open()) {
        throw std::runtime_error("Failed to open input file: " + path);
    }

    std::cout << "input txt path: " << path << std::endl;

    // ---------- read original general graph (NOT timed) ----------
    std::unordered_map<long long, int> idMap;
    std::unordered_set<uint64_t> seen;

    auto get_compact_id = [&](long long x) -> int {
        auto it = idMap.find(x);
        if (it != idMap.end()) return it->second;
        int nid = static_cast<int>(idMap.size());
        idMap.emplace(x, nid);
        return nid;
    };

    std::string line;
    while (std::getline(fin, line)) {
        if (line.empty()) continue;
        if (line[0] == '#') continue;

        std::stringstream ss(line);
        long long a_raw, b_raw;
        if (!(ss >> a_raw >> b_raw)) continue;

        if (a_raw == b_raw) continue; // ignore self-loop

        int a = get_compact_id(a_raw);
        int b = get_compact_id(b_raw);

        if (a == b) continue;
        if (a > b) std::swap(a, b);

        uint64_t key = make_pair_key(static_cast<uint32_t>(a),
                                     static_cast<uint32_t>(b));
        if (seen.insert(key).second) {
            originalEdges.emplace_back(a, b);
        }
    }
    fin.close();

    originalVertexCount = static_cast<int>(idMap.size());
    originalEdgeCount = static_cast<int>(originalEdges.size());

    std::cout << "original graph: n = " << originalVertexCount
              << ", m = " << originalEdgeCount << std::endl;

    // ---------- total timing starts from transformation ----------
    totalStartTime = get_current_time();

    // Build transformed bipartite graph \tilde{G}
    // Left side:  [0, originalVertexCount-1]
    // Right side: [originalVertexCount, 2*originalVertexCount-1]
    n = 2 * originalVertexCount;

    std::vector<std::vector<int>> adj(n);
    adj.reserve(n);

    for (int i = 0; i < originalEdgeCount; i++) {
        int x = originalEdges[i].first;
        int y = originalEdges[i].second;

        int xL = x;
        int yL = y;
        int xR = x + originalVertexCount;
        int yR = y + originalVertexCount;

        // add (x_L, y_R)
        adj[xL].push_back(yR);
        adj[yR].push_back(xL);

        // add (y_L, x_R)
        adj[yL].push_back(xR);
        adj[xR].push_back(yL);
    }

    degree = new int[n];
    nbr = new int *[n];

    long long totalAdjSize = 0;
    for (int u = 0; u < n; u++) {
        std::sort(adj[u].begin(), adj[u].end());
        adj[u].erase(std::unique(adj[u].begin(), adj[u].end()), adj[u].end());
        degree[u] = static_cast<int>(adj[u].size());
        totalAdjSize += degree[u];
    }

    nbrAll = new int[totalAdjSize];
    long long ptr = 0;
    for (int u = 0; u < n; u++) {
        nbr[u] = nbrAll + ptr;
        for (int v : adj[u]) {
            nbrAll[ptr++] = v;
        }
    }

    m = totalAdjSize / 2;  // number of undirected edges in transformed graph

    std::cout << "transformed bipartite graph: n = " << n
              << ", m = " << m << std::endl;

    edge = new Edge[m];
    bitrussNumber = new int[m];
    edgeToPeel = m;

    for (ui i = 0; i < m; i++) {
        edge[i].id = i;
    }
    std::memset(bitrussNumber, 0, sizeof(int) * m);

    transformedCopy1.resize(originalEdgeCount);
    transformedCopy2.resize(originalEdgeCount);
    originalTrussNumber.assign(originalEdgeCount, 0);

    std::cout << std::fixed << std::setprecision(6)
              << "Transformation time:\t"
              << get_current_time() - totalStartTime << " sec\n";
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

int Graph::find_neighbor_pos(int u, int v) const {
    int l = 0, r = degree[u] - 1;
    while (l <= r) {
        int mid = l + (r - l) / 2;
        if (nbr[u][mid] == v) return mid;
        if (nbr[u][mid] < v) l = mid + 1;
        else r = mid - 1;
    }
    return -1;
}

void Graph::construct_index() {
    double start = get_current_time();

    auto higher_priority = [&](int x, int y) -> bool {
        if (degree[x] != degree[y]) return degree[x] > degree[y];
        return x > y;
    };

    ui **e = new ui *[n];
    for (int u = 0; u < n; u++) {
        e[u] = new ui[degree[u]];
    }

    ui edgeIdx = 0;
    for (int u = 0; u < n; u++) {
        for (int i = 0; i < degree[u]; i++) {
            int v = nbr[u][i];
            if (u < v) {
                e[u][i] = edgeIdx++;
            } else {
                int pos = find_neighbor_pos(v, u);
                if (pos == -1) {
                    throw std::runtime_error("Internal error: symmetric edge not found.");
                }
                e[u][i] = e[v][pos];
            }
        }
    }

    // map transformed edge ids back to original edges
    for (int i = 0; i < originalEdgeCount; i++) {
        int x = originalEdges[i].first;
        int y = originalEdges[i].second;

        int pos1 = find_neighbor_pos(x, y + originalVertexCount);
        int pos2 = find_neighbor_pos(y, x + originalVertexCount);

        if (pos1 == -1 || pos2 == -1) {
            throw std::runtime_error("Internal error: transformed copies not found.");
        }

        transformedCopy1[i] = e[x][pos1]; // (x_L, y_R)
        transformedCopy2[i] = e[y][pos2]; // (y_L, x_R)
    }

    std::vector<int> bloomNumber;
    int u, v, w;
    int *lastUseVertex = new int[n];
    int *lastCount = new int[n];
    int *visitedStatusForW = new int[n];

    std::memset(lastCount, 0, sizeof(int) * n);
    std::memset(visitedStatusForW, -1, sizeof(int) * n);

    int **butterflyCount = new int *[n];
    for (int i = 0; i < n; i++) {
        butterflyCount[i] = new int[degree[i]]();
    }

    int lastUseIdx = 0;
    for (u = 0; u < n; u++) {
        for (int j = 0; j < lastUseIdx; j++) {
            lastCount[lastUseVertex[j]] = 0;
            visitedStatusForW[lastUseVertex[j]] = -1;
        }
        lastUseIdx = 0;

        for (int i = 0; i < degree[u]; i++) {
            v = nbr[u][i];

            if (!higher_priority(u, v)) continue;

            for (int j = 0; j < degree[v]; j++) {
                w = nbr[v][j];
                if (w == u) continue;
                if (!higher_priority(u, w)) continue;

                lastCount[w]++;
                if (lastCount[w] == 1) {
                    lastUseVertex[lastUseIdx++] = w;
                }
            }
        }

        for (int i = 0; i < degree[u]; i++) {
            v = nbr[u][i];

            if (!higher_priority(u, v)) continue;

            for (int j = 0; j < degree[v]; j++) {
                w = nbr[v][j];
                if (w == u) continue;
                if (!higher_priority(u, w)) continue;

                int lastCountNumber = lastCount[w];
                if (lastCountNumber > 1) {
                    butterflyCount[u][i] += lastCountNumber - 1;
                    butterflyCount[v][j] += lastCountNumber - 1;
                } else {
                    continue;
                }

                if (visitedStatusForW[w] == -1) {
                    ui indexV = edge[e[u][i]].add_host_bloom_and_twin_edge(
                            bloomCount, e[v][j]);
                    ui indexU = edge[e[v][j]].add_host_bloom_and_twin_edge(
                            bloomCount, e[u][i]);
                    edge[e[u][i]].add_host_bloom_index_of_twin_edge(indexU);
                    edge[e[v][j]].add_host_bloom_index_of_twin_edge(indexV);
                    bloomNumber.emplace_back(lastCountNumber);
                    visitedStatusForW[w] = bloomCount++;
                } else {
                    ui indexV = edge[e[u][i]].add_host_bloom_and_twin_edge(
                            visitedStatusForW[w], e[v][j]);
                    ui indexU = edge[e[v][j]].add_host_bloom_and_twin_edge(
                            visitedStatusForW[w], e[u][i]);
                    edge[e[u][i]].add_host_bloom_index_of_twin_edge(indexU);
                    edge[e[v][j]].add_host_bloom_index_of_twin_edge(indexV);
                }
            }
        }
    }

    delete[] lastUseVertex;
    lastUseVertex = nullptr;
    delete[] lastCount;
    lastCount = nullptr;
    delete[] visitedStatusForW;
    visitedStatusForW = nullptr;

    bloom = new Bloom[bloomCount];
    for (int i = 0; i < bloomCount; i++) {
        bloom[i].id = i;
        bloom[i].bloomNumber = bloomNumber[i];
        bloom[i].initialize_space();
    }

    for (u = 0; u < n; u++) {
        for (int i = 0; i < degree[u]; i++) {
            edge[e[u][i]].add_butterfly_support(butterflyCount[u][i]);
        }
    }
    
    std::cout << std::fixed << std::setprecision(6)
              << "bloom construction time:\t" << get_current_time() - start
              << " sec\n";

    double start1 = get_current_time();

    extraBloom.id = bloomCount;
    extraBloom.bloomNumber = m;
    extraBloom.initialize_space_member_edge_only();

    for (ui i = 0; i < m; i++) {
        auto &currentEdge = edge[i];
        int butterflySupport = currentEdge.get_butterfly_support();
        if (butterflySupport == 0) {
            edgeToPeel--;
            continue;
        }

        currentEdge.compute_slack_value();

        for (int j = 0; j < currentEdge.get_host_bloom_number(); j++) {
            int bloomID = currentEdge.get_host_bloom_id_by_index(j);
            pair_t index = bloom[bloomID].add_member_edge(i, j, edge);
            currentEdge.add_reverse_index_in_host_bloom(index);
        }

        pair_t index = extraBloom.add_member_edge(i, edge);
        currentEdge.set_reverse_index_in_extra_bloom(index);
    }

    for (u = 0; u < n; u++) {
        delete[] e[u];
        delete[] butterflyCount[u];
    }
    delete[] e;
    delete[] butterflyCount;

    delete[] nbrAll;
    nbrAll = nullptr;

    delete[] nbr;
    nbr = nullptr;

    delete[] degree;
    degree = nullptr;

    bloomNumber.clear();
    bloomNumber.shrink_to_fit();

    e = nullptr;
    butterflyCount = nullptr;

    std::cout << std::fixed << std::setprecision(6)
              << "Index construction time:\t" << get_current_time() - start1
              << " sec\n";
}

void Graph::remove_edge_from_bloom_by_index(int bloomID, pair_t index) {
    affect_edge_t affectEdgeInfo = bloom[bloomID].remove_member_by_index(index);
    if (affectEdgeInfo.first == -1) {
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
    affect_bloom_t affectBloomInfo =
            edge[edgeID].remove_host_bloom_by_index(index);
    if (std::get<0>(affectBloomInfo) == -1) {
        return;
    } else {
        if (std::get<1>(affectBloomInfo).first != -1)
            bloom[std::get<0>(affectBloomInfo)].set_reverse_index_by_index(
                    std::get<1>(affectBloomInfo), index);
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
              << "Bitruss decomposition time:\t"
              << get_current_time() - start << " sec\n";
}

void Graph::peel_edge(ui edgeID, std::queue<ui> &peelList) {
    auto *peelEdge = &edge[edgeID];
    pair_t index = peelEdge->get_reverse_index_in_extra_bloom();

    remove_edge_from_extra_bloom_by_index(index);

    for (ui i = 0; i < peelEdge->get_host_bloom_number(); i++) {
        int bloomID = peelEdge->get_host_bloom_id_by_index(i);
        TwinInfo twinEdgeInfo = peelEdge->get_twin_edge_info_by_index(i);
        pair_t reverseIndex =
                peelEdge->get_reverse_index_in_host_bloom_by_index(i);
        auto *currentBloom = &bloom[bloomID];
        int bloomNumber = currentBloom->bloomNumber;
        ui twinEdgeID = twinEdgeInfo.twinEdgeID;

        remove_edge_from_bloom_by_index(bloomID, reverseIndex);

        ui indexInTwinEdge = twinEdgeInfo.hostBloomIndex;
        pair_t indexInHostBloom =
                edge[twinEdgeID].get_reverse_index_in_host_bloom_by_index(
                        indexInTwinEdge);

        if (bloomNumber <= 1) {
            remove_edge_from_bloom_by_index(bloomID, indexInHostBloom);
            remove_bloom_from_edge_by_index(twinEdgeID, indexInTwinEdge);
            edge[twinEdgeID].decrease_butterfly_support(
                    currentBloom->get_counter());
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
        counter +=
                bloom[edge[edgeID].get_host_bloom_id_by_index(i)].get_counter();
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
                pair_t reverseIndex =
                        edge[edgeID].get_reverse_index_in_host_bloom_by_index(i);
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

void Graph::map_result_back_to_original() {
    double start = get_current_time();

    originalTrussNumber.assign(originalEdgeCount, 0);

    for (int i = 0; i < originalEdgeCount; i++) {
        int bn1 = bitrussNumber[transformedCopy1[i]];
        int bn2 = bitrussNumber[transformedCopy2[i]];

        if (bn1 != bn2) {
            std::cerr << "[Warning] transformed copies of original edge " << i
                      << " have different values: "
                      << bn1 << " vs " << bn2
                      << ". Output min(bn1,bn2)." << std::endl;
        }

        originalTrussNumber[i] = std::min(bn1, bn2);
    }

    std::cout << std::fixed << std::setprecision(6)
              << "Result mapping time:\t" << get_current_time() - start
              << " sec\n";

    totalRunningTime = get_current_time() - totalStartTime;

    std::cout << std::fixed << std::setprecision(6)
              << "Total running time (exclude I/O):\t"
              << totalRunningTime << " sec\n";
}

void Graph::output_bitruss_number(std::string inputPath) {
    // mapping is computation, so do it before file writing
    map_result_back_to_original();

    std::string outputPath = inputPath + ".c4truss.txt";
    std::ofstream fout(outputPath, std::ios::out);
    if (!fout.is_open()) {
        std::cerr << "Failed to open output file: " << outputPath << std::endl;
        return;
    }

    for (int i = 0; i < originalEdgeCount; i++) {
        fout << i << "\t" << originalTrussNumber[i] << "\n";
    }
    fout.close();

    std::cout << "4-cycle truss result written to: " << outputPath << std::endl;
}