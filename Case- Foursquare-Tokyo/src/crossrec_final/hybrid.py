"""Vendored training-graph and typed-witness primitives.

These definitions are copied from the audited experiment runtime so the
archive no longer imports a sibling project.  No candidate edge is inserted
before decomposition or witness enumeration.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from typing import Iterable, Literal, Mapping


Edge = tuple[str, str]
ScoreTable = dict[str, dict[str, float]]


def user_node(user: str) -> str:
    return f"U:{user}"


def item_node(item: str) -> str:
    return f"I:{item}"


def normalize_edge(left: str, right: str) -> Edge:
    if left == right:
        raise ValueError("self-loops are not supported")
    return (left, right) if left < right else (right, left)


@dataclass(frozen=True)
class TrainingGraph:
    user_items: Mapping[str, frozenset[str]]
    item_users: Mapping[str, frozenset[str]]
    item_neighbors: Mapping[str, frozenset[str]]
    catalog: frozenset[str]
    edges: frozenset[Edge]

    @classmethod
    def from_relations(
        cls,
        user_items: Mapping[str, Iterable[str]],
        item_relations: Iterable[tuple[str, str]],
        *,
        catalog: Iterable[str] | None = None,
    ) -> "TrainingGraph":
        frozen_user_items = {
            user: frozenset(items)
            for user, items in sorted(user_items.items())
        }
        item_users_mutable: dict[str, set[str]] = {}
        edges: set[Edge] = set()
        for user, items in frozen_user_items.items():
            for item in items:
                item_users_mutable.setdefault(item, set()).add(user)
                edges.add(normalize_edge(user_node(user), item_node(item)))

        item_neighbors_mutable: dict[str, set[str]] = {}
        for left, right in item_relations:
            if left == right:
                continue
            item_neighbors_mutable.setdefault(left, set()).add(right)
            item_neighbors_mutable.setdefault(right, set()).add(left)
            edges.add(normalize_edge(item_node(left), item_node(right)))
        observed_catalog = {
            item for items in frozen_user_items.values() for item in items
        }
        observed_catalog.update(item_neighbors_mutable)
        for neighbors in item_neighbors_mutable.values():
            observed_catalog.update(neighbors)
        explicit_catalog = (
            observed_catalog
            if catalog is None
            else {str(item) for item in catalog}
        )
        if not observed_catalog.issubset(explicit_catalog):
            raise ValueError(
                "explicit catalog omits an item present in a training edge"
            )
        return cls(
            user_items=frozen_user_items,
            item_users={
                item: frozenset(users)
                for item, users in sorted(item_users_mutable.items())
            },
            item_neighbors={
                item: frozenset(neighbors)
                for item, neighbors in sorted(item_neighbors_mutable.items())
            },
            catalog=frozenset(explicit_catalog),
            edges=frozenset(edges),
        )


@dataclass(frozen=True)
class CrossRecommendationWitness:
    user: str
    candidate_item: str
    other_user: str
    shared_item: str
    anchor_item: str
    edge_occurrences: tuple[Edge, Edge, Edge, Edge, Edge]
    is_simple: bool

    @property
    def unique_edges(self) -> frozenset[Edge]:
        return frozenset(self.edge_occurrences)


def enumerate_cross_recommendation_witnesses(
    graph: TrainingGraph,
    candidate_universe: Mapping[str, Iterable[str]],
    *,
    policy: Literal["simple", "walk"],
) -> dict[tuple[str, str], tuple[CrossRecommendationWitness, ...]]:
    if policy not in {"simple", "walk"}:
        raise ValueError(f"unknown witness policy: {policy}")
    allowed = {
        user: frozenset(items)
        for user, items in candidate_universe.items()
    }
    output: dict[
        tuple[str, str], list[CrossRecommendationWitness]
    ] = {}
    for user in sorted(allowed):
        consumed = graph.user_items.get(user, frozenset())
        for candidate in consumed:
            if candidate in allowed[user]:
                raise ValueError(
                    f"candidate universe contains observed edge {user!r}, "
                    f"{candidate!r}"
                )
        witnesses_by_candidate: dict[
            str, list[CrossRecommendationWitness]
        ] = {}
        for anchor in sorted(consumed):
            for candidate in sorted(
                graph.item_neighbors.get(anchor, frozenset())
            ):
                if candidate not in allowed[user] or candidate in consumed:
                    continue
                candidate_edge = normalize_edge(
                    user_node(user), item_node(candidate)
                )
                if candidate_edge in graph.edges:
                    raise AssertionError(
                        "candidate edge unexpectedly exists in training graph"
                    )
                witnesses = witnesses_by_candidate.setdefault(candidate, [])
                for other_user in sorted(
                    graph.item_users.get(candidate, frozenset())
                ):
                    if other_user == user:
                        continue
                    common = consumed.intersection(
                        graph.user_items.get(other_user, frozenset())
                    )
                    for shared in sorted(common):
                        is_simple = shared != anchor
                        if not is_simple and policy == "simple":
                            continue
                        nodes = (
                            user_node(user),
                            item_node(shared),
                            user_node(other_user),
                            item_node(candidate),
                            item_node(anchor),
                        )
                        if is_simple and len(set(nodes)) != 5:
                            raise AssertionError(
                                "simple witness did not have five nodes"
                            )
                        occurrences = tuple(
                            normalize_edge(
                                nodes[index],
                                nodes[(index + 1) % 5],
                            )
                            for index in range(5)
                        )
                        if any(
                            edge not in graph.edges for edge in occurrences
                        ):
                            raise AssertionError(
                                "witness referenced a non-training edge"
                            )
                        if candidate_edge in occurrences:
                            raise AssertionError(
                                "candidate edge was used as evidence"
                            )
                        witnesses.append(
                            CrossRecommendationWitness(
                                user=user,
                                candidate_item=candidate,
                                other_user=other_user,
                                shared_item=shared,
                                anchor_item=anchor,
                                edge_occurrences=occurrences,  # type: ignore[arg-type]
                                is_simple=is_simple,
                            )
                        )
        for candidate, witnesses in sorted(witnesses_by_candidate.items()):
            if witnesses:
                output[(user, candidate)] = tuple(
                    sorted(
                        witnesses,
                        key=lambda value: (
                            value.other_user,
                            value.shared_item,
                            value.anchor_item,
                            value.edge_occurrences,
                        ),
                    )
                )
    return output


def positive_edge_percentiles(
    values: Mapping[Edge, int],
) -> dict[Edge, float]:
    positives = sorted(value for value in values.values() if value > 0)
    if not positives:
        return {edge: 0.0 for edge in values}
    return {
        edge: (
            bisect.bisect_right(positives, value) / len(positives)
            if value > 0
            else 0.0
        )
        for edge, value in values.items()
    }


def _normalise_positive_scores(
    values: Mapping[str, float],
) -> dict[str, float]:
    if any(not math.isfinite(value) for value in values.values()):
        raise ValueError("scores must be finite")
    if any(value < 0 for value in values.values()):
        raise ValueError("scores must be non-negative")
    positives = sorted(value for value in values.values() if value > 0)
    if not positives:
        return {item: 0.0 for item in values}
    return {
        item: (
            bisect.bisect_right(positives, value) / len(positives)
            if value > 0
            else 0.0
        )
        for item, value in values.items()
    }


def normalise_score_table(
    values: Mapping[str, Mapping[str, float]],
) -> ScoreTable:
    return {
        user: _normalise_positive_scores(scores)
        for user, scores in values.items()
    }
