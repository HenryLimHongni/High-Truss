from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Interaction:
    user: str
    item: str
    rating: float
    timestamp: int


@dataclass
class StageData:
    stage: str
    interactions: list[Interaction]
    user_items: dict[str, set[str]]
    item_users: dict[str, set[str]]
    targets: dict[str, str]
    strictly_later_day_targets: dict[str, str]
    eligible_users: list[str]
    catalog: set[str]
    metadata: dict[str, dict]
    item_relations: set[tuple[str, str]]
    item_relation_field: str = "also_view"
