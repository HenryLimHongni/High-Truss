from __future__ import annotations

import unittest

from full_c5_recommend.c4_typed_methods import c4_typed_evidence


class C4TypedEvidenceTests(unittest.TestCase):
    def test_requires_two_distinct_observed_anchors(self) -> None:
        observed = {1, 2}
        item_neighbors = {
            1: [(9, 0)],
            2: [],
        }
        result = c4_typed_evidence(
            observed=observed,
            item_neighbors=item_neighbors,
            c4_truss=(7,),
        )
        self.assertEqual(result, {})

    def test_score_is_max_over_cycle_edge_maxima(self) -> None:
        # Candidate 9 has three observed anchors with tau4 2, 7, and 4.
        # There are C(3,2)=3 typed cycles and the pair score is 7.
        observed = {1, 2, 3}
        item_neighbors = {
            1: [(9, 0)],
            2: [(9, 1)],
            3: [(9, 2)],
        }
        result = c4_typed_evidence(
            observed=observed,
            item_neighbors=item_neighbors,
            c4_truss=(2, 7, 4),
        )
        evidence = result[9]
        self.assertEqual(evidence.max_value, 7)
        self.assertEqual(evidence.typed_cycle_count, 3)
        self.assertEqual(evidence.qualifying_ii_edge_ids, {0, 1, 2})


if __name__ == "__main__":
    unittest.main()
