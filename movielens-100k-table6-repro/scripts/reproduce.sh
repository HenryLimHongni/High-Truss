#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
WORK="${WORK_DIR:-$ROOT/work}"
mkdir -p "$WORK/logs"
export CYCLECASE_PYTHON="${CYCLECASE_PYTHON:-python3}"
bash "$ROOT/scripts/bootstrap.sh" 2>&1 | tee "$WORK/logs/bootstrap.log"
args=(--work-dir "$WORK" --top-users "${TOP_USERS:-500}" --c6 "${C6_MODE:-skip}" --c6-timeout "${C6_TIMEOUT_SECONDS:-36400}")
if [[ -n "${MOVIELENS_ARCHIVE:-}" ]]; then args+=(--archive "$MOVIELENS_ARCHIVE"); fi
if [[ "${CHECK_REFERENCE:-1}" == 0 ]]; then args+=(--no-reference-check); fi
"$CYCLECASE_PYTHON" -u "$ROOT/run.py" run "${args[@]}"
echo
echo "Results: $WORK/results/table6.tsv"
cat "$WORK/results/table6.tsv"
