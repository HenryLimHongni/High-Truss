#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
result="${1:-${project_root}/results/run/HEADLINE_METRICS.csv}"
if [[ ! -f "${result}" ]]; then
  result="${project_root}/reference/HEADLINE_METRICS.csv"
fi
column -s, -t < "${result}" 2>/dev/null || sed -n '1,20p' "${result}"
