#ifndef GRAPH_H
#define GRAPH_H

#include <algorithm>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <string>
#include <vector>
#include <queue>
#include <utility>

#include "bloom/bloom.h"
#include "graph/edge.h"
#include "utils/current_time.h"

class Graph {
protected:
    // transformed bipartite graph
    long long m;
    int n, n1;
    unsigned int edgeToPeel{0};
    int bloomCount{0};

    int *degree{nullptr};
    int *nbrAll{nullptr};
    int **nbr{nullptr};

    int *bitrussNumber{nullptr};

    Edge *edge{nullptr};
    Bloom *bloom{nullptr};
    Bloom extraBloom;

    // original general graph info
    int originalVertexCount{0};
    int originalEdgeCount{0};

    // original edges in the same order as input txt after deduplication
    std::vector<std::pair<int, int>> originalEdges;

    // for each original edge e_i, store its two corresponding transformed edges
    std::vector<ui> transformedCopy1; // (x_L, y_R)
    std::vector<ui> transformedCopy2; // (y_L, x_R)

    // mapped result for original graph
    std::vector<int> originalTrussNumber;

    // timing
    double totalStartTime{0.0};
    double totalRunningTime{0.0};

    // binary search neighbor position in sorted adjacency list
    int find_neighbor_pos(int u, int v) const;

    // map transformed bitruss numbers back to original graph
    void map_result_back_to_original();

public:
    Graph(const std::string path);

    ~Graph();

    virtual void construct_index();

    void remove_edge_from_bloom_by_index(int bloomID, pair_t index);

    void remove_edge_from_extra_bloom_by_index(pair_t index);

    void remove_bloom_from_edge_by_index(unsigned int edgeID, ui index);

    virtual void bitruss_decomposition();

    void peel_edge(unsigned int edgeID, std::queue<ui> &peelList);

    int collect_counter(unsigned int edgeID);

    void compute_and_restart(unsigned int edgeID, const int trackValue);

    void check_mature_edge(unsigned int edgeID, std::queue<ui> &peelList);

    // output mapped 4-cycle truss numbers for the original general graph
    void output_bitruss_number(std::string inputPath);
};

#endif