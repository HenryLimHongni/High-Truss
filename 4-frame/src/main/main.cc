#include <iostream>
#include <string>
#include <iomanip>

#include "graph/graph.h"
#include "utils/current_time.h"

int main(int argc, char **argv) {
    std::string filepath = "../dataset/graph.txt";
    if (argc >= 2) {
        filepath = argv[1];
    }

    auto *graph = new Graph(filepath);   // file reading happens here, not timed

    double algo_start = get_current_time();

    graph->construct_index();
    graph->bitruss_decomposition();

    double algo_end = get_current_time();

    std::cout << std::fixed << std::setprecision(6)
              << "Total algorithm time (excluding file read/write):\t"
              << (algo_end - algo_start) << " sec\n";

    graph->output_bitruss_number(filepath);  // output not timed

    delete graph;
    return 0;
}