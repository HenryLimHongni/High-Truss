#!/usr/bin/env python3
"""Run protocol-locked, resumable screening with one directory per PDB ID."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import pandas as pd

from screening_common import (
    PREVIOUSLY_USED_IDS,
    SCREENING_PROTOCOL,
    candidate_manifest,
    parse_candidate_file,
    protocol_sha256,
)


EXPECTED_CUTOFFS = [6.5, 7.0]
EXPECTED_CYCLES = [3, 4, 5, 6]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json_atomic(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Array jobs can update the shared phase manifest concurrently. A unique
    # same-directory temporary name preserves atomic replace without races.
    temporary = path.with_name(
        f"{path.name}.{os.getpid()}.{time.time_ns()}.tmp"
    )
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def load_json(path: Path) -> Dict[str, object]:
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def stream_command(command: Sequence[str], log_path: Path, cwd: Path) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    rendered = " ".join(command)
    with log_path.open("a", encoding="utf-8") as log:
        header = f"\n[{utc_now()}] RUN {rendered}\n"
        log.write(header)
        log.flush()
        print(header.rstrip(), flush=True)
        process = subprocess.Popen(
            list(command),
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=os.environ.copy(),
        )
        assert process.stdout is not None
        for line in process.stdout:
            log.write(line)
            log.flush()
            print(line, end="", flush=True)
        return process.wait()


def dssp_version(executable: str) -> str:
    try:
        output = subprocess.check_output(
            [executable, "--version"],
            stderr=subprocess.STDOUT,
            text=True,
            timeout=30,
        )
        return output.strip().splitlines()[0] if output.strip() else "unknown"
    except Exception as exc:
        return f"unknown ({type(exc).__name__})"


def build_is_complete(data_dir: Path, pdb_id: str, expected_dssp_version: str) -> bool:
    required = [
        data_dir / "labels.csv",
        data_dir / "graph_summary.csv",
        data_dir / "edges_ca6p5.csv",
        data_dir / "edges_ca7p0.csv",
        data_dir / "run_parameters.json",
    ]
    if not all(path.exists() and path.stat().st_size > 0 for path in required):
        return False

    params = load_json(data_dir / "run_parameters.json")
    expected = {
        "pdb_ids": [pdb_id],
        "cutoffs": EXPECTED_CUTOFFS,
        "graph_mode": "full_intrachain",
        "same_chain_only": True,
        "min_contact_seq_sep": 2,
        "maximum_contact_seq_sep": None,
        "backbone_ca_max": 4.5,
        "min_chain_len": 20,
        "label_mode": "helix-core",
        "min_helix_len": 7,
        "helix_core_trim": 2,
        "exclude_noncore_H": True,
        "dssp_version": expected_dssp_version,
    }
    for key, value in expected.items():
        if params.get(key) != value:
            return False
    if sorted(params.get("exclude_ss", [])) != ["G", "I"]:
        return False

    try:
        graph_summary = pd.read_csv(data_dir / "graph_summary.csv")
        labels = pd.read_csv(data_dir / "labels.csv")
    except Exception:
        return False
    if labels.empty or set(labels["pdb_id"].astype(str).str.upper()) != {pdb_id}:
        return False
    cutoffs = sorted(graph_summary["cutoff"].astype(float).unique().tolist())
    return cutoffs == EXPECTED_CUTOFFS


def result_state(results_dir: Path, pdb_id: str) -> str:
    summary_path = results_dir / "summary_wide.csv"
    metrics_path = results_dir / "metrics_by_entry_cycle.csv"
    if not summary_path.exists() or not metrics_path.exists():
        return "missing"
    try:
        summary = pd.read_csv(summary_path)
        metrics = pd.read_csv(metrics_path)
    except Exception:
        return "invalid"

    required_summary = {
        "cutoff", "pdb_id",
        "C3_ROC", "C3_PR", "C4_ROC", "C4_PR",
        "C5_ROC", "C5_PR", "C6_ROC", "C6_PR",
    }
    if not required_summary.issubset(summary.columns):
        return "invalid"
    if any("support" in column.lower() for column in summary.columns):
        return "invalid"
    if set(summary["pdb_id"].astype(str).str.upper()) != {pdb_id}:
        return "invalid"
    if sorted(summary["cutoff"].astype(float).unique().tolist()) != EXPECTED_CUTOFFS:
        return "invalid"
    if len(metrics) != len(EXPECTED_CUTOFFS) * len(EXPECTED_CYCLES):
        return "invalid"
    if sorted(metrics["L"].astype(int).unique().tolist()) != EXPECTED_CYCLES:
        return "invalid"

    auc_columns = [f"C{L}_{metric}" for L in EXPECTED_CYCLES for metric in ("ROC", "PR")]
    if summary[auc_columns].isna().any().any():
        return "ineligible"
    return "complete"


def select_candidates(args: argparse.Namespace, all_candidates: Sequence[str]) -> List[str]:
    if args.pdb_id and args.candidate_index is not None:
        raise ValueError("use only one of --pdb-id and --candidate-index")
    if args.pdb_id:
        pdb_id = args.pdb_id.upper()
        if pdb_id not in all_candidates:
            raise ValueError(f"{pdb_id} is not in {args.candidate_file}")
        selected = [pdb_id]
    elif args.candidate_index is not None:
        if not 0 <= args.candidate_index < len(all_candidates):
            raise ValueError(
                f"--candidate-index {args.candidate_index} is outside 0..{len(all_candidates) - 1}"
            )
        selected = [all_candidates[args.candidate_index]]
    else:
        selected = list(all_candidates)
    if args.max_candidates is not None:
        selected = selected[: args.max_candidates]
    return selected


def run_one(
    *,
    args: argparse.Namespace,
    project_dir: Path,
    phase_dir: Path,
    pdb_id: str,
    current_dssp_version: str,
) -> str:
    per_pdb_dir = phase_dir / "per_pdb" / pdb_id
    data_dir = per_pdb_dir / "data"
    results_dir = per_pdb_dir / "results"
    log_path = per_pdb_dir / "run.log"
    status_path = per_pdb_dir / "status.json"
    status = load_json(status_path)

    current_result_state = result_state(results_dir, pdb_id)
    if (
        not args.force
        and status.get("protocol_sha256") == protocol_sha256()
        and status.get("dssp_version") == current_dssp_version
        and status.get("status") in {"complete", "ineligible"}
        and current_result_state in {"complete", "ineligible"}
    ):
        print(f"[SKIP] {pdb_id}: resumable result is already {current_result_state}")
        return current_result_state

    started = time.perf_counter()
    status = {
        "pdb_id": pdb_id,
        "phase": args.phase,
        "status": "running",
        "stage": "initializing",
        "protocol_sha256": protocol_sha256(),
        "dssp_version": current_dssp_version,
        "started_at": utc_now(),
        "error": None,
    }
    write_json_atomic(status_path, status)

    if not build_is_complete(data_dir, pdb_id, current_dssp_version):
        status["stage"] = "graph_and_labels"
        write_json_atomic(status_path, status)
        build_command = [
            args.python_bin,
            str(project_dir / "build_graph_labels.py"),
            "--dssp-exe", args.dssp_exe,
            "--pdb-ids", pdb_id,
            "--cutoffs", "6.5,7.0",
            "--cache-dir", str(Path(args.state_dir).resolve() / "pdb_cache"),
            "--out-dir", str(data_dir),
            "--min-contact-seq-sep", "2",
            "--backbone-ca-max", "4.5",
            "--min-chain-len", "20",
            "--label-mode", "helix-core",
            "--min-helix-len", "7",
            "--helix-core-trim", "2",
            "--exclude-noncore-H",
            "--exclude-ss", "G,I",
        ]
        return_code = stream_command(build_command, log_path, project_dir)
        if return_code != 0 or not build_is_complete(data_dir, pdb_id, current_dssp_version):
            error = f"graph/label stage failed or produced incomplete fixed-protocol data (exit={return_code})"
            failed_path = data_dir / "failed_entries.csv"
            if failed_path.exists():
                try:
                    failed = pd.read_csv(failed_path)
                    if not failed.empty:
                        error += f": {failed.iloc[0].get('error', '')}"
                except Exception:
                    pass
            status.update({
                "status": "failed",
                "stage": "graph_and_labels",
                "error": error,
                "completed_at": utc_now(),
                "elapsed_sec": time.perf_counter() - started,
            })
            write_json_atomic(status_path, status)
            print(f"[FAILED] {pdb_id}: {error}")
            return "failed"

    if result_state(results_dir, pdb_id) not in {"complete", "ineligible"} or args.force:
        status["stage"] = "cycle_truss_metrics"
        write_json_atomic(status_path, status)
        compute_command = [
            args.python_bin,
            str(project_dir / "compute_roc_pr.py"),
            "--data-dir", str(data_dir),
            "--out-dir", str(results_dir),
            "--pdb-ids", pdb_id,
            "--cutoffs", "6.5,7.0",
            "--cycle-lengths", "3,4,5,6",
            "--verify",
        ]
        return_code = stream_command(compute_command, log_path, project_dir)
        current_result_state = result_state(results_dir, pdb_id)
        if return_code != 0 or current_result_state not in {"complete", "ineligible"}:
            error = f"cycle-truss stage failed or produced invalid output (exit={return_code})"
            status.update({
                "status": "failed",
                "stage": "cycle_truss_metrics",
                "error": error,
                "completed_at": utc_now(),
                "elapsed_sec": time.perf_counter() - started,
            })
            write_json_atomic(status_path, status)
            print(f"[FAILED] {pdb_id}: {error}")
            return "failed"
    else:
        current_result_state = result_state(results_dir, pdb_id)

    final_status = "ineligible" if current_result_state == "ineligible" else "complete"
    status.update({
        "status": final_status,
        "stage": "done",
        "error": (
            "ROC-AUC/PR-AUC undefined because at least one cutoff lacks both label classes"
            if final_status == "ineligible" else None
        ),
        "completed_at": utc_now(),
        "elapsed_sec": time.perf_counter() - started,
    })
    write_json_atomic(status_path, status)
    print(f"[OK] {pdb_id}: {final_status}")
    return final_status


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Protocol-locked, resumable C3/C4/C5/C6 full-intrachain PDB screening."
    )
    parser.add_argument("--candidate-file", required=True)
    parser.add_argument("--phase", choices=["exploratory", "confirmation"], required=True)
    parser.add_argument("--state-dir", default="./screen_state")
    parser.add_argument("--python-bin", default=sys.executable)
    parser.add_argument("--dssp-exe", default="mkdssp")
    parser.add_argument("--pdb-id")
    parser.add_argument("--candidate-index", type=int)
    parser.add_argument("--max-candidates", type=int)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    project_dir = Path(__file__).resolve().parent
    candidate_file = Path(args.candidate_file).resolve()
    all_candidates = parse_candidate_file(candidate_file)
    selected = select_candidates(args, all_candidates)
    current_dssp_version = dssp_version(args.dssp_exe)

    overlaps = sorted(set(all_candidates) & set(PREVIOUSLY_USED_IDS))
    if overlaps:
        raise SystemExit(
            "candidate file contains previously used IDs; remove them or use a separate legacy run: "
            + ",".join(overlaps)
        )

    phase_dir = Path(args.state_dir).resolve() / args.phase
    phase_dir.mkdir(parents=True, exist_ok=True)
    manifest = candidate_manifest(candidate_file, all_candidates, args.phase)
    manifest["python_bin"] = args.python_bin
    manifest["dssp_exe"] = args.dssp_exe
    manifest["dssp_version"] = current_dssp_version
    write_json_atomic(phase_dir / "manifest.json", manifest)
    write_json_atomic(phase_dir / "protocol.json", SCREENING_PROTOCOL)

    counts = {"complete": 0, "ineligible": 0, "failed": 0}
    for index, pdb_id in enumerate(selected, start=1):
        print(f"\n[SCREEN] {args.phase} {index}/{len(selected)}: {pdb_id}")
        outcome = run_one(
            args=args,
            project_dir=project_dir,
            phase_dir=phase_dir,
            pdb_id=pdb_id,
            current_dssp_version=current_dssp_version,
        )
        counts[outcome] = counts.get(outcome, 0) + 1

    print(
        "\n[SCREEN DONE] "
        + " ".join(f"{key}={value}" for key, value in counts.items())
    )
    print("Run summarize_screening.py after this invocation (or after all array jobs finish).")


if __name__ == "__main__":
    main()
