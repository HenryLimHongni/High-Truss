from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .data import StageData

Edge = tuple[str, str]


def user_node(user: str) -> str:
    return f"U:{user}"


def item_node(item: str) -> str:
    return f"I:{item}"


def normalize_edge(left: str, right: str) -> Edge:
    if left == right:
        raise ValueError("Self-loops are not supported")
    return (left, right) if left < right else (right, left)


@dataclass
class HeterogeneousGraph:
    adjacency: dict[str, set[str]]
    edge_type: dict[Edge, str]
    user_items: dict[str, set[str]]
    item_users: dict[str, set[str]]
    item_neighbors: dict[str, set[str]]

    @property
    def edges(self) -> list[Edge]:
        return sorted(self.edge_type)


def build_graph(stage: StageData) -> HeterogeneousGraph:
    adjacency: dict[str, set[str]] = defaultdict(set)
    edge_type: dict[Edge, str] = {}
    for user, items in stage.user_items.items():
        for item in items:
            left = user_node(user)
            right = item_node(item)
            edge = normalize_edge(left, right)
            adjacency[left].add(right)
            adjacency[right].add(left)
            edge_type[edge] = "INTERACTED_WITH"

    item_neighbors: dict[str, set[str]] = defaultdict(set)
    item_relation_type = {
        "also_view": "ALSO_VIEWED",
        "also_buy": "ALSO_BOUGHT",
    }.get(stage.item_relation_field, "ITEM_RELATED")
    for left_item, right_item in sorted(stage.item_relations):
        left = item_node(left_item)
        right = item_node(right_item)
        edge = normalize_edge(left, right)
        adjacency[left].add(right)
        adjacency[right].add(left)
        edge_type[edge] = item_relation_type
        item_neighbors[left_item].add(right_item)
        item_neighbors[right_item].add(left_item)

    return HeterogeneousGraph(
        adjacency={node: set(values) for node, values in adjacency.items()},
        edge_type=edge_type,
        user_items={
            user: set(items) for user, items in stage.user_items.items()
        },
        item_users={
            item: set(users) for item, users in stage.item_users.items()
        },
        item_neighbors={
            item: set(values) for item, values in item_neighbors.items()
        },
    )
