#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
python_bin="${CYCLECASE_PYTHON:-python3}"
case_dir="${CASE_DIR:-$root/work/movielens_case}"
results_dir="${RESULTS_DIR:-$root/work/all_cycle_results}"
timeout_short="${SHORT_TIMEOUT_SECONDS:-86400}"
timeout_c6="${C6_TIMEOUT_SECONDS:-604800}"
top_users="${TOP_USERS:-500}"
cutoffs="${CUTOFFS:-5,10}"
ranking_limit="${RANKING_LIMIT:-10}"

mkdir -p "$root/work" "$results_dir"

bash scripts/bootstrap.sh 2>&1 | tee "$root/work/bootstrap.log"
bash scripts/download_data.sh 2>&1 | tee "$root/work/download.log"

if [[ "${RESET_CASE:-0}" == "1" ]]; then
  rm -rf "$case_dir"
fi

if [[ ! -f "$case_dir/case.json" ]]; then
  "$python_bin" cyclecase.py build \
    --ratings data/movielens_100k/ml-100k/u.data \
    --movies data/movielens_100k/ml-100k/u.item \
    --train-quantile 0.80 \
    --ii-top-k 20 \
    --genre-weight 0.8 \
    --year-weight 0.2 \
    --ii-similarity-threshold 0.4 \
    --output "$case_dir" \
    2>&1 | tee "$root/work/movielens_build.log"
else
  echo "[resume] reusing case: $case_dir" | tee "$root/work/movielens_build.log"
  "$python_bin" cyclecase.py inspect --case "$case_dir" \
    2>&1 | tee -a "$root/work/movielens_build.log"
fi

"$python_bin" cyclecase.py preflight --case "$case_dir" \
  2>&1 | tee "$root/work/movielens_preflight.log"

# C3/C4/C5 are separated from C6 so a long C6 run does not discard completed
# shorter-cycle decompositions.
"$python_bin" cyclecase.py decompose-all \
  --case "$case_dir" \
  --lengths 3,4,5 \
  --timeout-seconds "$timeout_short" \
  ${FORCE_DECOMPOSE:+--force} \
  2>&1 | tee "$root/work/decompose_c3_c4_c5.log"

"$python_bin" cyclecase.py decompose-all \
  --case "$case_dir" \
  --lengths 6 \
  --timeout-seconds "$timeout_c6" \
  ${FORCE_C6:+--force} \
  2>&1 | tee "$root/work/decompose_c6.log"

rm -rf "$results_dir"
mkdir -p "$results_dir"

"$python_bin" cyclecase.py evaluate-all \
  --case "$case_dir" \
  --output-dir "$results_dir" \
  --top-users "$top_users" \
  --cutoffs "$cutoffs" \
  --ranking-limit "$ranking_limit" \
  2>&1 | tee "$results_dir/evaluation.log"

column -t -s $'\t' "$results_dir/aggregate.tsv" \
  | tee "$results_dir/aggregate.pretty.log"

echo "Results: $results_dir"
