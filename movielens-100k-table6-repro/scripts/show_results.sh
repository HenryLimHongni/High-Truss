#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RESULTS="${1:-${WORK_DIR:-$ROOT/work}/results}"
cat "$RESULTS/aggregate.pretty.log"
echo
if command -v column >/dev/null 2>&1; then
 column -t -s $'\t' "$RESULTS/table6.tsv"
else
 cat "$RESULTS/table6.tsv"
fi
