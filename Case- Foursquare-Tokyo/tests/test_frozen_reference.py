from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
FROZEN = PROJECT / "reference/frozen_run"


class FrozenReferenceTests(unittest.TestCase):
    def test_all_published_reference_hashes_match(self) -> None:
        manifest = json.loads(
            (PROJECT / "reference/VERIFICATION.json").read_text(
                encoding="utf-8"
            )
        )
        paths = {
            "HEADLINE_METRICS.csv": FROZEN / "HEADLINE_METRICS.csv",
            "METRICS.csv": FROZEN / "METRICS.csv",
            "PER_USER_METRICS.csv": FROZEN / "PER_USER_METRICS.csv",
            "AUDIT.json": FROZEN / "AUDIT.json",
            "MIXED_GRAPH_EDGES.tsv": (
                FROZEN / "decomposition/MIXED_GRAPH_EDGES.tsv"
            ),
            "EDGE_TRUSSNESS.csv": (
                FROZEN / "decomposition/EDGE_TRUSSNESS.csv"
            ),
            "items.csv": FROZEN / "graphdb/items.csv",
            "cross_recommendations.csv": (
                FROZEN / "graphdb/cross_recommendations.csv"
            ),
            "queries.cypher": FROZEN / "graphdb/queries.cypher",
        }
        self.assertEqual(set(manifest["sha256"]), set(paths))
        for name, path in paths.items():
            observed = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(observed, manifest["sha256"][name], name)
        self.assertEqual(
            (PROJECT / "reference/HEADLINE_METRICS.csv").read_bytes(),
            paths["HEADLINE_METRICS.csv"].read_bytes(),
        )

    def test_frozen_cycle_counts_ignore_machine_timings(self) -> None:
        import csv

        with (FROZEN / "decomposition/CYCLE_COUNTS.csv").open(
            encoding="utf-8", newline=""
        ) as handle:
            observed = {
                int(row["cycle_length"]): int(row["cycle_count"])
                for row in csv.DictReader(handle)
            }
        self.assertEqual(
            observed,
            {3: 4463, 4: 60511, 5: 866729, 6: 12966448},
        )

    def test_headline_contains_only_ndcg_and_precision_at_five(self) -> None:
        import csv

        with (FROZEN / "HEADLINE_METRICS.csv").open(
            encoding="utf-8", newline=""
        ) as handle:
            reader = csv.DictReader(handle)
            self.assertEqual(
                reader.fieldnames,
                [
                    "method",
                    "ndcg_at_5",
                    "precision_at_5",
                ],
            )


if __name__ == "__main__":
    unittest.main()
