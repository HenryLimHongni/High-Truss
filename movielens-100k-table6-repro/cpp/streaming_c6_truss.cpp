// Exact memory-bounded simple-C6 edge-truss decomposition.
//
// The backend does not materialize all C6 incidences. It enumerates simple
// C6s once for exact initial support, and during peeling enumerates active
// length-5 paths between the removed edge's endpoints. Trussness follows the
// support-threshold convention: an isolated chordless C6 has value 1.

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <fstream>
#include <functional>
#include <iostream>
#include <limits>
#include <numeric>
#include <queue>
#include <sstream>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

namespace {

using Clock = std::chrono::steady_clock;
using NodeId = std::uint32_t;
using EdgeId = std::uint32_t;
using Count = std::uint64_t;
constexpr EdgeId kMissingEdge = std::numeric_limits<EdgeId>::max();

std::uint64_t pack_pair(NodeId left, NodeId right) {
    if (left > right) std::swap(left, right);
    return (static_cast<std::uint64_t>(left) << 32U) | right;
}

struct Neighbor {
    NodeId node;
    EdgeId edge;
};

struct Graph {
    std::vector<std::pair<NodeId, NodeId>> edges;
    std::vector<std::vector<Neighbor>> adjacency;
    std::unordered_map<std::uint64_t, EdgeId> edge_ids;

    EdgeId edge_id(NodeId left, NodeId right) const {
        const auto found = edge_ids.find(pack_pair(left, right));
        return found == edge_ids.end() ? kMissingEdge : found->second;
    }
};

Graph read_graph(const std::string& path) {
    std::ifstream input(path);
    if (!input) throw std::runtime_error("cannot open input: " + path);

    std::vector<std::pair<NodeId, NodeId>> edges;
    std::unordered_set<std::uint64_t> seen;
    NodeId node_count = 0U;
    std::string line;
    std::size_t line_number = 0U;
    while (std::getline(input, line)) {
        ++line_number;
        const auto first = line.find_first_not_of(" \t\r\n");
        if (first == std::string::npos || line[first] == '#') continue;
        for (char& ch : line) {
            if (ch == ',') ch = ' ';
        }
        std::istringstream stream(line);
        std::int64_t raw_left = -1;
        std::int64_t raw_right = -1;
        std::string extra;
        if (!(stream >> raw_left >> raw_right) || (stream >> extra)) {
            throw std::runtime_error(
                "expected exactly two integer columns at line " +
                std::to_string(line_number));
        }
        if (raw_left < 0 || raw_right < 0) {
            throw std::runtime_error("node IDs must be non-negative");
        }
        if (raw_left == raw_right) continue;
        if (raw_left > std::numeric_limits<NodeId>::max() ||
            raw_right > std::numeric_limits<NodeId>::max()) {
            throw std::runtime_error("node ID exceeds uint32 range");
        }
        NodeId left = static_cast<NodeId>(raw_left);
        NodeId right = static_cast<NodeId>(raw_right);
        if (left > right) std::swap(left, right);
        const auto key = pack_pair(left, right);
        if (!seen.insert(key).second) continue;
        if (edges.size() >= static_cast<std::size_t>(
                               std::numeric_limits<EdgeId>::max())) {
            throw std::overflow_error("edge count exceeds uint32 range");
        }
        edges.emplace_back(left, right);
        const NodeId maximum = std::max(left, right);
        if (maximum == std::numeric_limits<NodeId>::max()) {
            throw std::overflow_error("node count exceeds uint32 range");
        }
        node_count = std::max(node_count, maximum + 1U);
    }
    if (edges.empty()) throw std::runtime_error("input graph is empty");

    std::vector<std::vector<Neighbor>> adjacency(node_count);
    std::unordered_map<std::uint64_t, EdgeId> edge_ids;
    edge_ids.reserve(edges.size() * 2U + 1U);
    for (std::size_t index = 0; index < edges.size(); ++index) {
        const EdgeId edge = static_cast<EdgeId>(index);
        const auto [left, right] = edges[index];
        adjacency[left].push_back({right, edge});
        adjacency[right].push_back({left, edge});
        edge_ids.emplace(pack_pair(left, right), edge);
    }
    for (auto& neighbors : adjacency) {
        std::sort(neighbors.begin(), neighbors.end(),
                  [](const Neighbor& left, const Neighbor& right) {
                      return left.node < right.node;
                  });
    }
    return Graph{std::move(edges), std::move(adjacency), std::move(edge_ids)};
}

struct InitialSupport {
    Count cycles = 0U;
    std::vector<Count> support;
};

InitialSupport enumerate_initial(const Graph& graph) {
    constexpr std::size_t length = 6U;
    InitialSupport output{0U, std::vector<Count>(graph.edges.size(), 0U)};
    std::vector<std::uint8_t> visited(graph.adjacency.size(), 0U);
    std::vector<NodeId> path;
    std::vector<EdgeId> path_edges;
    path.reserve(length);
    path_edges.reserve(length - 1U);

    std::function<void(NodeId, NodeId)> dfs = [&](NodeId start, NodeId current) {
        if (path.size() == length) {
            const EdgeId closing = graph.edge_id(current, start);
            if (closing != kMissingEdge && path[1] < path.back()) {
                if (output.cycles == std::numeric_limits<Count>::max()) {
                    throw std::overflow_error("C6 count exceeds uint64 range");
                }
                ++output.cycles;
                for (const EdgeId edge : path_edges) {
                    if (output.support[edge] == std::numeric_limits<Count>::max()) {
                        throw std::overflow_error("C6 support exceeds uint64 range");
                    }
                    ++output.support[edge];
                }
                if (output.support[closing] == std::numeric_limits<Count>::max()) {
                    throw std::overflow_error("C6 support exceeds uint64 range");
                }
                ++output.support[closing];
            }
            return;
        }
        const bool last = path.size() + 1U == length;
        for (const Neighbor next : graph.adjacency[current]) {
            if (next.node <= start || visited[next.node]) continue;
            if (last && graph.edge_id(next.node, start) == kMissingEdge) continue;
            visited[next.node] = 1U;
            path.push_back(next.node);
            path_edges.push_back(next.edge);
            dfs(start, next.node);
            path_edges.pop_back();
            path.pop_back();
            visited[next.node] = 0U;
        }
    };

    const auto started = Clock::now();
    for (NodeId start = 0; start < graph.adjacency.size(); ++start) {
        path.clear();
        path_edges.clear();
        path.push_back(start);
        visited[start] = 1U;
        dfs(start, start);
        visited[start] = 0U;
        if ((start % 1000U) == 0U && start > 0U) {
            const double seconds = std::chrono::duration<double>(
                Clock::now() - started).count();
            std::cerr << "[C6 enumerate] start_nodes=" << start
                      << " cycles=" << output.cycles
                      << " seconds=" << seconds << "\n";
        }
    }
    return output;
}

std::vector<Count> peel_streaming(
    const Graph& graph,
    const std::vector<Count>& initial_support
) {
    std::vector<Count> current = initial_support;
    std::vector<Count> trussness(graph.edges.size(), 0U);
    std::vector<std::uint8_t> active(graph.edges.size(), 1U);
    using Entry = std::pair<Count, EdgeId>;
    std::priority_queue<Entry, std::vector<Entry>, std::greater<Entry>> heap;
    for (std::size_t edge = 0; edge < current.size(); ++edge) {
        heap.emplace(current[edge], static_cast<EdgeId>(edge));
    }

    // Split a length-5 path into 2 edges from source and 3 from target.
    struct LeftPath {
        std::vector<NodeId> nodes;
        std::vector<EdgeId> edges;
    };
    std::vector<std::uint8_t> visited(graph.adjacency.size(), 0U);
    std::vector<NodeId> path_nodes;
    std::vector<EdgeId> path_edges;
    std::size_t removed_count = 0U;
    const auto started = Clock::now();

    while (!heap.empty()) {
        const auto [value, removed] = heap.top();
        heap.pop();
        if (!active[removed] || value != current[removed]) continue;
        active[removed] = 0U;
        trussness[removed] = value;

        const NodeId source = graph.edges[removed].first;
        const NodeId target = graph.edges[removed].second;
        const Count removal_value = value;

        std::unordered_map<NodeId, std::vector<LeftPath>> left_by_meeting;
        visited[source] = 1U;
        path_nodes.clear();
        path_nodes.push_back(source);
        path_edges.clear();
        std::function<void(NodeId, int)> enumerate_left =
            [&](NodeId current_node, int used_edges) {
                if (used_edges == 2) {
                    left_by_meeting[current_node].push_back(
                        LeftPath{path_nodes, path_edges});
                    return;
                }
                for (const Neighbor next : graph.adjacency[current_node]) {
                    if (!active[next.edge] || visited[next.node] ||
                        next.node == target) {
                        continue;
                    }
                    visited[next.node] = 1U;
                    path_nodes.push_back(next.node);
                    path_edges.push_back(next.edge);
                    enumerate_left(next.node, used_edges + 1);
                    path_edges.pop_back();
                    path_nodes.pop_back();
                    visited[next.node] = 0U;
                }
            };
        enumerate_left(source, 0);
        visited[source] = 0U;

        visited[target] = 1U;
        path_edges.clear();
        std::function<void(NodeId, int)> enumerate_right =
            [&](NodeId current_node, int used_edges) {
                if (used_edges == 3) {
                    const auto found = left_by_meeting.find(current_node);
                    if (found == left_by_meeting.end()) return;
                    for (const LeftPath& left_path : found->second) {
                        bool simple = true;
                        // Meeting node is the last left node and current right
                        // node; all preceding left nodes must be absent from the
                        // right-side visited set.
                        for (std::size_t index = 0;
                             index + 1U < left_path.nodes.size(); ++index) {
                            if (visited[left_path.nodes[index]]) {
                                simple = false;
                                break;
                            }
                        }
                        if (!simple) continue;
                        const auto decrement = [&](EdgeId other) {
                            // Level-clamped peeling is equivalent to raw
                            // decrement plus running maximum labels.
                            if (active[other] && current[other] > removal_value) {
                                --current[other];
                                heap.emplace(current[other], other);
                            }
                        };
                        for (const EdgeId other : left_path.edges) decrement(other);
                        for (const EdgeId other : path_edges) decrement(other);
                    }
                    return;
                }
                for (const Neighbor next : graph.adjacency[current_node]) {
                    if (!active[next.edge] || visited[next.node] ||
                        next.node == source) {
                        continue;
                    }
                    visited[next.node] = 1U;
                    path_edges.push_back(next.edge);
                    enumerate_right(next.node, used_edges + 1);
                    path_edges.pop_back();
                    visited[next.node] = 0U;
                }
            };
        enumerate_right(target, 0);
        visited[target] = 0U;

        ++removed_count;
        if ((removed_count % 10000U) == 0U || heap.empty()) {
            const double seconds = std::chrono::duration<double>(
                Clock::now() - started).count();
            std::cerr << "[C6 peel] removed=" << removed_count
                      << " remaining_heap_entries=" << heap.size()
                      << " level=" << removal_value
                      << " seconds=" << seconds << "\n";
        }
    }
    return trussness;
}

void write_result(
    const std::string& path,
    const Graph& graph,
    const std::vector<Count>& support,
    const std::vector<Count>& trussness
) {
    std::ofstream output(path);
    if (!output) throw std::runtime_error("cannot open output: " + path);
    output << "edge_id\tu\tv\tinitial_support\ttrussness\n";
    for (std::size_t edge = 0; edge < graph.edges.size(); ++edge) {
        output << edge << '\t' << graph.edges[edge].first << '\t'
               << graph.edges[edge].second << '\t' << support[edge] << '\t'
               << trussness[edge] << '\n';
    }
}

}  // namespace

int main(int argc, char** argv) {
    if (argc != 3) {
        std::cerr << "usage: streaming_c6_truss INPUT_EDGE_LIST OUTPUT_TSV\n";
        return 2;
    }
    try {
        const auto total_started = Clock::now();
        const Graph graph = read_graph(argv[1]);
        const auto enumeration_started = Clock::now();
        InitialSupport initial = enumerate_initial(graph);
        const double enumeration_seconds = std::chrono::duration<double>(
            Clock::now() - enumeration_started).count();
        const auto peeling_started = Clock::now();
        std::vector<Count> trussness = peel_streaming(graph, initial.support);
        const double peeling_seconds = std::chrono::duration<double>(
            Clock::now() - peeling_started).count();
        write_result(argv[2], graph, initial.support, trussness);
        const double total_seconds = std::chrono::duration<double>(
            Clock::now() - total_started).count();
        std::cerr << "[C6] vertices=" << graph.adjacency.size()
                  << " edges=" << graph.edges.size()
                  << " cycles=" << initial.cycles
                  << " enumeration_seconds=" << enumeration_seconds
                  << " peeling_seconds=" << peeling_seconds
                  << " total_seconds=" << total_seconds << "\n";
    } catch (const std::exception& error) {
        std::cerr << "error: " << error.what() << '\n';
        return 1;
    }
    return 0;
}
