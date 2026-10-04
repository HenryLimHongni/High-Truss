"""Training-only evidence and ranking. No test truth is read by this module.

Extracted without changing numerical algorithms from the final supplied code.
All score ties use FULL mixed-graph degree, then numeric local node ID.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Mapping
import math
from .artifacts import CaseArtifact

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

@dataclass
class C4TypedEvidence:
    max_value: int = 0
    typed_cycle_count: int = 0
    qualifying_ii_edge_ids: set[int] = field(default_factory=set)

def c4_typed_evidence(
    *,
    observed: set[int],
    item_neighbors: Mapping[int, list[tuple[int, int]]],
    c4_truss: tuple[int, ...],
) -> dict[int, C4TypedEvidence]:
    """Return exact evidence for typed C4 cycles u-a-b-c-u.

    The pivot user u has observed both a and c. Candidate b is unseen and has
    Item--Item edges (a,b) and (b,c). One unordered pair of distinct observed
    anchors {a,c} defines one simple typed C4. Its score is
    max(tau4(a,b), tau4(b,c)); the pair score is the maximum over all cycles.

    Since every pair of distinct incident observed anchors forms a cycle, the
    final maximum equals the maximum incident tau4 value, provided that at
    least two distinct observed anchors exist. We still record the exact
    cycle count n choose 2.
    """

    incident: dict[int, dict[int, tuple[int, int]]] = defaultdict(dict)
    for anchor in observed:
        for candidate, edge_id in item_neighbors.get(anchor, ()):
            if candidate in observed:
                continue
            incident[candidate][anchor] = (edge_id, int(c4_truss[edge_id]))

    output: dict[int, C4TypedEvidence] = {}
    for candidate, by_anchor in incident.items():
        if len(by_anchor) < 2:
            continue
        values = list(by_anchor.values())
        cycle_count = len(values) * (len(values) - 1) // 2
        maximum = max(value for _, value in values)
        if maximum <= 0:
            # A genuine u-a-b-c-u cycle gives positive C4 support/trussness
            # to both II edges. Treat a non-positive value as an invariant
            # failure instead of silently creating a zero-score candidate.
            raise RuntimeError(
                "typed C4 found but all participating II edges have "
                "non-positive C4-trussness"
            )
        output[candidate] = C4TypedEvidence(
            max_value=maximum,
            typed_cycle_count=cycle_count,
            qualifying_ii_edge_ids={edge_id for edge_id, _ in values},
        )
    return output

def _graph_relations(case: CaseArtifact):
    graph_rows = _read_graph_rows(case)
    node_count = len(case.nodes)
    degree = [0] * node_count
    user_items: dict[int, set[int]] = defaultdict(set)
    item_users: dict[int, set[int]] = defaultdict(set)
    item_neighbors: dict[int, list[tuple[int, int]]] = defaultdict(list)

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

    return graph_rows, degree, user_items, item_users, item_neighbors

