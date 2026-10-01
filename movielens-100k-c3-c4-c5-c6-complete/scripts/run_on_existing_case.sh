#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
python_bin="${CYCLECASE_PYTHON:-python3}"

if [[ $# -lt 2 || $# -gt 2 ]]; then
  echo "usage: $0 CASE_DIR RESULTS_DIR" >&2
  exit 2
fi
case_dir="$(realpath "$1")"
results_dir="$(realpath -m "$2")"
short_timeout="${SHORT_TIMEOUT_SECONDS:-86400}"
c6_timeout="${C6_TIMEOUT_SECONDS:-604800}"
top_users="${TOP_USERS:-500}"
cutoffs="${CUTOFFS:-5,10}"
ranking_limit="${RANKING_LIMIT:-10}"

mkdir -p "$results_dir"
bash scripts/bootstrap.sh 2>&1 | tee "$results_dir/bootstrap.log"

"$python_bin" cyclecase.py decompose-all \
  --case "$case_dir" --lengths 3,4,5 \
  --timeout-seconds "$short_timeout" \
  2>&1 | tee "$results_dir/decompose_c3_c4_c5.log"

"$python_bin" cyclecase.py decompose-all \
  --case "$case_dir" --lengths 6 \
  --timeout-seconds "$c6_timeout" \
  2>&1 | tee "$results_dir/decompose_c6.log"

"$python_bin" cyclecase.py evaluate-all \
  --case "$case_dir" \
  --output-dir "$results_dir" \
  --top-users "$top_users" \
  --cutoffs "$cutoffs" \
  --ranking-limit "$ranking_limit" \
  2>&1 | tee "$results_dir/evaluation.log"

column -t -s $'\t' "$results_dir/aggregate.tsv" \
  | tee "$results_dir/aggregate.pretty.log"
