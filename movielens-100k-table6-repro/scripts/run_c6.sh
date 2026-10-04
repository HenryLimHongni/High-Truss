#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export C6_MODE=run
# No artificial result is assigned on skip/failure. INF means a recorded timeout.
export C6_TIMEOUT_SECONDS="${C6_TIMEOUT_SECONDS:-36400}"
bash "$ROOT/scripts/reproduce.sh"
