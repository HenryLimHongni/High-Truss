from __future__ import annotations

import math
import unittest

from item_edge_c5.ranking_metrics import (
    METHOD_SPECS,
    audit_score_support_at_5,
    evaluate_precision_ndcg_at_5,
)


def _item(itemknn: float, c5: float, degree: int = 1) -> dict[str, float]:
    return {
        "training_item_degree": float(degree),
        "itemknn": itemknn,
        "max_c3_item_edge_tau_c3": 0.0,
        "max_c4_item_edge_tau_c4": 0.0,
        "max_c5_role_edge_tau_c5": c5,
        "max_native_c6_role_edge_tau_c6": 0.0,
    }


class RankingMetricTests(unittest.TestCase):
    def test_exact_five_method_contract(self) -> None:
        self.assertEqual(
            [spec.method for spec in METHOD_SPECS],
            [
                "itemknn",
                "c3_truss_only",
                "c4_truss_only",
                "c5_truss_only",
                "c6_truss_only",
            ],
        )

    def test_precision_uses_fixed_five_and_ndcg_uses_binary_relevance(self) -> None:
        ledger = {
            "u1": {
                item: _item(float(10 - index), 0.0)
                for index, item in enumerate("abcdef")
            },
            "u2": {
                item: _item(float(10 - index), 0.0)
                for index, item in enumerate("abcdef")
            },
        }
        positives = {
            "u1": frozenset({"a", "b", "f"}),
            "u2": frozenset({"f"}),
        }
        rows, per_user = evaluate_precision_ndcg_at_5(
            ledger, positives, dataset="toy"
        )
        itemknn = next(row for row in rows if row["method"] == "itemknn")
        self.assertEqual(itemknn["precision_at_5"], 2 / (2 * 5))
        user1_dcg = 1 + 1 / math.log2(3)
        user1_idcg = 1 + 1 / math.log2(3) + 1 / math.log2(4)
        self.assertAlmostEqual(
            itemknn["ndcg_at_5"], (user1_dcg / user1_idcg) / 2
        )
        self.assertEqual(
            list(per_user[0]),
            ["dataset", "method", "user", "precision_at_5", "ndcg_at_5"],
        )
        self.assertEqual(len(per_user), len(METHOD_SPECS) * 2)

    def test_equal_primary_scores_use_item_degree_then_item_id(self) -> None:
        ledger = {
            "u": {
                item: {
                    **_item(1.0, 1.0, degree=10 if item == "f" else 1),
                    "max_c3_item_edge_tau_c3": 1.0,
                    "max_c4_item_edge_tau_c4": 1.0,
                    "max_native_c6_role_edge_tau_c6": 1.0,
                }
                for item in "abcdef"
            }
        }
        positives = {"u": frozenset({"f"})}
        rows, _ = evaluate_precision_ndcg_at_5(
            ledger, positives, dataset="toy"
        )
        for row in rows:
            self.assertEqual(row["precision_at_5"], 1 / 5)
            self.assertEqual(row["ndcg_at_5"], 1.0)

    def test_zero_score_items_fill_top_five_by_degree(self) -> None:
        ledger = {
            "u": {
                item: _item(0.0, 0.0, degree=10 if item == "f" else 1)
                for item in "abcdef"
            }
        }
        positives = {"u": frozenset({"f"})}
        rows, _ = evaluate_precision_ndcg_at_5(
            ledger, positives, dataset="toy"
        )
        for row in rows:
            self.assertEqual(row["precision_at_5"], 1 / 5)
            self.assertEqual(row["ndcg_at_5"], 1.0)

        audit = audit_score_support_at_5(ledger, positives)
        self.assertEqual(audit["c5_truss_only"]["returned_items_at_5"], 5)
        self.assertEqual(audit["c5_truss_only"]["zero_score_items_at_5"], 5)
        self.assertEqual(
            audit["c5_truss_only"]["users_with_no_positive_scores"], 1
        )

    def test_fewer_than_five_catalog_items_fails_clearly(self) -> None:
        ledger = {"u": {item: _item(0.0, 0.0) for item in "abcd"}}
        with self.assertRaisesRegex(ValueError, "fewer than 5 items"):
            evaluate_precision_ndcg_at_5(
                ledger,
                {"u": frozenset({"a"})},
                dataset="toy",
            )

    def test_positive_must_be_in_the_common_item_universe(self) -> None:
        ledger = {
            "u": {
                item: _item(float(index), 0.0)
                for index, item in enumerate("abcdef", start=1)
            }
        }
        with self.assertRaisesRegex(ValueError, "outside the common"):
            evaluate_precision_ndcg_at_5(
                ledger,
                {"u": frozenset({"missing"})},
                dataset="toy",
            )


if __name__ == "__main__":
    unittest.main()
