#!/usr/bin/env python3
"""
Step 2: compute C3/C4/C5/C6 cycle-truss scores.

Input files are produced by build_graph_labels.py. For each full intrachain
contact graph, this script computes:

* C3/C4/C5/C6 support-core (cycle-truss) edge levels;
* incident-maximum residue scores;
* ROC-AUC and average precision (reported as PR-AUC in the tables).

The cycle-truss decomposition is implemented as an exact hypergraph-core
peeling algorithm. Graph edges are the peeled objects and simple L-cycles are
the supporting hyperedges. This avoids recomputing all cycle supports from
scratch for every integer threshold.
"""

from __future__ import annotations

import argparse
import heapq
import json
import math
import time
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Set, Tuple

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score

Edge = Tuple[int, int]
Cycle = Tuple[Edge, ...]
Adj = List[Set[int]]

DEFAULT_PDB_IDS = ["1UBQ"]
DEFAULT_CYCLES = [3, 4, 5, 6]


def norm_edge(u: int, v: int) -> Edge:
    return (u, v) if u < v else (v, u)


def cutoff_tag(cutoff: str | float) -> str:
    return str(cutoff).replace(".", "p")


def parse_pdb_ids(arg: str) -> List[str]:
    return [value.strip().upper() for value in arg.split(",") if value.strip()]


def parse_int_list(arg: str) -> List[int]:
    return [int(value.strip()) for value in arg.split(",") if value.strip()]


def edge_list(adj: Adj) -> List[Edge]:
    return [(u, v) for u in range(len(adj)) for v in adj[u] if u < v]


def iter_l_cycles_containing_edge(adj: Adj, u: int, v: int, L: int) -> Iterator[Cycle]:
    """Yield every active simple L-cycle containing edge (u, v) exactly once."""
    uv = norm_edge(u, v)

    if L == 3:
        for w in adj[u] & adj[v]:
            if w == u or w == v:
                continue
            yield (uv, norm_edge(u, w), norm_edge(v, w))

    elif L == 4:
        # u-a-b-v-u
        for a in adj[u]:
            if a == v:
                continue
            for b in adj[a] & adj[v]:
                if b == u or b == v or b == a:
                    continue
                yield (
                    uv,
                    norm_edge(u, a),
                    norm_edge(a, b),
                    norm_edge(b, v),
                )

    elif L == 5:
        # u-a-b-c-v-u
        for a in adj[u]:
            if a == v:
                continue
            for c in adj[v]:
                if c == u or c == a:
                    continue
                for b in adj[a] & adj[c]:
                    if b == u or b == v or b == a or b == c:
                        continue
                    yield (
                        uv,
                        norm_edge(u, a),
                        norm_edge(a, b),
                        norm_edge(b, c),
                        norm_edge(c, v),
                    )

    elif L == 6:
        # u-a-b-c-d-v-u
        for a in adj[u]:
            if a == v:
                continue
            for b in adj[a]:
                if b == u or b == v or b == a:
                    continue
                for c in adj[b]:
                    if c == u or c == v or c == a or c == b:
                        continue
                    for d in adj[c] & adj[v]:
                        if d == u or d == v or d == a or d == b or d == c:
                            continue
                        yield (
                            uv,
                            norm_edge(u, a),
                            norm_edge(a, b),
                            norm_edge(b, c),
                            norm_edge(c, d),
                            norm_edge(d, v),
                        )
    else:
        raise ValueError("Only L=3,4,5,6 are supported")


def count_l_cycle_supports(adj: Adj, L: int) -> Dict[Edge, int]:
    """Count simple L-cycles containing each edge in the current graph."""
    return {
        edge: sum(1 for _ in iter_l_cycles_containing_edge(adj, edge[0], edge[1], L))
        for edge in edge_list(adj)
    }


def l_cycle_support_core_decomposition(
    adj0: Adj,
    L: int,
    initial_supports: Optional[Mapping[Edge, int]] = None,
) -> Dict[Edge, int]:
    """Compute exact L-cycle support-core levels for every graph edge.

    This is the hypergraph analogue of the standard heap-based core
    decomposition. Each graph edge is a vertex of the implicit hypergraph and
    each simple L-cycle is a hyperedge. When an edge is peeled, every still
    active L-cycle containing it disappears and decreases the support of the
    other cycle edges.
    """
    adj: Adj = [set(neighbors) for neighbors in adj0]
    edges = edge_list(adj)
    if initial_supports is None:
        supports: Dict[Edge, int] = count_l_cycle_supports(adj, L)
    else:
        supports = {edge: int(initial_supports[edge]) for edge in edges}

    heap: List[Tuple[int, int, int]] = [
        (support, edge[0], edge[1]) for edge, support in supports.items()
    ]
    heapq.heapify(heap)

    removed: Set[Edge] = set()
    levels: Dict[Edge, int] = {}

    while heap:
        support, u, v = heapq.heappop(heap)
        edge = (u, v)
        if edge in removed:
            continue
        if supports[edge] != support:
            continue  # stale heap record

        levels[edge] = support

        # Enumerate active cycles before deleting (u, v) from adjacency.
        for cycle in iter_l_cycles_containing_edge(adj, u, v, L):
            for other in cycle:
                if other == edge or other in removed:
                    continue
                # The usual core-decomposition guard prevents an edge from
                # being pushed below the shell currently being peeled.
                if supports[other] > support:
                    supports[other] -= 1
                    heapq.heappush(
                        heap,
                        (supports[other], other[0], other[1]),
                    )

        adj[u].discard(v)
        adj[v].discard(u)
        removed.add(edge)

    if len(levels) != len(edges):
        raise RuntimeError(
            f"C{L} decomposition assigned {len(levels)} of {len(edges)} edges"
        )
    return levels


def incident_max_scores(n: int, edge_scores: Mapping[Edge, int]) -> np.ndarray:
    scores = np.zeros(n, dtype=float)
    for (u, v), value in edge_scores.items():
        if value > scores[u]:
            scores[u] = value
        if value > scores[v]:
            scores[v] = value
    return scores


def compute_metrics(y_true: np.ndarray, scores: np.ndarray) -> Tuple[float, float]:
    if len(y_true) == 0 or len(np.unique(y_true)) < 2:
        return float("nan"), float("nan")
    return (
        float(roc_auc_score(y_true, scores)),
        float(average_precision_score(y_true, scores)),
    )


def build_adj(n: int, edges: pd.DataFrame) -> Adj:
    adj: Adj = [set() for _ in range(n)]
    for row in edges.itertuples(index=False):
        u = int(row.u)
        v = int(row.v)
        if u == v:
            raise ValueError(f"self-loop ({u}, {v}) found")
        if not (0 <= u < n and 0 <= v < n):
            raise ValueError(f"edge endpoint out of range: ({u}, {v}) with n={n}")
        adj[u].add(v)
        adj[v].add(u)
    return adj


def load_run_parameters(data_dir: Path) -> Dict[str, object]:
    path = data_dir / "run_parameters.json"
    if not path.exists():
        return {}
    with open(path) as handle:
        return json.load(handle)


def validate_full_intrachain_input(
    *,
    cutoff: float,
    pdb_id: str,
    labels: pd.DataFrame,
    edges: pd.DataFrame,
    graph_summary: Optional[pd.DataFrame],
    run_parameters: Mapping[str, object],
) -> None:
    """Validate the full-intrachain construction instead of old expected AUCs."""
    node_ids = sorted(labels["node_id"].astype(int).tolist())
    if node_ids != list(range(len(node_ids))):
        raise ValueError(f"{pdb_id}: node_id values are not contiguous from 0")

    normalized = [norm_edge(int(row.u), int(row.v)) for row in edges.itertuples(index=False)]
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{pdb_id} cutoff={cutoff}: duplicate undirected edges found")
    if any(int(row.u) >= int(row.v) for row in edges.itertuples(index=False)):
        raise ValueError(f"{pdb_id} cutoff={cutoff}: edges.csv must store u < v")

    chain_by_node = labels.set_index("node_id")["chain_id"].to_dict()
    for row in edges.itertuples(index=False):
        u = int(row.u)
        v = int(row.v)
        if chain_by_node[u] != chain_by_node[v]:
            raise ValueError(
                f"{pdb_id} cutoff={cutoff}: cross-chain edge ({u}, {v}) found"
            )

    min_sep = int(run_parameters.get("min_contact_seq_sep", 2))
    backbone_max = float(run_parameters.get("backbone_ca_max", 4.5))
    if {"edge_types", "seq_sep", "ca_distance"}.issubset(edges.columns):
        tolerance = 1e-7
        for row in edges.itertuples(index=False):
            types = set(str(row.edge_types).split(";"))
            distance = float(row.ca_distance)
            if "contact" in types:
                if pd.isna(row.seq_sep) or int(row.seq_sep) < min_sep:
                    raise ValueError(
                        f"{pdb_id} cutoff={cutoff}: invalid contact seq_sep on ({row.u}, {row.v})"
                    )
                if distance > cutoff + tolerance:
                    raise ValueError(
                        f"{pdb_id} cutoff={cutoff}: contact exceeds cutoff on ({row.u}, {row.v})"
                    )
            if "backbone" in types and distance > backbone_max + tolerance:
                raise ValueError(
                    f"{pdb_id}: backbone edge exceeds --backbone-ca-max on ({row.u}, {row.v})"
                )

    if graph_summary is not None and not graph_summary.empty:
        match = graph_summary[
            (graph_summary["pdb_id"].astype(str).str.upper() == pdb_id)
            & (np.isclose(graph_summary["cutoff"].astype(float), cutoff))
        ]
        if len(match) != 1:
            raise ValueError(
                f"{pdb_id} cutoff={cutoff}: expected one graph_summary row, found {len(match)}"
            )
        summary_row = match.iloc[0]
        if int(summary_row["n_vertices"]) != len(labels):
            raise ValueError(f"{pdb_id}: graph_summary vertex count mismatch")
        if int(summary_row["n_edges"]) != len(edges):
            raise ValueError(f"{pdb_id}: graph_summary edge count mismatch")
        if str(summary_row.get("graph_mode", "")) != "full_intrachain":
            raise ValueError(f"{pdb_id}: graph_summary is not full_intrachain")


def append_metric_row(
    rows: List[Dict[str, object]],
    *,
    cutoff: str,
    pdb_id: str,
    n_vertices: int,
    n_edges: int,
    eval_n: int,
    positive_n: int,
    L: int,
    roc_auc: float,
    pr_auc: float,
    support_count_sec: float,
    peeling_sec: float,
) -> None:
    rows.append({
        "cutoff": cutoff,
        "pdb_id": pdb_id,
        "n_vertices": n_vertices,
        "n_edges": n_edges,
        "eval_n": eval_n,
        "positive_n": positive_n,
        "L": L,
        "roc_auc": roc_auc,
        "pr_auc": pr_auc,
        "support_count_sec": support_count_sec,
        "peeling_sec": peeling_sec,
        "total_sec": support_count_sec + peeling_sec,
    })


def make_summary(metrics: pd.DataFrame) -> pd.DataFrame:
    wide_rows: List[Dict[str, object]] = []
    for (cutoff, pdb_id), group in metrics.groupby(["cutoff", "pdb_id"], sort=False):
        first = group.iloc[0]
        record: Dict[str, object] = {
            "cutoff": cutoff,
            "pdb_id": pdb_id,
            "n_vertices": int(first["n_vertices"]),
            "n_edges": int(first["n_edges"]),
            "eval_n": int(first["eval_n"]),
            "positive_n": int(first["positive_n"]),
        }
        for row in group.itertuples(index=False):
            prefix = f"C{int(row.L)}"
            record[f"{prefix}_ROC"] = float(row.roc_auc)
            record[f"{prefix}_PR"] = float(row.pr_auc)
            record[f"{prefix}_time_sec"] = float(row.total_sec)
        wide_rows.append(record)

    summary = pd.DataFrame(wide_rows)
    preferred = [
        "cutoff", "pdb_id", "n_vertices", "n_edges", "eval_n", "positive_n",
        "C3_ROC", "C3_PR", "C4_ROC", "C4_PR", "C5_ROC", "C5_PR",
        "C6_ROC", "C6_PR",
        "C3_time_sec", "C4_time_sec", "C5_time_sec", "C6_time_sec",
    ]
    ordered = [column for column in preferred if column in summary.columns]
    ordered.extend(column for column in summary.columns if column not in ordered)
    return summary[ordered]


def make_cycle_aggregate(metrics: pd.DataFrame) -> pd.DataFrame:
    rows: List[Dict[str, object]] = []
    for (cutoff, L), group in metrics.groupby(["cutoff", "L"], sort=False):
        rows.append({
            "cutoff": cutoff,
            "L": int(L),
            "cycle": f"C{int(L)}",
            "entries": len(group),
            "mean_ROC": float(group["roc_auc"].mean()),
            "median_ROC": float(group["roc_auc"].median()),
            "mean_PR": float(group["pr_auc"].mean()),
            "median_PR": float(group["pr_auc"].median()),
            "mean_total_sec": float(group["total_sec"].mean()),
            "sum_total_sec": float(group["total_sec"].sum()),
        })
    return pd.DataFrame(rows)


def write_outputs(
    out_dir: Path,
    metric_rows: Sequence[Mapping[str, object]],
    residue_rows: Sequence[Mapping[str, object]],
) -> None:
    if not metric_rows:
        return

    metrics = pd.DataFrame(metric_rows)
    cycle_columns = [
        "cutoff", "pdb_id", "n_vertices", "n_edges", "eval_n", "positive_n",
        "L", "roc_auc", "pr_auc", "support_count_sec", "peeling_sec", "total_sec",
    ]
    metrics[cycle_columns].to_csv(
        out_dir / "metrics_by_entry_cycle.csv",
        index=False,
    )

    pd.DataFrame(residue_rows).to_csv(
        out_dir / "residue_scores_by_entry.csv",
        index=False,
    )

    summary = make_summary(metrics)
    summary.to_csv(out_dir / "summary_wide.csv", index=False)

    make_cycle_aggregate(metrics).to_csv(
        out_dir / "cycle_aggregate_by_cutoff.csv",
        index=False,
    )


def validate_metric_rows(metrics: pd.DataFrame) -> None:
    for column in ["roc_auc", "pr_auc"]:
        finite = metrics[column].dropna()
        if ((finite < 0) | (finite > 1)).any():
            raise ValueError(f"{column} contains a value outside [0, 1]")
    for column in ["support_count_sec", "peeling_sec", "total_sec"]:
        if (metrics[column] < 0).any():
            raise ValueError(f"{column} contains a negative runtime")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compute C3/C4/C5/C6 cycle-truss metrics on "
            "full intrachain residue contact graphs."
        )
    )
    parser.add_argument("--data-dir", default="./data_full_intrachain")
    parser.add_argument("--out-dir", default="./results_full_intrachain")
    parser.add_argument("--pdb-ids", default=",".join(DEFAULT_PDB_IDS))
    parser.add_argument("--cutoffs", default="6.5,7.0")
    parser.add_argument("--cycle-lengths", default=",".join(str(x) for x in DEFAULT_CYCLES))
    parser.add_argument(
        "--verify",
        action="store_true",
        help=(
            "Validate full-intrachain graph constraints, graph_summary counts, "
            "metric ranges, and cycle-truss decomposition invariants. This does not "
            "compare against the old local-graph AUC table."
        ),
    )
    parser.add_argument(
        "--no-checkpoint",
        action="store_true",
        help="Do not rewrite partial result CSVs after each PDB/cutoff entry.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    data_dir = Path(args.data_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    pdb_ids = parse_pdb_ids(args.pdb_ids)
    cutoffs = [value.strip() for value in args.cutoffs.split(",") if value.strip()]
    cycle_lengths = parse_int_list(args.cycle_lengths)
    invalid = sorted(set(cycle_lengths) - {3, 4, 5, 6})
    if invalid:
        raise ValueError(f"Unsupported cycle lengths: {invalid}; use only 3,4,5,6")
    if not cycle_lengths:
        raise ValueError("--cycle-lengths is empty")

    labels_path = data_dir / "labels.csv"
    if not labels_path.exists():
        raise FileNotFoundError(labels_path)
    labels_all = pd.read_csv(labels_path)
    labels_all["pdb_id"] = labels_all["pdb_id"].astype(str).str.upper()

    graph_summary_path = data_dir / "graph_summary.csv"
    graph_summary = pd.read_csv(graph_summary_path) if graph_summary_path.exists() else None
    run_parameters = load_run_parameters(data_dir)

    all_rows: List[Dict[str, object]] = []
    residue_score_rows: List[Dict[str, object]] = []

    for cutoff_text in cutoffs:
        cutoff = float(cutoff_text)
        edges_path = data_dir / f"edges_ca{cutoff_tag(cutoff_text)}.csv"
        if not edges_path.exists():
            raise FileNotFoundError(edges_path)
        edges_all = pd.read_csv(edges_path)
        edges_all["pdb_id"] = edges_all["pdb_id"].astype(str).str.upper()

        print("\n============================")
        print(f"Full intrachain cutoff = {cutoff_text} Å")
        print("============================")

        for pdb_id in pdb_ids:
            labels = labels_all[labels_all["pdb_id"] == pdb_id].copy()
            edges = edges_all[edges_all["pdb_id"] == pdb_id].copy()
            if labels.empty:
                print(f"[WARN] Missing labels for {pdb_id}")
                continue
            if edges.empty:
                print(f"[WARN] Missing/empty graph for {pdb_id} cutoff={cutoff_text}")
                continue

            labels["node_id"] = labels["node_id"].astype(int)
            n = int(labels["node_id"].max()) + 1

            if args.verify:
                validate_full_intrachain_input(
                    cutoff=cutoff,
                    pdb_id=pdb_id,
                    labels=labels,
                    edges=edges,
                    graph_summary=graph_summary,
                    run_parameters=run_parameters,
                )

            adj = build_adj(n, edges)
            graph_edges = edge_list(adj)
            n_edges = len(graph_edges)

            eval_df = labels[labels["is_eval_residue"].astype(int) == 1].copy()
            eval_df["eval_label"] = eval_df["eval_label"].astype(int)
            eval_nodes = eval_df["node_id"].to_numpy(dtype=int)
            y = eval_df["eval_label"].to_numpy(dtype=int)

            print(
                f"\n{pdb_id:5s} |V|={n:4d} |E|={n_edges:5d} "
                f"eval={len(eval_df):4d} positives={int(y.sum()):3d}",
                flush=True,
            )

            scores_by_L: Dict[int, np.ndarray] = {}
            supports_by_L: Dict[int, Dict[Edge, int]] = {}
            support_time_by_L: Dict[int, float] = {}

            for L in sorted(set(cycle_lengths)):
                print(f"  [C{L}] counting original edge supports...", flush=True)
                start = time.perf_counter()
                supports_by_L[L] = count_l_cycle_supports(adj, L)
                support_time_by_L[L] = time.perf_counter() - start
                max_support = max(supports_by_L[L].values(), default=0)
                total_incidence = sum(supports_by_L[L].values())
                cycle_count = total_incidence // L
                print(
                    f"       cycles={cycle_count:,} max_edge_support={max_support:,} "
                    f"time={support_time_by_L[L]:.3f}s",
                    flush=True,
                )

            for L in cycle_lengths:
                print(f"  [C{L}] peeling support-core/truss levels...", flush=True)
                start = time.perf_counter()
                truss = l_cycle_support_core_decomposition(
                    adj,
                    L,
                    initial_supports=supports_by_L[L],
                )
                peeling_sec = time.perf_counter() - start

                if set(truss) != set(graph_edges):
                    raise RuntimeError(f"{pdb_id} C{L}: truss edge set differs from input edge set")
                if any(truss[edge] > supports_by_L[L][edge] for edge in graph_edges):
                    raise RuntimeError(f"{pdb_id} C{L}: truss level exceeds original support")

                scores = incident_max_scores(n, truss)
                scores_by_L[L] = scores
                roc, pr = compute_metrics(y, scores[eval_nodes])
                append_metric_row(
                    all_rows,
                    cutoff=cutoff_text,
                    pdb_id=pdb_id,
                    n_vertices=n,
                    n_edges=n_edges,
                    eval_n=len(eval_df),
                    positive_n=int(y.sum()),
                    L=L,
                    roc_auc=roc,
                    pr_auc=pr,
                    support_count_sec=support_time_by_L[L],
                    peeling_sec=peeling_sec,
                )
                print(
                    f"       ROC={roc:.4f} PR={pr:.4f} "
                    f"peeling={peeling_sec:.3f}s total={support_time_by_L[L] + peeling_sec:.3f}s",
                    flush=True,
                )

            for row in eval_df.itertuples(index=False):
                node_id = int(row.node_id)
                record: Dict[str, object] = {
                    "cutoff": cutoff_text,
                    "pdb_id": pdb_id,
                    "node_id": node_id,
                    "chain_id": row.chain_id,
                    "chain_index": row.chain_index,
                    "residue_uid": row.residue_uid,
                    "resname": row.resname,
                    "aa": row.aa,
                    "dssp_ss": row.dssp_ss,
                    "eval_label": int(row.eval_label),
                }
                for L in cycle_lengths:
                    record[f"score_C{L}"] = scores_by_L[L][node_id]
                residue_score_rows.append(record)

            if not args.no_checkpoint:
                write_outputs(out_dir, all_rows, residue_score_rows)
                print("  [checkpoint] partial CSV files updated", flush=True)

    if not all_rows:
        raise RuntimeError("No metrics were produced; check PDB IDs, cutoffs, and input files")

    write_outputs(out_dir, all_rows, residue_score_rows)
    metrics = pd.DataFrame(all_rows)
    if args.verify:
        validate_metric_rows(metrics)
        print(
            "\n[OK] Verification passed: full-intrachain constraints, summary counts, "
            "metric ranges, and cycle-truss invariants are valid."
        )

    print(f"\n[OK] Saved results to: {out_dir.resolve()}")
    print("  - metrics_by_entry_cycle.csv")
    print("  - summary_wide.csv")
    print("  - residue_scores_by_entry.csv")
    print("  - cycle_aggregate_by_cutoff.csv")


if __name__ == "__main__":
    main()
