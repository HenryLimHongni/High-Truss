#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

ENV_PREFIX="${ENV_PREFIX:-${SCRIPT_DIR}/.selected8_env}" \
PACKAGE_CACHE="${PACKAGE_CACHE:-${SCRIPT_DIR}/.conda_pkgs}" \
bash "${SCRIPT_DIR}/setup_selected8_env.sh"

echo "[OK] Spartan environment is ready at ${ENV_PREFIX:-${SCRIPT_DIR}/.selected8_env}"
