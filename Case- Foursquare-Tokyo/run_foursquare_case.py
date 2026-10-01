#!/usr/bin/env python3
"""Reproduce the frozen Foursquare Tokyo Department Store case study."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "src"))

import experiment_runtime as runtime
from crossrec_final.base import compute_base_components, mix_base_components
from crossrec_final.graph import build_graph
from crossrec_final.hybrid import TrainingGraph
from item_edge_c5.comparison_scoring import build_comparison_feature_tables
from item_edge_c5.foursquare_tsmc import build_stage
from item_edge_c5.ranking_metrics import (
    METHOD_SPECS,
    audit_score_support_at_5,
    evaluate_precision_ndcg_at_5,
    load_score_ledger,
)
from property_graph_export import export_property_graph, load_edge_attributes


DATASET = "Foursquare Tokyo Department Store WITHIN_2KM cutoff 2012-12-01"


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        raise ValueError(f"refusing to write empty CSV: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def check_split(stage, positives) -> dict[str, int]:
    overlap = sum(
        item in stage.user_items.get(user, set())
        for user, items in positives.items()
        for item in items
    )
    cold = sum(item not in stage.catalog for items in positives.values() for item in items)
    unknown_users = sum(
        len(items) for user, items in positives.items() if user not in stage.user_items
    )
    if overlap or cold or unknown_users:
        raise ValueError(
            "invalid temporal split: "
            f"overlap={overlap}, cold={cold}, unknown_users={unknown_users}"
        )
    return {
        "training_test_edge_overlap": overlap,
        "cold_test_edges": cold,
        "test_edges_with_unknown_user": unknown_users,
    }


def reference_rows(
    path: Path,
) -> dict[str, tuple[float, float]]:
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return {
        row["method"]: (
            float(row["ndcg_at_5"]),
            float(row["precision_at_5"]),
        )
        for row in rows
    }


def validate_reference(rows: list[dict[str, object]], path: Path) -> float:
    expected = reference_rows(path)
    observed = {
        str(row["method"]): (
            float(row["ndcg_at_5"]),
            float(row["precision_at_5"]),
        )
        for row in rows
    }
    if set(expected) != set(observed):
        raise ValueError("reference and current method sets differ")
    maximum = max(
        abs(observed[method][index] - expected[method][index])
        for method in expected
        for index in range(2)
    )
    if maximum > 1e-12:
        raise ValueError(f"frozen-reference regression error: {maximum}")
    # Values within the declared tolerance are equivalent.  Canonicalizing
    # them avoids a platform-dependent 0.0 versus ~1e-17 value in AUDIT.json.
    return 0.0


def report(rows: list[dict[str, object]]) -> str:
    lines = [
        f"# {DATASET}",
        "",
        "| Method | NDCG@5 | Precision@5 |",
        "|---|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['method']} | {float(row['ndcg_at_5']):.4f} | "
            f"{float(row['precision_at_5']):.4f} |"
        )
    return "\n".join(lines) + "\n"


def run(checkins: Path, output: Path, reference: Path) -> None:
    if output.exists():
        raise ValueError("output already exists; choose a new output directory")
    if not runtime.BACKEND.is_file():
        raise FileNotFoundError("missing backend; run bash scripts/bootstrap.sh")

    stage, positives, source_audit = build_stage(checkins_path=checkins)
    split_audit = check_split(stage, positives)
    graph = TrainingGraph.from_relations(
        stage.user_items,
        stage.item_relations,
        catalog=stage.catalog,
    )
    independently_built = build_graph(stage)
    if set(graph.edges) != set(independently_built.edges):
        raise AssertionError("mixed graph implementations disagree")
    item_degrees = {
        item: (
            len(graph.item_users.get(item, frozenset()))
            + len(graph.item_neighbors.get(item, frozenset()))
        )
        for item in graph.catalog
    }
    if set(item_degrees) != set(stage.catalog):
        raise AssertionError("item degree catalog differs from training graph")
    if not item_degrees or min(item_degrees.values()) <= 0:
        raise AssertionError("every warm item must have positive training degree")

    output.mkdir(parents=True)
    decomposition = output / "decomposition"
    decomposition.mkdir()
    graph_path = decomposition / "MIXED_GRAPH_EDGES.tsv"
    graph_hash = runtime.prepare_graph(path=graph_path, edges=graph.edges)
    edges_out, counts_out = runtime.run_decomposition(
        graph_path=graph_path,
        output_dir=decomposition,
        expected_edges=graph.edges,
    )
    trussness = runtime.load_decomposition(edges_out, graph.edges)

    components = compute_base_components(stage, users=positives)
    itemknn = mix_base_components(
        components,
        content_mix=0.0,
        mode="itemknn_only",
    )
    expected_candidates = {
        user: set(stage.catalog).difference(stage.user_items[user])
        for user in positives
    }
    observed_candidates = {user: set(scores) for user, scores in itemknn.items()}
    if observed_candidates != expected_candidates:
        raise AssertionError("ItemKNN did not expose the complete warm-unseen catalog")

    candidate_universe = {
        user: scores.keys() for user, scores in itemknn.items()
    }
    features = build_comparison_feature_tables(
        graph,
        candidate_universe,
        trussness,
    )
    edge_attributes = load_edge_attributes(edges_out)
    export_property_graph(
        output_dir=output,
        stage=stage,
        edge_attributes=edge_attributes,
        base_scores=itemknn,
        percentile_features=features.features,
        raw_truss_features=features.raw_truss_features,
        item_degrees=item_degrees,
    )

    ledger = load_score_ledger(output / "graphdb/cross_recommendations.csv")
    rows, per_user = evaluate_precision_ndcg_at_5(
        ledger,
        positives,
        dataset=DATASET,
    )
    if [row["method"] for row in rows] != [spec.method for spec in METHOD_SPECS]:
        raise AssertionError("five-method order changed")
    score_support_audit = audit_score_support_at_5(ledger, positives)
    maximum_error = validate_reference(rows, reference)

    write_csv(output / "METRICS.csv", rows)
    write_csv(output / "PER_USER_METRICS.csv", per_user)
    headline = [
        {
            "method": row["method"],
            "ndcg_at_5": row["ndcg_at_5"],
            "precision_at_5": row["precision_at_5"],
        }
        for row in rows
    ]
    write_csv(output / "HEADLINE_METRICS.csv", headline)
    text = report(rows)
    (output / "RESULTS.md").write_text(text, encoding="utf-8")
    structural_cycle_counts = [
        {
            key: value
            for key, value in row.items()
            if not key.endswith("_seconds")
        }
        for row in runtime.read_cycle_counts(counts_out)
    ]
    audit = {
        **source_audit,
        **split_audit,
        "dataset_label": DATASET,
        "user_vertices": len(stage.user_items),
        "item_vertices": len(stage.catalog),
        "user_item_edges": sum(len(items) for items in stage.user_items.values()),
        "item_item_edges": len(stage.item_relations),
        "mixed_graph_edges": len(graph.edges),
        "evaluation_users": len(positives),
        "test_positive_edges": sum(len(items) for items in positives.values()),
        "candidate_pairs": sum(len(scores) for scores in itemknn.values()),
        "same_full_candidates_for_all_methods": True,
        "ranking_rule": (
            "rank the complete warm-unseen catalog by primary method score "
            "descending, training mixed-graph item degree descending, and "
            "item_id ascending; zero-score Items remain eligible"
        ),
        "recommendation_list_size": "exactly 5 for every User",
        "precision_denominator": "5 for every User",
        "precision_aggregation": "macro average over all evaluation Users",
        "ndcg_definition": (
            "binary NDCG@5 per User: DCG sums hit/log2(rank+1); IDCG "
            "uses min(number of test positives, 5) ideal hits; macro "
            "average over all evaluation Users"
        ),
        "zero_score_items_returned": True,
        "item_degree_definition": (
            "incident training User--Item edges plus incident training "
            "Item--Item edges"
        ),
        "item_degree_min": min(item_degrees.values()),
        "item_degree_max": max(item_degrees.values()),
        "test_edges_used_for_item_degree": False,
        "candidate_edge_inserted_before_decomposition": False,
        "decomposition": "exact edge-level simple C3/C4/C5/C6 trussness",
        "cycle_counts_without_machine_timings": structural_cycle_counts,
        "feature_audit": features.audit,
        "top5_score_support_audit": score_support_audit,
        "graph_sha256": graph_hash,
        "reference_max_absolute_error": maximum_error,
    }
    runtime.atomic_write_json(output / "AUDIT.json", audit)
    print(text)
    print(f"Result: {output / 'HEADLINE_METRICS.csv'}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--foursquare-tokyo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--reference",
        type=Path,
        default=HERE / "reference/HEADLINE_METRICS.csv",
    )
    args = parser.parse_args()
    run(args.foursquare_tokyo, args.output, args.reference)


if __name__ == "__main__":
    main()
