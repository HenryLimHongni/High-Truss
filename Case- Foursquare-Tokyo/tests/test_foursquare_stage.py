from __future__ import annotations

import csv
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from item_edge_c5.foursquare_tsmc import build_stage


def _utc(value: str) -> str:
    parsed = datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    return parsed.strftime("%a %b %d %H:%M:%S +0000 %Y")


class FoursquareStageTests(unittest.TestCase):
    def test_fixed_cutoff_warm_unseen_links_and_complete_radius_relation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tokyo.csv"
            columns = (
                "userId", "venueId", "venueCategoryId", "venueCategory",
                "latitude", "longitude", "timezoneOffset", "utcTimestamp",
            )
            rows: list[dict[str, object]] = []
            items = [f"venue-{index:02d}" for index in range(12)]
            for index, item in enumerate(items):
                rows.append(
                    {
                        "userId": f"catalog-user-{index:02d}",
                        "venueId": item,
                        "venueCategoryId": "department",
                        "venueCategory": "Department Store",
                        "latitude": 35.0,
                        "longitude": 139.0 + index * 0.0001,
                        "timezoneOffset": 540,
                        "utcTimestamp": _utc("2012-06-01"),
                    }
                )
            for item, value in zip(items[:2], ("2012-07-01", "2012-08-01")):
                rows.append(
                    {
                        "userId": "evaluation-user",
                        "venueId": item,
                        "venueCategoryId": "department",
                        "venueCategory": "Department Store",
                        "latitude": 35.0,
                        "longitude": 139.0,
                        "timezoneOffset": 540,
                        "utcTimestamp": _utc(value),
                    }
                )
            rows.append(
                {
                    "userId": "evaluation-user",
                    "venueId": items[2],
                    "venueCategoryId": "department",
                    "venueCategory": "Department Store",
                    "latitude": 35.0,
                    "longitude": 139.0,
                    "timezoneOffset": 540,
                    "utcTimestamp": _utc("2012-12-15"),
                }
            )
            with path.open("w", encoding="latin-1", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=columns)
                writer.writeheader()
                writer.writerows(rows)

            stage, positives, audit = build_stage(checkins_path=path)

            self.assertEqual(
                positives, {"evaluation-user": frozenset({items[2]})}
            )
            self.assertEqual(stage.user_items["evaluation-user"], set(items[:2]))
            self.assertEqual(len(stage.catalog), 12)
            self.assertEqual(len(stage.item_relations), 66)
            self.assertEqual(stage.item_relation_field, "within_2km")
            self.assertEqual(audit["minimum_training_profile"], 2)
            self.assertEqual(audit["graph_pruning"], "none")
            self.assertFalse(audit["future_rows_used_for_market_or_relation"])
            self.assertEqual(audit["training_test_edge_overlap"], 0)


if __name__ == "__main__":
    unittest.main()
