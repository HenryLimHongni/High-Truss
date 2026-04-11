#include <iostream>
#include <iomanip>
#include <string>

#include "graph/graph.h"
#include "utils/current_time.h"

int main(int argc, char **argv) {
    std::string filepath = "../dataset/graph.txt";
    if (argc >= 2) {
        filepath = argv[1];
    }

    // File input happens inside Graph constructor, so it is excluded.
    auto *graph = new Graph(filepath);

    // Start timing from bloom/index construction.
    double total_compute_start = get_current_time();

    graph->construct_index();
    graph->bitruss_decomposition();

    double total_compute_time = get_current_time() - total_compute_start;

    std::cout << std::fixed << std::setprecision(6)
              << "Total compute time (index + decomposition, excluding I/O):\t"
              << total_compute_time << "sec\n";

    // Output mapping / writing is excluded from timing.
    graph->output_bitruss_number(filepath);

    delete graph;
    return 0;
}