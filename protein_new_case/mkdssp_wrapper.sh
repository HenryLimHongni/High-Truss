#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DSSP_ENV_PREFIX="${DSSP_ENV_PREFIX:-${SCRIPT_DIR}/.selected8_env}"
DSSP_BIN="${DSSP_BIN:-${DSSP_ENV_PREFIX}/bin/mkdssp}"

if [[ -d "${DSSP_ENV_PREFIX}/share/libcifpp" ]]; then
  export LIBCIFPP_DATA_DIR="${LIBCIFPP_DATA_DIR:-${DSSP_ENV_PREFIX}/share/libcifpp}"
fi

# Prefer libraries from the same isolated conda prefix. This avoids loading an
# incompatible system/module libmcfp, which otherwise causes a symbol lookup
# error on some Linux clusters.
case "$(uname -s)" in
  Linux)
    export LD_LIBRARY_PATH="${DSSP_ENV_PREFIX}/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
    ;;
  Darwin)
    export DYLD_FALLBACK_LIBRARY_PATH="${DSSP_ENV_PREFIX}/lib${DYLD_FALLBACK_LIBRARY_PATH:+:${DYLD_FALLBACK_LIBRARY_PATH}}"
    ;;
esac

exec "${DSSP_BIN}" "$@"
