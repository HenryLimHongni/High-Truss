// Exact edge C3/C4 truss decomposition with bounded motif storage.
//
// C3 initial support is common-neighbor count. C4 initial support and peeling
// use a Bloom-style opposite-pair index: a bloom (x,y) stores vertices p with
// edges (x,p) and (y,p). Each simple C4 is assigned to exactly one of its two
// opposite-pair blooms by a deterministic pair-key order. No C4 incidence
// list is materialized.
//
// Output trussness uses the support-threshold convention: an isolated
// triangle or chordless C4 has trussness 1 on every participating edge.

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <limits>
#include <numeric>
#include <sstream>
#include <stdexcept>
#include <string>
#include <unordered_set>
#include <utility>
#include <vector>

namespace {

using Clock = std::chrono::steady_clock;
using NodeId = std::uint32_t;
using EdgeId = std::uint32_t;
using Count = std::uint64_t;
using Edge = std::pair<NodeId, NodeId>;
constexpr EdgeId kMissingEdge = std::numeric_limits<EdgeId>::max();

std::uint64_t pair_key(NodeId left, NodeId right) {
    if (left > right) std::swap(left, right);
    return (static_cast<std::uint64_t>(left) << 32U) | right;
}

struct InputGraph {
    std::vector<Edge> edges;
    NodeId node_count = 0;
};

InputGraph read_graph(const std::string& path) {
    std::ifstream input(path);
    if (!input) throw std::runtime_error("cannot open input: " + path);
    InputGraph graph;
    std::unordered_set<std::uint64_t> seen;
    std::string line;
    std::size_t line_number = 0;
    while (std::getline(input, line)) {
        ++line_number;
        const auto first = line.find_first_not_of(" \t\r\n");
        if (first == std::string::npos || line[first] == '#') continue;
        for (char& character : line) {
            if (character == ',') character = ' ';
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
        if (!seen.insert(pair_key(left, right)).second) continue;
        if (graph.edges.size() >=
            static_cast<std::size_t>(std::numeric_limits<EdgeId>::max())) {
            throw std::overflow_error("edge count exceeds uint32 range");
        }
        graph.edges.emplace_back(left, right);
        const NodeId maximum = std::max(left, right);
        if (maximum == std::numeric_limits<NodeId>::max()) {
            throw std::overflow_error("node count exceeds uint32 range");
        }
        graph.node_count = std::max(graph.node_count, maximum + 1U);
    }
    if (graph.edges.empty()) throw std::runtime_error("input graph is empty");
    return graph;
}

struct Neighbor {
    NodeId node;
    EdgeId edge;
};

class IndexedMinHeap {
public:
    explicit IndexedMinHeap(const std::vector<Count>& values)
        : values_(values), heap_(values.size()), position_(values.size()) {
        std::iota(heap_.begin(), heap_.end(), 0U);
        for (std::size_t index = 0; index < heap_.size(); ++index) {
            position_[heap_[index]] = static_cast<EdgeId>(index);
        }
        if (!heap_.empty()) {
            for (std::size_t index = heap_.size() / 2U; index-- > 0U;) {
                sift_down(index);
            }
        }
    }
    bool empty() const { return heap_.empty(); }
    std::size_t size() const { return heap_.size(); }
    EdgeId pop_min() {
        if (heap_.empty()) throw std::runtime_error("pop from empty heap");
        const EdgeId result = heap_.front();
        position_[result] = kMissingEdge;
        if (heap_.size() == 1U) {
            heap_.pop_back();
            return result;
        }
        heap_.front() = heap_.back();
        position_[heap_.front()] = 0U;
        heap_.pop_back();
        sift_down(0U);
        return result;
    }
    void decrease(EdgeId edge) {
        const EdgeId position = position_.at(edge);
        if (position != kMissingEdge) sift_up(position);
    }
private:
    bool less(EdgeId left, EdgeId right) const {
        if (values_[left] != values_[right]) return values_[left] < values_[right];
        return left < right;
    }
    void swap_at(std::size_t left, std::size_t right) {
        std::swap(heap_[left], heap_[right]);
        position_[heap_[left]] = static_cast<EdgeId>(left);
        position_[heap_[right]] = static_cast<EdgeId>(right);
    }
    void sift_up(std::size_t index) {
        while (index > 0U) {
            const std::size_t parent = (index - 1U) / 2U;
            if (!less(heap_[index], heap_[parent])) break;
            swap_at(index, parent);
            index = parent;
        }
    }
    void sift_down(std::size_t index) {
        while (true) {
            const std::size_t left = index * 2U + 1U;
            if (left >= heap_.size()) break;
            const std::size_t right = left + 1U;
            std::size_t best = left;
            if (right < heap_.size() && less(heap_[right], heap_[left])) best = right;
            if (!less(heap_[best], heap_[index])) break;
            swap_at(index, best);
            index = best;
        }
    }
    const std::vector<Count>& values_;
    std::vector<EdgeId> heap_;
    std::vector<EdgeId> position_;
};

struct BloomIncidence {
    EdgeId bloom;
    NodeId petal;
};

class ShortCycleTruss {
public:
    ShortCycleTruss(InputGraph graph, int length)
        : graph_(std::move(graph)),
          length_(length),
          adjacency_(graph_.node_count),
          active_(graph_.edges.size(), 1U),
          support_(graph_.edges.size(), 0U),
          initial_support_(graph_.edges.size(), 0U),
          trussness_(graph_.edges.size(), 0U),
          decrement_(graph_.edges.size(), 0U),
          touched_epoch_(graph_.edges.size(), 0U),
          edge_blooms_(graph_.edges.size()) {
        if (length_ != 3 && length_ != 4) {
            throw std::runtime_error("length must be 3 or 4");
        }
        const std::uint64_t n = graph_.node_count;
        const std::uint64_t cells = n * n;
        if (n != 0U && cells / n != n) {
            throw std::overflow_error("dense matrix size overflow");
        }
        if (cells > std::numeric_limits<std::size_t>::max()) {
            throw std::overflow_error("dense matrix exceeds address space");
        }
        common_.assign(static_cast<std::size_t>(cells), 0U);
        edge_lookup_.assign(static_cast<std::size_t>(cells), kMissingEdge);
    }

    void run() {
        build_graph_and_common_counts();
        if (length_ == 3) {
            compute_c3_support();
        } else {
            build_c4_blooms_and_support();
        }
        initial_support_ = support_;
        std::vector<std::uint32_t>().swap(common_);

        IndexedMinHeap heap(support_);
        Count level = 0U;
        std::size_t removed_count = 0U;
        const auto started = Clock::now();
        while (!heap.empty()) {
            const EdgeId removed = heap.pop_min();
            if (!active_[removed]) throw std::runtime_error("heap returned inactive edge");
            level = std::max(level, support_[removed]);
            trussness_[removed] = level;

            touched_.clear();
            bump_epoch();
            if (length_ == 3) aggregate_destroyed_triangles(removed);
            else aggregate_destroyed_c4s(removed);

            for (const EdgeId affected : touched_) {
                if (!active_[affected] || affected == removed) continue;
                const Count value = decrement_[affected];
                if (value > support_[affected]) {
                    throw std::runtime_error("support decrement exceeds current support");
                }
                support_[affected] -= value;
                heap.decrease(affected);
            }
            active_[removed] = 0U;
            ++removed_count;
            if ((removed_count % 10000U) == 0U || heap.empty()) {
                const double seconds = std::chrono::duration<double>(Clock::now() - started).count();
                std::cerr << "[C" << length_ << "] removed=" << removed_count
                          << " remaining=" << heap.size() << " level=" << level
                          << " seconds=" << seconds << "\n";
            }
        }
    }

    void write(const std::string& path) const {
        std::ofstream output(path);
        if (!output) throw std::runtime_error("cannot open output: " + path);
        output << "edge_id\tu\tv\tinitial_support\ttrussness\n";
        for (std::size_t edge = 0; edge < graph_.edges.size(); ++edge) {
            output << edge << '\t' << graph_.edges[edge].first << '\t'
                   << graph_.edges[edge].second << '\t' << initial_support_[edge]
                   << '\t' << trussness_[edge] << '\n';
        }
    }

private:
    std::size_t matrix_index(NodeId left, NodeId right) const {
        return static_cast<std::size_t>(left) * graph_.node_count + right;
    }
    EdgeId edge_id(NodeId left, NodeId right) const {
        return edge_lookup_[matrix_index(left, right)];
    }
    void checked_increment(Count& value) {
        if (value == std::numeric_limits<Count>::max()) {
            throw std::overflow_error("motif support exceeds uint64 range");
        }
        ++value;
    }

    void build_graph_and_common_counts() {
        for (std::size_t raw_edge = 0; raw_edge < graph_.edges.size(); ++raw_edge) {
            const EdgeId edge = static_cast<EdgeId>(raw_edge);
            const auto [left, right] = graph_.edges[edge];
            adjacency_[left].push_back({right, edge});
            adjacency_[right].push_back({left, edge});
            edge_lookup_[matrix_index(left, right)] = edge;
            edge_lookup_[matrix_index(right, left)] = edge;
        }
        for (auto& neighbors : adjacency_) {
            std::sort(neighbors.begin(), neighbors.end(),
                      [](const Neighbor& left, const Neighbor& right) {
                          return left.node < right.node;
                      });
        }
        for (NodeId middle = 0; middle < graph_.node_count; ++middle) {
            const auto& neighbors = adjacency_[middle];
            for (std::size_t i = 0; i < neighbors.size(); ++i) {
                for (std::size_t j = i + 1U; j < neighbors.size(); ++j) {
                    const NodeId left = neighbors[i].node;
                    const NodeId right = neighbors[j].node;
                    std::uint32_t& forward = common_[matrix_index(left, right)];
                    std::uint32_t& reverse = common_[matrix_index(right, left)];
                    if (forward == std::numeric_limits<std::uint32_t>::max()) {
                        throw std::overflow_error("common-neighbor count exceeds uint32 range");
                    }
                    ++forward;
                    ++reverse;
                }
            }
        }
    }

    void compute_c3_support() {
        for (std::size_t raw_edge = 0; raw_edge < graph_.edges.size(); ++raw_edge) {
            const EdgeId edge = static_cast<EdgeId>(raw_edge);
            const auto [left, right] = graph_.edges[edge];
            support_[edge] = common_[matrix_index(left, right)];
        }
    }

    void build_c4_blooms_and_support() {
        const std::uint64_t cells =
            static_cast<std::uint64_t>(graph_.node_count) * graph_.node_count;
        pair_to_bloom_.assign(static_cast<std::size_t>(cells), kMissingEdge);
        for (NodeId left = 0; left < graph_.node_count; ++left) {
            for (NodeId right = left + 1U; right < graph_.node_count; ++right) {
                const std::uint32_t count = common_[matrix_index(left, right)];
                if (count < 2U) continue;
                if (bloom_left_.size() >=
                    static_cast<std::size_t>(std::numeric_limits<EdgeId>::max())) {
                    throw std::overflow_error("bloom count exceeds uint32 range");
                }
                const EdgeId bloom = static_cast<EdgeId>(bloom_left_.size());
                pair_to_bloom_[matrix_index(left, right)] = bloom;
                pair_to_bloom_[matrix_index(right, left)] = bloom;
                bloom_left_.push_back(left);
                bloom_right_.push_back(right);
                bloom_offsets_.push_back(0U);
            }
        }
        bloom_offsets_.push_back(0U);
        for (std::size_t bloom = 0; bloom < bloom_left_.size(); ++bloom) {
            const std::uint32_t count =
                common_[matrix_index(bloom_left_[bloom], bloom_right_[bloom])];
            if (std::numeric_limits<std::size_t>::max() - bloom_offsets_[bloom] < count) {
                throw std::overflow_error("bloom petal storage overflow");
            }
            bloom_offsets_[bloom + 1U] = bloom_offsets_[bloom] + count;
        }
        bloom_petals_.assign(bloom_offsets_.back(), 0U);
        std::vector<std::size_t> cursor = bloom_offsets_;
        for (NodeId middle = 0; middle < graph_.node_count; ++middle) {
            const auto& neighbors = adjacency_[middle];
            for (std::size_t i = 0; i < neighbors.size(); ++i) {
                for (std::size_t j = i + 1U; j < neighbors.size(); ++j) {
                    const EdgeId bloom = pair_to_bloom_[
                        matrix_index(neighbors[i].node, neighbors[j].node)];
                    if (bloom != kMissingEdge) {
                        bloom_petals_[cursor[bloom]++] = middle;
                    }
                }
            }
        }

        for (std::size_t raw_bloom = 0; raw_bloom < bloom_left_.size(); ++raw_bloom) {
            const EdgeId bloom = static_cast<EdgeId>(raw_bloom);
            const NodeId left = bloom_left_[bloom];
            const NodeId right = bloom_right_[bloom];
            const std::uint64_t endpoints_key = pair_key(left, right);
            const std::size_t begin = bloom_offsets_[bloom];
            const std::size_t end = bloom_offsets_[bloom + 1U];

            for (std::size_t i = begin; i < end; ++i) {
                const NodeId petal = bloom_petals_[i];
                const EdgeId left_petal = edge_id(left, petal);
                const EdgeId right_petal = edge_id(right, petal);
                if (left_petal == kMissingEdge || right_petal == kMissingEdge) {
                    throw std::runtime_error("invalid bloom petal");
                }
                edge_blooms_[left_petal].push_back({bloom, petal});
                edge_blooms_[right_petal].push_back({bloom, petal});
            }

            for (std::size_t i = begin; i < end; ++i) {
                const NodeId first = bloom_petals_[i];
                for (std::size_t j = i + 1U; j < end; ++j) {
                    const NodeId second = bloom_petals_[j];
                    if (endpoints_key >= pair_key(first, second)) continue;
                    checked_increment(support_[edge_id(left, first)]);
                    checked_increment(support_[edge_id(right, first)]);
                    checked_increment(support_[edge_id(left, second)]);
                    checked_increment(support_[edge_id(right, second)]);
                }
            }
        }
        std::vector<EdgeId>().swap(pair_to_bloom_);
        std::cerr << "[C4] blooms=" << bloom_left_.size()
                  << " bloom_petals=" << bloom_petals_.size() << "\n";
    }

    void bump_epoch() {
        ++epoch_;
        if (epoch_ == 0U) {
            std::fill(touched_epoch_.begin(), touched_epoch_.end(), 0U);
            epoch_ = 1U;
        }
    }
    void add_decrement(EdgeId edge, EdgeId removed) {
        if (edge == kMissingEdge || edge == removed || !active_[edge]) return;
        if (touched_epoch_[edge] != epoch_) {
            touched_epoch_[edge] = epoch_;
            decrement_[edge] = 1U;
            touched_.push_back(edge);
        } else {
            if (decrement_[edge] == std::numeric_limits<Count>::max()) {
                throw std::overflow_error("support decrement overflow");
            }
            ++decrement_[edge];
        }
    }

    void aggregate_destroyed_triangles(EdgeId removed) {
        auto [source, target] = graph_.edges[removed];
        if (adjacency_[source].size() > adjacency_[target].size()) {
            std::swap(source, target);
        }
        for (const Neighbor first : adjacency_[source]) {
            if (!active_[first.edge] || first.node == target) continue;
            const EdgeId second = edge_id(first.node, target);
            if (second == kMissingEdge || !active_[second]) continue;
            add_decrement(first.edge, removed);
            add_decrement(second, removed);
        }
    }

    void aggregate_destroyed_c4s(EdgeId removed) {
        for (const BloomIncidence incidence : edge_blooms_[removed]) {
            const EdgeId bloom = incidence.bloom;
            const NodeId petal = incidence.petal;
            const NodeId left = bloom_left_[bloom];
            const NodeId right = bloom_right_[bloom];
            const EdgeId left_petal = edge_id(left, petal);
            const EdgeId right_petal = edge_id(right, petal);
            if (!active_[left_petal] || !active_[right_petal]) continue;
            const std::uint64_t endpoints_key = pair_key(left, right);
            const std::size_t begin = bloom_offsets_[bloom];
            const std::size_t end = bloom_offsets_[bloom + 1U];
            for (std::size_t index = begin; index < end; ++index) {
                const NodeId other = bloom_petals_[index];
                if (other == petal) continue;
                if (endpoints_key >= pair_key(petal, other)) continue;
                const EdgeId left_other = edge_id(left, other);
                const EdgeId right_other = edge_id(right, other);
                if (!active_[left_other] || !active_[right_other]) continue;
                add_decrement(left_petal, removed);
                add_decrement(right_petal, removed);
                add_decrement(left_other, removed);
                add_decrement(right_other, removed);
            }
        }
    }

    InputGraph graph_;
    int length_;
    std::vector<std::vector<Neighbor>> adjacency_;
    std::vector<std::uint8_t> active_;
    std::vector<Count> support_;
    std::vector<Count> initial_support_;
    std::vector<Count> trussness_;
    std::vector<Count> decrement_;
    std::vector<std::uint32_t> touched_epoch_;
    std::uint32_t epoch_ = 0U;
    std::vector<EdgeId> touched_;
    std::vector<std::uint32_t> common_;
    std::vector<EdgeId> edge_lookup_;

    std::vector<EdgeId> pair_to_bloom_;
    std::vector<NodeId> bloom_left_;
    std::vector<NodeId> bloom_right_;
    std::vector<std::size_t> bloom_offsets_;
    std::vector<NodeId> bloom_petals_;
    std::vector<std::vector<BloomIncidence>> edge_blooms_;
};

int parse_length(const std::string& text) {
    const int length = std::stoi(text);
    if (length != 3 && length != 4) {
        throw std::runtime_error("--length must be 3 or 4");
    }
    return length;
}

}  // namespace

int main(int argc, char** argv) {
    if (argc != 5 || std::string(argv[1]) != "--length") {
        std::cerr << "usage: short_cycle_truss --length {3|4} INPUT OUTPUT\n";
        return 2;
    }
    try {
        const int length = parse_length(argv[2]);
        ShortCycleTruss decomposition(read_graph(argv[3]), length);
        decomposition.run();
        decomposition.write(argv[4]);
    } catch (const std::exception& error) {
        std::cerr << "error: " << error.what() << '\n';
        return 1;
    }
    return 0;
}
