#!/usr/bin/env python3
from __future__ import annotations

import random
import unittest
from typing import Dict, Iterable, List, Set, Tuple

from build_graph_labels import ResidueRecord, build_full_intrachain_graph
from compute_roc_pr import (
    Adj,
    count_l_cycle_supports,
    edge_list,
    incident_max_scores,
    l_cycle_support_core_decomposition,
)

Edge = Tuple[int, int]


def make_adj(n: int, edges: Iterable[Edge]) -> Adj:
    adj: List[Set[int]] = [set() for _ in range(n)]
    for u, v in edges:
        adj[u].add(v)
        adj[v].add(u)
    return adj


def slow_reference_decomposition(adj0: Adj, L: int) -> Dict[Edge, int]:
    """Small-graph reference matching the threshold definition directly."""
    adj = [set(neighbors) for neighbors in adj0]
    original_m = len(edge_list(adj))
    levels: Dict[Edge, int] = {}
    k = 1

    while len(levels) < original_m:
        while True:
            supports = count_l_cycle_supports(adj, L)
            to_remove = [edge for edge, support in supports.items() if support < k]
            if not to_remove:
                break
            for edge in to_remove:
                levels[edge] = k - 1
            for u, v in to_remove:
                adj[u].discard(v)
                adj[v].discard(u)
            if len(levels) == original_m:
                break
        k += 1
    return levels


def residue(node_id: int, chain: str, index: int, xyz: Tuple[float, float, float]) -> ResidueRecord:
    return ResidueRecord(
        node_id=node_id,
        pdb_id="TEST",
        chain_id=chain,
        chain_index=index,
        residue_uid=f"*:{index + 1}:*",
        resname="ALA",
        aa="A",
        dssp_ss="-",
        is_alpha_helix_raw=0,
        ca_x=xyz[0],
        ca_y=xyz[1],
        ca_z=xyz[2],
    )


class CycleScoreTests(unittest.TestCase):
    def test_single_c5_cycle_and_truss_level(self) -> None:
        edges = [(0, 1), (1, 2), (2, 3), (3, 4), (4, 0)]
        adj = make_adj(5, edges)
        support = count_l_cycle_supports(adj, 5)
        self.assertEqual(set(support.values()), {1})
        levels = l_cycle_support_core_decomposition(adj, 5, support)
        self.assertEqual(set(levels.values()), {1})

    def test_k5_each_edge_has_six_c5_cycles(self) -> None:
        edges = [(u, v) for u in range(5) for v in range(u + 1, 5)]
        support = count_l_cycle_supports(make_adj(5, edges), 5)
        self.assertEqual(set(support.values()), {6})

    def test_heap_peeling_matches_slow_threshold_reference(self) -> None:
        rng = random.Random(7)
        for L in (3, 4, 5, 6):
            for _ in range(25):
                n = 7
                edges = [
                    (u, v)
                    for u in range(n)
                    for v in range(u + 1, n)
                    if rng.random() < 0.42
                ]
                adj = make_adj(n, edges)
                expected = slow_reference_decomposition(adj, L)
                support = count_l_cycle_supports(adj, L)
                got = l_cycle_support_core_decomposition(adj, L, support)
                self.assertEqual(got, expected, msg=f"L={L}, edges={edges}")

    def test_incident_max_edge_score(self) -> None:
        scores = incident_max_scores(4, {(0, 1): 3, (1, 2): 7, (2, 3): 2})
        self.assertEqual(scores.tolist(), [3.0, 7.0, 7.0, 2.0])

    def test_full_intrachain_graph_has_no_maximum_sequence_separation(self) -> None:
        records = [
            residue(0, "A", 0, (0.0, 0.0, 0.0)),
            residue(1, "A", 1, (3.8, 0.0, 0.0)),
            residue(2, "A", 2, (3.8, 3.8, 0.0)),
            residue(3, "A", 3, (0.0, 3.8, 0.0)),
            residue(4, "A", 4, (0.0, 0.5, 0.0)),
            residue(5, "A", 5, (0.0, 4.3, 0.0)),
            residue(6, "A", 6, (0.0, 8.1, 0.0)),
            # Sequence separation 7, spatially close to residue 0.
            residue(7, "A", 7, (0.0, 0.8, 0.0)),
            # A second chain is spatially close but must not connect to chain A.
            residue(8, "B", 0, (0.1, 0.1, 0.0)),
            residue(9, "B", 1, (3.9, 0.1, 0.0)),
        ]
        bundle = build_full_intrachain_graph(
            records,
            ca_cutoff=7.0,
            min_contact_seq_sep=2,
            backbone_ca_max=4.5,
        )

        # Sequence separation 7 is retained; there is no upper sep=5 cutoff.
        self.assertIn((0, 7), bundle.edges)
        self.assertIn("contact", bundle.edge_types[(0, 7)])
        self.assertEqual(bundle.edge_seq_sep[(0, 7)], 7)

        # Adjacent residues are represented by backbone, not by contact.
        self.assertIn((0, 1), bundle.edges)
        self.assertEqual(bundle.edge_types[(0, 1)], {"backbone"})

        # Cross-chain pairs are excluded even when spatially very close.
        self.assertNotIn((0, 8), bundle.edges)
        self.assertNotIn((1, 9), bundle.edges)

        for edge, types in bundle.edge_types.items():
            if "contact" in types:
                self.assertGreaterEqual(bundle.edge_seq_sep[edge] or 0, 2)


if __name__ == "__main__":
    unittest.main()
