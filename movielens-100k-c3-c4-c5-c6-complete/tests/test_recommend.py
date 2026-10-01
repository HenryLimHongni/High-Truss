from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from full_c5_recommend.artifacts import (
    EdgeRecord,
    NodeRecord,
    node_key,
    write_case,
    write_value_file,
)
from full_c5_recommend.recommend import evaluate_top_users
from full_c5_recommend.utils import key_edge


class RecommendationSemanticsTests(unittest.TestCase):
    def test_cycle_weighted_average_and_degree_tie_break(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            kinds = {
                "u": "user",
                "v": "user",
                "a": "item",
                "b1": "item",
                "b2": "item",
                "c1": "item",
                "c2": "item",
                "d": "item",
            }
            keys = {name: node_key(kind, name) for name, kind in kinds.items()}
            nodes = {
                key: NodeRecord(
                    key=key,
                    node_type=kinds[name],
                    entity_id=name,
                    title=name if kinds[name] == "item" else "",
                )
                for name, key in keys.items()
            }
            edges = {
                key_edge(keys["u"], keys["a"]): EdgeRecord("UI"),
                key_edge(keys["u"], keys["b1"]): EdgeRecord("UI"),
                key_edge(keys["u"], keys["b2"]): EdgeRecord("UI"),
                key_edge(keys["v"], keys["b1"]): EdgeRecord("UI"),
                key_edge(keys["v"], keys["b2"]): EdgeRecord("UI"),
                key_edge(keys["v"], keys["c1"]): EdgeRecord("UI"),
                key_edge(keys["v"], keys["c2"]): EdgeRecord("UI"),
                key_edge(keys["a"], keys["c1"]): EdgeRecord("II"),
                key_edge(keys["a"], keys["c2"]): EdgeRecord("II"),
                # Extra degree for c2. It is not incident to an observed item,
                # so it does not create a new recommendation candidate.
                key_edge(keys["c2"], keys["d"]): EdgeRecord("II"),
            }
            case = write_case(
                output_dir=root / "case",
                node_records=nodes,
                edge_records=edges,
                truth=[(keys["u"], keys["c2"])],
                metadata={"dataset": "recommendation-semantics-test"},
            )
            values = [1] * case.edge_count
            for edge_id, edge in enumerate(case.edges):
                if set(edge) in ({keys["a"], keys["c1"]}, {keys["a"], keys["c2"]}):
                    values[edge_id] = 10
            value_path = write_value_file(
                case,
                values,
                output=case.root / "values" / "c5_truss.txt",
                algorithm="c5_truss",
                backend="unit-test",
                elapsed_seconds=0.0,
            )
            aggregate = evaluate_top_users(
                case_dir=case.root,
                output_dir=root / "results",
                values_path=value_path,
                top_users=1,
                cutoffs=(5,),
                include_itemknn=True,
                ranking_limit=5,
            )
            with (root / "results" / "rankings_top.tsv").open(
                newline="", encoding="utf-8"
            ) as handle:
                rows = list(csv.DictReader(handle, delimiter="\t"))
            max_rows = [row for row in rows if row["method"] == "c5-max"]
            avg_rows = [row for row in rows if row["method"] == "c5-avg"]
            self.assertEqual([row["item_id"] for row in max_rows], ["c2", "c1"])
            self.assertEqual([row["item_id"] for row in avg_rows], ["c2", "c1"])
            self.assertEqual(int(max_rows[0]["typed_c5_count"]), 2)
            self.assertAlmostEqual(float(avg_rows[0]["score"]), 10.0)

            with aggregate.open(newline="", encoding="utf-8") as handle:
                aggregate_rows = list(csv.DictReader(handle, delimiter="\t"))
            max_summary = next(row for row in aggregate_rows if row["method"] == "c5-max")
            # Two C5 candidates, K=5 -> effective K=2, one hit -> 1/2.
            self.assertAlmostEqual(float(max_summary["average_hit_ratio"]), 0.5)
            itemknn = next(
                row for row in aggregate_rows if row["method"] == "itemknn-full-catalog"
            )
            self.assertGreater(float(itemknn["average_candidate_count"]), 2.0)


if __name__ == "__main__":
    unittest.main()
