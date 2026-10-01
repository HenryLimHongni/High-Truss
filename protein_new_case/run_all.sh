#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

# Primary local workflow: exactly the selected 8 proteins and the paper table.
exec bash run_selected8_local.sh "$@"
