"""Small, self-contained runtime for exact edge-level cycle trussness.

The public experiment needs only four operations: serialize the immutable
training graph, invoke the C++ backend for C3--C6, validate its output, and
load the edge trussness maps.  Keeping those operations here makes this
five-method replication package independent of earlier prototypes.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import subprocess
from pathlib import Path

from crossrec_final.graph import normalize_edge


HERE = Path(__file__).resolve().parent
BACKEND = HERE / "bin" / "streaming_cycle_truss"
CYCLE_LENGTHS = (3, 4, 5, 6)


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    _atomic_write_bytes(
        path,
        (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        ),
    )


def _graph_payload(edges) -> bytes:
    return "".join(
        f"{left}\t{right}\n" for left, right in sorted(edges)
    ).encode("utf-8")


def prepare_graph(*, path: Path, edges) -> str:
    """Write the exact training edge relation and return its SHA-256."""

    payload = _graph_payload(edges)
    _atomic_write_bytes(path, payload)
    return _sha256_bytes(payload)


def _read_edge_rows(path: Path, expected_edges) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"src", "dst"}
        for length in CYCLE_LENGTHS:
            required.update(
                {f"support_c{length}", f"trussness_c{length}"}
            )
        if reader.fieldnames is None or not required.issubset(
            reader.fieldnames
        ):
            raise ValueError(f"invalid decomposition schema: {path}")
        rows = list(reader)

    observed = [normalize_edge(row["src"], row["dst"]) for row in rows]
    if len(observed) != len(set(observed)):
        raise ValueError("duplicate edge in decomposition output")
    if set(observed) != set(expected_edges):
        raise ValueError("decomposition output differs from training graph")
    for row in rows:
        for length in CYCLE_LENGTHS:
            support = int(row[f"support_c{length}"])
            trussness = int(row[f"trussness_c{length}"])
            if support < 0 or trussness < 0:
                raise ValueError("negative support or trussness")
    return rows


def _validate_counts(path: Path) -> None:
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    observed = [int(row["cycle_length"]) for row in rows]
    if observed != list(CYCLE_LENGTHS):
        raise ValueError(f"invalid C3--C6 cycle-count output: {path}")


def run_decomposition(
    *,
    graph_path: Path,
    output_dir: Path,
    expected_edges,
) -> tuple[Path, Path]:
    """Compute exact simple-cycle C3--C6 edge trussness in one invocation."""

    if not BACKEND.is_file():
        raise FileNotFoundError(
            "missing C++ backend; run bash scripts/bootstrap.sh"
        )
    edges_out = output_dir / "EDGE_TRUSSNESS.csv"
    counts_out = output_dir / "CYCLE_COUNTS.csv"
    if edges_out.exists() or counts_out.exists():
        raise ValueError("decomposition output already exists")

    temporary_edges = output_dir / ".EDGE_TRUSSNESS.tmp.csv"
    temporary_counts = output_dir / ".CYCLE_COUNTS.tmp.csv"
    try:
        subprocess.run(
            [
                str(BACKEND),
                "--input",
                str(graph_path),
                "--edges-out",
                str(temporary_edges),
                "--counts-out",
                str(temporary_counts),
                "--lengths",
                "3,4,5,6",
                "--quiet",
            ],
            check=True,
        )
        _read_edge_rows(temporary_edges, expected_edges)
        _validate_counts(temporary_counts)
        os.replace(temporary_edges, edges_out)
        os.replace(temporary_counts, counts_out)
    finally:
        for temporary in (temporary_edges, temporary_counts):
            if temporary.exists():
                temporary.unlink()
    return edges_out, counts_out


def load_decomposition(path: Path, expected_edges):
    rows = _read_edge_rows(path, expected_edges)
    output = {length: {} for length in CYCLE_LENGTHS}
    for row in rows:
        edge = normalize_edge(row["src"], row["dst"])
        for length in CYCLE_LENGTHS:
            output[length][edge] = int(row[f"trussness_c{length}"])
    return output


def read_cycle_counts(path: Path) -> list[dict[str, object]]:
    _validate_counts(path)
    with path.open(encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]
