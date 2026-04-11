#include <iostream>
#include <string>
#include <iomanip>
#include <algorithm>
#include <vector>
#include <numeric>
#include <sys/types.h>
#include <sys/stat.h>


#include "edge.h"
#include "3-bloom.h"
#include "2-bloom.h"
#include "graph.h"
#include "extraBloom.h"
#include "current_time.h"

static size_t sum_bucket_members(const vector<vector<int>>& buckets) {
    size_t s = 0;
    for (const auto& b : buckets) s += b.size();
    return s;
}

int main(int argc, char** argv) {
    double start = get_current_time();
    
    std::string filename = (argc >= 2)
        ? std::string(argv[1])
        : std::string("");
        
    cout<<"DT decomposition"<<endl;
    cout<<"file:"<<filename<<endl;
    Graph G;

    G.read_edges_from_file(filename);
    if (G.edges_raw.empty()) {
        std::cerr << "No edges read.\n";
        return 0;
    }
    G.remap_ids();
    G.dedup_and_build_edges();
    //std::ofstream logfile("app.log");
	//std::streambuf* oldbuf = std::cout.rdbuf(logfile.rdbuf());

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

   
    /*
    for (int bid = 0; bid < std::min(5, G.bloom_count_3()); ++bid) {
        const Bloom& B = G.get_bloom(bid);
        uint32_t x = (uint32_t)(B.key >> 32);
        uint32_t y = (uint32_t)(B.key & 0xffffffffu);
        size_t members = sum_bucket_members(B.memberEdge);
        std::cerr << "BloomID " << bid
                  << " key=(" << x << "," << y << ")"
                  << " paths=" << B.total_paths
                  << " buckets=" << B.bloomNumber
                  << " members=" << members
                  << "\n";
    }
                  */

    
    auto count_overlap = [](vector<int> a, vector<int> b) -> int {
        std::sort(a.begin(), a.end());
        a.erase(std::unique(a.begin(), a.end()), a.end());
        std::sort(b.begin(), b.end());
        b.erase(std::unique(b.begin(), b.end()), b.end());
        size_t i=0, j=0; int cnt=0;
        while (i<a.size() && j<b.size()) {
            if (a[i]==b[j]) { ++cnt; ++i; ++j; }
            else if (a[i]<b[j]) ++i; else ++j;
        }
        return cnt;
    };

    const int show_e = std::min<int>(30, (int)G.edges.size());
    /*
    for (int eid = 19550; eid < 19560; ++eid) {
        const Edge& E = G.edges[eid];
        //int overlap = count_overlap(E.host2Bloom, E.host3Bloom);
        std::cerr << "EdgeID " << eid
                  << " (" << E.u << "," << E.v << ")"
                  << " in 2-blooms: " << E.host2Bloom.size()
                  << ", in 3-blooms: " << E.host3Bloom.size()
                  << ", five-cycle support: " << E.five_cycle_support
                  << ", slack: " << E.slackValue
                  << ", isDT: " << (E.isDT ? "true" : "false")
                  <<", target: "<<E.targetValue
                  << "\n";
    }
                  */
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
