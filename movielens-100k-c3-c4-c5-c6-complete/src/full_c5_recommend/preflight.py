from __future__ import annotations

import json
from pathlib import Path

from .artifacts import load_case


def graph_preflight(*, case_dir: Path) -> dict[str, object]:
    case = load_case(case_dir)
    degree = [0] * len(case.nodes)
    with case.graph_path.open(encoding="ascii") as handle:
        for line in handle:
            left, right = map(int, line.split())
            degree[left] += 1
            degree[right] += 1

    wedges = sum(value * (value - 1) // 2 for value in degree)
    # The sparse backend stores one A^2 record per distinct unordered endpoint
    # pair. That count q is at most the total number of wedges, but duplicates
    # from multiple common neighbors make q often much smaller. The estimate is
    # deliberately conservative and is a preflight warning, not a promise.
    conservative_bytes = 40 * wedges + 48 * case.edge_count + 64 * len(case.nodes)
    top = sorted(enumerate(degree), key=lambda row: (-row[1], row[0]))[:20]
    result = {
        "vertices": len(case.nodes),
        "edges": case.edge_count,
        "users": sum(record.node_type == "user" for record in case.nodes),
        "items": sum(record.node_type == "item" for record in case.nodes),
        "ui_edges": sum(record.edge_type == "UI" for record in case.edge_records),
        "ii_edges": sum(record.edge_type == "II" for record in case.edge_records),
        "maximum_degree": max(degree, default=0),
        "total_wedges_upper_bound_for_A2_pairs": wedges,
        "conservative_sparse_backend_memory_gib": conservative_bytes / (1024**3),
        "top_degree_nodes": [
            {
                "node_id": node,
                "node_type": case.nodes[node].node_type,
                "entity_id": case.nodes[node].entity_id,
                "degree": value,
            }
            for node, value in top
        ],
        "warning": (
            "The memory estimate uses total wedges as an upper bound on distinct A^2 pairs. "
            "Run the exact backend on a high-memory compute node; its stderr reports the actual pair count."
        ),
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return result
