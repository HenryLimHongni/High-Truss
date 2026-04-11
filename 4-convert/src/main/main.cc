#include <iostream>
#include <string>

#include "graph/graph.h"

int main(int argc, char **argv) {
    if (argc < 2) {
        std::cerr << "Usage: " << argv[0] << " <input_graph_txt>\n";
        return 1;
    }

    std::string input_path = argv[1];

    Graph graph(input_path);
    graph.construct_index();
    graph.bitruss_decomposition();
    graph.output_bitruss_number(input_path);

    return 0;
}