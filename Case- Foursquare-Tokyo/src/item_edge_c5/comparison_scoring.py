"""Cycle-length comparison features for the Foursquare case study.

C3 uses the application-specific open-triangle rule requested by the case
study.  For every unseen pair ``(u,c)``, inspect the training patterns
``u--b--c`` where ``u--b`` is User--Item and ``b--c`` is Item--Item.  Its raw
score is the maximum ``tau_3(b,c)`` over those Item--Item role edges.  Every
warm unseen Item stays in the common score table; pairs without such a
training pattern receive zero.

C4 uses the typed simple cycle ``u-a-b-c-u``, where ``u`` is a User and
``a,b,c`` are distinct Items.  The unseen pair is ``u--b``.  One cycle is
scored by ``max(tau_4(a,b), tau_4(b,c))`` and repeated cycles are aggregated
by maximum.  No missing user--item edge is inserted.

C5 keeps the application-specific rule requested by the case study: for a
strict typed witness ``a-b-c-d-e-a`` supporting ``a -> d``, score the existing
Item--Item role edge ``d--e`` and take the maximum over supporting role edges.

C6 uses its own typed witness ``a-b-c-d-e-f-a``, where ``a,d`` are Users and
``b,c,e,f`` are Items.  It recommends ``c,e`` to ``a`` and ``b,f`` to ``d``.
Each recommendation reads the adjacent Item--Item role edge (``b--c`` or
``e--f``), and multiple witnesses are aggregated by maximum edge trussness.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from typing import Iterable, Mapping

from crossrec_final.hybrid import (
    Edge,
    ScoreTable,
    TrainingGraph,
    item_node,
    normalize_edge,
    user_node,
)
from item_edge_c5.scoring import build_item_edge_feature_tables


COMPARISON_METHODS = tuple(f"c{length}_truss" for length in (3, 4, 5, 6))


@dataclass(frozen=True)
class ComparisonFeatureResult:
    """Raw and normalized C3--C6 features on one candidate universe."""

    features: dict[str, ScoreTable]
    raw_truss_features: dict[str, ScoreTable]
    supported_candidates_by_method: dict[
        str, dict[str, frozenset[str]]
    ]
    audit: dict[str, object]


def _validate_candidate_universe(
    graph: TrainingGraph,
    candidate_universe: Mapping[str, Iterable[str]],
) -> dict[str, tuple[str, ...]]:
    normalized = {
        str(user): tuple(sorted({str(item) for item in items}))
        for user, items in sorted(candidate_universe.items())
    }
    for user, items in normalized.items():
        observed = graph.user_items.get(user)
        if observed is None:
            raise ValueError(f"unknown evaluation user: {user}")
        if set(items).intersection(observed):
            raise ValueError(
                f"candidate universe for {user!r} contains observed items"
            )
        if set(items).difference(graph.catalog):
            raise ValueError(
                f"candidate universe for {user!r} contains unknown items"
            )
    return normalized


def _validate_trussness(
    graph: TrainingGraph,
    trussness: Mapping[int, Mapping[Edge, int]],
) -> None:
    for length in (3, 4, 5, 6):
        if length not in trussness:
            raise ValueError(f"missing C{length} trussness")
        values = trussness[length]
        if set(values) != set(graph.edges):
            raise ValueError(
                f"C{length} trussness must cover every training edge"
            )
        if any(value < 0 for value in values.values()):
            raise ValueError(f"C{length} trussness contains a negative value")


def _c3_item_neighbor_score_tables(
    graph: TrainingGraph,
    candidate_universe: Mapping[str, tuple[str, ...]],
    values: Mapping[Edge, int],
) -> tuple[ScoreTable, dict[str, frozenset[str]]]:
    """Score unseen ``u--c`` by max tau3 of a training ``u--b--c``.

    Only the existing Item--Item role edge ``b--c`` is scored.  The complete
    warm-unseen catalog is retained and therefore receives an explicit zero
    when no such role edge exists.
    """

    raw = _zero_score_table(candidate_universe)
    supported: dict[str, frozenset[str]] = {}
    for user, candidates in candidate_universe.items():
        allowed = frozenset(candidates)
        role_edges_by_candidate: dict[str, set[Edge]] = {}
        for bought_item in graph.user_items.get(user, frozenset()):
            for candidate in graph.item_neighbors.get(
                bought_item, frozenset()
            ):
                if candidate not in allowed:
                    continue
                role_edges_by_candidate.setdefault(candidate, set()).add(
                    normalize_edge(
                        item_node(bought_item), item_node(candidate)
                    )
                )
        for candidate, role_edges in role_edges_by_candidate.items():
            raw[user][candidate] = float(
                max(values[edge] for edge in role_edges)
            )
        supported[user] = frozenset(role_edges_by_candidate)
    return raw, supported


def _typed_c4_item_edge_score_tables(
    graph: TrainingGraph,
    candidate_universe: Mapping[str, tuple[str, ...]],
    values: Mapping[Edge, int],
) -> tuple[ScoreTable, dict[str, frozenset[str]], dict[str, int]]:
    """Score typed simple C4 ``u-a-b-c-u`` by its two Item edges."""

    raw = _zero_score_table(candidate_universe)
    supported: dict[str, frozenset[str]] = {}
    typed_cycles = 0
    role_edge_occurrences = 0
    for user, candidates in candidate_universe.items():
        consumed = graph.user_items.get(user, frozenset())
        supported_for_user: set[str] = set()
        for candidate in candidates:
            role_edges = {
                normalize_edge(item_node(anchor), item_node(candidate))
                for anchor in consumed
                if candidate in graph.item_neighbors.get(
                    anchor, frozenset()
                )
            }
            anchor_count = len(role_edges)
            if anchor_count < 2:
                continue
            # Every pair of distinct purchased anchor Items forms exactly one
            # simple typed C4 with the unseen candidate Item in the middle.
            typed_cycles += anchor_count * (anchor_count - 1) // 2
            role_edge_occurrences += anchor_count
            supported_for_user.add(candidate)
            # max over cycles of max over their two role edges simplifies to
            # the maximum tau4 among all role edges participating in a cycle.
            raw[user][candidate] = float(
                max(values[edge] for edge in role_edges)
            )
        supported[user] = frozenset(supported_for_user)
    return raw, supported, {
        "typed_simple_c4_cycles": typed_cycles,
        "typed_c4_supported_pairs": sum(len(items) for items in supported.values()),
        "typed_c4_candidate_role_edges": role_edge_occurrences,
    }


def _item_item_edge_values(
    graph: TrainingGraph,
    values: Mapping[Edge, int],
) -> dict[Edge, int]:
    return {
        normalize_edge(item_node(left), item_node(right)): values[
            normalize_edge(item_node(left), item_node(right))
        ]
        for left, neighbors in graph.item_neighbors.items()
        for right in neighbors
        if left < right
    }


def _edge_cdf_score_table(
    raw_scores: Mapping[str, Mapping[str, float]],
    edge_values: Mapping[Edge, int],
) -> ScoreTable:
    """Map truss levels through the positive training-edge empirical CDF."""

    positives = sorted(value for value in edge_values.values() if value > 0)
    if not positives:
        return {
            user: {item: 0.0 for item in scores}
            for user, scores in raw_scores.items()
        }
    return {
        user: {
            item: (
                bisect.bisect_right(positives, value) / len(positives)
                if value > 0
                else 0.0
            )
            for item, value in scores.items()
        }
        for user, scores in raw_scores.items()
    }


def _zero_score_table(
    candidate_universe: Mapping[str, tuple[str, ...]],
) -> ScoreTable:
    return {
        user: {item: 0.0 for item in items}
        for user, items in candidate_universe.items()
    }


def _native_typed_c6_role_edges(
    graph: TrainingGraph,
    candidate_universe: Mapping[str, tuple[str, ...]],
) -> tuple[dict[tuple[str, str], set[Edge]], dict[str, int]]:
    """Return role edges supporting native typed-C6 recommendations.

    For ``a-b-c-d-e-f-a``, oriented Item--Item edges are ``b->c`` and
    ``f->e``.  User ``a`` has Items ``b,f`` and User ``d`` has Items ``c,e``.
    The cycle proposes:

    * ``a -> c`` and ``d -> b``, scored by ``b--c``;
    * ``a -> e`` and ``d -> f``, scored by ``e--f``.

    Candidate membership is checked against the common full-catalog universe;
    candidates outside it (including already observed User--Item edges) never
    enter the score table.
    """

    allowed = {
        user: frozenset(items)
        for user, items in candidate_universe.items()
    }
    role_edges: dict[tuple[str, str], set[Edge]] = {}
    cycle_count = 0
    unique_cycle_keys: set[
        tuple[str, str, Edge, Edge]
    ] = set()
    candidate_occurrences = 0

    # Evaluate one requested User endpoint at a time.  For evaluation User a,
    # collect cross-Item relations (b,c) grouped by the opposite User d, where
    # a owns b and d owns c.  Any two endpoint-disjoint relations for the same
    # d form a simple typed cycle a-b-c-d-e-f-a.  This is exactly the subset
    # of native C6 witnesses that can affect a requested score, without ever
    # materializing unrelated user-pair Cartesian products.
    for user_a in sorted(allowed):
        cross_edges_by_other: dict[
            str, set[tuple[str, str, Edge]]
        ] = {}
        for own_item in sorted(graph.user_items.get(user_a, frozenset())):
            for other_item in sorted(
                graph.item_neighbors.get(own_item, frozenset())
            ):
                role_edge = normalize_edge(
                    item_node(own_item), item_node(other_item)
                )
                for user_d in graph.item_users.get(
                    other_item, frozenset()
                ):
                    if user_d == user_a:
                        continue
                    cross_edges_by_other.setdefault(user_d, set()).add(
                        (own_item, other_item, role_edge)
                    )

        for user_d, connections_set in cross_edges_by_other.items():
            connections = sorted(connections_set)
            for first_index, (b, c, bc_edge) in enumerate(connections):
                for f, e, ef_edge in connections[first_index + 1 :]:
                    if len({b, c, e, f}) != 4:
                        continue
                    cycle_count += 1
                    user_left, user_right = sorted((user_a, user_d))
                    edge_left, edge_right = sorted((bc_edge, ef_edge))
                    unique_cycle_keys.add(
                        (
                            user_left,
                            user_right,
                            edge_left,
                            edge_right,
                        )
                    )
                    for candidate, role_edge in (
                        (c, bc_edge),
                        (e, ef_edge),
                    ):
                        if candidate not in allowed[user_a]:
                            continue
                        candidate_edge = normalize_edge(
                            user_node(user_a), item_node(candidate)
                        )
                        if candidate_edge in graph.edges:
                            raise AssertionError(
                                "native C6 used an observed candidate edge"
                            )
                        role_edges.setdefault(
                            (user_a, candidate), set()
                        ).add(role_edge)
                        candidate_occurrences += 1

    return role_edges, {
        "native_typed_c6_cycles": len(unique_cycle_keys),
        "native_typed_c6_evaluation_side_cycles": cycle_count,
        "native_c6_supported_pairs": len(role_edges),
        "native_c6_candidate_occurrences_before_role_edge_dedup": (
            candidate_occurrences
        ),
        "native_c6_candidate_role_edges_after_dedup": sum(
            len(edges) for edges in role_edges.values()
        ),
    }


def _native_c6_score_tables(
    graph: TrainingGraph,
    candidate_universe: Mapping[str, tuple[str, ...]],
    values: Mapping[Edge, int],
) -> tuple[
    ScoreTable,
    ScoreTable,
    dict[str, frozenset[str]],
    dict[str, int],
]:
    role_edges, audit = _native_typed_c6_role_edges(
        graph,
        candidate_universe,
    )
    raw = _zero_score_table(candidate_universe)
    for (user, candidate), edges in role_edges.items():
        raw[user][candidate] = float(max(values[edge] for edge in edges))
    item_edge_values = _item_item_edge_values(graph, values)
    normalized = _edge_cdf_score_table(raw, item_edge_values)
    supported = {
        user: frozenset(
            candidate
            for candidate in candidate_universe[user]
            if (user, candidate) in role_edges
        )
        for user in candidate_universe
    }
    return raw, normalized, supported, audit


def build_comparison_feature_tables(
    graph: TrainingGraph,
    candidate_universe: Mapping[str, Iterable[str]],
    trussness: Mapping[int, Mapping[Edge, int]],
) -> ComparisonFeatureResult:
    """Build typed C3/C4/C5/C6 comparison features.

    All four methods receive the complete unseen-item universe.  Structural
    C3--C6 role-edge scores are normalized against positive training
    Item--Item edges of their corresponding decomposition.
    """

    normalized_universe = _validate_candidate_universe(
        graph,
        candidate_universe,
    )
    _validate_trussness(graph, trussness)

    item_edge = build_item_edge_feature_tables(
        graph,
        normalized_universe,
        trussness,
        include_raw=True,
    )
    raw: dict[str, ScoreTable] = {}
    raw["c3_truss"], c3_supported = _c3_item_neighbor_score_tables(
        graph,
        normalized_universe,
        trussness[3],
    )
    (
        raw["c4_truss"],
        c4_supported,
        typed_c4_audit,
    ) = _typed_c4_item_edge_score_tables(
        graph,
        normalized_universe,
        trussness[4],
    )
    raw["c5_truss"] = item_edge.raw_truss_features[
        "c5_truss_item_edge"
    ]
    (
        raw_c6,
        normalized_c6,
        native_c6_supported,
        native_c6_audit,
    ) = _native_c6_score_tables(
        graph,
        normalized_universe,
        trussness[6],
    )
    raw["c6_truss"] = raw_c6

    features = {
        "c3_truss": _edge_cdf_score_table(
            raw["c3_truss"],
            _item_item_edge_values(graph, trussness[3]),
        ),
        "c4_truss": _edge_cdf_score_table(
            raw["c4_truss"],
            _item_item_edge_values(graph, trussness[4]),
        ),
    }
    features["c5_truss"] = item_edge.features["c5_truss_item_edge"]
    features["c6_truss"] = normalized_c6
    expected = {
        user: tuple(items)
        for user, items in normalized_universe.items()
    }
    for label, tables in (
        ("raw", raw),
        ("normalized", features),
    ):
        for method, table in tables.items():
            observed = {
                user: tuple(sorted(scores))
                for user, scores in table.items()
            }
            if observed != expected:
                raise AssertionError(
                    f"{label} {method} changed the candidate universe"
                )

    positive_pairs = {
        method: sum(
            value > 0
            for scores in table.values()
            for value in scores.values()
        )
        for method, table in raw.items()
    }
    audit = {
        **item_edge.audit,
        **typed_c4_audit,
        **native_c6_audit,
        "method_semantics": {
            "c3_truss": (
                "maximum tau3(b,c) over training u--b--c patterns"
            ),
            "c4_truss": (
                "max over typed simple C4 cycles of max(tau4(a,b), "
                "tau4(b,c)) for u-a-b-c-u"
            ),
            "c5_truss": (
                "maximum tau5(d,e) over strict typed C5 role edges"
            ),
            "c6_truss": (
                "maximum tau6(b,c) or tau6(e,f) over native typed "
                "C6 a-b-c-d-e-f-a role edges"
            ),
        },
        "candidate_edge_inserted": 0,
        "normalization": (
            "C3/C4/C5/C6: positive Item--Item-edge tau CDF"
        ),
        "positive_candidate_pairs_by_method": positive_pairs,
        "all_lengths_share_witnesses": False,
        "all_lengths_share_role_edge": False,
        "candidate_aggregation": {
            "c3_truss": (
                "maximum over Item--Item b--c edges in training u--b--c"
            ),
            "c4_truss": (
                "maximum over the two Item--Item edges in every typed C4, "
                "then maximum across typed C4 cycles"
            ),
            "c5_truss": "maximum over unique supporting d--e role edges",
            "c6_truss": (
                "maximum over unique native-C6 b--c/e--f role edges"
            ),
        },
        "percentile_reference": (
            "positive training Item--Item edges for every cycle length"
        ),
        "scored_role_edge": (
            "C3: Item--Item b--c; C4: Item--Item a--b and b--c; "
            "C5: Item--Item d--e; C6: Item--Item b--c or e--f"
        ),
    }
    return ComparisonFeatureResult(
        features=features,
        raw_truss_features=raw,
        supported_candidates_by_method={
            "c3_truss": c3_supported,
            "c4_truss": c4_supported,
            "c5_truss": item_edge.witness_supported_candidates,
            "c6_truss": native_c6_supported,
        },
        audit=audit,
    )
