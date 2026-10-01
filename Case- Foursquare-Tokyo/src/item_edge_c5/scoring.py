"""Strict typed-C5 recommendation scored only by the item--item edge.

For a witness ``a-b-c-d-e-a`` supporting the missing recommendation
``a -> d``, this module reads only the existing item--item edge ``d--e``.
It never takes the minimum over all five witness edges and never inserts the
candidate edge before decomposition.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from crossrec_final.hybrid import (
    Edge,
    ScoreTable,
    TrainingGraph,
    enumerate_cross_recommendation_witnesses,
    item_node,
    normalize_edge,
    positive_edge_percentiles,
)


C5_TRUSS_METHOD = "c5_truss_item_edge"
# Kept as a one-element tuple for the internal legacy evaluator.  C3/C4 and
# native C6 are deliberately implemented only in comparison_scoring.py.
TRUSS_METHODS = (C5_TRUSS_METHOD,)


@dataclass(frozen=True)
class ItemEdgeFeatureResult:
    """Strict typed-C5 truss feature plus witness provenance."""

    features: dict[str, ScoreTable]
    raw_truss_features: dict[str, ScoreTable]
    witness_supported_candidates: dict[str, frozenset[str]]
    audit: dict[str, object]


def _validate_edge_map(
    graph: TrainingGraph,
    values: Mapping[Edge, int],
    *,
    label: str,
) -> None:
    expected = set(graph.edges)
    observed = set(values)
    if observed != expected:
        missing = expected.difference(observed)
        extra = observed.difference(expected)
        raise ValueError(
            f"{label} must describe exactly the original training edges "
            f"(missing={len(missing)}, extra={len(extra)})"
        )
    if any(value < 0 for value in values.values()):
        raise ValueError(f"{label} contains a negative edge value")


def _empty_feature_table(
    candidate_universe: Mapping[str, Iterable[str]],
) -> ScoreTable:
    return {
        str(user): {
            str(item): 0.0
            for item in sorted({str(value) for value in items})
        }
        for user, items in sorted(candidate_universe.items())
    }


def _item_edge_percentiles(
    graph: TrainingGraph,
    values: Mapping[Edge, int],
) -> dict[Edge, float]:
    """Normalize only against Item--Item relationships.

    Every application feature below reads an Item--Item role edge ``d--e``.
    User--Item edges therefore must not change the empirical reference
    distribution merely because a dataset has more users or interactions.
    """

    item_edges = {
        normalize_edge(item_node(left), item_node(right))
        for left, neighbors in graph.item_neighbors.items()
        for right in neighbors
    }
    return positive_edge_percentiles(
        {edge: values[edge] for edge in item_edges}
    )


def build_item_edge_feature_tables(
    graph: TrainingGraph,
    candidate_universe: Mapping[str, Iterable[str]],
    trussness: Mapping[int, Mapping[Edge, int]],
    *,
    include_raw: bool = False,
    method_name: str = C5_TRUSS_METHOD,
    measure_label: str = "C5 trussness",
) -> ItemEdgeFeatureResult:
    """Score one common candidate universe with item-edge-only evidence.

    The strict witness family is fixed to ``a-b-c-d-e-a`` with five distinct
    nodes.  For a candidate ``(a,d)``, each witness contributes the percentile
    of the *same role edge* ``(d,e)``.  Candidate-level aggregation is max:

    ``q_5(a,d) = max_C percentile(tau_5(d,e_C))``.

    Repeating the same ``d--e`` edge through several users or shared items
    cannot increase this score.  Different supporting anchor items ``e`` are
    allowed, and the strongest corresponding item--item edge wins.
    """

    if 5 not in trussness:
        raise ValueError("missing C5 trussness map")
    _validate_edge_map(
        graph,
        trussness[5],
        label=measure_label,
    )
    normalized_universe = {
        str(user): tuple(sorted({str(item) for item in items}))
        for user, items in sorted(candidate_universe.items())
    }
    for user, items in normalized_universe.items():
        observed = graph.user_items.get(user)
        if observed is None:
            raise ValueError(f"unknown evaluation user: {user}")
        overlap = set(items).intersection(observed)
        if overlap:
            raise ValueError(
                f"candidate universe for {user!r} contains "
                f"{len(overlap)} observed item(s)"
            )
        outside = set(items).difference(graph.catalog)
        if outside:
            raise ValueError(
                f"candidate universe for {user!r} contains "
                f"{len(outside)} item(s) outside the training catalog"
            )

    witnesses = enumerate_cross_recommendation_witnesses(
        graph,
        normalized_universe,
        policy="simple",
    )
    truss_percentiles = _item_edge_percentiles(graph, trussness[5])

    features = {
        method_name: _empty_feature_table(normalized_universe)
    }
    raw_truss_features = (
        {
            method_name: _empty_feature_table(normalized_universe)
        }
        if include_raw
        else {}
    )
    selected_item_edges: dict[tuple[str, str], set[Edge]] = {}
    witness_count = 0
    for (user, candidate), candidate_witnesses in witnesses.items():
        item_edges: set[Edge] = set()
        for witness in candidate_witnesses:
            if not witness.is_simple:
                raise AssertionError("non-simple witness entered strict model")
            if witness.user != user or witness.candidate_item != candidate:
                raise AssertionError("witness/candidate role mismatch")
            item_edge = normalize_edge(
                item_node(witness.candidate_item),
                item_node(witness.anchor_item),
            )
            if item_edge not in graph.edges:
                raise AssertionError(
                    "witness item--item role edge is not a training edge"
                )
            item_edges.add(item_edge)
            witness_count += 1
        if not item_edges:
            continue
        selected_item_edges[(user, candidate)] = item_edges
        if include_raw:
            raw_truss_features[method_name][user][candidate] = float(
                max(
                    trussness[5].get(edge, 0)
                    for edge in item_edges
                )
            )
        features[method_name][user][candidate] = max(
            truss_percentiles.get(edge, 0.0)
            for edge in item_edges
        )

    reference = {
        user: tuple(items)
        for user, items in normalized_universe.items()
    }
    for method, table in features.items():
        observed = {
            user: tuple(sorted(items))
            for user, items in table.items()
        }
        if observed != reference:
            raise AssertionError(
                f"{method} does not use the common candidate universe"
            )
    for method, table in raw_truss_features.items():
        observed = {
            user: tuple(sorted(items))
            for user, items in table.items()
        }
        if observed != reference:
            raise AssertionError(
                f"raw {method} does not use the common candidate universe"
            )

    unique_candidate_item_edges = sum(
        len(edges) for edges in selected_item_edges.values()
    )
    audit = {
        "candidate_pairs": sum(
            len(items) for items in normalized_universe.values()
        ),
        "strict_supported_pairs": len(selected_item_edges),
        "strict_witnesses": witness_count,
        "candidate_item_edge_occurrences_after_dedup": (
            unique_candidate_item_edges
        ),
        "candidate_edges_inserted": 0,
        "trussness_assignment": "edge",
        "witness_policy": (
            "strict typed simple C5 a-b-c-d-e-a; "
            "a,c users and b,d,e items"
        ),
        "scored_role_edge": "item--item d--e only",
        "candidate_aggregation": "max over unique supporting d--e edges",
        "edge_measure": measure_label,
        "method_name": method_name,
        "feature_scope": "C5 only",
        "percentile_reference": "positive Item--Item edges only",
    }
    witness_supported_candidates = {
        user: frozenset(
            candidate
            for candidate in normalized_universe[user]
            if (user, candidate) in selected_item_edges
        )
        for user in normalized_universe
    }
    return ItemEdgeFeatureResult(
        features=features,
        raw_truss_features=raw_truss_features,
        witness_supported_candidates=witness_supported_candidates,
        audit=audit,
    )
