"""Leakage-safe Foursquare Tokyo next-venue case construction.

The case uses one global UTC cutoff.  Distinct pre-cutoff check-ins define
User--Venue edges, while later warm and previously unseen check-ins are test
links.  The market is the native ``Department Store`` venue category.  Two
training venues receive an Item--Item relationship exactly when their frozen
pre-cutoff coordinates are at most two kilometres apart.  Every qualifying
relationship is retained; there is no top-k graph sparsification.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import math
from collections import defaultdict
from itertools import combinations
from pathlib import Path
from typing import Iterator

from crossrec_final.data import Interaction, StageData


MARKET_CATEGORY = "Department Store"
CUTOFF = "2012-12-01"
RADIUS_KM = 2.0
MINIMUM_PROFILE = 2
EXPECTED_COLUMNS = (
    "userId",
    "venueId",
    "venueCategoryId",
    "venueCategory",
    "latitude",
    "longitude",
    "timezoneOffset",
    "utcTimestamp",
)


def _cutoff_timestamp(value: str) -> int:
    return int(
        dt.datetime.strptime(value, "%Y-%m-%d")
        .replace(tzinfo=dt.timezone.utc)
        .timestamp()
    )


def _checkin_timestamp(value: str) -> int:
    return int(
        dt.datetime.strptime(value, "%a %b %d %H:%M:%S %z %Y").timestamp()
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _rows(path: Path) -> Iterator[dict[str, str]]:
    """Read either the comma-separated mirror or original tab-separated file."""

    with path.open(encoding="latin-1", newline="") as handle:
        first = handle.readline()
        if not first:
            raise ValueError(f"empty Foursquare file: {path}")
        delimiter = "\t" if first.count("\t") > first.count(",") else ","
        handle.seek(0)
        reader = csv.DictReader(handle, delimiter=delimiter)
        missing = set(EXPECTED_COLUMNS).difference(reader.fieldnames or ())
        if missing:
            raise ValueError(
                f"Foursquare file is missing columns: {sorted(missing)}"
            )
        yield from reader


def _haversine_km(
    left: tuple[float, float], right: tuple[float, float]
) -> float:
    lat1, lon1 = map(math.radians, left)
    lat2, lon2 = map(math.radians, right)
    dlat, dlon = lat2 - lat1, lon2 - lon1
    value = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    )
    return 2 * 6371.0088 * math.asin(math.sqrt(value))


def build_stage(
    *,
    checkins_path: Path,
    market_category: str = MARKET_CATEGORY,
    cutoff: str = CUTOFF,
    radius_km: float = RADIUS_KM,
    minimum_profile: int = MINIMUM_PROFILE,
) -> tuple[StageData, dict[str, frozenset[str]], dict[str, object]]:
    """Build the fixed Tokyo Department Store temporal prediction stage."""

    if radius_km <= 0:
        raise ValueError("radius_km must be positive")
    if minimum_profile < 1:
        raise ValueError("minimum_profile must be positive")
    cutoff_value = _cutoff_timestamp(cutoff)

    # Freeze venue category and coordinates at the earliest pre-cutoff record.
    venue_metadata: dict[
        str, tuple[str, str, float, float, int]
    ] = {}
    parsed_rows = 0
    for row in _rows(checkins_path):
        parsed_rows += 1
        timestamp = _checkin_timestamp(row["utcTimestamp"])
        if timestamp >= cutoff_value:
            continue
        venue = row["venueId"]
        candidate = (
            row["venueCategoryId"],
            row["venueCategory"],
            float(row["latitude"]),
            float(row["longitude"]),
            timestamp,
        )
        current = venue_metadata.get(venue)
        if current is None or timestamp < current[-1]:
            venue_metadata[venue] = candidate

    market_items = {
        venue
        for venue, (_category_id, category, _lat, _lon, _time)
        in venue_metadata.items()
        if category == market_category
    }
    if len(market_items) < 11:
        raise ValueError("Foursquare market has fewer than eleven warm venues")

    earliest_training: dict[tuple[str, str], int] = {}
    future: dict[str, set[str]] = defaultdict(set)
    for row in _rows(checkins_path):
        venue = row["venueId"]
        if venue not in market_items:
            continue
        user = row["userId"]
        timestamp = _checkin_timestamp(row["utcTimestamp"])
        if timestamp < cutoff_value:
            key = (user, venue)
            earliest_training[key] = min(
                timestamp, earliest_training.get(key, timestamp)
            )
        else:
            future[user].add(venue)

    user_items: dict[str, set[str]] = defaultdict(set)
    item_users: dict[str, set[str]] = defaultdict(set)
    interactions: list[Interaction] = []
    for (user, item), timestamp in sorted(earliest_training.items()):
        user_items[user].add(item)
        item_users[item].add(user)
        interactions.append(Interaction(user, item, 1.0, timestamp))
    catalog = set(item_users)

    item_relations: set[tuple[str, str]] = set()
    for left, right in combinations(sorted(catalog), 2):
        left_xy = (venue_metadata[left][2], venue_metadata[left][3])
        right_xy = (venue_metadata[right][2], venue_metadata[right][3])
        if _haversine_km(left_xy, right_xy) <= radius_km:
            item_relations.add((left, right))

    positives: dict[str, frozenset[str]] = {}
    for user, observed in sorted(user_items.items()):
        if len(observed) < minimum_profile:
            continue
        unseen = frozenset(future.get(user, set()).intersection(catalog) - observed)
        if unseen and len(catalog.difference(observed)) >= 10:
            positives[user] = unseen
    if not positives:
        raise ValueError("no warm unseen future Foursquare links")

    targets = {user: min(items) for user, items in positives.items()}
    stage = StageData(
        stage="foursquare_tky_department_store_2012_12",
        interactions=interactions,
        user_items={user: set(items) for user, items in user_items.items()},
        item_users={item: set(users) for item, users in item_users.items()},
        targets=targets,
        strictly_later_day_targets=targets,
        eligible_users=sorted(positives),
        catalog=catalog,
        metadata={
            item: {
                "title": item,
                "category": venue_metadata[item][1],
                "category_id": venue_metadata[item][0],
                "latitude": venue_metadata[item][2],
                "longitude": venue_metadata[item][3],
            }
            for item in catalog
        },
        item_relations=item_relations,
        item_relation_field=f"within_{radius_km:g}km",
    )
    audit: dict[str, object] = {
        "dataset": "Foursquare TSMC2014 Tokyo check-ins",
        "dataset_source": (
            "https://www-public.imtbs-tsp.eu/~zhang_da/pub/"
            "dataset_tsmc2014.zip"
        ),
        "input_sha256": _sha256(checkins_path),
        "input_rows": parsed_rows,
        "market": market_category,
        "market_rule": "exact native pre-cutoff venue category",
        "cutoff": cutoff,
        "cutoff_timezone": "UTC",
        "training_rule": "distinct User--Venue check-in before cutoff",
        "positive_rule": (
            "warm unseen User--Venue check-in at or after cutoff"
        ),
        "minimum_training_profile": minimum_profile,
        "item_relation": (
            f"all market venues at most {radius_km:g} km apart by "
            "haversine distance using frozen pre-cutoff coordinates"
        ),
        "radius_km": radius_km,
        "graph_pruning": "none",
        "top_k_item_relation_filter": False,
        "future_rows_used_for_market_or_relation": False,
        "training_users": len(user_items),
        "warm_items": len(catalog),
        "training_ui_edges": len(interactions),
        "training_ii_edges": len(item_relations),
        "evaluation_users": len(positives),
        "test_positive_edges": sum(map(len, positives.values())),
        "training_test_edge_overlap": sum(
            len(items.intersection(user_items[user]))
            for user, items in positives.items()
        ),
    }
    return stage, positives, audit
