from __future__ import annotations

import csv
import json
import subprocess
import tempfile
import time
from pathlib import Path

from .artifacts import CaseArtifact, load_case, load_value_file, write_value_file
from .utils import atomic_write_text

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BIN_DIR = PROJECT_ROOT / "bin"


def _binary(bin_dir: Path) -> Path:
    path = (Path(bin_dir) / "c5_truss_sparse").resolve()
    if not path.is_file():
        raise FileNotFoundError(f"missing executable {path}; run: bash scripts/bootstrap.sh")
    if not path.stat().st_mode & 0o111:
        raise PermissionError(f"backend is not executable: {path}")
    return path


def _graph_rows(case: CaseArtifact) -> tuple[tuple[int, int], ...]:
    rows: list[tuple[int, int]] = []
    with case.graph_path.open(encoding="ascii") as handle:
        for line in handle:
            left, right = map(int, line.split())
            rows.append((left, right))
    return tuple(rows)


def _parse_backend(case: CaseArtifact, path: Path) -> tuple[tuple[int, ...], tuple[int, ...]]:
    support = [0] * case.edge_count
    truss = [0] * case.edge_count
    seen: set[int] = set()
    expected = _graph_rows(case)
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        required = {"edge_id", "u", "v", "initial_support", "trussness"}
        if not required.issubset(reader.fieldnames or ()):
            raise ValueError("C5 backend returned an invalid schema")
        for row in reader:
            edge_id = int(row["edge_id"])
            if edge_id in seen or not 0 <= edge_id < case.edge_count:
                raise ValueError(f"invalid or duplicate backend edge ID: {edge_id}")
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


def compute_c5(
    *,
    case_dir: Path,
    output_dir: Path | None = None,
    bin_dir: Path = DEFAULT_BIN_DIR,
    timeout_seconds: float | None = None,
    force: bool = False,
) -> dict[str, Path]:
    case = load_case(case_dir)
    output_dir = Path(output_dir or case.root / "values").resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    support_path = output_dir / "c5_support.txt"
    truss_path = output_dir / "c5_truss.txt"

    if not force:
        try:
            load_value_file(case, support_path, expected_algorithm="c5_support")
            load_value_file(case, truss_path, expected_algorithm="c5_truss")
            return {"support": support_path, "truss": truss_path}
        except (FileNotFoundError, ValueError):
            pass

    with tempfile.TemporaryDirectory(prefix="c5-", dir=output_dir) as temporary_name:
        temporary = Path(temporary_name)
        native_output = temporary / "result.tsv"
        command = [str(_binary(bin_dir)), str(case.graph_path), str(native_output)]
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
                output_dir / "c5_backend.log",
                json.dumps({"command": command, "timeout_seconds": timeout_seconds, "elapsed": elapsed}, indent=2)
                + "\n\n"
                + (error.stdout or "")
                + "\n"
                + (error.stderr or ""),
            )
            raise RuntimeError("C5 backend timed out; see c5_backend.log") from error
        elapsed = time.monotonic() - started
        atomic_write_text(
            output_dir / "c5_backend.log",
            json.dumps(
                {"command": command, "returncode": completed.returncode, "elapsed_seconds": elapsed},
                indent=2,
            )
            + "\n\n--- stdout ---\n"
            + completed.stdout
            + "\n--- stderr ---\n"
            + completed.stderr,
        )
        if completed.returncode != 0:
            raise RuntimeError(f"C5 backend failed; see {output_dir / 'c5_backend.log'}")
        support, truss = _parse_backend(case, native_output)

    write_value_file(
        case,
        support,
        output=support_path,
        algorithm="c5_support",
        backend="exact_sparse_A2",
        elapsed_seconds=elapsed,
    )
    write_value_file(
        case,
        truss,
        output=truss_path,
        algorithm="c5_truss",
        backend="exact_sparse_A2",
        elapsed_seconds=elapsed,
    )
    return {"support": support_path, "truss": truss_path}
