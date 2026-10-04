#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export CYCLECASE_PYTHON="${CYCLECASE_PYTHON:-python3}"
mkdir -p "$ROOT/work/test-logs"
bash scripts/bootstrap.sh
"$CYCLECASE_PYTHON" -m unittest discover -s tests -v 2>&1 | tee work/test-logs/unit_tests.log
