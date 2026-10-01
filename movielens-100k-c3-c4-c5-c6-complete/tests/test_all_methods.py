from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from full_c5_recommend.all_methods import evaluate_all_methods
from full_c5_recommend.artifacts import (
    EdgeRecord,
    NodeRecord,
    node_key,
    write_case,
    write_value_file,
)
from full_c5_recommend.utils import key_edge


class AllMethodTests(unittest.TestCase):
    def test_c3_c5_padding_fixed_precision_hr_and_typed_c6(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            kinds = {
                "u": "user",
                "v": "user",
                "w": "user",
                "a": "item",
                "b": "item",
                "c": "item",
                "d": "item",
                "e": "item",
                "f": "item",
                "g": "item",
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
                key_edge(keys["u"], keys["d"]): EdgeRecord("UI"),
                key_edge(keys["v"], keys["a"]): EdgeRecord("UI"),
                key_edge(keys["v"], keys["b"]): EdgeRecord("UI"),
                key_edge(keys["v"], keys["c"]): EdgeRecord("UI"),
                key_edge(keys["v"], keys["e"]): EdgeRecord("UI"),
                key_edge(keys["w"], keys["e"]): EdgeRecord("UI"),
                key_edge(keys["w"], keys["f"]): EdgeRecord("UI"),
                key_edge(keys["w"], keys["g"]): EdgeRecord("UI"),
                key_edge(keys["a"], keys["b"]): EdgeRecord("II"),
                key_edge(keys["c"], keys["d"]): EdgeRecord("II"),
            }
            case = write_case(
                output_dir=root / "case",
                node_records=nodes,
                edge_records=edges,
                truth=[(keys["u"], keys["c"]), (keys["u"], keys["e"])],
                metadata={"dataset": "all-method-test"},
            )
            edge_ids = {edge: index for index, edge in enumerate(case.edges)}

            def values(default: int = 1) -> list[int]:
                return [default] * case.edge_count

            c3 = values(1)
            c3[edge_ids[key_edge(keys["a"], keys["b"])]] = 5
            c3[edge_ids[key_edge(keys["c"], keys["d"])]] = 3
            c4 = values(1)
            c5 = values(1)
            c5[edge_ids[key_edge(keys["c"], keys["d"])]] = 7
            c6 = values(1)
            c6[edge_ids[key_edge(keys["a"], keys["b"])]] = 4
            c6[edge_ids[key_edge(keys["c"], keys["d"])]] = 9

            for length, vector in ((3, c3), (4, c4), (5, c5), (6, c6)):
                write_value_file(
                    case,
                    vector,
                    output=case.root / "values" / f"c{length}_truss.txt",
                    algorithm=f"c{length}_truss",
                    backend="unit-test",
                    elapsed_seconds=0.0,
                )

            aggregate = evaluate_all_methods(
                case_dir=case.root,
                output_dir=root / "results",
                top_users=1,
                cutoffs=(5,),
                ranking_limit=5,
                include_itemknn=True,
            )

            with (root / "results" / "rankings_top.tsv").open(
                newline="", encoding="utf-8"
            ) as handle:
                ranking_rows = list(csv.DictReader(handle, delimiter="\t"))

            c3_rows = [row for row in ranking_rows if row["method"] == "c3-truss-direct"]
            self.assertEqual([row["item_id"] for row in c3_rows[:2]], ["b", "c"])
            self.assertEqual(len(c3_rows), 5)
            self.assertEqual(sum(int(row["padding_item"]) for row in c3_rows), 3)

            c5_rows = [row for row in ranking_rows if row["method"] == "c5-max"]
            self.assertEqual(c5_rows[0]["item_id"], "c")
            self.assertEqual(int(c5_rows[0]["typed_c5_count"]), 1)
            self.assertEqual(len(c5_rows), 5)
            self.assertEqual(sum(int(row["padding_item"]) for row in c5_rows), 4)

            c6_rows = [row for row in ranking_rows if row["method"] == "c6-max"]
            self.assertEqual([row["item_id"] for row in c6_rows], ["c", "b"])
            self.assertEqual(float(c6_rows[0]["score"]), 9.0)
            self.assertEqual(float(c6_rows[1]["score"]), 4.0)
            self.assertGreater(int(c6_rows[0]["typed_c6_count"]), 0)

            with aggregate.open(newline="", encoding="utf-8") as handle:
                aggregate_rows = list(csv.DictReader(handle, delimiter="\t"))
            c3_summary = next(
                row for row in aggregate_rows if row["method"] == "c3-truss-direct"
            )
            # c and padded e are both ground truth: 2 hits / fixed K=5.
            self.assertEqual(int(c3_summary["total_hits"]), 2)
            self.assertAlmostEqual(float(c3_summary["average_precision"]), 0.4)
            self.assertAlmostEqual(float(c3_summary["average_hr"]), 1.0)

            c6_summary = next(
                row for row in aggregate_rows if row["method"] == "c6-max"
            )
            # C6 returns two items but precision still divides by requested K=5.
            self.assertEqual(int(c6_summary["total_hits"]), 1)
            self.assertAlmostEqual(float(c6_summary["average_precision"]), 0.2)
            self.assertAlmostEqual(float(c6_summary["average_hr"]), 1.0)


if __name__ == "__main__":
    unittest.main()
