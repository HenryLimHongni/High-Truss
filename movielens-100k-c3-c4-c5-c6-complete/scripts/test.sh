#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
bash scripts/bootstrap.sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
