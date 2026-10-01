"""Export edge-level trussness and recommendation scores for graph databases."""

from __future__ import annotations

import csv
from pathlib import Path

from crossrec_final.graph import normalize_edge
from crossrec_final.hybrid import item_node, user_node


CYCLE_LENGTHS = (3, 4, 5, 6)


def _write_rows(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        raise ValueError(f"refusing to write empty graph export: {path}")
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def load_edge_attributes(
    path: Path,
) -> dict[tuple[str, str], dict[str, int]]:
    output: dict[tuple[str, str], dict[str, int]] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            edge = normalize_edge(row["src"], row["dst"])
            output[edge] = {
                f"support_c{length}": int(row[f"support_c{length}"])
                for length in CYCLE_LENGTHS
            } | {
                f"tau_c{length}": int(row[f"trussness_c{length}"])
                for length in CYCLE_LENGTHS
            }
    return output


def export_property_graph(
    *,
    output_dir: Path,
    stage,
    edge_attributes,
    base_scores,
    percentile_features,
    raw_truss_features,
    item_degrees,
) -> None:
    graphdb = output_dir / "graphdb"
    graphdb.mkdir(parents=True, exist_ok=True)
    _write_rows(
        graphdb / "users.csv",
        [{"user_id": user} for user in sorted(stage.user_items)],
    )
    _write_rows(
        graphdb / "items.csv",
        [
            {
                "item_id": item,
                "title": stage.metadata[item].get("title", ""),
                "publisher": stage.metadata[item].get("publisher", ""),
                "training_ui_degree": len(stage.item_users.get(item, set())),
                "training_ii_degree": (
                    item_degrees[item]
                    - len(stage.item_users.get(item, set()))
                ),
                "training_item_degree": item_degrees[item],
            }
            for item in sorted(stage.catalog)
        ],
    )

    reviewed_rows: list[dict[str, object]] = []
    for event in stage.interactions:
        edge = normalize_edge(user_node(event.user), item_node(event.item))
        reviewed_rows.append(
            {
                "user_id": event.user,
                "item_id": event.item,
                "rating": event.rating,
                "timestamp": event.timestamp,
                **edge_attributes[edge],
            }
        )
    _write_rows(graphdb / "interactions.csv", reviewed_rows)

    relation_rows: list[dict[str, object]] = []
    for left, right in sorted(stage.item_relations):
        edge = normalize_edge(item_node(left), item_node(right))
        relation_rows.append(
            {
                "src_item_id": left,
                "dst_item_id": right,
                "relation_type": stage.item_relation_field,
                **edge_attributes[edge],
            }
        )
    _write_rows(graphdb / "item_relations.csv", relation_rows)

    recommendation_rows: list[dict[str, object]] = []
    for user in sorted(base_scores):
        for item in sorted(base_scores[user]):
            row: dict[str, object] = {
                "user_id": user,
                "item_id": item,
                "training_item_degree": item_degrees[item],
                "itemknn": base_scores[user][item],
            }
            for length in CYCLE_LENGTHS:
                name = f"c{length}_truss"
                raw = raw_truss_features[name][user][item]
                normalized = percentile_features[name][user][item]
                if length == 3:
                    row["max_c3_item_edge_tau_c3"] = raw
                    row["percentile_c3_item_edge_tau_c3"] = normalized
                elif length == 4:
                    row["max_c4_item_edge_tau_c4"] = raw
                    row["percentile_c4_item_edge_tau_c4"] = normalized
                elif length == 5:
                    row["max_c5_role_edge_tau_c5"] = raw
                    row["percentile_c5_role_edge_tau_c5"] = normalized
                else:
                    row["max_native_c6_role_edge_tau_c6"] = raw
                    row["percentile_native_c6_role_edge_tau_c6"] = normalized
            recommendation_rows.append(row)
    _write_rows(graphdb / "cross_recommendations.csv", recommendation_rows)
    (graphdb / "queries.cypher").write_text(
        """// C5-truss only
MATCH (:User {user_id: $user})-[r:CROSS_RECOMMEND]->(i:Item)
RETURN i.item_id, i.title, r.max_c5_role_edge_tau_c5
ORDER BY r.max_c5_role_edge_tau_c5 DESC,
         i.training_item_degree DESC, i.item_id
LIMIT $limit;

// ItemKNN baseline
MATCH (:User {user_id: $user})-[r:CROSS_RECOMMEND]->(i:Item)
RETURN i.item_id, i.title, r.itemknn
ORDER BY r.itemknn DESC, i.training_item_degree DESC, i.item_id
LIMIT $limit;

""",
        encoding="utf-8",
    )
