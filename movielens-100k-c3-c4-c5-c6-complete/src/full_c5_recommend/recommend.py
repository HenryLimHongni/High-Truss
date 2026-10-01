from __future__ import annotations

import heapq
import json
import math
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from statistics import fmean
from typing import Iterable, Mapping

from .artifacts import CaseArtifact, load_case, load_value_file
from .utils import atomic_write_text, write_tsv


@dataclass
class CandidateEvidence:
    """Typed-C5 evidence for one evaluated user and one candidate item."""

    max_tau: int = 0
    cycle_tau_sum: int = 0
    typed_c5_count: int = 0
    qualifying_ii_edges: int = 0


@dataclass
class UserMethodMetrics:
    user_node: int
    user_id: str
    selected_rank: int
    global_training_rank: int
    training_degree: int
    method: str
    cutoff: int
    effective_k: int
    candidate_count: int
    positive_score_candidates: int
    ground_truth_all: int
    candidate_ground_truth: int
    hits: int
    positive_score_hits: int
    hit_ratio: float
    candidate_recall: float
    all_gt_recall: float
    hit_any: float
    ndcg: float


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


def _mean(values: Iterable[float]) -> float:
    parsed = list(values)
    return fmean(parsed) if parsed else 0.0


def _top_items(
    items: Iterable[int],
    scores: Mapping[int, float],
    full_degree: list[int],
    limit: int,
) -> list[int]:
    """Return a deterministic Top-L without sorting a large full catalog."""

    if limit <= 0:
        return []
    return heapq.nsmallest(
        limit,
        items,
        key=lambda item: (-scores[item], -full_degree[item], item),
    )


def _typed_c5_evidence(
    *,
    user: int,
    observed: set[int],
    user_items: Mapping[int, set[int]],
    item_users: Mapping[int, set[int]],
    item_neighbors: Mapping[int, list[tuple[int, int]]],
    tau5: tuple[int, ...],
) -> tuple[set[int], dict[int, CandidateEvidence]]:
    """Compute two-hop candidates and exact typed-C5 evidence.

    A typed simple C5 has the form ``u-b-v-c-a-u`` where ``u,v`` are
    distinct users, ``a,b,c`` are distinct items, and ``(a,c)`` is the unique
    Item--Item edge. For a fixed II edge ``(a,c)`` and other user ``v``, every
    shared observed item ``b != a`` gives exactly one distinct typed C5.
    """

    # Number of items shared by the evaluated user and every other user.
    shared_count: dict[int, int] = defaultdict(int)
    for shared_item in observed:
        for other_user in item_users.get(shared_item, ()):
            if other_user != user:
                shared_count[other_user] += 1

    two_hop_candidates: set[int] = set()
    evidence: dict[int, CandidateEvidence] = defaultdict(CandidateEvidence)

    for anchor in observed:
        for candidate, ii_edge_id in item_neighbors.get(anchor, ()):
            if candidate in observed:
                continue
            two_hop_candidates.add(candidate)

            typed_cycle_count = 0
            for other_user in item_users.get(candidate, ()):
                if other_user == user:
                    continue
                count = shared_count.get(other_user, 0)
                # The shared item b must be distinct from II-side anchor a.
                if anchor in user_items[other_user]:
                    count -= 1
                if count > 0:
                    typed_cycle_count += count

            if typed_cycle_count <= 0:
                continue

            value = tau5[ii_edge_id]
            if value <= 0:
                raise RuntimeError(
                    "typed C5 found on an II edge with non-positive C5-trussness"
                )

            entry = evidence[candidate]
            entry.max_tau = max(entry.max_tau, value)
            # AVG is weighted by every distinct typed C5, not distinct II edges.
            entry.cycle_tau_sum += value * typed_cycle_count
            entry.typed_c5_count += typed_cycle_count
            entry.qualifying_ii_edges += 1

    return two_hop_candidates, dict(evidence)


def _itemknn_positive_scores(
    *,
    observed: set[int],
    user_items: Mapping[int, set[int]],
    item_users: Mapping[int, set[int]],
    inverse_sqrt_item_users: Mapping[int, float],
) -> dict[int, float]:
    """Cosine item-cooccurrence scores on the complete unseen catalog.

    Only positive-score items are materialized. Zero-score unseen items are
    appended later using the common degree tie-break, which is sufficient for
    an exact Top-K ranking.
    """

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


def _full_catalog_top_items(
    *,
    observed: set[int],
    positive_scores: Mapping[int, float],
    catalog_by_degree: tuple[int, ...],
    full_degree: list[int],
    limit: int,
) -> list[int]:
    """Exact ItemKNN Top-L over every unseen item in the warm catalog."""

    positive_ranking = _top_items(
        positive_scores,
        positive_scores,
        full_degree,
        min(limit, len(positive_scores)),
    )
    if len(positive_ranking) >= limit:
        return positive_ranking[:limit]

    ranking = list(positive_ranking)
    positive_items = set(positive_scores)
    for item in catalog_by_degree:
        if item in observed or item in positive_items:
            continue
        ranking.append(item)
        if len(ranking) >= limit:
            break
    return ranking


def evaluate_top_users(
    *,
    case_dir: Path,
    output_dir: Path,
    values_path: Path | None = None,
    top_users: int = 500,
    cutoffs: Iterable[int] = (5, 10, 20),
    include_itemknn: bool = True,
    ranking_limit: int | None = None,
) -> Path:
    """Evaluate the requested full-graph Top-N recommendation experiment.

    User selection
    --------------
    Users must have at least one warm-unseen test edge. Among those users, the
    globally highest distinct training-item degrees are selected.

    C5 candidate universe
    ---------------------
    A C5 candidate is retained *only* if at least one typed simple C5
    ``u-b-v-c-a-u`` exists. Thus ``candidate_count`` for C5-MAX/C5-AVG is
    exactly ``|C_5(u)|`` rather than the broader distance-two set.

    C5 scores
    ---------
    MAX is the maximum trussness of the unique II edge over supporting typed
    C5s. AVG is ``sum_C tau5(e_II(C)) / number_of_typed_C5s``; repeated cycles
    through the same II edge contribute repeatedly.

    ItemKNN universe
    ----------------
    ItemKNN ranks the complete warm item catalog excluding the user's training
    items. It does not reuse the C5 candidate set.

    Hit ratio
    ---------
    Per-user HitRatio@K is ``hits / min(K, candidate_count_for_method)``. Empty
    candidate universes receive zero.
    """

    case = load_case(case_dir)
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    if top_users <= 0:
        raise ValueError("top_users must be positive")
    cutoff_values = tuple(sorted(set(int(value) for value in cutoffs)))
    if not cutoff_values or any(value <= 0 for value in cutoff_values):
        raise ValueError("cutoffs must contain positive integers")
    max_cutoff = max(cutoff_values)
    ranking_limit = max_cutoff if ranking_limit is None else int(ranking_limit)
    if ranking_limit <= 0:
        raise ValueError("ranking_limit must be positive")
    ranking_limit = max(ranking_limit, max_cutoff)

    values_path = Path(values_path or case.root / "values" / "c5_truss.txt")
    tau5 = load_value_file(case, values_path, expected_algorithm="c5_truss")

    graph_rows = _read_graph_rows(case)
    node_count = len(case.nodes)
    full_degree = [0] * node_count
    user_items: dict[int, set[int]] = defaultdict(set)
    item_users: dict[int, set[int]] = defaultdict(set)
    item_neighbors: dict[int, list[tuple[int, int]]] = defaultdict(list)

    for edge_id, (((left_key, right_key), record), (graph_left, graph_right)) in enumerate(
        zip(zip(case.edges, case.edge_records), graph_rows)
    ):
        left = case.node_to_id[left_key]
        right = case.node_to_id[right_key]
        if (left, right) != (graph_left, graph_right):
            raise ValueError(f"edge alignment differs at edge {edge_id}")
        full_degree[left] += 1
        full_degree[right] += 1
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
        node_id for node_id, record in enumerate(case.nodes) if record.node_type == "item"
    )
    catalog_set = set(catalog_items)
    catalog_by_degree = tuple(
        sorted(catalog_items, key=lambda item: (-full_degree[item], item))
    )

    truth_by_user: dict[int, set[int]] = defaultdict(set)
    for user_key, item_key in case.truth:
        user = case.node_to_id[user_key]
        item = case.node_to_id[item_key]
        if item not in catalog_set:
            raise ValueError("test truth contains an item outside the warm catalog")
        truth_by_user[user].add(item)

    globally_ranked = sorted(
        user_items,
        key=lambda user: (-len(user_items[user]), case.nodes[user].entity_id, user),
    )
    global_rank = {user: rank for rank, user in enumerate(globally_ranked, 1)}
    eligible = [user for user in globally_ranked if truth_by_user.get(user)]
    selected_users = eligible[:top_users]
    if not selected_users:
        raise ValueError("no training user has a warm-unseen test edge")

    inverse_sqrt_item_users = {
        item: 1.0 / math.sqrt(len(users))
        for item, users in item_users.items()
        if users
    }

    methods = ["c5-max", "c5-avg"]
    if include_itemknn:
        methods.append("itemknn-full-catalog")

    user_summary_rows: list[dict[str, object]] = []
    per_user_rows: list[dict[str, object]] = []
    ranking_rows: list[dict[str, object]] = []
    metrics_objects: list[UserMethodMetrics] = []

    started = time.monotonic()
    for selected_index, user in enumerate(selected_users, 1):
        observed = user_items[user]
        truth = truth_by_user[user]

        two_hop_candidates, evidence = _typed_c5_evidence(
            user=user,
            observed=observed,
            user_items=user_items,
            item_users=item_users,
            item_neighbors=item_neighbors,
            tau5=tau5,
        )
        c5_candidates = set(evidence)
        c5_candidate_truth = c5_candidates.intersection(truth)

        c5_max_scores = {
            candidate: float(entry.max_tau) for candidate, entry in evidence.items()
        }
        c5_avg_scores = {
            candidate: entry.cycle_tau_sum / entry.typed_c5_count
            for candidate, entry in evidence.items()
        }

        itemknn_catalog_candidate_count = len(catalog_items) - len(observed)
        itemknn_candidate_truth = set(truth)  # all truth is warm and unseen by construction
        itemknn_positive_scores: dict[int, float] = {}
        if include_itemknn:
            itemknn_positive_scores = _itemknn_positive_scores(
                observed=observed,
                user_items=user_items,
                item_users=item_users,
                inverse_sqrt_item_users=inverse_sqrt_item_users,
            )

        user_summary_rows.append(
            {
                "selected_rank": selected_index,
                "global_training_degree_rank": global_rank[user],
                "user_node": user,
                "user_id": case.nodes[user].entity_id,
                "training_item_degree": len(observed),
                "ground_truth_all": len(truth),
                "two_hop_candidate_count": len(two_hop_candidates),
                "c5_candidate_count": len(c5_candidates),
                "c5_candidate_ground_truth": len(c5_candidate_truth),
                "c5_candidate_gt_coverage": len(c5_candidate_truth) / len(truth),
                "itemknn_catalog_candidate_count": itemknn_catalog_candidate_count,
                "itemknn_candidate_ground_truth": len(itemknn_candidate_truth),
            }
        )

        method_specs: list[
            tuple[
                str,
                set[int] | None,
                Mapping[int, float],
                set[int],
                int,
                list[int],
            ]
        ] = []

        c5_max_ranking = _top_items(
            c5_candidates, c5_max_scores, full_degree, min(ranking_limit, len(c5_candidates))
        )
        c5_avg_ranking = _top_items(
            c5_candidates, c5_avg_scores, full_degree, min(ranking_limit, len(c5_candidates))
        )
        method_specs.extend(
            [
                (
                    "c5-max",
                    c5_candidates,
                    c5_max_scores,
                    c5_candidate_truth,
                    len(c5_candidates),
                    c5_max_ranking,
                ),
                (
                    "c5-avg",
                    c5_candidates,
                    c5_avg_scores,
                    c5_candidate_truth,
                    len(c5_candidates),
                    c5_avg_ranking,
                ),
            ]
        )

        if include_itemknn:
            itemknn_ranking = _full_catalog_top_items(
                observed=observed,
                positive_scores=itemknn_positive_scores,
                catalog_by_degree=catalog_by_degree,
                full_degree=full_degree,
                limit=min(ranking_limit, itemknn_catalog_candidate_count),
            )
            method_specs.append(
                (
                    "itemknn-full-catalog",
                    None,
                    itemknn_positive_scores,
                    itemknn_candidate_truth,
                    itemknn_catalog_candidate_count,
                    itemknn_ranking,
                )
            )

        for method, method_candidates, scores, method_truth, candidate_count, ranking in method_specs:
            positive_score_candidates = len(scores)

            for rank, item in enumerate(ranking, 1):
                entry = evidence.get(item, CandidateEvidence())
                ranking_rows.append(
                    {
                        "selected_rank": selected_index,
                        "global_training_degree_rank": global_rank[user],
                        "user_node": user,
                        "user_id": case.nodes[user].entity_id,
                        "method": method,
                        "rank": rank,
                        "item_node": item,
                        "item_id": case.nodes[item].entity_id,
                        "title": case.nodes[item].title,
                        "score": f"{scores.get(item, 0.0):.12g}",
                        "item_full_graph_degree": full_degree[item],
                        "qualifying_ii_edges": (
                            entry.qualifying_ii_edges if method.startswith("c5-") else ""
                        ),
                        "typed_c5_count": (
                            entry.typed_c5_count if method.startswith("c5-") else ""
                        ),
                        "positive_score": int(scores.get(item, 0.0) > 0.0),
                        "ground_truth": int(item in method_truth),
                    }
                )

            for requested_k in cutoff_values:
                effective_k = min(requested_k, candidate_count)
                # ranking_limit is always at least max cutoff, so this is complete.
                top = ranking[:effective_k]
                relevance = [int(item in method_truth) for item in top]
                hits = sum(relevance)
                positive_score_hits = sum(
                    item in method_truth and scores.get(item, 0.0) > 0.0 for item in top
                )
                ideal_hits = min(len(method_truth), effective_k)
                metric = UserMethodMetrics(
                    user_node=user,
                    user_id=case.nodes[user].entity_id,
                    selected_rank=selected_index,
                    global_training_rank=global_rank[user],
                    training_degree=len(observed),
                    method=method,
                    cutoff=requested_k,
                    effective_k=effective_k,
                    candidate_count=candidate_count,
                    positive_score_candidates=positive_score_candidates,
                    ground_truth_all=len(truth),
                    candidate_ground_truth=len(method_truth),
                    hits=hits,
                    positive_score_hits=positive_score_hits,
                    hit_ratio=(hits / effective_k if effective_k else 0.0),
                    candidate_recall=(hits / len(method_truth) if method_truth else 0.0),
                    all_gt_recall=(hits / len(truth) if truth else 0.0),
                    hit_any=float(hits > 0),
                    ndcg=(
                        _dcg(relevance) / _dcg([1] * ideal_hits)
                        if ideal_hits
                        else 0.0
                    ),
                )
                metrics_objects.append(metric)
                per_user_rows.append(
                    {
                        "selected_rank": selected_index,
                        "global_training_degree_rank": global_rank[user],
                        "user_node": user,
                        "user_id": case.nodes[user].entity_id,
                        "training_item_degree": len(observed),
                        "method": method,
                        "cutoff": requested_k,
                        "effective_k": effective_k,
                        "candidate_count": candidate_count,
                        "positive_score_candidates": positive_score_candidates,
                        "ground_truth_all": len(truth),
                        "candidate_ground_truth": len(method_truth),
                        "hits": hits,
                        "positive_score_hits": positive_score_hits,
                        "hit_ratio": metric.hit_ratio,
                        "candidate_recall": metric.candidate_recall,
                        "all_gt_recall": metric.all_gt_recall,
                        "hit_any": metric.hit_any,
                        "ndcg": metric.ndcg,
                    }
                )

        if selected_index % 10 == 0 or selected_index == len(selected_users):
            seconds = time.monotonic() - started
            print(
                f"[evaluate] users={selected_index}/{len(selected_users)} "
                f"elapsed_seconds={seconds:.1f}",
                file=sys.stderr,
            )

    write_tsv(
        output_dir / "selected_users.tsv",
        [
            "selected_rank",
            "global_training_degree_rank",
            "user_node",
            "user_id",
            "training_item_degree",
            "ground_truth_all",
            "two_hop_candidate_count",
            "c5_candidate_count",
            "c5_candidate_ground_truth",
            "c5_candidate_gt_coverage",
            "itemknn_catalog_candidate_count",
            "itemknn_candidate_ground_truth",
        ],
        user_summary_rows,
    )
    write_tsv(
        output_dir / "per_user_metrics.tsv",
        [
            "selected_rank",
            "global_training_degree_rank",
            "user_node",
            "user_id",
            "training_item_degree",
            "method",
            "cutoff",
            "effective_k",
            "candidate_count",
            "positive_score_candidates",
            "ground_truth_all",
            "candidate_ground_truth",
            "hits",
            "positive_score_hits",
            "hit_ratio",
            "candidate_recall",
            "all_gt_recall",
            "hit_any",
            "ndcg",
        ],
        per_user_rows,
    )
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
            "qualifying_ii_edges",
            "typed_c5_count",
            "positive_score",
            "ground_truth",
        ],
        ranking_rows,
    )

    aggregate_rows: list[dict[str, object]] = []
    for method in methods:
        for cutoff in cutoff_values:
            rows = [m for m in metrics_objects if m.method == method and m.cutoff == cutoff]
            nonempty = [m for m in rows if m.candidate_count > 0]
            candidate_gt_positive = [m for m in rows if m.candidate_ground_truth > 0]
            total_effective_k = sum(m.effective_k for m in rows)
            total_hits = sum(m.hits for m in rows)
            total_candidate_gt = sum(m.candidate_ground_truth for m in rows)
            total_all_gt = sum(m.ground_truth_all for m in rows)
            aggregate_rows.append(
                {
                    "method": method,
                    "cutoff": cutoff,
                    "selected_users": len(rows),
                    "users_with_candidates": len(nonempty),
                    "users_with_candidate_ground_truth": len(candidate_gt_positive),
                    "average_training_item_degree": _mean(m.training_degree for m in rows),
                    "average_candidate_count": _mean(m.candidate_count for m in rows),
                    "average_candidate_ground_truth": _mean(
                        m.candidate_ground_truth for m in rows
                    ),
                    "average_ground_truth_all": _mean(m.ground_truth_all for m in rows),
                    "micro_candidate_gt_coverage": (
                        total_candidate_gt / total_all_gt if total_all_gt else 0.0
                    ),
                    "average_effective_k": _mean(m.effective_k for m in rows),
                    "total_hits": total_hits,
                    "average_hits": _mean(m.hits for m in rows),
                    "average_hit_ratio": _mean(m.hit_ratio for m in rows),
                    "average_hit_ratio_nonempty_candidates": _mean(
                        m.hit_ratio for m in nonempty
                    ),
                    "micro_hit_ratio": (
                        total_hits / total_effective_k if total_effective_k else 0.0
                    ),
                    "micro_candidate_recall": (
                        total_hits / total_candidate_gt if total_candidate_gt else 0.0
                    ),
                    "micro_all_gt_recall": (
                        total_hits / total_all_gt if total_all_gt else 0.0
                    ),
                    "average_hit_any": _mean(m.hit_any for m in rows),
                    "average_ndcg": _mean(m.ndcg for m in rows),
                }
            )

    aggregate_path = output_dir / "aggregate.tsv"
    write_tsv(
        aggregate_path,
        [
            "method",
            "cutoff",
            "selected_users",
            "users_with_candidates",
            "users_with_candidate_ground_truth",
            "average_training_item_degree",
            "average_candidate_count",
            "average_candidate_ground_truth",
            "average_ground_truth_all",
            "micro_candidate_gt_coverage",
            "average_effective_k",
            "total_hits",
            "average_hits",
            "average_hit_ratio",
            "average_hit_ratio_nonempty_candidates",
            "micro_hit_ratio",
            "micro_candidate_recall",
            "micro_all_gt_recall",
            "average_hit_any",
            "average_ndcg",
        ],
        aggregate_rows,
    )

    metadata = {
        "format_version": 2,
        "graph_sha256": case.graph_sha256,
        "selection_policy": (
            "highest distinct training-item degree among users with at least one "
            "warm-unseen test edge"
        ),
        "requested_top_users": top_users,
        "selected_users": len(selected_users),
        "c5_candidate_definition": (
            "unseen item supported by at least one typed simple C5 u-b-v-c-a-u; "
            "zero-typed-C5 distance-two items are excluded"
        ),
        "itemknn_candidate_definition": (
            "all warm catalog items not observed by the evaluated user in training"
        ),
        "c5_pattern": "typed simple C5 u-b-v-c-a-u with unique II edge (a,c)",
        "c5_max": "maximum tau5 of the unique II edge over every supporting typed C5",
        "c5_avg": (
            "sum over every typed C5 of tau5 of its unique II edge divided by "
            "the number of typed C5s"
        ),
        "itemknn": "cosine item co-occurrence over the complete unseen warm catalog",
        "tie_break": "score descending, full-graph item degree descending, node ID ascending",
        "hit_ratio": (
            "hits / min(requested cutoff, method-specific candidate set size); "
            "zero for an empty candidate set"
        ),
        "cutoffs": list(cutoff_values),
        "ranking_limit": ranking_limit,
        "elapsed_seconds": time.monotonic() - started,
    }
    atomic_write_text(
        output_dir / "evaluation.json",
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
    )

    print("===== FULL-GRAPH TOP-USERS EVALUATION =====")
    print(f"selected_users_with_test_gt:       {len(selected_users)}")
    print(f"selection_policy:                  {metadata['selection_policy']}")
    print()
    for row in aggregate_rows:
        print(
            f"{row['method']:>20s}@{row['cutoff']:<2d} "
            f"avg_candidates={row['average_candidate_count']:.4f} "
            f"avg_candidate_gt={row['average_candidate_ground_truth']:.4f} "
            f"avg_hit_ratio={row['average_hit_ratio']:.8f} "
            f"total_hits={row['total_hits']}"
        )
    print(f"aggregate: {aggregate_path}")
    return aggregate_path
