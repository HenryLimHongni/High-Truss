#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
results="${1:-$root/work/all_cycle_results}"
column -t -s $'\t' "$results/aggregate.tsv"
