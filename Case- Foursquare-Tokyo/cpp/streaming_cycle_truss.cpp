// Exact memory-bounded C3--C6 edge-truss decomposition.
//
// Unlike the project's fast incidence backend, this implementation does not
// materialize every cycle.  It first enumerates cycles once to obtain initial
// edge support. During peeling, when an edge is removed, it enumerates the
// still-active length-(l-1) paths between that edge's endpoints.

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <fstream>
#include <functional>
#include <iomanip>
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

using NodeId = std::uint32_t;
using EdgeId = std::uint32_t;
using Count = std::uint64_t;
constexpr EdgeId kMissingEdge = std::numeric_limits<EdgeId>::max();

std::uint64_t pack_pair(NodeId left, NodeId right) {
    if (left > right) std::swap(left, right);
    return (static_cast<std::uint64_t>(left) << 32U) |
           static_cast<std::uint64_t>(right);
}

std::string trim(const std::string& value) {
    const auto first = value.find_first_not_of(" \t\r\n");
    if (first == std::string::npos) return {};
    const auto last = value.find_last_not_of(" \t\r\n");
    return value.substr(first, last - first + 1);
}

std::string csv_escape(const std::string& value) {
    if (value.find_first_of(",\"\r\n") == std::string::npos) return value;
    std::string escaped{"\""};
    for (char character : value) {
        if (character == '"') escaped.push_back('"');
        escaped.push_back(character);
    }
    escaped.push_back('"');
    return escaped;
}

std::vector<int> parse_lengths(const std::string& encoded) {
    std::vector<int> output;
    std::stringstream stream(encoded);
    std::string token;
    while (std::getline(stream, token, ',')) {
        token = trim(token);
        if (token.empty()) continue;
        const int value = std::stoi(token);
        if (value < 3 || value > 6) {
            throw std::runtime_error("only lengths 3,4,5,6 are supported");
        }
        output.push_back(value);
    }
    std::sort(output.begin(), output.end());
    output.erase(std::unique(output.begin(), output.end()), output.end());
    if (output.empty()) throw std::runtime_error("empty --lengths");
    return output;
}

struct Options {
    std::string input;
    std::string edges_output;
    std::string counts_output;
    std::vector<int> lengths{3, 4, 5, 6};
    bool quiet = false;
};

Options parse_options(int argc, char** argv) {
    Options options;
    for (int index = 1; index < argc; ++index) {
        const std::string argument = argv[index];
        const auto value = [&]() {
            if (index + 1 >= argc) {
                throw std::runtime_error(argument + " requires a value");
            }
            return std::string(argv[++index]);
        };
        if (argument == "--input") options.input = value();
        else if (argument == "--edges-out") options.edges_output = value();
        else if (argument == "--counts-out") options.counts_output = value();
        else if (argument == "--lengths") {
            options.lengths = parse_lengths(value());
        } else if (argument == "--quiet") {
            options.quiet = true;
        } else {
            throw std::runtime_error("unknown argument: " + argument);
        }
    }
    if (options.input.empty() || options.edges_output.empty() ||
        options.counts_output.empty()) {
        throw std::runtime_error(
            "--input, --edges-out, and --counts-out are required");
    }
    return options;
}

struct Graph {
    std::vector<std::string> names;
    std::vector<std::pair<NodeId, NodeId>> edges;
    std::vector<std::vector<std::pair<NodeId, EdgeId>>> adjacency;
    std::unordered_map<std::uint64_t, EdgeId> edge_ids;

    EdgeId edge_id(NodeId left, NodeId right) const {
        const auto found = edge_ids.find(pack_pair(left, right));
        return found == edge_ids.end() ? kMissingEdge : found->second;
    }
};

Graph read_graph(const std::string& path) {
    std::ifstream input(path);
    if (!input) throw std::runtime_error("cannot open input: " + path);
    std::unordered_map<std::string, NodeId> ids;
    std::unordered_set<std::uint64_t> seen;
    std::vector<std::string> names;
    std::vector<std::pair<NodeId, NodeId>> edges;
    const auto node_id = [&](const std::string& name) -> NodeId {
        const auto found = ids.find(name);
        if (found != ids.end()) return found->second;
        const NodeId created = static_cast<NodeId>(names.size());
        ids.emplace(name, created);
        names.push_back(name);
        return created;
    };
    std::string line;
    while (std::getline(input, line)) {
        line = trim(line);
        if (line.empty() || line[0] == '#') continue;
        std::istringstream stream(line);
        std::string left_name, right_name, extra;
        if (!(stream >> left_name >> right_name) || (stream >> extra)) {
            throw std::runtime_error("expected two columns per input row");
        }
        if (left_name == right_name) {
            throw std::runtime_error("self-loop is not supported");
        }
        NodeId left = node_id(left_name);
        NodeId right = node_id(right_name);
        if (left > right) std::swap(left, right);
        if (seen.insert(pack_pair(left, right)).second) {
            edges.emplace_back(left, right);
        }
    }
    std::sort(edges.begin(), edges.end());
    std::vector<std::vector<std::pair<NodeId, EdgeId>>> adjacency(names.size());
    std::unordered_map<std::uint64_t, EdgeId> edge_ids;
    edge_ids.reserve(edges.size() * 2 + 1);
    for (std::size_t index = 0; index < edges.size(); ++index) {
        const EdgeId edge = static_cast<EdgeId>(index);
        const auto [left, right] = edges[index];
        adjacency[left].emplace_back(right, edge);
        adjacency[right].emplace_back(left, edge);
        edge_ids.emplace(pack_pair(left, right), edge);
    }
    for (auto& values : adjacency) std::sort(values.begin(), values.end());
    return Graph{
        std::move(names),
        std::move(edges),
        std::move(adjacency),
        std::move(edge_ids),
    };
}

struct InitialCounts {
    Count cycles = 0;
    std::vector<Count> support;
    std::vector<Count> user_histogram;
};

InitialCounts enumerate_initial(const Graph& graph, int length) {
    InitialCounts output{
        0,
        std::vector<Count>(graph.edges.size(), 0),
        std::vector<Count>(static_cast<std::size_t>(length + 1), 0),
    };
    std::vector<std::uint8_t> visited(graph.names.size(), 0);
    std::vector<NodeId> path;
    std::vector<EdgeId> path_edges;
    path.reserve(static_cast<std::size_t>(length));
    path_edges.reserve(static_cast<std::size_t>(length));

    std::function<void(NodeId, NodeId)> dfs =
        [&](NodeId start, NodeId current) {
            if (path.size() == static_cast<std::size_t>(length)) {
                const EdgeId closing = graph.edge_id(current, start);
                if (closing != kMissingEdge && path[1] < path.back()) {
                    ++output.cycles;
                    for (EdgeId edge : path_edges) ++output.support[edge];
                    ++output.support[closing];
                    std::size_t users = 0;
                    for (NodeId node : path) {
                        users += graph.names[node].rfind("U:", 0) == 0;
                    }
                    ++output.user_histogram[users];
                }
                return;
            }
            const bool last =
                path.size() + 1 == static_cast<std::size_t>(length);
            for (const auto [neighbor, edge] : graph.adjacency[current]) {
                if (neighbor <= start || visited[neighbor]) continue;
                if (last && graph.edge_id(neighbor, start) == kMissingEdge) {
                    continue;
                }
                visited[neighbor] = 1;
                path.push_back(neighbor);
                path_edges.push_back(edge);
                dfs(start, neighbor);
                path_edges.pop_back();
                path.pop_back();
                visited[neighbor] = 0;
            }
        };

    for (NodeId start = 0; start < graph.names.size(); ++start) {
        path.clear();
        path_edges.clear();
        path.push_back(start);
        visited[start] = 1;
        dfs(start, start);
        visited[start] = 0;
    }
    return output;
}

std::vector<Count> peel_streaming(
    const Graph& graph,
    int length,
    const std::vector<Count>& initial_support
) {
    std::vector<Count> current = initial_support;
    std::vector<Count> trussness(graph.edges.size(), 0);
    std::vector<std::uint8_t> active(graph.edges.size(), 1);
    using Entry = std::pair<Count, EdgeId>;
    std::priority_queue<Entry, std::vector<Entry>, std::greater<Entry>> heap;
    for (std::size_t edge = 0; edge < current.size(); ++edge) {
        heap.emplace(current[edge], static_cast<EdgeId>(edge));
    }

    std::vector<std::uint8_t> visited(graph.names.size(), 0);
    struct LeftPath {
        std::vector<NodeId> nodes;
        std::vector<EdgeId> edges;
    };
    std::vector<NodeId> path_nodes;
    std::vector<EdgeId> path_edges;
    std::vector<std::uint32_t> touched_stamp(graph.edges.size(), 0);
    std::uint32_t stamp = 0;
    std::vector<EdgeId> touched;

    while (!heap.empty()) {
        const auto [value, removed] = heap.top();
        heap.pop();
        if (!active[removed] || value != current[removed]) continue;
        active[removed] = 0;
        trussness[removed] = value;

        if (++stamp == 0) {
            std::fill(touched_stamp.begin(), touched_stamp.end(), 0);
            stamp = 1;
        }
        touched.clear();
        const NodeId source = graph.edges[removed].first;
        const NodeId target = graph.edges[removed].second;
        const Count removal_value = value;
        const int total_path_length = length - 1;
        const int left_length = total_path_length / 2;
        const int right_length = total_path_length - left_length;

        std::unordered_map<NodeId, std::vector<LeftPath>> left_by_meeting;
        visited[source] = 1;
        path_nodes.clear();
        path_nodes.push_back(source);
        path_edges.clear();
        std::function<void(NodeId, int)> enumerate_left =
            [&](NodeId current_node, int used_edges) {
                if (used_edges == left_length) {
                    left_by_meeting[current_node].push_back(
                        LeftPath{path_nodes, path_edges}
                    );
                    return;
                }
                for (const auto [neighbor, edge] :
                     graph.adjacency[current_node]) {
                    if (!active[edge] || visited[neighbor]) continue;
                    if (neighbor == target) continue;
                    visited[neighbor] = 1;
                    path_nodes.push_back(neighbor);
                    path_edges.push_back(edge);
                    enumerate_left(neighbor, used_edges + 1);
                    path_edges.pop_back();
                    path_nodes.pop_back();
                    visited[neighbor] = 0;
                }
            };
        enumerate_left(source, 0);
        visited[source] = 0;

        visited[target] = 1;
        path_edges.clear();
        std::function<void(NodeId, int)> enumerate_right =
            [&](NodeId current_node, int used_edges) {
                if (used_edges == right_length) {
                    const auto found = left_by_meeting.find(current_node);
                    if (found == left_by_meeting.end()) return;
                    for (const LeftPath& left_path : found->second) {
                        bool simple = true;
                        for (
                            std::size_t index = 0;
                            index + 1 < left_path.nodes.size();
                            ++index
                        ) {
                            if (visited[left_path.nodes[index]]) {
                                simple = false;
                                break;
                            }
                        }
                        if (!simple) continue;
                        const auto decrement = [&](EdgeId other) {
                            if (
                                active[other] &&
                                current[other] > removal_value
                            ) {
                                --current[other];
                                if (touched_stamp[other] != stamp) {
                                    touched_stamp[other] = stamp;
                                    touched.push_back(other);
                                }
                            }
                        };
                        for (EdgeId other : left_path.edges) decrement(other);
                        for (EdgeId other : path_edges) decrement(other);
                    }
                    return;
                }
                for (const auto [neighbor, edge] :
                     graph.adjacency[current_node]) {
                    if (!active[edge] || visited[neighbor]) continue;
                    if (neighbor == source) continue;
                    visited[neighbor] = 1;
                    path_edges.push_back(edge);
                    enumerate_right(neighbor, used_edges + 1);
                    path_edges.pop_back();
                    visited[neighbor] = 0;
                }
            };
        enumerate_right(target, 0);
        visited[target] = 0;
        for (EdgeId edge : touched) heap.emplace(current[edge], edge);
    }
    return trussness;
}

struct Result {
    int length;
    Count cycles;
    double enumeration_seconds;
    double peeling_seconds;
    std::vector<Count> support;
    std::vector<Count> trussness;
    std::vector<Count> user_histogram;
};

Result compute(const Graph& graph, int length) {
    const auto start_enumeration = std::chrono::steady_clock::now();
    InitialCounts initial = enumerate_initial(graph, length);
    const auto end_enumeration = std::chrono::steady_clock::now();
    const auto start_peeling = std::chrono::steady_clock::now();
    std::vector<Count> trussness =
        peel_streaming(graph, length, initial.support);
    const auto end_peeling = std::chrono::steady_clock::now();
    return Result{
        length,
        initial.cycles,
        std::chrono::duration<double>(
            end_enumeration - start_enumeration).count(),
        std::chrono::duration<double>(
            end_peeling - start_peeling).count(),
        std::move(initial.support),
        std::move(trussness),
        std::move(initial.user_histogram),
    };
}

void write_outputs(
    const Options& options,
    const Graph& graph,
    const std::vector<Result>& results
) {
    std::ofstream edges(options.edges_output);
    if (!edges) throw std::runtime_error("cannot open edges output");
    edges << "src,dst";
    for (const auto& result : results) {
        edges << ",support_c" << result.length
              << ",trussness_c" << result.length;
    }
    edges << '\n';
    for (std::size_t edge = 0; edge < graph.edges.size(); ++edge) {
        const auto [left, right] = graph.edges[edge];
        edges << csv_escape(graph.names[left]) << ','
              << csv_escape(graph.names[right]);
        for (const auto& result : results) {
            edges << ',' << result.support[edge]
                  << ',' << result.trussness[edge];
        }
        edges << '\n';
    }

    std::ofstream counts(options.counts_output);
    if (!counts) throw std::runtime_error("cannot open counts output");
    counts << "cycle_length,cycle_count,incidence_count,"
              "enumeration_seconds,peeling_seconds";
    for (int users = 0; users <= 6; ++users) {
        counts << ",cycles_with_" << users << "_users";
    }
    counts << '\n' << std::setprecision(10);
    for (const auto& result : results) {
        counts << result.length << ',' << result.cycles << ','
               << result.cycles * static_cast<Count>(result.length) << ','
               << result.enumeration_seconds << ','
               << result.peeling_seconds;
        for (int users = 0; users <= 6; ++users) {
            counts << ','
                   << (users < static_cast<int>(result.user_histogram.size())
                           ? result.user_histogram[users]
                           : 0);
        }
        counts << '\n';
    }
}

}  // namespace

int main(int argc, char** argv) {
    try {
        const Options options = parse_options(argc, argv);
        const Graph graph = read_graph(options.input);
        if (!options.quiet) {
            std::cerr << "Loaded " << graph.names.size() << " nodes and "
                      << graph.edges.size() << " edges\n";
        }
        std::vector<Result> results;
        for (int length : options.lengths) {
            Result result = compute(graph, length);
            if (!options.quiet) {
                std::cerr << "C" << length << ": " << result.cycles
                          << " cycles, " << result.enumeration_seconds
                          << " s enumerate, " << result.peeling_seconds
                          << " s streaming peel\n";
            }
            results.push_back(std::move(result));
        }
        write_outputs(options, graph, results);
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "error: " << error.what() << '\n';
        return 1;
    }
}
