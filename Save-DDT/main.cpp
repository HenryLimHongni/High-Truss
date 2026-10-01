#include <iostream>
#include <string>
#include <iomanip>
#include <algorithm>
#include <vector>
#include <numeric>
#include <fstream>
#include <sys/types.h>
#include <sys/stat.h>

#include "edge.h"
#include "3-bloom.h"
#include "2-bloom.h"
#include "graph.h"
#include "extraBloom.h"
#include "current_time.h"

int main(int argc, char** argv) {
    double start = get_current_time();

    std::string filename = (argc >= 2)
        ? std::string(argv[1])
        : std::string("/home/fengnianl/decomposition/five-cycle/5cycle/Email-Enron.txt");

    std::cout << "DT decomposition" << std::endl;
    std::cout << "file:" << filename << std::endl;

    Graph G;

    G.read_edges_from_file(filename);
    if (G.edges_raw.empty()) {
        std::cerr << "No edges read.\n";
        return 0;
    }

    G.remap_ids();
    G.dedup_and_build_edges();

    std::cerr << "[INFO] Nodes: " << G.n
              << ", Edges(dedup): " << G.edges.size() << "\n";

    G.core_decomposition_bz();
    G.build_oriented_out();
    G.build_blooms_strict_pipeline_with_DT();

    const int B2 = G.bloom_count_2();
    const int B3 = G.bloom_count_3();
    std::cerr << "[INFO] 2-Bloom count: " << B2 << "\n";
    std::cerr << "[INFO] 3-Bloom count: " << B3 << "\n";
    if (B2 != B3) {
        std::cerr << "[BUG] counts mismatch! B2=" << B2 << ", B3=" << B3 << "\n";
    } else {
        std::cerr << "[OK] counts match.\n";
    }

    std::cerr << "[DONE] finish\n";

    std::cout << std::fixed << std::setprecision(6)
              << " total time:\t" << (get_current_time() - start)
              << "sec\n";

    std::string basename = filename;
    auto pos_slash = basename.find_last_of("/\\");
    if (pos_slash != std::string::npos) {
        basename = basename.substr(pos_slash + 1);
    }

    std::string stem = basename;
    auto pos_dot = stem.find_last_of('.');
    if (pos_dot != std::string::npos) {
        stem = stem.substr(0, pos_dot);
    }

    std::string out_dir  = "result";
    std::string out_path = out_dir + "/" + stem + "-result.txt";

    mkdir(out_dir.c_str(), 0755);

    std::ofstream fout(out_path);
    if (!fout) {
        std::cerr << "Cannot open output file: " << out_path << "\n";
        return 1;
    }

    for (ui i = 0; i < G.edges.size(); ++i) {
        fout << i << "\t" << G.five_cycle_decomposition_number[i] << "\n";
    }
    fout.close();

    std::cerr << "[INFO] result written to " << out_path << "\n";
    return 0;
}
