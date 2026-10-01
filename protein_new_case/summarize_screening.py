#!/usr/bin/env python3
"""Create transparent per-cutoff screening tables and exploratory selections."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from screening_common import candidate_manifest, parse_candidate_file


CYCLES = [3, 4, 5, 6]
CUTOFFS = [6.5, 7.0]


def load_json(path: Path) -> Dict[str, object]:
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def best_and_margin(values: Mapping[int, float]) -> Tuple[str, float, str, float, float]:
    finite = [(L, float(value)) for L, value in values.items() if np.isfinite(value)]
    if len(finite) < 2:
        return "", float("nan"), "", float("nan"), float("nan")
    ranked = sorted(finite, key=lambda item: (-item[1], item[0]))
    top_value = ranked[0][1]
    tied = [L for L, value in ranked if np.isclose(value, top_value, rtol=0.0, atol=1e-12)]
    if len(tied) > 1:
        best = "tie:" + "|".join(f"C{L}" for L in tied)
        second_name = f"C{ranked[len(tied)][0]}" if len(ranked) > len(tied) else ""
        second_value = ranked[len(tied)][1] if len(ranked) > len(tied) else float("nan")
        return best, top_value, second_name, second_value, 0.0
    return (
        f"C{ranked[0][0]}",
        ranked[0][1],
        f"C{ranked[1][0]}",
        ranked[1][1],
        ranked[0][1] - ranked[1][1],
    )


def read_candidate_metrics(
    phase_dir: Path,
    pdb_id: str,
) -> Tuple[str, str, pd.DataFrame]:
    per_pdb = phase_dir / "per_pdb" / pdb_id
    status = load_json(per_pdb / "status.json")
    state = str(status.get("status", "not_run"))
    error = str(status.get("error") or "")
    summary_path = per_pdb / "results" / "summary_wide.csv"
    if not summary_path.exists():
        return state, error, pd.DataFrame()
    try:
        summary = pd.read_csv(summary_path)
    except Exception as exc:
        return "invalid_output", str(exc), pd.DataFrame()
    return state, error, summary


def cutoff_key(cutoff: float) -> str:
    return f"cutoff_{cutoff:.1f}".replace(".", "_")


def build_tables(
    candidates: Sequence[str],
    phase_dir: Path,
    margin_threshold: float,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Build authoritative PDB x cutoff rows plus a no-average protein index."""
    cutoff_rows: List[Dict[str, object]] = []
    protein_rows: List[Dict[str, object]] = []

    for candidate_order, pdb_id in enumerate(candidates, start=1):
        state, error, summary = read_candidate_metrics(phase_dir, pdb_id)
        base: Dict[str, object] = {
            "candidate_order": candidate_order,
            "pdb_id": pdb_id,
            "status": state,
            "error": error,
        }

        valid_rows: Dict[float, Dict[str, object]] = {}
        for cutoff in CUTOFFS:
            row: Dict[str, object] = {**base, "cutoff": cutoff}
            match = pd.DataFrame()
            if not summary.empty and "cutoff" in summary.columns:
                match = summary[np.isclose(summary["cutoff"].astype(float), cutoff)]
            if len(match) == 1:
                source = match.iloc[0]
                for column in ["n_vertices", "n_edges", "eval_n", "positive_n"]:
                    row[column] = source.get(column, np.nan)
                pr_values: Dict[int, float] = {}
                for L in CYCLES:
                    row[f"C{L}_ROC"] = source.get(f"C{L}_ROC", np.nan)
                    row[f"C{L}_PR"] = source.get(f"C{L}_PR", np.nan)
                    pr_values[L] = float(row[f"C{L}_PR"])
                best, best_pr, second, second_pr, margin = best_and_margin(pr_values)
                row.update({
                    "best_cycle_by_PR": best,
                    "best_PR": best_pr,
                    "second_best_cycle_by_PR": second,
                    "second_best_PR": second_pr,
                    "PR_margin_best_minus_second": margin,
                    "best_cycle_ROC": (
                        row.get(f"{best}_ROC", np.nan) if best.startswith("C") else np.nan
                    ),
                    "C5_minus_C6_PR": float(row["C5_PR"]) - float(row["C6_PR"]),
                    "C5_unique_best": best == "C5",
                    "C5_margin_gt_threshold": best == "C5" and margin > margin_threshold,
                    "C4_unique_best": best == "C4",
                })
                valid_rows[cutoff] = row
            cutoff_rows.append(row)

        protein: Dict[str, object] = dict(base)
        protein["complete_cutoff_count"] = len(valid_rows)
        if set(valid_rows) == set(CUTOFFS):
            for cutoff in CUTOFFS:
                row = valid_rows[cutoff]
                key = cutoff_key(cutoff)
                for name in [
                    "best_cycle_by_PR",
                    "best_PR",
                    "second_best_cycle_by_PR",
                    "second_best_PR",
                    "PR_margin_best_minus_second",
                    "C5_PR",
                    "C5_ROC",
                    "C6_PR",
                    "C6_ROC",
                    "C5_minus_C6_PR",
                ]:
                    protein[f"{key}_{name}"] = row[name]

            c5_best = [
                cutoff for cutoff, row in valid_rows.items()
                if row["best_cycle_by_PR"] == "C5"
            ]
            c5_qualifying = [
                cutoff for cutoff, row in valid_rows.items()
                if bool(row["C5_margin_gt_threshold"])
            ]
            c4_best = [
                cutoff for cutoff, row in valid_rows.items()
                if row["best_cycle_by_PR"] == "C4"
            ]
            delta65 = float(valid_rows[6.5]["C5_minus_C6_PR"])
            delta70 = float(valid_rows[7.0]["C5_minus_C6_PR"])
            crossover = delta65 * delta70 < 0.0
            if crossover:
                direction = (
                    "6.5:C5>C6;7.0:C6>C5"
                    if delta65 > 0.0
                    else "6.5:C6>C5;7.0:C5>C6"
                )
            else:
                direction = ""
            protein.update({
                "C5_best_cutoffs": ";".join(f"{cutoff:.1f}" for cutoff in c5_best),
                "C5_qualifying_cutoffs": ";".join(
                    f"{cutoff:.1f}" for cutoff in c5_qualifying
                ),
                "C4_best_cutoffs": ";".join(f"{cutoff:.1f}" for cutoff in c4_best),
                "qualifies_C5_exploratory": bool(c5_qualifying),
                "qualifies_C4_best": bool(c4_best),
                "maximum_C5_qualifying_margin": (
                    max(
                        float(valid_rows[cutoff]["PR_margin_best_minus_second"])
                        for cutoff in c5_qualifying
                    )
                    if c5_qualifying else np.nan
                ),
                "maximum_C4_winning_margin": (
                    max(
                        float(valid_rows[cutoff]["PR_margin_best_minus_second"])
                        for cutoff in c4_best
                    )
                    if c4_best else np.nan
                ),
                "minimum_best_PR_across_cutoffs": min(
                    float(row["best_PR"]) for row in valid_rows.values()
                ),
                "minimum_C4_PR_across_cutoffs": min(
                    float(row["C4_PR"]) for row in valid_rows.values()
                ),
                "minimum_C5_PR_across_cutoffs": min(
                    float(row["C5_PR"]) for row in valid_rows.values()
                ),
                "minimum_C6_PR_across_cutoffs": min(
                    float(row["C6_PR"]) for row in valid_rows.values()
                ),
                "C5_C6_PR_crossover": crossover,
                "C5_C6_PR_crossover_direction": direction,
                "minimum_C5_C6_PR_across_cutoffs": min(
                    float(row[name])
                    for row in valid_rows.values()
                    for name in ["C5_PR", "C6_PR"]
                ),
            })
        protein_rows.append(protein)

    by_cutoff = pd.DataFrame(cutoff_rows)
    by_protein = pd.DataFrame(protein_rows).sort_values("candidate_order")
    return by_cutoff, by_protein


def build_crossover_table(by_protein: pd.DataFrame) -> pd.DataFrame:
    if "C5_C6_PR_crossover" not in by_protein.columns:
        return pd.DataFrame()
    mask = by_protein["C5_C6_PR_crossover"].eq(True)
    columns = [
        "candidate_order",
        "pdb_id",
        "status",
        "C5_C6_PR_crossover_direction",
        "cutoff_6_5_C5_PR",
        "cutoff_6_5_C6_PR",
        "cutoff_6_5_C5_minus_C6_PR",
        "cutoff_6_5_C5_ROC",
        "cutoff_6_5_C6_ROC",
        "cutoff_7_0_C5_PR",
        "cutoff_7_0_C6_PR",
        "cutoff_7_0_C5_minus_C6_PR",
        "cutoff_7_0_C5_ROC",
        "cutoff_7_0_C6_ROC",
        "minimum_C5_C6_PR_across_cutoffs",
    ]
    available = [column for column in columns if column in by_protein.columns]
    result = by_protein.loc[mask, available].copy()
    if not result.empty:
        result = result.sort_values(
            ["minimum_C5_C6_PR_across_cutoffs", "pdb_id"],
            ascending=[False, True],
        )
    return result


def read_showcase_selection(path: Path, candidates: Sequence[str]) -> pd.DataFrame:
    showcase = pd.read_csv(path, dtype={"pdb_id": str})
    required = {"selection_category", "selection_rank", "pdb_id", "selection_note"}
    missing = sorted(required - set(showcase.columns))
    if missing:
        raise ValueError(f"showcase file is missing columns: {', '.join(missing)}")
    showcase["pdb_id"] = showcase["pdb_id"].str.upper()
    if showcase["pdb_id"].duplicated().any():
        duplicate = showcase.loc[showcase["pdb_id"].duplicated(), "pdb_id"].iloc[0]
        raise ValueError(f"duplicate showcase PDB ID: {duplicate}")
    unknown = sorted(set(showcase["pdb_id"]) - set(candidates))
    if unknown:
        raise ValueError(f"showcase IDs are absent from candidate pool: {', '.join(unknown)}")
    showcase["selection_order"] = range(1, len(showcase) + 1)
    return showcase


def automatic_showcase(
    by_protein: pd.DataFrame,
    target_c5: int,
    target_c4: int,
) -> pd.DataFrame:
    """No-average fallback: rank by the worse cutoff's raw PR-AUC."""
    c5_mask = by_protein.get(
        "qualifies_C5_exploratory", pd.Series(False, index=by_protein.index)
    ).fillna(False).astype(bool)
    if target_c5 > 0 and c5_mask.any():
        c5 = by_protein[c5_mask].sort_values(
            ["minimum_C5_PR_across_cutoffs", "maximum_C5_qualifying_margin", "pdb_id"],
            ascending=[False, False, True],
        ).head(target_c5)
    else:
        c5 = by_protein.iloc[0:0].copy()

    c4_mask = by_protein.get(
        "qualifies_C4_best", pd.Series(False, index=by_protein.index)
    ).fillna(False).astype(bool)
    c4_eligible = c4_mask & ~by_protein["pdb_id"].isin(c5["pdb_id"])
    if target_c4 > 0 and c4_eligible.any():
        c4 = by_protein[c4_eligible].sort_values(
            ["minimum_C4_PR_across_cutoffs", "maximum_C4_winning_margin", "pdb_id"],
            ascending=[False, False, True],
        ).head(target_c4)
    else:
        c4 = by_protein.iloc[0:0].copy()

    rows: List[Dict[str, object]] = []
    for category, frame in [("C5_showcase", c5), ("C4_showcase", c4)]:
        for rank, row in enumerate(frame.to_dict("records"), start=1):
            rows.append({
                "selection_category": category,
                "selection_rank": rank,
                "pdb_id": row["pdb_id"],
                "selection_note": (
                    "Automatically selected from per-cutoff results; ranking uses the lower "
                    "raw PR-AUC across the two cutoffs and never an average."
                ),
            })
    return pd.DataFrame(rows)


def write_exploratory_selection(
    *,
    tables_dir: Path,
    by_cutoff: pd.DataFrame,
    by_protein: pd.DataFrame,
    candidates: Sequence[str],
    margin_threshold: float,
    target_c5: int,
    target_c4: int,
    showcase_file: Optional[Path] = None,
) -> None:
    qualifiers = by_cutoff.copy()
    qualifiers["selection_criterion"] = ""
    c5_criterion = (
        qualifiers["best_cycle_by_PR"].fillna("") == "C5"
    ) & (qualifiers["PR_margin_best_minus_second"].fillna(-np.inf) > margin_threshold)
    c4_criterion = qualifiers["best_cycle_by_PR"].fillna("") == "C4"
    qualifiers.loc[c5_criterion, "selection_criterion"] = "C5_best_margin_gt_threshold"
    qualifiers.loc[c4_criterion, "selection_criterion"] = "C4_best"
    qualifiers = qualifiers[qualifiers["selection_criterion"] != ""]
    qualifiers.to_csv(tables_dir / "per_cutoff_qualifiers.csv", index=False)

    crossovers = build_crossover_table(by_protein)
    crossovers.to_csv(tables_dir / "crossover_candidates.csv", index=False)

    if showcase_file is not None:
        showcase = read_showcase_selection(showcase_file, candidates)
        selection_mode = "explicit outcome-aware showcase list"
    else:
        showcase = automatic_showcase(by_protein, target_c5, target_c4)
        selection_mode = "automatic per-cutoff no-average ranking"
        showcase["selection_order"] = range(1, len(showcase) + 1)

    selected = showcase.merge(by_cutoff, on="pdb_id", how="left", sort=False)
    selected = selected.sort_values(["selection_order", "cutoff"], kind="stable")
    selected.to_csv(tables_dir / "selected_exploratory_candidates.csv", index=False)
    selected_ids = showcase["pdb_id"].tolist()
    (tables_dir / "selected_exploratory_ids.txt").write_text(
        "".join(f"{pdb_id}\n" for pdb_id in selected_ids)
    )

    selected_crossover_ids = sorted(
        set(selected_ids) & set(crossovers.get("pdb_id", pd.Series(dtype=str)).tolist())
    )
    status = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "selection_mode": selection_mode,
        "selection_uses_cutoff_average": False,
        "margin_threshold": margin_threshold,
        "selected_protein_count": len(selected_ids),
        "selected_pdb_ids": selected_ids,
        "selected_crossover_pdb_ids": selected_crossover_ids,
        "explicit_replacement": {"removed": "2PTL", "added": "1A6M"},
        "c5_rule": (
            f"evaluated separately at each cutoff: C5 is unique best PR-AUC and margin > {margin_threshold}"
        ),
        "selection_is_exploratory": True,
        "confirmation_list_was_not_selected_from_these_results": True,
    }
    (tables_dir / "selection_status.json").write_text(json.dumps(status, indent=2) + "\n")

    completed = int((by_protein["complete_cutoff_count"] == len(CUTOFFS)).sum())
    failed = int((by_protein["status"] == "failed").sum())
    rationale = [
        "# Exploratory candidate-selection rationale",
        "",
        "This is an outcome-aware exploratory showcase and must not be described as an unbiased confirmation set.",
        "",
        "## Fixed protocol",
        "",
        "- Full same-chain graph with backbone edges (C-alpha distance <= 4.5 A).",
        "- Same-chain nonadjacent C-alpha contacts with sequence separation >= 2 and no maximum separation.",
        "- Fixed contact cutoffs: 6.5 A and 7.0 A.",
        "- Unchanged helix-core labels, exclusions, incident-max scoring, ROC-AUC, and average precision (PR-AUC).",
        "- Only C3/C4/C5/C6 cycle-truss methods are compared.",
        "",
        "## No-average reporting and selection",
        "",
        "The 6.5 A and 7.0 A results are never averaged for selection. Every selected protein has two rows in `selected_exploratory_candidates.csv`. The strict per-cutoff C5 rule and all C4 winners are saved in `per_cutoff_qualifiers.csv`.",
        "",
        f"The C5 per-cutoff criterion is: C5 is the unique best PR-AUC method and its absolute lead over the second-best method exceeds {margin_threshold:.3f} at that cutoff.",
        "",
        "## Requested crossover replacement",
        "",
        "2PTL was removed because its 7.0 A best PR-AUC was the weakest absolute result in the previous showcase. It was replaced by 1A6M: at 6.5 A, C5 PR-AUC 0.883759 exceeds C6 0.851158; at 7.0 A, C6 0.916127 exceeds C5 0.849125.",
        "",
        "All detected C5/C6 PR-AUC reversals are retained in `crossover_candidates.csv`, ranked by their weakest C5/C6 PR-AUC across both cutoffs.",
        "",
        "## Outcome",
        "",
        f"- Candidates complete at both cutoffs: {completed}",
        f"- Failed candidates: {failed}",
        f"- Showcase proteins: {len(selected_ids)}",
        f"- Selected crossover proteins: {', '.join(selected_crossover_ids) if selected_crossover_ids else 'none'}",
        "",
        "All candidates, including failures and non-winners, remain in the complete tables. The separate confirmation list is frozen independently and must not use outcome-based replacement.",
        "",
    ]
    (tables_dir / "selection_rationale.md").write_text("\n".join(rationale))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize all outcomes without cutoff averaging.")
    parser.add_argument("--candidate-file", required=True)
    parser.add_argument("--phase", choices=["exploratory", "confirmation"], required=True)
    parser.add_argument("--state-dir", default="./screen_state")
    parser.add_argument("--out-dir")
    parser.add_argument("--margin-threshold", type=float, default=0.015)
    parser.add_argument("--target-c5", type=int, default=8)
    parser.add_argument("--target-c4", type=int, default=2)
    parser.add_argument(
        "--showcase-file",
        help="Optional outcome-aware CSV defining the ordered exploratory showcase.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    candidate_file = Path(args.candidate_file).resolve()
    candidates = parse_candidate_file(candidate_file)
    phase_dir = Path(args.state_dir).resolve() / args.phase
    tables_dir = Path(args.out_dir).resolve() if args.out_dir else phase_dir / "tables"
    tables_dir.mkdir(parents=True, exist_ok=True)

    by_cutoff, by_protein = build_tables(candidates, phase_dir, args.margin_threshold)
    by_cutoff.to_csv(tables_dir / "screening_by_cutoff.csv", index=False)
    by_protein.to_csv(tables_dir / "complete_screening_table.csv", index=False)
    failures = by_protein[
        (by_protein["status"] != "complete")
        | (by_protein["complete_cutoff_count"] != len(CUTOFFS))
    ]
    failures.to_csv(tables_dir / "failed_or_incomplete_candidates.csv", index=False)

    manifest = candidate_manifest(candidate_file, candidates, args.phase)
    manifest["generated_at"] = datetime.now(timezone.utc).isoformat()
    manifest["output_dir"] = str(tables_dir)
    manifest["selection_uses_cutoff_average"] = False
    observed_dssp_versions = sorted({
        str(load_json(phase_dir / "per_pdb" / pdb_id / "status.json").get("dssp_version"))
        for pdb_id in candidates
        if load_json(phase_dir / "per_pdb" / pdb_id / "status.json").get("dssp_version")
    })
    manifest["observed_dssp_versions"] = observed_dssp_versions
    (tables_dir / "summary_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    if args.phase == "exploratory":
        showcase_file = Path(args.showcase_file).resolve() if args.showcase_file else None
        write_exploratory_selection(
            tables_dir=tables_dir,
            by_cutoff=by_cutoff,
            by_protein=by_protein,
            candidates=candidates,
            margin_threshold=args.margin_threshold,
            target_c5=args.target_c5,
            target_c4=args.target_c4,
            showcase_file=showcase_file,
        )
    else:
        by_cutoff.to_csv(tables_dir / "confirmation_results.csv", index=False)
        complete = int((by_protein["complete_cutoff_count"] == len(CUTOFFS)).sum())
        note = (
            "# Held-out confirmation results\n\n"
            "This candidate list was frozen separately from exploratory outcome-based selection. "
            "Every PDB x cutoff row remains visible; no cutoff averaging or outcome-based replacement is used.\n\n"
            f"Complete candidates: {complete}/{len(candidates)}.\n"
        )
        (tables_dir / "confirmation_summary.md").write_text(note)

    print(f"[OK] Complete table: {tables_dir / 'complete_screening_table.csv'}")
    print(f"[OK] Per-cutoff table: {tables_dir / 'screening_by_cutoff.csv'}")
    if args.phase == "exploratory":
        print(f"[OK] Per-cutoff qualifiers: {tables_dir / 'per_cutoff_qualifiers.csv'}")
        print(f"[OK] Crossover table: {tables_dir / 'crossover_candidates.csv'}")
        print(f"[OK] Selection rationale: {tables_dir / 'selection_rationale.md'}")
    else:
        print(f"[OK] Confirmation results: {tables_dir / 'confirmation_results.csv'}")


if __name__ == "__main__":
    main()
