from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from itertools import combinations
from typing import Iterable, Mapping

from .data import StageData
from .hybrid import normalise_score_table


@dataclass(frozen=True)
class BaseAudit:
    model: str
    users: int
    catalog_items: int
    candidate_pairs: int
    positive_itemknn_pairs: int
    positive_content_pairs: int
    popularity_fallback_pairs: int


@dataclass(frozen=True)
class BaseComponents:
    collaborative: Mapping[str, Mapping[str, float]]
    content: Mapping[str, Mapping[str, float]]
    popularity: Mapping[str, Mapping[str, float]]
    audit: BaseAudit


def compute_base_components(
    stage: StageData,
    *,
    users: Iterable[str] | None = None,
) -> BaseComponents:
    evaluation_users = sorted(users if users is not None else stage.targets)
    popularity = {
        item: len(stage.item_users.get(item, set()))
        for item in stage.catalog
    }
    cooccurrence: dict[tuple[str, str], int] = defaultdict(int)
    for items in stage.user_items.values():
        for left, right in combinations(sorted(items), 2):
            cooccurrence[(left, right)] += 1

    similarities: dict[str, dict[str, float]] = defaultdict(dict)
    for (left, right), count in cooccurrence.items():
        denominator = math.sqrt(
            popularity.get(left, 0) * popularity.get(right, 0)
        )
        if denominator <= 0:
            continue
        value = count / denominator
        similarities[left][right] = value
        similarities[right][left] = value

    native_neighbors: dict[str, set[str]] = defaultdict(set)
    for left, right in stage.item_relations:
        native_neighbors[left].add(right)
        native_neighbors[right].add(left)

    max_popularity = max(popularity.values(), default=1)
    collaborative_scores: dict[str, dict[str, float]] = {}
    content_scores: dict[str, dict[str, float]] = {}
    popularity_scores: dict[str, dict[str, float]] = {}
    positive_collaborative = 0
    positive_content = 0
    fallback = 0
    for user in evaluation_users:
        consumed = stage.user_items.get(user, set())
        collaborative_user: dict[str, float] = {}
        content_user: dict[str, float] = {}
        popularity_user: dict[str, float] = {}
        for candidate in sorted(stage.catalog.difference(consumed)):
            collaborative = sum(
                similarities.get(candidate, {}).get(item, 0.0)
                for item in consumed
            )
            content = float(
                len(
                    native_neighbors.get(candidate, set()).intersection(
                        consumed
                    )
                )
            )
            collaborative_user[candidate] = collaborative
            content_user[candidate] = content
            popularity_user[candidate] = (
                popularity.get(candidate, 0) / max_popularity
            )
            positive_collaborative += collaborative > 0
            positive_content += content > 0
            fallback += collaborative == 0 and content == 0
        collaborative_scores[user] = collaborative_user
        content_scores[user] = content_user
        popularity_scores[user] = popularity_user

    audit = BaseAudit(
        model=(
            "binary collaborative ItemKNN cosine + native "
            f"{stage.item_relation_field} content + popularity tie fallback"
        ),
        users=len(evaluation_users),
        catalog_items=len(stage.catalog),
        candidate_pairs=sum(
            len(values) for values in collaborative_scores.values()
        ),
        positive_itemknn_pairs=positive_collaborative,
        positive_content_pairs=positive_content,
        popularity_fallback_pairs=fallback,
    )
    return BaseComponents(
        collaborative=collaborative_scores,
        content=content_scores,
        popularity=popularity_scores,
        audit=audit,
    )


def mix_base_components(
    components: BaseComponents,
    *,
    content_mix: float,
    mode: str,
) -> dict[str, dict[str, float]]:
    if mode not in {"content_aware", "itemknn_only"}:
        raise ValueError(f"unsupported base mode: {mode}")
    if not 0.0 <= content_mix <= 1.0:
        raise ValueError("content_mix must lie in [0,1]")
    if mode == "itemknn_only" and content_mix != 0.0:
        raise ValueError("itemknn_only requires content_mix=0")

    collaborative = normalise_score_table(components.collaborative)
    content = normalise_score_table(components.content)
    output: dict[str, dict[str, float]] = {}
    for user in sorted(collaborative):
        output[user] = {}
        for item in collaborative[user]:
            mixture = (
                (1.0 - content_mix) * collaborative[user][item]
                + content_mix * content[user][item]
            )
            # The formal comparison calls this mode ItemKNN, so its score and
            # tie-breaking must not contain an undisclosed popularity signal.
            # Content-aware legacy experiments may retain the tiny fallback.
            output[user][item] = (
                mixture
                if mode == "itemknn_only"
                else mixture + 1e-12 * components.popularity[user][item]
            )
    return output
