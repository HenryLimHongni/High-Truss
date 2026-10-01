from __future__ import annotations

import csv
import json
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Iterable

from .artifacts import CaseArtifact, load_case, load_value_file, write_value_file
from .decompose import DEFAULT_BIN_DIR, compute_c5
from .utils import atomic_write_text


def _binary(bin_dir: Path, name: str) -> Path:
    path = (Path(bin_dir) / name).resolve()
    if not path.is_file():
        raise FileNotFoundError(
            f"missing executable {path}; run: bash scripts/bootstrap.sh"
        )
    if not path.stat().st_mode & 0o111:
        raise PermissionError(f"backend is not executable: {path}")
    return path


def _graph_rows(case: CaseArtifact) -> tuple[tuple[int, int], ...]:
    rows: list[tuple[int, int]] = []
    with case.graph_path.open(encoding="ascii") as handle:
        for line in handle:
            left, right = map(int, line.split())
            rows.append((left, right))
    if len(rows) != case.edge_count:
        raise ValueError("graph row count differs from case")
    return tuple(rows)


def _parse_standard(
    case: CaseArtifact,
    path: Path,
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    support = [0] * case.edge_count
    truss = [0] * case.edge_count
    expected = _graph_rows(case)
    seen: set[int] = set()
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        required = {"edge_id", "u", "v", "initial_support", "trussness"}
        if not required.issubset(reader.fieldnames or ()):
            raise ValueError(f"invalid backend schema: {path}")
        for row in reader:
            edge_id = int(row["edge_id"])
            if edge_id in seen or not 0 <= edge_id < case.edge_count:
                raise ValueError(f"invalid or duplicate edge ID: {edge_id}")
            observed = tuple(sorted((int(row["u"]), int(row["v"]))))
            if observed != expected[edge_id]:
                raise ValueError(f"backend endpoints differ at edge {edge_id}")
            support[edge_id] = int(row["initial_support"])
            truss[edge_id] = int(row["trussness"])
            if support[edge_id] < 0 or truss[edge_id] < 0:
                raise ValueError("backend produced a negative value")
            seen.add(edge_id)
    if seen != set(range(case.edge_count)):
        raise ValueError("backend output does not cover every graph edge")
    return tuple(support), tuple(truss)


def _run_backend(
    *,
    case: CaseArtifact,
    length: int,
    output_dir: Path,
    bin_dir: Path,
    timeout_seconds: float | None,
) -> tuple[tuple[int, ...], tuple[int, ...], float, str]:
    with tempfile.TemporaryDirectory(prefix=f"c{length}-", dir=output_dir) as name:
        temporary = Path(name)
        native_output = temporary / "result.tsv"
        if length in {3, 4}:
            command = [
                str(_binary(bin_dir, "short_cycle_truss")),
                "--length",
                str(length),
                str(case.graph_path),
                str(native_output),
            ]
            backend = "exact_short_cycle_common_neighbor_bloom"
        elif length == 6:
            command = [
                str(_binary(bin_dir, "streaming_c6_truss")),
                str(case.graph_path),
                str(native_output),
            ]
            backend = "exact_streaming_simple_c6"
        else:
            raise ValueError("internal unsupported length")

        started = time.monotonic()
        try:
            completed = subprocess.run(
                command,
                cwd=temporary,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            elapsed = time.monotonic() - started
            atomic_write_text(
                output_dir / f"c{length}_backend.log",
                json.dumps(
                    {
                        "command": command,
                        "timeout_seconds": timeout_seconds,
                        "elapsed_seconds": elapsed,
                    },
                    indent=2,
                )
                + "\n\n--- stdout ---\n"
                + (error.stdout or "")
                + "\n--- stderr ---\n"
                + (error.stderr or ""),
            )
            raise RuntimeError(
                f"C{length} backend timed out; see "
                f"{output_dir / f'c{length}_backend.log'}"
            ) from error

        elapsed = time.monotonic() - started
        atomic_write_text(
            output_dir / f"c{length}_backend.log",
            json.dumps(
                {
                    "command": command,
                    "returncode": completed.returncode,
                    "elapsed_seconds": elapsed,
                },
                indent=2,
            )
            + "\n\n--- stdout ---\n"
            + completed.stdout
            + "\n--- stderr ---\n"
            + completed.stderr,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"C{length} backend failed; see "
                f"{output_dir / f'c{length}_backend.log'}"
            )
        support, truss = _parse_standard(case, native_output)
    return support, truss, elapsed, backend


def compute_cycle_decompositions(
    *,
    case_dir: Path,
    lengths: Iterable[int] = (3, 4, 5, 6),
    output_dir: Path | None = None,
    bin_dir: Path = DEFAULT_BIN_DIR,
    timeout_seconds: float | None = None,
    force: bool = False,
) -> dict[str, Path]:
    case = load_case(case_dir)
    requested = tuple(sorted(set(int(length) for length in lengths)))
    if not requested or any(length not in {3, 4, 5, 6} for length in requested):
        raise ValueError("lengths must be a nonempty subset of {3,4,5,6}")
    output_dir = Path(output_dir or case.root / "values").resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    output: dict[str, Path] = {}

    for length in requested:
        support_path = output_dir / f"c{length}_support.txt"
        truss_path = output_dir / f"c{length}_truss.txt"
        if not force:
            try:
                load_value_file(
                    case,
                    support_path,
                    expected_algorithm=f"c{length}_support",
                )
                load_value_file(
                    case,
                    truss_path,
                    expected_algorithm=f"c{length}_truss",
                )
                output[f"c{length}_support"] = support_path
                output[f"c{length}_truss"] = truss_path
                print(f"[resume] C{length} values already valid")
                continue
            except (FileNotFoundError, ValueError):
                pass

        if length == 5:
            result = compute_c5(
                case_dir=case.root,
                output_dir=output_dir,
                bin_dir=bin_dir,
                timeout_seconds=timeout_seconds,
                force=True,
            )
            output["c5_support"] = result["support"]
            output["c5_truss"] = result["truss"]
            continue

        support, truss, elapsed, backend = _run_backend(
            case=case,
            length=length,
            output_dir=output_dir,
            bin_dir=bin_dir,
            timeout_seconds=timeout_seconds,
        )
        write_value_file(
            case,
            support,
            output=support_path,
            algorithm=f"c{length}_support",
            backend=backend,
            elapsed_seconds=elapsed,
        )
        write_value_file(
            case,
            truss,
            output=truss_path,
            algorithm=f"c{length}_truss",
            backend=backend,
            elapsed_seconds=elapsed,
        )
        output[f"c{length}_support"] = support_path
        output[f"c{length}_truss"] = truss_path
        print(
            f"[C{length}] edges={case.edge_count} "
            f"positive_truss_edges={sum(value > 0 for value in truss)} "
            f"max_truss={max(truss, default=0)} "
            f"elapsed_seconds={elapsed:.3f}"
        )
    return output
