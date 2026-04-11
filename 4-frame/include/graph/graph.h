#ifndef GRAPH_H
#define GRAPH_H

#include <algorithm>
#include <cstring>
#include <fstream>
#include <iomanip>
#include<vector>
#include <iostream>
#include <string>
#include <queue>

#include "bloom/bloom.h"
#include "graph/edge.h"
#include "utils/current_time.h"

class Graph {
protected:
    long long m{0};
    int n{0};
    unsigned int edgeToPeel{0};
    int bloomCount{0};
    int *degree{nullptr};
    int *nbrAll{nullptr};
    int **nbr{nullptr};
    std::vector<int> initialButterflySupport;
    std::string inputGraphPath;

    // unique edges in the order of first appearance in the input file
    std::vector<std::pair<int, int>> uniqueEdgesCompressedInputOrder;

    // original raw vertex IDs for output / mapping back
    std::vector<std::pair<long long, long long>> uniqueEdgesOriginalInputOrder;

    // first line number (1-based) where this unique edge first appears
    std::vector<long long> uniqueEdgeFirstLineNo;

    int *bitrussNumber{nullptr};

    Edge *edge{nullptr};
    Bloom *bloom{nullptr};
    Bloom extraBloom;

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

    void check_mature_edge(unsigned int edgeID, std::queue<ui> &peelList);

    void output_bitruss_number(std::string outputPath);
};

#endif