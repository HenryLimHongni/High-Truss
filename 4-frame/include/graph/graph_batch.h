#ifndef GRAPH_BATCH_H
#define GRAPH_BATCH_H

#include "graph/graph.h"
class GraphBatch : public Graph {
protected:
    int *visited{nullptr};
    int *bloomCounter{nullptr};
    char *isAffected{nullptr};
    std::vector<int> affectedBloom;
    //bool bloomrecord;
    std::vector<ui> affectEdge;
    ui visitedEdge;

public:
    GraphBatch(const std::string filePath) : Graph(filePath) {}

    void construct_index() override;

    void bitruss_decomposition() override;

    void peel_edge(std::vector<ui> edgeList,
                           std::vector<ui> &peelList);
    void check_mature_edge(ui edgeID,
                              std::vector<ui> &peelList);
};

#endif