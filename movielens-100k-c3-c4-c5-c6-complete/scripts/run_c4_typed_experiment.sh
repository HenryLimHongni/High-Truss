#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${project_root}"

if [[ $# -lt 2 || $# -gt 2 ]]; then
  echo "usage: bash scripts/run_c4_typed_experiment.sh CASE_DIR OUTPUT_DIR" >&2
  exit 2
fi

case_dir="$1"
output_dir="$2"
python_bin="${CYCLECASE_PYTHON:-python3}"
top_users="${TOP_USERS:-500}"

mkdir -p "${output_dir}"

"${python_bin}" c4typedcase.py \
  --case "${case_dir}" \
  --output-dir "${output_dir}" \
  --top-users "${top_users}" \
  --cutoff 5 \
  --ranking-limit 5 \
  2>&1 | tee "${output_dir}/evaluation.log"

column -t -s $'\t' "${output_dir}/aggregate.tsv" \
  | tee "${output_dir}/aggregate.pretty.log"
