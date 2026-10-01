"""Precision@5 and binary NDCG@5 for the Foursquare case study."""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class MethodSpec:
    method: str
    score_column: str
    cycle_length: int | None = None


METHOD_SPECS: tuple[MethodSpec, ...] = (
    MethodSpec("itemknn", "itemknn"),
    MethodSpec("c3_truss_only", "max_c3_item_edge_tau_c3", 3),
    MethodSpec("c4_truss_only", "max_c4_item_edge_tau_c4", 4),
    MethodSpec("c5_truss_only", "max_c5_role_edge_tau_c5", 5),
    MethodSpec("c6_truss_only", "max_native_c6_role_edge_tau_c6", 6),
)


ScoreLedger = dict[str, dict[str, dict[str, float]]]


def load_score_ledger(path: Path) -> ScoreLedger:
    required = {"user_id", "item_id", "training_item_degree"} | {
        spec.score_column for spec in METHOD_SPECS
    }
    output: ScoreLedger = {}
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = required - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f"missing score columns: {sorted(missing)}")
        numeric = sorted(required - {"user_id", "item_id"})
        for row in reader:
            user, item = row["user_id"], row["item_id"]
            if item in output.setdefault(user, {}):
                raise ValueError(f"duplicate recommendation pair: {(user, item)}")
            output[user][item] = {
                column: float(row[column]) for column in numeric
            }
    if not output:
        raise ValueError(f"empty score ledger: {path}")
    return output


def _ranking(
    scores: Mapping[str, Mapping[str, float]],
    spec: MethodSpec,
) -> list[str]:
    """Rank the complete warm-unseen Item set, including zero scores."""

    return sorted(
        scores,
        key=lambda item: (
            -scores[item][spec.score_column],
            -scores[item]["training_item_degree"],
            item,
        ),
    )


def evaluate_precision_ndcg_at_5(
    ledger: ScoreLedger,
    positives: Mapping[str, frozenset[str]],
    *,
    dataset: str,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Evaluate the five methods on the same complete Top-5 catalog.

    Every User receives exactly five Items. Zero-score Items remain eligible
    and use the common degree/Item-ID tie-breaker. Precision uses a fixed
    denominator of five; binary NDCG uses the User's full test-positive set.
    """

    cutoff = 5
    if set(ledger) != set(positives):
        raise ValueError("score and positive user cohorts differ")
    for user, scores in ledger.items():
        if len(scores) < cutoff:
            raise ValueError(f"user {user!r} has fewer than {cutoff} items")
        missing_positives = set(positives[user]).difference(scores)
        if missing_positives:
            raise ValueError(
                f"user {user!r} has test positives outside the common "
                "item universe"
            )

    users = sorted(positives)
    positive_count = sum(len(positives[user]) for user in users)
    aggregate: list[dict[str, object]] = []
    per_user: list[dict[str, object]] = []
    for spec in METHOD_SPECS:
        total_hits = 0
        ndcg_sum = 0.0
        for user in users:
            relevant = positives[user]
            if not relevant:
                raise ValueError(f"empty positive set for user {user!r}")
            recommendations = _ranking(ledger[user], spec)[:cutoff]
            if len(recommendations) != cutoff:
                raise AssertionError("full-catalog Top-5 was not filled")
            hits = sum(item in relevant for item in recommendations)
            dcg = sum(
                1.0 / math.log2(rank + 1)
                for rank, item in enumerate(recommendations, start=1)
                if item in relevant
            )
            ideal_hits = min(len(relevant), cutoff)
            idcg = sum(
                1.0 / math.log2(rank + 1)
                for rank in range(1, ideal_hits + 1)
            )
            ndcg = dcg / idcg
            total_hits += hits
            ndcg_sum += ndcg
            per_user.append(
                {
                    "dataset": dataset,
                    "method": spec.method,
                    "user": user,
                    "precision_at_5": hits / cutoff,
                    "ndcg_at_5": ndcg,
                }
            )
        aggregate.append(
            {
                "dataset": dataset,
                "method": spec.method,
                "cycle_length": spec.cycle_length or "",
                "users": len(users),
                "positives": positive_count,
                "ndcg_at_5": ndcg_sum / len(users),
                "precision_at_5": total_hits / (len(users) * cutoff),
            }
        )
    return aggregate, per_user


def audit_score_support_at_5(
    ledger: ScoreLedger,
    positives: Mapping[str, frozenset[str]],
) -> dict[str, dict[str, int]]:
    """Audit Top-5 filling, score support, and boundary ties."""

    cutoff = 5
    output: dict[str, dict[str, int]] = {}
    for spec in METHOD_SPECS:
        returned_items = 0
        zero_score_items = 0
        users_with_no_positive_scores = 0
        boundary_tie_users = 0
        for user in sorted(positives):
            ranking = _ranking(ledger[user], spec)
            if len(ranking) < cutoff:
                raise AssertionError("full-catalog Top-5 was not filled")
            if len(ranking) > cutoff:
                boundary = ledger[user][ranking[cutoff - 1]][spec.score_column]
                following = ledger[user][ranking[cutoff]][spec.score_column]
                boundary_tie_users += int(boundary == following)
            recommendations = ranking[:cutoff]
            returned_items += cutoff
            zero_score_items += sum(
                ledger[user][item][spec.score_column] <= 0
                for item in recommendations
            )
            users_with_no_positive_scores += int(
                all(
                    scores[spec.score_column] <= 0
                    for scores in ledger[user].values()
                )
            )
        output[spec.method] = {
            "returned_items_at_5": returned_items,
            "zero_score_items_at_5": zero_score_items,
            "users_with_no_positive_scores": users_with_no_positive_scores,
            "score_tie_at_rank_5_users": boundary_tie_users,
        }
    return output
