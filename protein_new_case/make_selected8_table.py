#!/usr/bin/env python3
"""Create and verify the paper-ready selected-8 LaTeX tables."""

from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple


CYCLES = (3, 4, 5, 6)
CUTOFFS = (6.5, 7.0)
COUNT_COLUMNS = ("n_vertices", "n_edges", "eval_n", "positive_n")
METRIC_COLUMNS = tuple(
    f"C{cycle}_{metric}" for cycle in CYCLES for metric in ("ROC", "PR")
)
REQUIRED_COLUMNS = ("pdb_id", "cutoff") + COUNT_COLUMNS + METRIC_COLUMNS


def read_ids(path: Path) -> List[str]:
    ids = [line.split("#", 1)[0].strip().upper() for line in path.read_text().splitlines()]
    ids = [value for value in ids if value]
    if len(ids) != 8 or len(set(ids)) != 8:
        raise ValueError(f"expected exactly 8 unique PDB IDs in {path}, found {len(ids)}")
    return ids


def read_csv_rows(path: Path) -> List[Dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f"no rows found in {path}")
    missing = set(REQUIRED_COLUMNS) - set(rows[0])
    if missing:
        raise ValueError(f"{path} is missing columns: {sorted(missing)}")
    return rows


def load_actual_rows(state_dir: Path, phase: str, pdb_ids: Sequence[str]) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    for pdb_id in pdb_ids:
        per_pdb = state_dir / phase / "per_pdb" / pdb_id
        summary_path = per_pdb / "results" / "summary_wide.csv"
        if not summary_path.exists():
            raise FileNotFoundError(f"missing result for {pdb_id}: {summary_path}")
        with summary_path.open(newline="", encoding="utf-8") as handle:
            summary_rows = list(csv.DictReader(handle))
        if len(summary_rows) != 2:
            raise ValueError(f"{summary_path} must contain exactly two cutoff rows")
        for row in summary_rows:
            row = dict(row)
            row["pdb_id"] = pdb_id
            missing = set(REQUIRED_COLUMNS) - set(row)
            if missing:
                raise ValueError(f"{summary_path} is missing columns: {sorted(missing)}")
            rows.append(row)
    return ordered_rows(rows, pdb_ids)


def key(row: Mapping[str, str]) -> Tuple[str, float]:
    return str(row["pdb_id"]).upper(), float(row["cutoff"])


def ordered_rows(rows: Iterable[Dict[str, str]], pdb_ids: Sequence[str]) -> List[Dict[str, str]]:
    by_key: Dict[Tuple[str, float], Dict[str, str]] = {}
    for row in rows:
        row_key = key(row)
        if row_key in by_key:
            raise ValueError(f"duplicate result row: {row_key}")
        by_key[row_key] = row
    expected_keys = {(pdb_id, cutoff) for pdb_id in pdb_ids for cutoff in CUTOFFS}
    if set(by_key) != expected_keys:
        missing = sorted(expected_keys - set(by_key))
        extra = sorted(set(by_key) - expected_keys)
        raise ValueError(f"result-key mismatch; missing={missing}, extra={extra}")
    return [by_key[(pdb_id, cutoff)] for pdb_id in pdb_ids for cutoff in CUTOFFS]


def validate_numeric_rows(rows: Sequence[Mapping[str, str]]) -> None:
    for row in rows:
        row_key = key(row)
        for column in COUNT_COLUMNS:
            value = float(row[column])
            if not math.isfinite(value) or int(value) != value or value < 0:
                raise ValueError(f"invalid count {row_key} {column}={row[column]}")
        for column in METRIC_COLUMNS:
            value = float(row[column])
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError(f"invalid AUC {row_key} {column}={row[column]}")


def compare_to_reference(
    actual: Sequence[Mapping[str, str]],
    expected: Sequence[Mapping[str, str]],
    tolerance: float,
) -> List[str]:
    expected_by_key = {key(row): row for row in expected}
    differences: List[str] = []
    for row in actual:
        row_key = key(row)
        reference = expected_by_key[row_key]
        for column in COUNT_COLUMNS:
            actual_value = int(float(row[column]))
            expected_value = int(float(reference[column]))
            if actual_value != expected_value:
                differences.append(
                    f"{row_key} {column}: actual={actual_value} expected={expected_value}"
                )
        for column in METRIC_COLUMNS:
            actual_value = float(row[column])
            expected_value = float(reference[column])
            if not math.isclose(actual_value, expected_value, rel_tol=0.0, abs_tol=tolerance):
                differences.append(
                    f"{row_key} {column}: actual={actual_value:.17g} "
                    f"expected={expected_value:.17g} diff={actual_value - expected_value:.3g}"
                )
    return differences


def latex_metric(value: float, best: float) -> str:
    rendered = f"{value:.4f}"
    if math.isclose(value, best, rel_tol=0.0, abs_tol=1e-15):
        return rf"\textbf{{{rendered}}}"
    return rendered


def render_data_row(row: Mapping[str, str]) -> str:
    roc_values = [float(row[f"C{cycle}_ROC"]) for cycle in CYCLES]
    pr_values = [float(row[f"C{cycle}_PR"]) for cycle in CYCLES]
    values: List[str] = []
    for cycle in CYCLES:
        values.append(latex_metric(float(row[f"C{cycle}_ROC"]), max(roc_values)))
        values.append(latex_metric(float(row[f"C{cycle}_PR"]), max(pr_values)))
    prefix = [
        rf"\texttt{{{str(row['pdb_id']).upper()}}}",
        f"{int(float(row['n_vertices'])):,}",
        f"{int(float(row['n_edges'])):,}",
    ]
    return " & ".join(prefix + values) + r" \\"


def render_half(cutoff: float, rows: Sequence[Mapping[str, str]]) -> str:
    suffix = "65" if cutoff == 6.5 else "70"
    data_rows = "\n".join(render_data_row(row) for row in rows if float(row["cutoff"]) == cutoff)
    return rf"""\begin{{minipage}}[t]{{0.49\textwidth}}
\vspace{{0pt}}
\centering
\captionof{{table}}{{Helix-core detection at {cutoff:.1f}~\AA{{}}.}}
\label{{tab:rebuttal-case-study-ca{suffix}}}
\begin{{adjustbox}}{{max width=\linewidth}}
\begin{{tabular}}{{@{{}}lrrcccccccc@{{}}}}
\toprule
PDB & $|V|$ & $|E|$
& \multicolumn{{2}}{{c}}{{$C_3$}}
& \multicolumn{{2}}{{c}}{{$C_4$}}
& \multicolumn{{2}}{{c}}{{$C_5$}}
& \multicolumn{{2}}{{c}}{{$C_6$}} \\
\cmidrule(lr){{4-5}}
\cmidrule(lr){{6-7}}
\cmidrule(lr){{8-9}}
\cmidrule(lr){{10-11}}
& & & ROC & PR & ROC & PR & ROC & PR & ROC & PR \\
\midrule
{data_rows}
\bottomrule
\end{{tabular}}
\end{{adjustbox}}
\end{{minipage}}"""


def render_table(rows: Sequence[Mapping[str, str]]) -> str:
    left = render_half(6.5, rows)
    right = render_half(7.0, rows)
    return rf"""\begin{{table*}}[t]
\centering
\scriptsize
\captionsetup{{font=scriptsize}}
\setlength{{\tabcolsep}}{{1.2pt}}
\renewcommand{{\arraystretch}}{{0.82}}

{left}
\hfill
{right}

\end{{table*}}
"""


def write_combined_csv(path: Path, rows: Sequence[Mapping[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(REQUIRED_COLUMNS))
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row[column] for column in REQUIRED_COLUMNS})


def parse_args() -> argparse.Namespace:
    project = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", default=str(project / "selected8_state"))
    parser.add_argument("--phase", default="exploratory")
    parser.add_argument(
        "--candidate-file", default=str(project / "candidate_lists" / "selected8.txt")
    )
    parser.add_argument(
        "--reference", default=str(project / "reference_results" / "selected8_expected.csv")
    )
    parser.add_argument("--out-dir", default=str(project / "selected8_outputs"))
    parser.add_argument("--tolerance", type=float, default=1e-12)
    parser.add_argument("--skip-reference-check", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    pdb_ids = read_ids(Path(args.candidate_file))
    actual = load_actual_rows(Path(args.state_dir), args.phase, pdb_ids)
    validate_numeric_rows(actual)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    comparison_path = out_dir / "reference_comparison.txt"
    if not args.skip_reference_check:
        expected = ordered_rows(read_csv_rows(Path(args.reference)), pdb_ids)
        validate_numeric_rows(expected)
        differences = compare_to_reference(actual, expected, args.tolerance)
        if differences:
            comparison_path.write_text("FAILED\n" + "\n".join(differences) + "\n")
            raise SystemExit(
                f"fresh results differ from the locked reference in {len(differences)} cells; "
                f"see {comparison_path}"
            )
        comparison_path.write_text(
            "PASS\nAll 16 PDB-by-cutoff rows and all required cells match the locked "
            f"reference within absolute tolerance {args.tolerance:g}.\n"
        )

    combined_path = out_dir / "selected8_metrics.csv"
    table_path = out_dir / "helix_core_selected8_table.tex"
    write_combined_csv(combined_path, actual)
    table = render_table(actual)
    table_path.write_text(table, encoding="utf-8")

    print(f"[VERIFIED] 8 proteins x 2 cutoffs; reference check passed")
    print(f"[OUTPUT] Metrics: {combined_path}")
    print(f"[OUTPUT] LaTeX table: {table_path}")
    print("\n===== BEGIN LATEX TABLE =====")
    print(table, end="")
    print("===== END LATEX TABLE =====")


if __name__ == "__main__":
    main()
