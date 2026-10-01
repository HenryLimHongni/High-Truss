from __future__ import annotations

import heapq
import json
import math
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from statistics import fmean
from typing import Iterable, Mapping

from .artifacts import CaseArtifact, load_case, load_value_file
from .utils import atomic_write_text, write_tsv


@dataclass
class C5Evidence:
    max_value: int = 0
    cycle_value_sum: int = 0
    typed_cycle_count: int = 0
    qualifying_ii_edges: int = 0


@dataclass
class C6Evidence:
    max_value: int = 0
    typed_cycle_count: int = 0
    qualifying_ii_edge_ids: set[int] = field(default_factory=set)


@dataclass
class MethodResult:
    method: str
    structural_candidates: set[int]
    scores: dict[int, float]
    ranking: list[int]
    padding_items: set[int]
    typed_c5_count: Mapping[int, int] = field(default_factory=dict)
    typed_c6_count: Mapping[int, int] = field(default_factory=dict)
    qualifying_ii_edges: Mapping[int, int] = field(default_factory=dict)


def _mean(values: Iterable[float]) -> float:
    parsed = list(values)
    return fmean(parsed) if parsed else 0.0


def _dcg(values: list[int]) -> float:
    return sum(value / math.log2(index + 2) for index, value in enumerate(values))


def _read_graph_rows(case: CaseArtifact) -> tuple[tuple[int, int], ...]:
    rows: list[tuple[int, int]] = []
    with case.graph_path.open(encoding="ascii") as handle:
        for line in handle:
            left, right = map(int, line.split())
            rows.append((left, right))
    if len(rows) != case.edge_count:
        raise ValueError("graph row count differs from case")
    return tuple(rows)


def _rank_items(
    items: Iterable[int],
    scores: Mapping[int, float],
    degree: list[int],
) -> list[int]:
    return sorted(items, key=lambda item: (-scores.get(item, 0.0), -degree[item], item))


def _rank_with_optional_padding(
    *,
    structural_candidates: set[int],
    scores: Mapping[int, float],
    observed: set[int],
    catalog_by_degree: tuple[int, ...],
    degree: list[int],
    target_length: int,
    pad: bool,
) -> tuple[list[int], set[int]]:
    ranking = _rank_items(structural_candidates, scores, degree)
    padding: set[int] = set()
    if not pad or len(ranking) >= target_length:
        return ranking, padding
    excluded = set(observed)
    excluded.update(structural_candidates)
    for item in catalog_by_degree:
        if item in excluded:
            continue
        ranking.append(item)
        padding.add(item)
        if len(ranking) >= target_length:
            break
    return ranking, padding


def _widest_scores(
    source: int,
    adjacency: list[list[tuple[int, int]]],
    maximum_weight: int,
) -> list[int]:
    best = [0] * len(adjacency)
    if maximum_weight <= 0:
        return best
    best[source] = maximum_weight
    queue: list[tuple[int, int]] = [(-maximum_weight, source)]
    while queue:
        negative, node = heapq.heappop(queue)
        current = -negative
        if current != best[node]:
            continue
        for neighbor, weight in adjacency[node]:
            candidate = min(current, weight)
            if candidate > best[neighbor]:
                best[neighbor] = candidate
                heapq.heappush(queue, (-candidate, neighbor))
    return best


def _c3_direct_scores(
    *,
    observed: set[int],
    item_neighbors: Mapping[int, list[tuple[int, int]]],
    c3_truss: tuple[int, ...],
) -> tuple[set[int], dict[int, float], dict[int, int]]:
    """C3 baseline requested by the paper revision.

    If the user observed item b and II edge (b,c) exists, unseen item c is a
    candidate. Its score is the maximum C3-trussness among all such II edges.
    Edges with trussness zero still define a structural candidate.
    """

    candidates: set[int] = set()
    scores: dict[int, float] = {}
    edge_counts: dict[int, int] = defaultdict(int)
    for bought in observed:
        for candidate, edge_id in item_neighbors.get(bought, ()):
            if candidate in observed:
                continue
            candidates.add(candidate)
            value = float(c3_truss[edge_id])
            if candidate not in scores or value > scores[candidate]:
                scores[candidate] = value
            edge_counts[candidate] += 1
    for candidate in candidates:
        scores.setdefault(candidate, 0.0)
    return candidates, scores, dict(edge_counts)


def _c5_evidence(
    *,
    user: int,
    observed: set[int],
    user_items: Mapping[int, set[int]],
    item_users: Mapping[int, set[int]],
    item_neighbors: Mapping[int, list[tuple[int, int]]],
    c5_truss: tuple[int, ...],
) -> dict[int, C5Evidence]:
    """Typed C5 u-b-v-c-a-u with unique II edge (a,c)."""

    shared_count: dict[int, int] = defaultdict(int)
    for shared_item in observed:
        for other_user in item_users.get(shared_item, ()):
            if other_user != user:
                shared_count[other_user] += 1

    evidence: dict[int, C5Evidence] = defaultdict(C5Evidence)
    for anchor in observed:
        for candidate, ii_edge_id in item_neighbors.get(anchor, ()):
            if candidate in observed:
                continue
            typed_cycle_count = 0
            for other_user in item_users.get(candidate, ()):
                if other_user == user:
                    continue
                count = shared_count.get(other_user, 0)
                if anchor in user_items[other_user]:
                    count -= 1
                if count > 0:
                    typed_cycle_count += count
            if typed_cycle_count <= 0:
                continue
            value = c5_truss[ii_edge_id]
            if value <= 0:
                raise RuntimeError(
                    "typed C5 found on an II edge with non-positive C5-trussness"
                )
            entry = evidence[candidate]
            entry.max_value = max(entry.max_value, value)
            entry.cycle_value_sum += value * typed_cycle_count
            entry.typed_cycle_count += typed_cycle_count
            entry.qualifying_ii_edges += 1
    return dict(evidence)


def _valid_anchor_pair_values(
    left_item: int,
    right_item: int,
    left_edges: list[tuple[int, int, int]],
    right_edges: list[tuple[int, int, int]],
) -> tuple[int, int, int, set[int], set[int]]:
    """Return exact typed-C6 anchor-pair count and best edge values.

    left_edges contains (anchor, edge_id, tau6) for anchor--left_item. A valid
    pair chooses distinct anchors and four distinct item vertices.
    """

    count = 0
    best_left = 0
    best_right = 0
    valid_left_edges: set[int] = set()
    valid_right_edges: set[int] = set()
    for left_anchor, left_edge, left_value in left_edges:
        if left_anchor == right_item:
            continue
        for right_anchor, right_edge, right_value in right_edges:
            if right_anchor in {left_item, left_anchor}:
                continue
            # left_item != right_item by caller; each II edge already has
            # distinct endpoints. The remaining checks establish four distinct
            # item vertices a,b,c,d.
            count += 1
            best_left = max(best_left, left_value)
            best_right = max(best_right, right_value)
            valid_left_edges.add(left_edge)
            valid_right_edges.add(right_edge)
    return count, best_left, best_right, valid_left_edges, valid_right_edges


def _c6_evidence(
    *,
    user: int,
    observed: set[int],
    user_items: Mapping[int, set[int]],
    item_users: Mapping[int, set[int]],
    item_neighbors: Mapping[int, list[tuple[int, int]]],
    c6_truss: tuple[int, ...],
) -> dict[int, C6Evidence]:
    """Typed C6 u-a-b-v-c-d-u, scored exactly as requested.

    The cycle recommends b and c to u. Recommendation b uses II edge (a,b),
    while recommendation c uses II edge (c,d). For every candidate, the score
    is the maximum tau6 over all corresponding II edges in all typed C6s.
    """

    anchor_edges: dict[int, list[tuple[int, int, int]]] = defaultdict(list)
    relevant_users: set[int] = set()
    for anchor in observed:
        for item, edge_id in item_neighbors.get(anchor, ()):
            value = c6_truss[edge_id]
            if value <= 0:
                continue
            anchor_edges[item].append((anchor, edge_id, value))
            relevant_users.update(item_users.get(item, ()))

    evidence: dict[int, C6Evidence] = defaultdict(C6Evidence)
    anchor_items = set(anchor_edges)
    for other_user in relevant_users:
        if other_user == user:
            continue
        relevant_items = sorted(user_items[other_user].intersection(anchor_items))
        for left_index, left_item in enumerate(relevant_items):
            left_edges = anchor_edges[left_item]
            for right_item in relevant_items[left_index + 1 :]:
                right_edges = anchor_edges[right_item]
                (
                    cycle_count,
                    best_left,
                    best_right,
                    valid_left_edges,
                    valid_right_edges,
                ) = _valid_anchor_pair_values(
                    left_item,
                    right_item,
                    left_edges,
                    right_edges,
                )
                if cycle_count <= 0:
                    continue
                if left_item not in observed:
                    entry = evidence[left_item]
                    entry.max_value = max(entry.max_value, best_left)
                    entry.typed_cycle_count += cycle_count
                    entry.qualifying_ii_edge_ids.update(valid_left_edges)
                if right_item not in observed:
                    entry = evidence[right_item]
                    entry.max_value = max(entry.max_value, best_right)
                    entry.typed_cycle_count += cycle_count
                    entry.qualifying_ii_edge_ids.update(valid_right_edges)
    return dict(evidence)


def _itemknn_positive_scores(
    *,
    observed: set[int],
    user_items: Mapping[int, set[int]],
    item_users: Mapping[int, set[int]],
    inverse_sqrt_item_users: Mapping[int, float],
) -> dict[int, float]:
    scores: dict[int, float] = defaultdict(float)
    for anchor in observed:
        inverse_anchor = inverse_sqrt_item_users.get(anchor, 0.0)
        if inverse_anchor == 0.0:
            continue
        for other_user in item_users.get(anchor, ()):
            for candidate in user_items[other_user]:
                if candidate in observed:
                    continue
                inverse_candidate = inverse_sqrt_item_users.get(candidate, 0.0)
                if inverse_candidate > 0.0:
                    scores[candidate] += inverse_anchor * inverse_candidate
    return dict(scores)


def _itemknn_ranking(
    *,
    observed: set[int],
    positive_scores: Mapping[int, float],
    catalog_by_degree: tuple[int, ...],
    degree: list[int],
    target_length: int,
) -> list[int]:
    positive = _rank_items(positive_scores, positive_scores, degree)
    ranking = positive[:target_length]
    if len(ranking) >= target_length:
        return ranking
    positive_items = set(positive_scores)
    for item in catalog_by_degree:
        if item in observed or item in positive_items:
            continue
        ranking.append(item)
        if len(ranking) >= target_length:
            break
    return ranking


def evaluate_all_methods(
    *,
    case_dir: Path,
    output_dir: Path,
    top_users: int = 500,
    cutoffs: Iterable[int] = (5, 10),
    ranking_limit: int | None = None,
    include_itemknn: bool = True,
    skip_c6: bool = False,
) -> Path:
    case = load_case(case_dir)
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if top_users <= 0:
        raise ValueError("top_users must be positive")
    cutoff_values = tuple(sorted(set(int(value) for value in cutoffs)))
    if not cutoff_values or any(value <= 0 for value in cutoff_values):
        raise ValueError("cutoffs must contain positive integers")
    max_cutoff = max(cutoff_values)
    ranking_limit = max_cutoff if ranking_limit is None else max(
        int(ranking_limit), max_cutoff
    )

    graph_rows = _read_graph_rows(case)
    node_count = len(case.nodes)
    degree = [0] * node_count
    user_items: dict[int, set[int]] = defaultdict(set)
    item_users: dict[int, set[int]] = defaultdict(set)
    item_neighbors: dict[int, list[tuple[int, int]]] = defaultdict(list)
    truth_by_user: dict[int, set[int]] = defaultdict(set)

    for edge_id, (((left_key, right_key), record), (left, right)) in enumerate(
        zip(zip(case.edges, case.edge_records), graph_rows)
    ):
        if (case.node_to_id[left_key], case.node_to_id[right_key]) != (left, right):
            raise ValueError(f"edge alignment differs at edge {edge_id}")
        degree[left] += 1
        degree[right] += 1
        if record.edge_type == "UI":
            if case.nodes[left].node_type == "user":
                user, item = left, right
            else:
                user, item = right, left
            user_items[user].add(item)
            item_users[item].add(user)
        elif record.edge_type == "II":
            item_neighbors[left].append((right, edge_id))
            item_neighbors[right].append((left, edge_id))
        else:
            raise ValueError(f"unexpected edge type: {record.edge_type}")

    catalog_items = tuple(
        node_id
        for node_id, record in enumerate(case.nodes)
        if record.node_type == "item"
    )
    catalog_by_degree = tuple(sorted(catalog_items, key=lambda item: (-degree[item], item)))
    for user_key, item_key in case.truth:
        truth_by_user[case.node_to_id[user_key]].add(case.node_to_id[item_key])

    globally_ranked = sorted(
        user_items,
        key=lambda user: (-len(user_items[user]), case.nodes[user].entity_id, user),
    )
    global_rank = {user: rank for rank, user in enumerate(globally_ranked, 1)}
    eligible = [user for user in globally_ranked if truth_by_user.get(user)]
    selected_users = eligible[:top_users]
    if not selected_users:
        raise ValueError("no training user has a warm-unseen test edge")

    c3_truss = load_value_file(
        case,
        case.root / "values" / "c3_truss.txt",
        expected_algorithm="c3_truss",
    )
    c4_truss = load_value_file(
        case,
        case.root / "values" / "c4_truss.txt",
        expected_algorithm="c4_truss",
    )
    c5_truss = load_value_file(
        case,
        case.root / "values" / "c5_truss.txt",
        expected_algorithm="c5_truss",
    )
    c6_truss: tuple[int, ...] | None = None
    if not skip_c6:
        c6_truss = load_value_file(
            case,
            case.root / "values" / "c6_truss.txt",
            expected_algorithm="c6_truss",
        )

    c4_adjacency: list[list[tuple[int, int]]] = [[] for _ in range(node_count)]
    for edge_id, (left, right) in enumerate(graph_rows):
        value = c4_truss[edge_id]
        if value > 0:
            c4_adjacency[left].append((right, value))
            c4_adjacency[right].append((left, value))
    c4_maximum = max(c4_truss, default=0)

    inverse_sqrt_item_users = {
        item: 1.0 / math.sqrt(len(users))
        for item, users in item_users.items()
        if users
    }

    methods = [
        "c3-truss-direct",
        "c4-truss-connectivity",
        "c5-max",
        "c5-avg",
    ]
    if not skip_c6:
        methods.append("c6-max")
    if include_itemknn:
        methods.append("itemknn-full-catalog")

    per_user_rows: list[dict[str, object]] = []
    ranking_rows: list[dict[str, object]] = []
    selected_user_rows: list[dict[str, object]] = []
    metric_records: list[dict[str, object]] = []
    started = time.monotonic()

    for selected_rank, user in enumerate(selected_users, 1):
        observed = user_items[user]
        truth = truth_by_user[user]

        c3_candidates, c3_scores, c3_edges = _c3_direct_scores(
            observed=observed,
            item_neighbors=item_neighbors,
            c3_truss=c3_truss,
        )
        c3_ranking, c3_padding = _rank_with_optional_padding(
            structural_candidates=c3_candidates,
            scores=c3_scores,
            observed=observed,
            catalog_by_degree=catalog_by_degree,
            degree=degree,
            target_length=ranking_limit,
            pad=True,
        )

        c4_scores_all = _widest_scores(user, c4_adjacency, c4_maximum)
        c4_candidates = {
            item
            for item in catalog_items
            if item not in observed and c4_scores_all[item] > 0
        }
        c4_scores = {item: float(c4_scores_all[item]) for item in c4_candidates}
        c4_ranking, c4_padding = _rank_with_optional_padding(
            structural_candidates=c4_candidates,
            scores=c4_scores,
            observed=observed,
            catalog_by_degree=catalog_by_degree,
            degree=degree,
            target_length=ranking_limit,
            pad=False,
        )

        c5_evidence = _c5_evidence(
            user=user,
            observed=observed,
            user_items=user_items,
            item_users=item_users,
            item_neighbors=item_neighbors,
            c5_truss=c5_truss,
        )
        c5_candidates = set(c5_evidence)
        c5_max_scores = {
            item: float(evidence.max_value) for item, evidence in c5_evidence.items()
        }
        c5_avg_scores = {
            item: evidence.cycle_value_sum / evidence.typed_cycle_count
            for item, evidence in c5_evidence.items()
        }
        c5_max_ranking, c5_max_padding = _rank_with_optional_padding(
            structural_candidates=c5_candidates,
            scores=c5_max_scores,
            observed=observed,
            catalog_by_degree=catalog_by_degree,
            degree=degree,
            target_length=ranking_limit,
            pad=True,
        )
        c5_avg_ranking, c5_avg_padding = _rank_with_optional_padding(
            structural_candidates=c5_candidates,
            scores=c5_avg_scores,
            observed=observed,
            catalog_by_degree=catalog_by_degree,
            degree=degree,
            target_length=ranking_limit,
            pad=True,
        )

        c6_evidence: dict[int, C6Evidence] = {}
        c6_candidates: set[int] = set()
        c6_scores: dict[int, float] = {}
        c6_ranking: list[int] = []
        c6_padding: set[int] = set()

        if not skip_c6:
            if c6_truss is None:
                raise RuntimeError("C6 values unexpectedly unavailable")

            c6_evidence = _c6_evidence(
                user=user,
                observed=observed,
                user_items=user_items,
                item_users=item_users,
                item_neighbors=item_neighbors,
                c6_truss=c6_truss,
            )

            c6_candidates = set(c6_evidence)

            c6_scores = {
                item: float(evidence.max_value)
                for item, evidence in c6_evidence.items()
            }

            c6_ranking, c6_padding = _rank_with_optional_padding(
                structural_candidates=c6_candidates,
                scores=c6_scores,
                observed=observed,
                catalog_by_degree=catalog_by_degree,
                degree=degree,
                target_length=ranking_limit,
                pad=False,
            )

        method_results: list[MethodResult] = [
            MethodResult(
                method="c3-truss-direct",
                structural_candidates=c3_candidates,
                scores=c3_scores,
                ranking=c3_ranking,
                padding_items=c3_padding,
                qualifying_ii_edges=c3_edges,
            ),
            MethodResult(
                method="c4-truss-connectivity",
                structural_candidates=c4_candidates,
                scores=c4_scores,
                ranking=c4_ranking,
                padding_items=c4_padding,
            ),
            MethodResult(
                method="c5-max",
                structural_candidates=c5_candidates,
                scores=c5_max_scores,
                ranking=c5_max_ranking,
                padding_items=c5_max_padding,
                typed_c5_count={
                    item: evidence.typed_cycle_count
                    for item, evidence in c5_evidence.items()
                },
                qualifying_ii_edges={
                    item: evidence.qualifying_ii_edges
                    for item, evidence in c5_evidence.items()
                },
            ),
            MethodResult(
                method="c5-avg",
                structural_candidates=c5_candidates,
                scores=c5_avg_scores,
                ranking=c5_avg_ranking,
                padding_items=c5_avg_padding,
                typed_c5_count={
                    item: evidence.typed_cycle_count
                    for item, evidence in c5_evidence.items()
                },
                qualifying_ii_edges={
                    item: evidence.qualifying_ii_edges
                    for item, evidence in c5_evidence.items()
                },
            ),
        ]

        if not skip_c6:
            method_results.append(
                MethodResult(
                    method="c6-max",
                    structural_candidates=c6_candidates,
                    scores=c6_scores,
                    ranking=c6_ranking,
                    padding_items=c6_padding,
                    typed_c6_count={
                        item: evidence.typed_cycle_count
                        for item, evidence in c6_evidence.items()
                    },
                    qualifying_ii_edges={
                        item: len(evidence.qualifying_ii_edge_ids)
                        for item, evidence in c6_evidence.items()
                    },
                )
            )

        if include_itemknn:
            itemknn_scores = _itemknn_positive_scores(
                observed=observed,
                user_items=user_items,
                item_users=item_users,
                inverse_sqrt_item_users=inverse_sqrt_item_users,
            )
            itemknn_candidates = set(catalog_items).difference(observed)
            itemknn_ranking = _itemknn_ranking(
                observed=observed,
                positive_scores=itemknn_scores,
                catalog_by_degree=catalog_by_degree,
                degree=degree,
                target_length=ranking_limit,
            )
            method_results.append(
                MethodResult(
                    method="itemknn-full-catalog",
                    structural_candidates=itemknn_candidates,
                    scores={item: itemknn_scores.get(item, 0.0) for item in itemknn_candidates},
                    ranking=itemknn_ranking,
                    padding_items={
                        item for item in itemknn_ranking if item not in itemknn_scores
                    },
                )
            )

        user_summary: dict[str, object] = {
            "selected_rank": selected_rank,
            "global_training_degree_rank": global_rank[user],
            "user_node": user,
            "user_id": case.nodes[user].entity_id,
            "training_item_degree": len(observed),
            "ground_truth_all": len(truth),
        }

        for result in method_results:
            method_truth = result.structural_candidates.intersection(truth)
            method_key = result.method.replace("-", "_")
            user_summary[f"{method_key}_candidate_count"] = len(
                result.structural_candidates
            )
            user_summary[f"{method_key}_candidate_ground_truth"] = len(method_truth)

            for rank, item in enumerate(result.ranking[:ranking_limit], 1):
                ranking_rows.append(
                    {
                        "selected_rank": selected_rank,
                        "global_training_degree_rank": global_rank[user],
                        "user_node": user,
                        "user_id": case.nodes[user].entity_id,
                        "method": result.method,
                        "rank": rank,
                        "item_node": item,
                        "item_id": case.nodes[item].entity_id,
                        "title": case.nodes[item].title,
                        "score": result.scores.get(item, 0.0),
                        "item_full_graph_degree": degree[item],
                        "structural_candidate": int(item in result.structural_candidates),
                        "padding_item": int(item in result.padding_items),
                        "qualifying_ii_edges": result.qualifying_ii_edges.get(item, 0),
                        "typed_c5_count": result.typed_c5_count.get(item, 0),
                        "typed_c6_count": result.typed_c6_count.get(item, 0),
                        "ground_truth": int(item in truth),
                    }
                )

            for cutoff in cutoff_values:
                top = result.ranking[:cutoff]
                hits = sum(item in truth for item in top)
                structural_hits = sum(
                    item in truth and item in result.structural_candidates for item in top
                )
                padding_hits = sum(item in truth and item in result.padding_items for item in top)
                relevance = [int(item in truth) for item in top]
                if len(relevance) < cutoff:
                    relevance.extend([0] * (cutoff - len(relevance)))
                ideal_hits = min(len(truth), cutoff)
                precision = hits / cutoff
                hr = float(hits > 0)
                recall = hits / len(truth) if truth else 0.0
                ndcg = (
                    _dcg(relevance) / _dcg([1] * ideal_hits)
                    if ideal_hits
                    else 0.0
                )
                candidate_recall = (
                    structural_hits / len(method_truth) if method_truth else 0.0
                )
                row = {
                    "selected_rank": selected_rank,
                    "global_training_degree_rank": global_rank[user],
                    "user_node": user,
                    "user_id": case.nodes[user].entity_id,
                    "training_item_degree": len(observed),
                    "method": result.method,
                    "cutoff": cutoff,
                    "recommendation_count": len(top),
                    "missing_slots": cutoff - len(top),
                    "structural_candidate_count": len(result.structural_candidates),
                    "candidate_ground_truth": len(method_truth),
                    "ground_truth_all": len(truth),
                    "hits": hits,
                    "structural_hits": structural_hits,
                    "padding_hits": padding_hits,
                    "precision": precision,
                    "hr": hr,
                    "recall": recall,
                    "candidate_recall": candidate_recall,
                    "ndcg": ndcg,
                }
                per_user_rows.append(row)
                metric_records.append(row)
        selected_user_rows.append(user_summary)

        if selected_rank % 10 == 0 or selected_rank == len(selected_users):
            print(
                f"[evaluate-all] users={selected_rank}/{len(selected_users)} "
                f"elapsed_seconds={time.monotonic() - started:.1f}",
                file=sys.stderr,
            )

    user_fields = [
        "selected_rank",
        "global_training_degree_rank",
        "user_node",
        "user_id",
        "training_item_degree",
        "ground_truth_all",
    ]
    for method in methods:
        key = method.replace("-", "_")
        user_fields.extend([f"{key}_candidate_count", f"{key}_candidate_ground_truth"])
    write_tsv(output_dir / "selected_users.tsv", user_fields, selected_user_rows)

    per_user_fields = [
        "selected_rank",
        "global_training_degree_rank",
        "user_node",
        "user_id",
        "training_item_degree",
        "method",
        "cutoff",
        "recommendation_count",
        "missing_slots",
        "structural_candidate_count",
        "candidate_ground_truth",
        "ground_truth_all",
        "hits",
        "structural_hits",
        "padding_hits",
        "precision",
        "hr",
        "recall",
        "candidate_recall",
        "ndcg",
    ]
    write_tsv(output_dir / "per_user_metrics.tsv", per_user_fields, per_user_rows)

    write_tsv(
        output_dir / "rankings_top.tsv",
        [
            "selected_rank",
            "global_training_degree_rank",
            "user_node",
            "user_id",
            "method",
            "rank",
            "item_node",
            "item_id",
            "title",
            "score",
            "item_full_graph_degree",
            "structural_candidate",
            "padding_item",
            "qualifying_ii_edges",
            "typed_c5_count",
            "typed_c6_count",
            "ground_truth",
        ],
        ranking_rows,
    )

    aggregate_rows: list[dict[str, object]] = []
    for method in methods:
        for cutoff in cutoff_values:
            rows = [
                row
                for row in metric_records
                if row["method"] == method and row["cutoff"] == cutoff
            ]
            total_hits = sum(int(row["hits"]) for row in rows)
            total_structural_hits = sum(int(row["structural_hits"]) for row in rows)
            total_padding_hits = sum(int(row["padding_hits"]) for row in rows)
            total_gt = sum(int(row["ground_truth_all"]) for row in rows)
            total_candidate_gt = sum(int(row["candidate_ground_truth"]) for row in rows)
            aggregate_rows.append(
                {
                    "method": method,
                    "cutoff": cutoff,
                    "selected_users": len(rows),
                    "users_with_structural_candidates": sum(
                        int(row["structural_candidate_count"]) > 0 for row in rows
                    ),
                    "zero_structural_candidate_users": sum(
                        int(row["structural_candidate_count"]) == 0 for row in rows
                    ),
                    "average_structural_candidate_count": _mean(
                        int(row["structural_candidate_count"]) for row in rows
                    ),
                    "average_candidate_ground_truth": _mean(
                        int(row["candidate_ground_truth"]) for row in rows
                    ),
                    "average_ground_truth_all": _mean(
                        int(row["ground_truth_all"]) for row in rows
                    ),
                    "average_recommendation_count": _mean(
                        int(row["recommendation_count"]) for row in rows
                    ),
                    "total_hits": total_hits,
                    "total_structural_hits": total_structural_hits,
                    "total_padding_hits": total_padding_hits,
                    "average_precision": _mean(float(row["precision"]) for row in rows),
                    "micro_precision": (
                        total_hits / (len(rows) * cutoff) if rows else 0.0
                    ),
                    "average_hr": _mean(float(row["hr"]) for row in rows),
                    "users_with_hit": sum(float(row["hr"]) > 0 for row in rows),
                    "average_recall": _mean(float(row["recall"]) for row in rows),
                    "micro_recall": total_hits / total_gt if total_gt else 0.0,
                    "average_candidate_recall": _mean(
                        float(row["candidate_recall"]) for row in rows
                    ),
                    "micro_candidate_recall": (
                        total_structural_hits / total_candidate_gt
                        if total_candidate_gt
                        else 0.0
                    ),
                    "average_ndcg": _mean(float(row["ndcg"]) for row in rows),
                }
            )

    aggregate_path = output_dir / "aggregate.tsv"
    write_tsv(
        aggregate_path,
        [
            "method",
            "cutoff",
            "selected_users",
            "users_with_structural_candidates",
            "zero_structural_candidate_users",
            "average_structural_candidate_count",
            "average_candidate_ground_truth",
            "average_ground_truth_all",
            "average_recommendation_count",
            "total_hits",
            "total_structural_hits",
            "total_padding_hits",
            "average_precision",
            "micro_precision",
            "average_hr",
            "users_with_hit",
            "average_recall",
            "micro_recall",
            "average_candidate_recall",
            "micro_candidate_recall",
            "average_ndcg",
        ],
        aggregate_rows,
    )

    metadata = {
        "format_version": 1,
        "graph_sha256": case.graph_sha256,
        "selected_users": len(selected_users),
        "requested_top_users": top_users,
        "selection_policy": (
            "highest distinct training-item degree among users with at least one "
            "warm-unseen test edge"
        ),
        "c3_definition": (
            "candidate c exists when user observed b and II edge (b,c) exists; "
            "score is max tau3(b,c) across observed anchors"
        ),
        "c4_definition": (
            "maximum k such that user and unseen item are connected in the "
            "subgraph of edges with tau4 >= k (maximum-bottleneck connectivity)"
        ),
        "c5_definition": "typed C5 u-b-v-c-a-u; MAX and per-typed-C5 AVG",
        "c6_included": not skip_c6,
        "c6_definition": (
            None
            if skip_c6
            else (
                "typed C6 u-a-b-v-c-d-u; b uses tau6(a,b), "
                "c uses tau6(c,d); score is maximum over all "
                "recommending typed C6s"
            )
        ),
        "padding_policy": (
            "C3 and both C5 methods append highest full-graph-degree unseen items "
            "when structural candidates are fewer than the requested ranking length; "
            "padded items have score zero. C4 and C6 are not padded."
        ),
        "tie_break": "score descending, full mixed-graph item degree descending, node ID ascending",
        "precision": "hits / requested K, always with fixed denominator K",
        "hr": "1 if Top-K contains at least one ground-truth item, otherwise 0",
        "cutoffs": list(cutoff_values),
        "ranking_limit": ranking_limit,
        "elapsed_seconds": time.monotonic() - started,
    }
    atomic_write_text(
        output_dir / "evaluation.json",
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
    )

    print(
        "===== FULL-GRAPH C3/C4/C5"
        + (
            " RECOMMENDATION ====="
            if skip_c6
            else "/C6 RECOMMENDATION ====="
        )
    )
    print(f"selected_users_with_test_gt: {len(selected_users)}")
    print("Precision@K uses the fixed denominator K; HR@K is binary hit-any.")
    print()
    for row in aggregate_rows:
        print(
            f"{row['method']:>24s}@{row['cutoff']:<2d} "
            f"avg_candidates={row['average_structural_candidate_count']:.4f} "
            f"avg_candidate_gt={row['average_candidate_ground_truth']:.4f} "
            f"avg_precision={row['average_precision']:.8f} "
            f"avg_hr={row['average_hr']:.8f} "
            f"avg_ndcg={row['average_ndcg']:.8f} "
            f"total_hits={row['total_hits']}"
        )
    print(f"aggregate: {aggregate_path}")
    return aggregate_path
