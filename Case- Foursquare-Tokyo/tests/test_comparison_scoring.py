from __future__ import annotations

import unittest

from crossrec_final.hybrid import (
    TrainingGraph,
    item_node,
    normalize_edge,
    user_node,
)
from item_edge_c5.comparison_scoring import (
    build_comparison_feature_tables,
)


def _edge(left: str, right: str):
    return normalize_edge(left, right)


class ComparisonScoringAndMetricsTests(unittest.TestCase):
    def test_each_cycle_length_uses_its_declared_scoring_semantics(self):
        # Strict C5: u-s-v-d-e-u, supporting u -> d through d--e.
        # Native C6: u-b-c-v-d-e-u, also supporting u -> d through d--e.
        graph = TrainingGraph.from_relations(
            {
                "u": {"s", "e", "b"},
                "v": {"s", "d", "c"},
            },
            {("d", "e"), ("b", "c")},
            catalog={"s", "b", "c", "d", "e", "z"},
        )
        maps = {
            length: {edge: 0 for edge in graph.edges}
            for length in (3, 4, 5, 6)
        }

        # C3 uses only the Item--Item role edge in u--e--d.  Other paths do
        # not affect the new open-triangle score.
        maps[3][_edge(user_node("u"), item_node("e"))] = 4
        maps[3][_edge(item_node("e"), item_node("d"))] = 6
        maps[3][_edge(user_node("u"), item_node("s"))] = 8
        maps[3][_edge(item_node("s"), user_node("v"))] = 7
        maps[3][_edge(user_node("v"), item_node("d"))] = 5

        # This graph has no typed C4 u-a-d-c-u for candidate d: only the
        # purchased Item e has an Item--Item relation to d.
        maps[4][_edge(user_node("u"), item_node("s"))] = 7
        maps[4][_edge(item_node("s"), user_node("v"))] = 5
        maps[4][_edge(user_node("v"), item_node("d"))] = 6

        # C5 and native C6 both use an Item--Item role edge, but their
        # witnesses are distinct.  The extra b--c edge completes the C6.
        role = _edge(item_node("d"), item_node("e"))
        maps[5][role] = 9
        maps[6][role] = 8
        maps[6][_edge(item_node("b"), item_node("c"))] = 7

        result = build_comparison_feature_tables(
            graph,
            {"u": {"c", "d", "z"}},
            maps,
        )
        self.assertEqual(
            result.raw_truss_features["c3_truss"]["u"]["d"],
            6.0,
        )
        self.assertEqual(
            result.raw_truss_features["c4_truss"]["u"]["d"],
            0.0,
        )
        self.assertEqual(
            result.raw_truss_features["c5_truss"]["u"]["d"],
            9.0,
        )
        self.assertEqual(
            result.raw_truss_features["c6_truss"]["u"]["d"],
            8.0,
        )
        self.assertEqual(
            result.raw_truss_features["c6_truss"]["u"]["c"],
            7.0,
        )
        for method in result.raw_truss_features:
            self.assertEqual(
                result.raw_truss_features[method]["u"]["z"],
                0.0,
            )
        self.assertEqual(result.audit["candidate_edge_inserted"], 0)

    def test_c3_uses_max_item_edge_for_direct_open_triangle_only(self):
        graph = TrainingGraph.from_relations(
            {
                "u": {"b1", "b2"},
                "v": {"d"},
            },
            {("b1", "c"), ("b2", "c"), ("c", "d")},
            catalog={"b1", "b2", "c", "d", "z"},
        )
        maps = {
            length: {edge: 50 for edge in graph.edges}
            for length in (3, 4, 5, 6)
        }
        maps[3][_edge(item_node("b1"), item_node("c"))] = 4
        maps[3][_edge(item_node("b2"), item_node("c"))] = 7
        maps[3][_edge(item_node("c"), item_node("d"))] = 99

        result = build_comparison_feature_tables(
            graph,
            {"u": {"c", "d", "z"}},
            maps,
        )
        c3 = result.raw_truss_features["c3_truss"]["u"]
        # c has two valid u--b--c patterns, so the role-edge maximum is 7.
        self.assertEqual(c3["c"], 7.0)
        # d is reachable by the longer u--b--c--d path, but it has no direct
        # u--b--d training pattern and must therefore remain zero.
        self.assertEqual(c3["d"], 0.0)
        # The full warm-unseen catalog is retained even without evidence.
        self.assertEqual(c3["z"], 0.0)
        self.assertEqual(
            result.supported_candidates_by_method["c3_truss"]["u"],
            frozenset({"c"}),
        )

    def test_c4_scores_two_item_role_edges_and_requires_simple_cycle(self):
        graph = TrainingGraph.from_relations(
            {"u": {"a", "c", "d"}},
            {("a", "b"), ("b", "c"), ("b", "d"), ("a", "x")},
            catalog={"a", "b", "c", "d", "x", "z"},
        )
        maps = {
            length: {edge: 0 for edge in graph.edges}
            for length in (3, 4, 5, 6)
        }
        maps[4][_edge(item_node("a"), item_node("b"))] = 4
        maps[4][_edge(item_node("b"), item_node("c"))] = 7
        maps[4][_edge(item_node("b"), item_node("d"))] = 9
        maps[4][_edge(item_node("a"), item_node("x"))] = 100

        result = build_comparison_feature_tables(
            graph,
            {"u": {"b", "x", "z"}},
            maps,
        )
        c4 = result.raw_truss_features["c4_truss"]["u"]
        # Candidate b has three role edges and three anchor pairs.  The
        # requested max-within-cycle then max-across-cycles score is 9.
        self.assertEqual(c4["b"], 9.0)
        # x has only one purchased neighbor, so u-a-x cannot close a C4.
        self.assertEqual(c4["x"], 0.0)
        self.assertEqual(c4["z"], 0.0)
        self.assertEqual(
            result.supported_candidates_by_method["c4_truss"]["u"],
            frozenset({"b"}),
        )
        self.assertEqual(result.audit["typed_simple_c4_cycles"], 3)
        self.assertEqual(result.audit["typed_c4_supported_pairs"], 1)
        # Positive Item--Item tau4 values are [4, 7, 9, 100].
        self.assertEqual(result.features["c4_truss"]["u"]["b"], 0.75)

    def test_native_c6_generates_all_four_role_edge_recommendations(self):
        # a-b-c-d-e-f-a, where a,d are Users and b,c,e,f are Items.
        graph = TrainingGraph.from_relations(
            {
                "a": {"b", "f"},
                "d": {"c", "e"},
            },
            {("b", "c"), ("e", "f")},
            catalog={"b", "c", "e", "f", "z"},
        )
        maps = {
            length: {edge: 0 for edge in graph.edges}
            for length in (3, 4, 5, 6)
        }
        bc = _edge(item_node("b"), item_node("c"))
        ef = _edge(item_node("e"), item_node("f"))
        maps[6][bc] = 7
        maps[6][ef] = 4

        result = build_comparison_feature_tables(
            graph,
            {
                "a": {"c", "e", "z"},
                "d": {"b", "f", "z"},
            },
            maps,
        )
        c6 = result.raw_truss_features["c6_truss"]
        self.assertEqual(c6["a"]["c"], 7.0)
        self.assertEqual(c6["a"]["e"], 4.0)
        self.assertEqual(c6["d"]["b"], 7.0)
        self.assertEqual(c6["d"]["f"], 4.0)
        self.assertEqual(c6["a"]["z"], 0.0)
        self.assertEqual(c6["d"]["z"], 0.0)
        self.assertEqual(result.audit["native_typed_c6_cycles"], 1)
        self.assertEqual(result.audit["native_c6_supported_pairs"], 4)

    def test_strict_c5_without_a_second_item_bridge_has_no_native_c6(self):
        # u-b-v-d-e-u is a valid strict C5, but there is only one Item--Item
        # bridge d--e, so no native six-cycle can be formed.
        graph = TrainingGraph.from_relations(
            {
                "u": {"b", "e"},
                "v": {"b", "d"},
            },
            {("d", "e")},
            catalog={"b", "d", "e", "z"},
        )
        maps = {
            length: {edge: 0 for edge in graph.edges}
            for length in (3, 4, 5, 6)
        }
        de = _edge(item_node("d"), item_node("e"))
        maps[5][de] = 5
        maps[6][de] = 99
        result = build_comparison_feature_tables(
            graph,
            {"u": {"d", "z"}},
            maps,
        )
        self.assertEqual(
            result.raw_truss_features["c5_truss"]["u"]["d"],
            5.0,
        )
        self.assertEqual(
            result.raw_truss_features["c6_truss"]["u"]["d"],
            0.0,
        )
        self.assertEqual(result.audit["native_typed_c6_cycles"], 0)

    def test_native_c6_rejects_two_item_bridges_sharing_an_item(self):
        graph = TrainingGraph.from_relations(
            {
                "a": {"b"},
                "d": {"c", "e"},
            },
            {("b", "c"), ("b", "e")},
            catalog={"b", "c", "e", "z"},
        )
        maps = {
            length: {edge: 1 for edge in graph.edges}
            for length in (3, 4, 5, 6)
        }
        result = build_comparison_feature_tables(
            graph,
            {
                "a": {"c", "e", "z"},
                "d": {"b", "z"},
            },
            maps,
        )
        self.assertEqual(result.audit["native_typed_c6_cycles"], 0)
        self.assertTrue(
            all(
                value == 0.0
                for scores in result.raw_truss_features[
                    "c6_truss"
                ].values()
                for value in scores.values()
            )
        )

    def test_repeated_native_c6_witnesses_do_not_duplicate_role_edge_score(self):
        graph = TrainingGraph.from_relations(
            {
                "a": {"b", "f1", "f2"},
                "d": {"c", "e1", "e2"},
            },
            {("b", "c"), ("f1", "e1"), ("f2", "e2")},
            catalog={"b", "c", "f1", "f2", "e1", "e2", "z"},
        )
        maps = {
            length: {edge: 0 for edge in graph.edges}
            for length in (3, 4, 5, 6)
        }
        maps[6][_edge(item_node("b"), item_node("c"))] = 7
        maps[6][_edge(item_node("f1"), item_node("e1"))] = 99
        maps[6][_edge(item_node("f2"), item_node("e2"))] = 100
        result = build_comparison_feature_tables(
            graph,
            {
                "a": {"c", "e1", "e2", "z"},
                "d": {"b", "f1", "f2", "z"},
            },
            maps,
        )
        # Two different companion bridges support a -> c, but both use the
        # same direct role edge b--c, so max tau6 remains exactly 7.
        self.assertEqual(
            result.raw_truss_features["c6_truss"]["a"]["c"],
            7.0,
        )

    def test_observed_native_c6_candidate_does_not_remove_other_three(self):
        graph = TrainingGraph.from_relations(
            {
                "a": {"b", "f", "c"},
                "d": {"c", "e"},
            },
            {("b", "c"), ("e", "f")},
            catalog={"b", "c", "e", "f", "z"},
        )
        maps = {
            length: {edge: 0 for edge in graph.edges}
            for length in (3, 4, 5, 6)
        }
        maps[6][_edge(item_node("b"), item_node("c"))] = 7
        maps[6][_edge(item_node("e"), item_node("f"))] = 4
        result = build_comparison_feature_tables(
            graph,
            {
                "a": {"e", "z"},
                "d": {"b", "f", "z"},
            },
            maps,
        )
        c6 = result.raw_truss_features["c6_truss"]
        self.assertEqual(c6["a"]["e"], 4.0)
        self.assertEqual(c6["d"]["b"], 7.0)
        self.assertEqual(c6["d"]["f"], 4.0)
        self.assertEqual(result.audit["native_c6_supported_pairs"], 3)

    def test_native_c6_percentiles_use_only_item_item_edges(self):
        graph = TrainingGraph.from_relations(
            {
                "a": {"b", "f"},
                "d": {"c", "e"},
            },
            {("b", "c"), ("e", "f")},
            catalog={"b", "c", "e", "f", "z"},
        )
        maps = {
            length: {edge: 0 for edge in graph.edges}
            for length in (3, 4, 5, 6)
        }
        bc = _edge(item_node("b"), item_node("c"))
        ef = _edge(item_node("e"), item_node("f"))
        maps[6][bc] = 7
        maps[6][ef] = 4
        for edge in graph.edges:
            if edge not in {bc, ef}:
                maps[6][edge] = 10000
        result = build_comparison_feature_tables(
            graph,
            {
                "a": {"c", "e", "z"},
                "d": {"b", "f", "z"},
            },
            maps,
        )
        c6 = result.features["c6_truss"]
        self.assertEqual(c6["a"]["c"], 1.0)
        self.assertEqual(c6["a"]["e"], 0.5)

if __name__ == "__main__":
    unittest.main()
