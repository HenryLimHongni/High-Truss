#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

from screening_common import parse_candidate_file
from screen_candidates import write_json_atomic
from summarize_screening import automatic_showcase, best_and_margin, build_tables


class ScreeningTests(unittest.TestCase):
    def test_atomic_json_write_is_safe_for_array_style_concurrency(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "manifest.json"
            with ThreadPoolExecutor(max_workers=8) as executor:
                list(executor.map(lambda value: write_json_atomic(path, {"value": value}), range(50)))
            final = json.loads(path.read_text())
            self.assertIn(final["value"], range(50))
            self.assertEqual(list(Path(temporary).glob("*.tmp")), [])

    def test_candidate_parser_preserves_order_and_deduplicates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "candidates.txt"
            path.write_text("# comment\n1ubq, 1mbo\n1UBQ\n")
            self.assertEqual(parse_candidate_file(path), ["1UBQ", "1MBO"])

    def test_best_and_margin_requires_unique_best(self) -> None:
        best, best_pr, second, second_pr, margin = best_and_margin(
            {3: 0.60, 4: 0.71, 5: 0.75, 6: 0.72}
        )
        self.assertEqual((best, second), ("C5", "C6"))
        self.assertAlmostEqual(best_pr, 0.75)
        self.assertAlmostEqual(second_pr, 0.72)
        self.assertAlmostEqual(margin, 0.03)

        tied = best_and_margin({3: 0.6, 4: 0.75, 5: 0.75, 6: 0.7})
        self.assertTrue(tied[0].startswith("tie:"))
        self.assertEqual(tied[4], 0.0)

    def test_complete_table_keeps_failure_and_never_averages_cutoffs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            phase_dir = Path(temporary) / "exploratory"
            good = phase_dir / "per_pdb" / "1UBQ"
            failed = phase_dir / "per_pdb" / "1MBO"
            (good / "results").mkdir(parents=True)
            failed.mkdir(parents=True)
            (good / "status.json").write_text(json.dumps({"status": "complete"}))
            (failed / "status.json").write_text(
                json.dumps({"status": "failed", "error": "synthetic failure"})
            )
            pd.DataFrame([
                {
                    "cutoff": cutoff,
                    "pdb_id": "1UBQ",
                    "n_vertices": 10,
                    "n_edges": 20,
                    "eval_n": 9,
                    "positive_n": 3,
                    "C3_ROC": 0.60,
                    "C3_PR": 0.61,
                    "C4_ROC": 0.70,
                    "C4_PR": 0.71,
                    "C5_ROC": 0.80,
                    "C5_PR": 0.76 if cutoff == 6.5 else 0.74,
                    "C6_ROC": 0.72,
                    "C6_PR": 0.72,
                }
                for cutoff in (6.5, 7.0)
            ]).to_csv(good / "results" / "summary_wide.csv", index=False)

            by_cutoff, by_protein = build_tables(
                ["1UBQ", "1MBO"], phase_dir, margin_threshold=0.015
            )
            self.assertEqual(len(by_cutoff), 4)
            self.assertEqual(set(by_protein["pdb_id"]), {"1UBQ", "1MBO"})
            good_row = by_protein[by_protein["pdb_id"] == "1UBQ"].iloc[0]
            self.assertEqual(good_row["cutoff_6_5_best_cycle_by_PR"], "C5")
            self.assertEqual(good_row["cutoff_7_0_best_cycle_by_PR"], "C5")
            self.assertAlmostEqual(float(good_row["cutoff_6_5_C5_PR"]), 0.76)
            self.assertAlmostEqual(float(good_row["cutoff_7_0_C5_PR"]), 0.74)
            self.assertNotIn("mean_C5_PR", good_row.index)
            self.assertTrue(bool(good_row["qualifies_C5_exploratory"]))
            failed_row = by_protein[by_protein["pdb_id"] == "1MBO"].iloc[0]
            self.assertEqual(failed_row["status"], "failed")
            self.assertEqual(failed_row["error"], "synthetic failure")

    def test_c4_selection_prioritizes_worse_cutoff_pr_not_an_average(self) -> None:
        rows = pd.DataFrame([
            {
                "pdb_id": "1AAA", "status": "complete",
                "qualifies_C5_exploratory": False, "qualifies_C4_best": True,
                "minimum_C4_PR_across_cutoffs": 0.70,
                "maximum_C4_winning_margin": 0.02,
            },
            {
                "pdb_id": "1BBB", "status": "complete",
                "qualifies_C5_exploratory": False, "qualifies_C4_best": True,
                "minimum_C4_PR_across_cutoffs": 0.20,
                "maximum_C4_winning_margin": 0.10,
            },
        ])
        selected = automatic_showcase(rows, target_c5=0, target_c4=1)
        self.assertEqual(selected.iloc[0]["pdb_id"], "1AAA")


if __name__ == "__main__":
    unittest.main()
