from __future__ import annotations

import unittest

from crossrec_final.hybrid import (
    TrainingGraph,
    enumerate_cross_recommendation_witnesses,
    item_node,
    normalize_edge,
    positive_edge_percentiles,
)
from item_edge_c5.scoring import (
    TRUSS_METHODS,
    build_item_edge_feature_tables,
)


def edge(user_or_item_left: str, user_or_item_right: str):
    return normalize_edge(user_or_item_left, user_or_item_right)


def base_graph() -> TrainingGraph:
    # u-b-v-d-e-u is a strict typed C5 supporting u -> d.
    return TrainingGraph.from_relations(
        {
            "u": {"b", "e"},
            "v": {"b", "d"},
        },
        {("d", "e")},
        catalog={"b", "d", "e", "z"},
    )


def zero_maps(graph: TrainingGraph):
    return {
        length: {value: 0 for value in graph.edges}
        for length in (3, 4, 5, 6)
    }


class ItemEdgeScoringTests(unittest.TestCase):
    def test_item_edge_rule_differs_from_old_all_edge_bottleneck(self):
        graph = base_graph()
        maps = zero_maps(graph)
        de = edge(item_node("d"), item_node("e"))
        maps[5][de] = 9
        result = build_item_edge_feature_tables(
            graph,
            {"u": {"d", "z"}},
            maps,
            include_raw=True,
        )
        self.assertEqual(
            result.features["c5_truss_item_edge"]["u"]["d"],
            1.0,
        )
        self.assertEqual(
            result.raw_truss_features["c5_truss_item_edge"]["u"]["d"],
            9.0,
        )

        witness = enumerate_cross_recommendation_witnesses(
            graph,
            {"u": {"d", "z"}},
            policy="simple",
        )[("u", "d")][0]
        percentiles = positive_edge_percentiles(maps[5])
        old_all_edge_bottleneck = min(
            percentiles.get(value, 0.0)
            for value in witness.edge_occurrences
        )
        self.assertEqual(old_all_edge_bottleneck, 0.0)
        self.assertNotEqual(
            result.features["c5_truss_item_edge"]["u"]["d"],
            old_all_edge_bottleneck,
        )

    def test_duplicate_witnesses_using_same_de_do_not_inflate_max(self):
        one = base_graph()
        duplicate = TrainingGraph.from_relations(
            {
                "u": {"b", "e", "q"},
                "v": {"b", "d"},
                "w": {"q", "d"},
            },
            {("d", "e")},
            catalog={"b", "d", "e", "q", "z"},
        )

        def evaluate(graph):
            maps = zero_maps(graph)
            de = edge(item_node("d"), item_node("e"))
            maps[5][de] = 4
            return build_item_edge_feature_tables(
                graph,
                {"u": {"d", "z"}},
                maps,
                include_raw=True,
            )

        first = evaluate(one)
        second = evaluate(duplicate)
        self.assertEqual(
            first.features["c5_truss_item_edge"]["u"]["d"],
            second.features["c5_truss_item_edge"]["u"]["d"],
        )
        self.assertEqual(second.audit["strict_witnesses"], 2)
        self.assertEqual(
            second.audit["candidate_item_edge_occurrences_after_dedup"],
            1,
        )

    def test_missing_strict_witness_scores_zero_for_c5(self):
        graph = base_graph()
        maps = zero_maps(graph)
        result = build_item_edge_feature_tables(
            graph,
            {"u": {"d", "z"}},
            maps,
            include_raw=True,
        )
        for method in TRUSS_METHODS:
            self.assertEqual(result.features[method]["u"]["z"], 0.0)
            self.assertEqual(
                result.raw_truss_features[method]["u"]["z"],
                0.0,
            )
        self.assertEqual(
            result.witness_supported_candidates["u"],
            frozenset({"d"}),
        )

    def test_c5_reads_only_the_de_role_edge(self):
        graph = base_graph()
        de = edge(item_node("d"), item_node("e"))
        distractor = edge(item_node("b"), "U:u")
        maps = zero_maps(graph)
        maps[5][de] = 6
        # A much larger value on a non-role witness edge must be ignored.
        maps[5][distractor] = 105
        result = build_item_edge_feature_tables(
            graph,
            {"u": {"d", "z"}},
            maps,
            include_raw=True,
        )
        self.assertEqual(
            result.features["c5_truss_item_edge"]["u"]["d"],
            1.0,
        )
        self.assertEqual(
            result.raw_truss_features["c5_truss_item_edge"]["u"]["d"],
            6.0,
        )
        self.assertEqual(set(result.features), set(TRUSS_METHODS))
        self.assertEqual(result.audit["scored_role_edge"], "item--item d--e only")
        self.assertEqual(
            result.audit["percentile_reference"],
            "positive Item--Item edges only",
        )

    def test_input_maps_must_be_edge_level_and_complete(self):
        graph = base_graph()
        maps = zero_maps(graph)
        maps[5].pop(next(iter(graph.edges)))
        with self.assertRaises(ValueError):
            build_item_edge_feature_tables(
                graph,
                {"u": {"d", "z"}},
                maps,
            )

    def test_raw_support_uses_same_role_edge_with_honest_label(self):
        graph = base_graph()
        maps = zero_maps(graph)
        de = edge(item_node("d"), item_node("e"))
        maps[5][de] = 17
        result = build_item_edge_feature_tables(
            graph,
            {"u": {"d", "z"}},
            maps,
            include_raw=True,
            method_name="c5_support_item_edge",
            measure_label="raw C5 support before peeling",
        )
        self.assertEqual(
            result.raw_truss_features["c5_support_item_edge"]["u"]["d"],
            17.0,
        )
        self.assertEqual(
            result.audit["edge_measure"],
            "raw C5 support before peeling",
        )


if __name__ == "__main__":
    unittest.main()
