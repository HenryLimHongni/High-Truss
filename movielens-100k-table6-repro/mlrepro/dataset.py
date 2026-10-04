from __future__ import annotations

import math
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .artifacts import EdgeRecord, NodeRecord, node_key, write_case
from .utils import key_edge, sha256_file

GENRES = (
    "unknown",
    "Action",
    "Adventure",
    "Animation",
    "Children's",
    "Comedy",
    "Crime",
    "Documentary",
    "Drama",
    "Fantasy",
    "Film-Noir",
    "Horror",
    "Musical",
    "Mystery",
    "Romance",
    "Sci-Fi",
    "Thriller",
    "War",
    "Western",
)
_YEAR_RE = re.compile(r"\((\d{4})\)\s*$")


@dataclass(frozen=True)
class Movie:
    movie_id: str
    title: str
    year: int | None
    genres: frozenset[str]


@dataclass(frozen=True)
class BuildOptions:
    train_quantile: float = 0.80
    fixed_cutoff: int | None = None
    minimum_rating: float = 0.0
    ii_top_k: int = 20
    genre_weight: float = 0.8
    year_weight: float = 0.2
    ii_similarity_threshold: float = 0.4


def load_movies(path: Path) -> dict[str, Movie]:
    path = Path(path).resolve()
    movies: dict[str, Movie] = {}
    with path.open(encoding="latin-1", errors="replace") as handle:
        for line_number, line in enumerate(handle, 1):
            columns = line.rstrip("\n").split("|")
            if len(columns) < 24:
                raise ValueError(f"u.item line {line_number} has {len(columns)} columns")
            movie_id = columns[0].strip()
            title = columns[1].strip() or movie_id
            release_date = columns[2].strip()
            flags = columns[5:24]
            genres = frozenset(
                genre
                for genre, flag in zip(GENRES, flags)
                if flag == "1" and genre != "unknown"
            )
            year = None
            match = _YEAR_RE.search(title)
            if match:
                year = int(match.group(1))
            elif release_date:
                try:
                    year = datetime.strptime(release_date, "%d-%b-%Y").year
                except ValueError:
                    year = None
            movies[movie_id] = Movie(movie_id, title, year, genres)
    if not movies:
        raise ValueError(f"no movies parsed from {path}")
    return movies


def _genre_cosine(left: Movie, right: Movie) -> float:
    if not left.genres or not right.genres:
        return 0.0
    overlap = len(left.genres & right.genres)
    return overlap / math.sqrt(len(left.genres) * len(right.genres))


def _movie_similarity(
    left: Movie,
    right: Movie,
    *,
    genre_weight: float,
    year_weight: float,
) -> tuple[float, str]:
    genre = _genre_cosine(left, right)
    if left.year is None or right.year is None:
        year_closeness = 0.0
        year_gap = -1
    else:
        year_gap = abs(left.year - right.year)
        year_closeness = 1.0 / (1.0 + year_gap / 10.0)

    score = genre_weight * genre + year_weight * year_closeness
    reason = (
        f"content-only:genre_cosine={genre:.6f};"
        f"genre_weight={genre_weight:.6f};"
        f"year_gap={year_gap};year_closeness={year_closeness:.6f};"
        f"year_weight={year_weight:.6f}"
    )
    return score, reason


def build_content_item_edges(
    movies: dict[str, Movie],
    warm_items: set[str],
    *,
    top_k: int,
    genre_weight: float,
    year_weight: float,
    similarity_threshold: float,
) -> dict[tuple[str, str], str]:
    """Connect movies using only genres and release year, never ratings.

    Semantics:
      1. Compute weighted content similarity for every pair of warm movies.
      2. Keep only pairs with score STRICTLY greater than similarity_threshold.
      3. For each movie, retain at most top_k highest-scoring surviving neighbors.
      4. Take the undirected union of those directed Top-K selections.

    The exhaustive pair scan keeps the implementation correct if the weights or
    threshold are changed later (for example, a low threshold where year-only
    similarity might become sufficient).
    """

    if top_k <= 0:
        raise ValueError("ii_top_k must be positive")
    if genre_weight < 0.0 or year_weight < 0.0:
        raise ValueError("genre_weight and year_weight must be non-negative")
    if genre_weight == 0.0 and year_weight == 0.0:
        raise ValueError("at least one similarity weight must be positive")
    if similarity_threshold < 0.0:
        raise ValueError("ii_similarity_threshold must be non-negative")

    items = sorted(warm_items)
    neighbors: dict[str, list[tuple[float, str, str]]] = defaultdict(list)
    checked_pairs = 0
    passing_pairs = 0

    for i, left in enumerate(items):
        for right in items[i + 1 :]:
            checked_pairs += 1
            score, reason = _movie_similarity(
                movies[left],
                movies[right],
                genre_weight=genre_weight,
                year_weight=year_weight,
            )
            # User requested "greater than 0.4", so this is intentionally strict.
            if score <= similarity_threshold:
                continue
            passing_pairs += 1
            neighbors[left].append((score, right, reason))
            neighbors[right].append((score, left, reason))

    selected: dict[tuple[str, str], str] = {}
    for item in items:
        ranked = sorted(
            neighbors.get(item, ()),
            key=lambda row: (-row[0], row[1]),
        )[:top_k]
        for score, other, reason in ranked:
            key = (item, other) if item < other else (other, item)
            selected.setdefault(
                key,
                (
                    f"{reason};score={score:.6f};"
                    f"threshold_gt={similarity_threshold:.6f};top_k={top_k}"
                ),
            )

    print(
        f"[II] warm_items={len(items)} checked_pairs={checked_pairs} "
        f"pairs_above_threshold={passing_pairs} "
        f"selected_undirected_edges={len(selected)} "
        f"genre_weight={genre_weight} year_weight={year_weight} "
        f"threshold_gt={similarity_threshold} top_k={top_k}",
        file=sys.stderr,
    )
    return selected

def _choose_cutoff(timestamps: list[int], options: BuildOptions) -> int:
    if options.fixed_cutoff is not None:
        return int(options.fixed_cutoff)
    if not 0.0 < options.train_quantile < 1.0:
        raise ValueError("train_quantile must be between 0 and 1")
    ordered = sorted(timestamps)
    index = min(len(ordered) - 1, max(1, math.floor(len(ordered) * options.train_quantile)))
    return ordered[index]


def build_case(
    *,
    ratings_path: Path,
    movies_path: Path,
    output_dir: Path,
    options: BuildOptions,
):
    ratings_path = Path(ratings_path).resolve()
    movies_path = Path(movies_path).resolve()
    movies = load_movies(movies_path)

    events: list[tuple[int, str, str]] = []
    invalid = 0
    below_rating = 0
    with ratings_path.open(encoding="ascii", errors="replace") as handle:
        for line_number, line in enumerate(handle, 1):
            columns = line.split()
            if len(columns) != 4:
                invalid += 1
                continue
            user, item, raw_rating, raw_timestamp = columns
            try:
                rating = float(raw_rating)
                timestamp = int(raw_timestamp)
            except (ValueError, OverflowError):
                invalid += 1
                continue
            if rating < options.minimum_rating:
                below_rating += 1
                continue
            if item not in movies:
                invalid += 1
                continue
            events.append((timestamp, user, item))
    if not events:
        raise ValueError("no valid MovieLens interactions")

    cutoff = _choose_cutoff([timestamp for timestamp, _, _ in events], options)
    train_pairs = {(user, item) for timestamp, user, item in events if timestamp < cutoff}
    test_pairs = {(user, item) for timestamp, user, item in events if timestamp >= cutoff}
    unseen_test = test_pairs.difference(train_pairs)
    if not train_pairs:
        raise ValueError("temporal split produced no training pairs")

    user_items: dict[str, set[str]] = defaultdict(set)
    item_users: dict[str, set[str]] = defaultdict(set)
    for user, item in train_pairs:
        user_items[user].add(item)
        item_users[item].add(user)
    warm_users = set(user_items)
    warm_items = set(item_users)
    truth_pairs = {
        (user, item)
        for user, item in unseen_test
        if user in warm_users and item in warm_items
    }

    ii_pairs = build_content_item_edges(
        movies,
        warm_items,
        top_k=options.ii_top_k,
        genre_weight=options.genre_weight,
        year_weight=options.year_weight,
        similarity_threshold=options.ii_similarity_threshold,
    )

    nodes: dict[str, NodeRecord] = {}
    for user in warm_users:
        key = node_key("user", user)
        nodes[key] = NodeRecord(key=key, node_type="user", entity_id=user)
    for item in warm_items:
        movie = movies[item]
        key = node_key("item", item)
        nodes[key] = NodeRecord(
            key=key,
            node_type="item",
            entity_id=item,
            title=movie.title,
            side_info="genres=" + "|".join(sorted(movie.genres)),
        )

    edges: dict[tuple[str, str], EdgeRecord] = {}
    for user, item in train_pairs:
        edges[key_edge(node_key("user", user), node_key("item", item))] = EdgeRecord(
            "UI", "pre-cutoff MovieLens rating interaction"
        )
    for (left, right), reason in ii_pairs.items():
        edges[key_edge(node_key("item", left), node_key("item", right))] = EdgeRecord(
            "II", reason
        )

    truth = {
        (node_key("user", user), node_key("item", item))
        for user, item in truth_pairs
    }

    return write_case(
        output_dir=output_dir,
        node_records=nodes,
        edge_records=edges,
        truth=truth,
        metadata={
            "dataset": "MovieLens 100K",
            "ratings_path": str(ratings_path),
            "movies_path": str(movies_path),
            "ratings_sha256": sha256_file(ratings_path),
            "movies_sha256": sha256_file(movies_path),
            "split_mode": "fixed timestamp" if options.fixed_cutoff is not None else "global timestamp quantile",
            "train_quantile": options.train_quantile,
            "cutoff_timestamp": cutoff,
            "minimum_rating": options.minimum_rating,
            "interaction_semantics": "binary distinct user--movie rating pair",
            "test_rule": "warm unseen pairs at or after cutoff",
            "item_relation_semantics": (
                "content-only weighted genre/year similarity; strict score threshold then per-item top-k"
            ),
            "ratings_used_for_ii_edges": False,
            "tags_used_for_ii_edges": False,
            "ii_top_k": options.ii_top_k,
            "genre_weight": options.genre_weight,
            "year_weight": options.year_weight,
            "ii_similarity_threshold_strict_gt": options.ii_similarity_threshold,
            "source_events": len(events),
            "invalid_rows": invalid,
            "rows_below_rating": below_rating,
            "training_pairs": len(train_pairs),
            "test_window_pairs": len(test_pairs),
            "warm_unseen_truth": len(truth_pairs),
            "construction_uses_test_answers": False,
        },
    )
