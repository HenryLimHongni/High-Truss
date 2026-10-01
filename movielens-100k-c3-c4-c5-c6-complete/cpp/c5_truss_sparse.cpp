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

// Exact sparse simple-C5 edge-truss decomposition.
//
// The implementation stores A^2 only for unordered vertex pairs that have at
// least one common neighbor. It never materializes C5s, length-3 paths, or
// cycle-edge incidence lists. During peeling it updates A^2 and aggregates all
// active C5s destroyed by the removed edge. The reported trussness follows the
// support-threshold convention: an isolated chordless C5 has value 1.

namespace {

using Clock = std::chrono::steady_clock;
using Edge = std::pair<std::uint32_t, std::uint32_t>;
constexpr std::uint64_t kEmptyKey = std::numeric_limits<std::uint64_t>::max();

std::uint64_t pack_pair(std::uint32_t left, std::uint32_t right) {
    if (left > right) std::swap(left, right);
    return (static_cast<std::uint64_t>(left) << 32U) | right;
}

std::uint64_t mix64(std::uint64_t value) {
    value += 0x9e3779b97f4a7c15ULL;
    value = (value ^ (value >> 30U)) * 0xbf58476d1ce4e5b9ULL;
    value = (value ^ (value >> 27U)) * 0x94d049bb133111ebULL;
    return value ^ (value >> 31U);
}

class PairTable {
public:
    explicit PairTable(std::size_t expected = 0U) { reserve(expected); }

    void reserve(std::size_t expected) {
        std::size_t capacity = 8U;
        while (expected > max_entries(capacity)) {
            if (capacity > std::numeric_limits<std::size_t>::max() / 2U) {
                throw std::overflow_error("pair table capacity overflow");
            }
            capacity *= 2U;
        }
        if (capacity > keys_.size()) rehash(capacity);
    }

    std::size_t size() const { return size_; }
    std::size_t capacity() const { return keys_.size(); }

    std::uint32_t get(std::uint64_t key) const {
        const std::size_t slot = find_slot(key);
        return keys_[slot] == key ? values_[slot] : 0U;
    }

    void increment(std::uint64_t key) {
        ensure_insert_capacity();
        const std::size_t slot = find_slot(key);
        if (keys_[slot] == kEmptyKey) {
            keys_[slot] = key;
            values_[slot] = 1U;
            ++size_;
            return;
        }
        if (values_[slot] == std::numeric_limits<std::uint32_t>::max()) {
            throw std::overflow_error("A^2 entry exceeds uint32 range");
        }
        ++values_[slot];
    }

    void insert_unique(std::uint64_t key, std::uint32_t value) {
        ensure_insert_capacity();
        const std::size_t slot = find_slot(key);
        if (keys_[slot] != kEmptyKey) {
            throw std::runtime_error("duplicate key in pair table");
        }
        keys_[slot] = key;
        values_[slot] = value;
        ++size_;
    }

    void decrement(std::uint64_t key) {
        const std::size_t slot = find_slot(key);
        if (keys_[slot] != key || values_[slot] == 0U) {
            throw std::runtime_error("A^2 underflow while deleting edge");
        }
        --values_[slot];
    }

    template <typename Function>
    void for_each(Function&& function) const {
        for (std::size_t slot = 0; slot < keys_.size(); ++slot) {
            if (keys_[slot] != kEmptyKey) {
                function(keys_[slot], values_[slot]);
            }
        }
    }

private:
    static std::size_t max_entries(std::size_t capacity) {
        return capacity - capacity / 4U;
    }

    std::size_t find_slot(std::uint64_t key) const {
        const std::size_t mask = keys_.size() - 1U;
        std::size_t slot = static_cast<std::size_t>(mix64(key)) & mask;
        while (keys_[slot] != kEmptyKey && keys_[slot] != key) {
            slot = (slot + 1U) & mask;
        }
        return slot;
    }

    void ensure_insert_capacity() {
        if (size_ + 1U > max_entries(keys_.size())) {
            if (keys_.size() > std::numeric_limits<std::size_t>::max() / 2U) {
                throw std::overflow_error("pair table capacity overflow");
            }
            rehash(keys_.size() * 2U);
        }
    }

    void rehash(std::size_t capacity) {
        std::vector<std::uint64_t> old_keys = std::move(keys_);
        std::vector<std::uint32_t> old_values = std::move(values_);
        const std::size_t old_size = size_;
        keys_.assign(capacity, kEmptyKey);
        values_.assign(capacity, 0U);
        size_ = 0U;
        for (std::size_t slot = 0; slot < old_keys.size(); ++slot) {
            if (old_keys[slot] == kEmptyKey) continue;
            const std::size_t destination = find_slot(old_keys[slot]);
            keys_[destination] = old_keys[slot];
            values_[destination] = old_values[slot];
            ++size_;
        }
        if (size_ != old_size) {
            throw std::runtime_error("pair table rehash lost entries");
        }
    }

    std::vector<std::uint64_t> keys_;
    std::vector<std::uint32_t> values_;
    std::size_t size_ = 0U;
};

struct InputGraph {
    std::vector<Edge> edges;
    std::uint32_t node_count = 0U;
};

InputGraph read_graph(const std::string& path) {
    std::ifstream input(path);
    if (!input) throw std::runtime_error("cannot open input: " + path);

    InputGraph graph;
    std::unordered_set<std::uint64_t> seen;
    std::string line;
    while (std::getline(input, line)) {
        const auto first_non_space = line.find_first_not_of(" \t\r\n");
        if (first_non_space == std::string::npos || line[first_non_space] == '#') continue;
        for (char& character : line) {
            if (character == ',') character = ' ';
        }
        std::istringstream stream(line);
        std::int64_t raw_left = -1;
        std::int64_t raw_right = -1;
        std::string extra;
        if (!(stream >> raw_left >> raw_right) || (stream >> extra)) {
            throw std::runtime_error("each input row must contain exactly two integers");
        }
        if (raw_left < 0 || raw_right < 0) {
            throw std::runtime_error("node IDs must be non-negative");
        }
        if (raw_left == raw_right) continue;
        if (raw_left >= std::numeric_limits<std::uint32_t>::max() ||
            raw_right >= std::numeric_limits<std::uint32_t>::max()) {
            throw std::runtime_error("node ID exceeds uint32 range");
        }
        std::uint32_t left = static_cast<std::uint32_t>(raw_left);
        std::uint32_t right = static_cast<std::uint32_t>(raw_right);
        if (left > right) std::swap(left, right);
        if (seen.insert(pack_pair(left, right)).second) {
            if (graph.edges.size() >= std::numeric_limits<std::uint32_t>::max()) {
                throw std::overflow_error("edge count exceeds uint32 range");
            }
            graph.edges.emplace_back(left, right);
            graph.node_count = std::max(graph.node_count, std::max(left, right) + 1U);
        }
    }
    if (graph.edges.empty()) throw std::runtime_error("input graph is empty");
    return graph;
}

std::uint64_t checked_support(
    std::uint64_t a4,
    std::uint64_t triangle_walk_left,
    std::uint64_t triangle_walk_right,
    std::uint64_t degree_left,
    std::uint64_t degree_right,
    std::uint64_t common_neighbors,
    std::uint64_t common_degree_sum
) {
    const __int128 value = static_cast<__int128>(a4)
        - static_cast<__int128>(triangle_walk_left)
        - static_cast<__int128>(triangle_walk_right)
        - (static_cast<__int128>(degree_left + degree_right) - 5)
              * static_cast<__int128>(common_neighbors)
        - static_cast<__int128>(common_degree_sum);
    if (value < 0) {
        throw std::runtime_error("negative C5 support: internal invariant failed");
    }
    if (value > static_cast<__int128>(std::numeric_limits<std::uint64_t>::max())) {
        throw std::overflow_error("C5 support exceeds uint64 range");
    }
    return static_cast<std::uint64_t>(value);
}

class SparseC5Truss {
public:
    explicit SparseC5Truss(InputGraph graph)
        : graph_(std::move(graph)),
          active_(graph_.edges.size(), 1U),
          initial_support_(graph_.edges.size(), 0U),
          trussness_(graph_.edges.size(), 0U),
          support_(graph_.edges.size(), 0U),
          adjacency_(graph_.node_count),
          degree_(graph_.node_count, 0U),
          triangle_walk_(graph_.node_count, 0U),
          pair_counts_(initial_table_estimate(graph_.edges.size())),
          edge_index_(initial_table_estimate(graph_.edges.size())),
          decrement_(graph_.edges.size(), 0U),
          touched_epoch_(graph_.edges.size(), 0U) {}

    void run() {
        build_initial_state();
        compute_supports();
        initial_support_ = support_;
        IndexedMinHeap heap(support_);

        std::uint64_t level = 0U;
        std::size_t removed = 0U;
        const auto started = Clock::now();
        while (!heap.empty()) {
            const std::uint32_t edge_id = heap.pop_min();
            if (!active_[edge_id]) {
                throw std::runtime_error("heap returned an inactive edge");
            }
            level = std::max(level, support_[edge_id]);
            trussness_[edge_id] = level;

            touched_.clear();
            bump_epoch();
            aggregate_destroyed_cycles(edge_id);
            for (const std::uint32_t affected : touched_) {
                if (!active_[affected] || affected == edge_id) continue;
                const std::uint64_t value = decrement_[affected];
                if (value > support_[affected]) {
                    throw std::runtime_error("C5 support decrement exceeds current support");
                }
                support_[affected] -= value;
                heap.decrease(affected);
            }
            remove_from_active_graph(edge_id);
            ++removed;
            if ((removed % 10000U) == 0U || heap.empty()) {
                const double seconds = std::chrono::duration<double>(Clock::now() - started).count();
                std::cerr << "[C5] removed=" << removed
                          << " remaining=" << heap.size()
                          << " level=" << level
                          << " seconds=" << seconds << '\n';
            }
        }
    }

    void write(const std::string& path) const {
        std::ofstream output(path);
        if (!output) throw std::runtime_error("cannot open output: " + path);
        output << "edge_id\tu\tv\tinitial_support\ttrussness\n";
        for (std::size_t edge_id = 0; edge_id < graph_.edges.size(); ++edge_id) {
            output << edge_id << '\t'
                   << graph_.edges[edge_id].first << '\t'
                   << graph_.edges[edge_id].second << '\t'
                   << initial_support_[edge_id] << '\t'
                   << trussness_[edge_id] << '\n';
        }
    }

private:
    struct Neighbor {
        std::uint32_t node;
        std::uint32_t edge_id;
    };

    class IndexedMinHeap {
    public:
        explicit IndexedMinHeap(const std::vector<std::uint64_t>& values)
            : values_(values), heap_(values.size()), position_(values.size()) {
            std::iota(heap_.begin(), heap_.end(), 0U);
            for (std::size_t index = 0; index < heap_.size(); ++index) {
                position_[heap_[index]] = static_cast<std::uint32_t>(index);
            }
            if (!heap_.empty()) {
                for (std::size_t index = heap_.size() / 2U; index-- > 0U;) {
                    sift_down(index);
                }
            }
        }

        bool empty() const { return heap_.empty(); }
        std::size_t size() const { return heap_.size(); }

        std::uint32_t pop_min() {
            if (heap_.empty()) throw std::runtime_error("pop from empty heap");
            const std::uint32_t result = heap_.front();
            position_[result] = kRemoved;
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

        void decrease(std::uint32_t edge_id) {
            const std::uint32_t position = position_.at(edge_id);
            if (position != kRemoved) sift_up(position);
        }

    private:
        bool less(std::uint32_t left, std::uint32_t right) const {
            if (values_[left] != values_[right]) return values_[left] < values_[right];
            return left < right;
        }

        void swap_at(std::size_t left, std::size_t right) {
            std::swap(heap_[left], heap_[right]);
            position_[heap_[left]] = static_cast<std::uint32_t>(left);
            position_[heap_[right]] = static_cast<std::uint32_t>(right);
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

        static constexpr std::uint32_t kRemoved = std::numeric_limits<std::uint32_t>::max();
        const std::vector<std::uint64_t>& values_;
        std::vector<std::uint32_t> heap_;
        std::vector<std::uint32_t> position_;
    };

    static std::size_t initial_table_estimate(std::size_t edge_count) {
        if (edge_count > std::numeric_limits<std::size_t>::max() / 2U) {
            throw std::overflow_error("edge-derived table estimate overflow");
        }
        return std::max<std::size_t>(1024U, edge_count * 2U);
    }

    void build_initial_state() {
        for (std::size_t edge_id = 0; edge_id < graph_.edges.size(); ++edge_id) {
            const auto [left, right] = graph_.edges[edge_id];
            adjacency_[left].push_back({right, static_cast<std::uint32_t>(edge_id)});
            adjacency_[right].push_back({left, static_cast<std::uint32_t>(edge_id)});
            edge_index_.insert_unique(pack_pair(left, right), static_cast<std::uint32_t>(edge_id) + 1U);
        }
        for (std::uint32_t node = 0; node < graph_.node_count; ++node) {
            auto& neighbors = adjacency_[node];
            std::sort(neighbors.begin(), neighbors.end(), [](const Neighbor& a, const Neighbor& b) {
                return a.node < b.node;
            });
            degree_[node] = static_cast<std::uint32_t>(neighbors.size());
        }

        for (std::uint32_t middle = 0; middle < graph_.node_count; ++middle) {
            const auto& neighbors = adjacency_[middle];
            for (std::size_t i = 0; i < neighbors.size(); ++i) {
                for (std::size_t j = i + 1U; j < neighbors.size(); ++j) {
                    pair_counts_.increment(pack_pair(neighbors[i].node, neighbors[j].node));
                }
            }
        }
        build_pair_csr();

        for (std::uint32_t node = 0; node < graph_.node_count; ++node) {
            std::uint64_t closed_three_walks = 0U;
            for (const Neighbor neighbor : adjacency_[node]) {
                closed_three_walks += a2(node, neighbor.node);
            }
            triangle_walk_[node] = closed_three_walks;
        }
        std::cerr << "[C5] nodes=" << graph_.node_count
                  << " edges=" << graph_.edges.size()
                  << " nonzero-A2-pairs=" << pair_counts_.size()
                  << " pair-table-capacity=" << pair_counts_.capacity() << '\n';
    }

    void build_pair_csr() {
        std::vector<std::uint64_t> row_sizes(graph_.node_count, 0U);
        pair_counts_.for_each([&](std::uint64_t key, std::uint32_t) {
            const std::uint32_t left = static_cast<std::uint32_t>(key >> 32U);
            const std::uint32_t right = static_cast<std::uint32_t>(key);
            ++row_sizes[left];
            ++row_sizes[right];
        });
        pair_offsets_.assign(static_cast<std::size_t>(graph_.node_count) + 1U, 0U);
        for (std::uint32_t node = 0; node < graph_.node_count; ++node) {
            pair_offsets_[node + 1U] = pair_offsets_[node] + row_sizes[node];
        }
        if (pair_offsets_.back() > std::numeric_limits<std::size_t>::max()) {
            throw std::overflow_error("sparse A^2 CSR exceeds address space");
        }
        pair_neighbors_.assign(static_cast<std::size_t>(pair_offsets_.back()), 0U);
        std::vector<std::uint64_t> cursor = pair_offsets_;
        pair_counts_.for_each([&](std::uint64_t key, std::uint32_t) {
            const std::uint32_t left = static_cast<std::uint32_t>(key >> 32U);
            const std::uint32_t right = static_cast<std::uint32_t>(key);
            pair_neighbors_[static_cast<std::size_t>(cursor[left]++)] = right;
            pair_neighbors_[static_cast<std::size_t>(cursor[right]++)] = left;
        });
        for (std::uint32_t node = 0; node < graph_.node_count; ++node) {
            const auto begin = pair_neighbors_.begin() + static_cast<std::ptrdiff_t>(pair_offsets_[node]);
            const auto end = pair_neighbors_.begin() + static_cast<std::ptrdiff_t>(pair_offsets_[node + 1U]);
            std::sort(begin, end);
        }
    }

    std::uint32_t a2(std::uint32_t left, std::uint32_t right) const {
        if (left == right) return degree_[left];
        return pair_counts_.get(pack_pair(left, right));
    }

    bool has_active_edge(std::uint32_t left, std::uint32_t right) const {
        if (left == right) return false;
        const std::uint32_t encoded = edge_index_.get(pack_pair(left, right));
        return encoded != 0U && active_[encoded - 1U] != 0U;
    }

    std::uint64_t sparse_a4(std::uint32_t left, std::uint32_t right) const {
        const std::uint64_t common = a2(left, right);
        __int128 total = static_cast<__int128>(common)
            * static_cast<__int128>(static_cast<std::uint64_t>(degree_[left]) + degree_[right]);

        std::uint64_t i = pair_offsets_[left];
        std::uint64_t j = pair_offsets_[right];
        const std::uint64_t i_end = pair_offsets_[left + 1U];
        const std::uint64_t j_end = pair_offsets_[right + 1U];
        while (i < i_end && j < j_end) {
            const std::uint32_t a = pair_neighbors_[static_cast<std::size_t>(i)];
            const std::uint32_t b = pair_neighbors_[static_cast<std::size_t>(j)];
            if (a == b) {
                total += static_cast<__int128>(a2(left, a)) * static_cast<__int128>(a2(right, a));
                if (total > static_cast<__int128>(std::numeric_limits<std::uint64_t>::max())) {
                    throw std::overflow_error("A^4 entry exceeds uint64 range");
                }
                ++i;
                ++j;
            } else if (a < b) {
                ++i;
            } else {
                ++j;
            }
        }
        if (total < 0) throw std::runtime_error("negative A^4 entry");
        return static_cast<std::uint64_t>(total);
    }

    std::uint64_t common_degree_sum(std::uint32_t left, std::uint32_t right) const {
        const auto& a = adjacency_[left];
        const auto& b = adjacency_[right];
        std::size_t i = 0U;
        std::size_t j = 0U;
        std::uint64_t total = 0U;
        while (i < a.size() && j < b.size()) {
            if (a[i].node == b[j].node) {
                total += degree_[a[i].node];
                ++i;
                ++j;
            } else if (a[i].node < b[j].node) {
                ++i;
            } else {
                ++j;
            }
        }
        return total;
    }

    void compute_supports() {
        for (std::size_t edge_id = 0; edge_id < graph_.edges.size(); ++edge_id) {
            const auto [left, right] = graph_.edges[edge_id];
            const std::uint64_t common = a2(left, right);
            support_[edge_id] = checked_support(
                sparse_a4(left, right),
                triangle_walk_[left],
                triangle_walk_[right],
                degree_[left],
                degree_[right],
                common,
                common_degree_sum(left, right)
            );
        }
    }

    void bump_epoch() {
        ++epoch_;
        if (epoch_ == 0U) {
            std::fill(touched_epoch_.begin(), touched_epoch_.end(), 0U);
            epoch_ = 1U;
        }
    }

    void add_decrement(std::uint32_t edge_id, std::uint64_t value) {
        if (value == 0U || !active_[edge_id]) return;
        if (touched_epoch_[edge_id] != epoch_) {
            touched_epoch_[edge_id] = epoch_;
            decrement_[edge_id] = value;
            touched_.push_back(edge_id);
        } else {
            if (std::numeric_limits<std::uint64_t>::max() - decrement_[edge_id] < value) {
                throw std::overflow_error("C5 decrement exceeds uint64 range");
            }
            decrement_[edge_id] += value;
        }
    }

    std::uint64_t two_hop_completion_count(
        std::uint32_t opposite_endpoint,
        std::uint32_t middle,
        std::uint32_t forbidden_endpoint,
        std::uint32_t first_internal
    ) const {
        std::uint64_t value = a2(opposite_endpoint, middle);
        if (has_active_edge(forbidden_endpoint, middle)) {
            if (value == 0U) throw std::runtime_error("invalid common-neighbor subtraction");
            --value;
        }
        if (has_active_edge(first_internal, opposite_endpoint)) {
            if (value == 0U) throw std::runtime_error("invalid common-neighbor subtraction");
            --value;
        }
        return value;
    }

    void aggregate_one_side(std::uint32_t endpoint, std::uint32_t opposite_endpoint) {
        for (const Neighbor first : adjacency_[endpoint]) {
            if (!active_[first.edge_id] || first.node == opposite_endpoint) continue;
            for (const Neighbor middle : adjacency_[first.node]) {
                if (!active_[middle.edge_id] ||
                    middle.node == endpoint || middle.node == opposite_endpoint) {
                    continue;
                }
                const std::uint64_t cycles = two_hop_completion_count(
                    opposite_endpoint, middle.node, endpoint, first.node
                );
                add_decrement(first.edge_id, cycles);
                add_decrement(middle.edge_id, cycles);
            }
        }
    }

    void aggregate_destroyed_cycles(std::uint32_t edge_id) {
        const auto [left, right] = graph_.edges[edge_id];
        aggregate_one_side(left, right);
        aggregate_one_side(right, left);
    }

    void remove_from_active_graph(std::uint32_t edge_id) {
        const auto [left, right] = graph_.edges[edge_id];
        for (const Neighbor neighbor : adjacency_[left]) {
            if (!active_[neighbor.edge_id] || neighbor.node == right) continue;
            pair_counts_.decrement(pack_pair(neighbor.node, right));
        }
        for (const Neighbor neighbor : adjacency_[right]) {
            if (!active_[neighbor.edge_id] || neighbor.node == left) continue;
            pair_counts_.decrement(pack_pair(neighbor.node, left));
        }
        if (degree_[left] == 0U || degree_[right] == 0U) {
            throw std::runtime_error("degree underflow while deleting edge");
        }
        --degree_[left];
        --degree_[right];
        active_[edge_id] = 0U;
    }

    InputGraph graph_;
    std::vector<std::uint8_t> active_;
    std::vector<std::uint64_t> initial_support_;
    std::vector<std::uint64_t> trussness_;
    std::vector<std::uint64_t> support_;
    std::vector<std::vector<Neighbor>> adjacency_;
    std::vector<std::uint32_t> degree_;
    std::vector<std::uint64_t> triangle_walk_;
    PairTable pair_counts_;
    PairTable edge_index_;
    std::vector<std::uint64_t> pair_offsets_;
    std::vector<std::uint32_t> pair_neighbors_;
    std::vector<std::uint64_t> decrement_;
    std::vector<std::uint32_t> touched_epoch_;
    std::uint32_t epoch_ = 0U;
    std::vector<std::uint32_t> touched_;
};

}  // namespace

int main(int argc, char** argv) {
    if (argc != 3) {
        std::cerr << "usage: c5_truss_sparse INPUT_EDGE_LIST OUTPUT_TSV\n";
        return 2;
    }
    try {
        SparseC5Truss decomposition(read_graph(argv[1]));
        decomposition.run();
        decomposition.write(argv[2]);
    } catch (const std::exception& error) {
        std::cerr << "c5_truss_sparse: " << error.what() << '\n';
        return 1;
    }
    return 0;
}
