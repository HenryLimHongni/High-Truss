#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python_bin="${CROSSREC_PYTHON:-python3}"

bash "${project_root}/scripts/bootstrap.sh"
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH="${project_root}/src:${project_root}" \
  "${python_bin}" -m unittest discover -s "${project_root}/tests" -v
