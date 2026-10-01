#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
output="${1:-${project_root}/results/reproduction}"
python_bin="${CROSSREC_PYTHON:-python3}"

if [[ -e "${output}" ]]; then
  echo "Output already exists; choose a new directory: ${output}" >&2
  exit 1
fi

bash "${project_root}/scripts/bootstrap.sh"
bash "${project_root}/scripts/download_foursquare_tsmc.sh"

PYTHONDONTWRITEBYTECODE=1 \
"${python_bin}" "${project_root}/run_foursquare_case.py" \
  --foursquare-tokyo \
    "${project_root}/data/foursquare_tsmc/dataset_TSMC2014_TKY.csv" \
  --output "${output}"

echo "Result: ${output}/HEADLINE_METRICS.csv"
