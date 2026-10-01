#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

ENV_PREFIX="${ENV_PREFIX:-${SCRIPT_DIR}/.selected8_env}"
PACKAGE_CACHE="${PACKAGE_CACHE:-${SCRIPT_DIR}/.conda_pkgs}"
ENV_MARKER="${ENV_PREFIX}/.protein_new_case_env_v2"
mkdir -p "${PACKAGE_CACHE}"

NEEDS_ENV_SETUP=0
if [[ ! -x "${ENV_PREFIX}/bin/python" || ! -x "${ENV_PREFIX}/bin/mkdssp" ]] || \
   ! compgen -G "${ENV_PREFIX}/conda-meta/dssp-4.5.8-*.json" >/dev/null || \
   [[ ! -f "${ENV_MARKER}" ]]; then
  NEEDS_ENV_SETUP=1
fi

if [[ "${NEEDS_ENV_SETUP}" == "1" ]]; then
  echo "[SETUP] Creating/updating isolated Python 3.12 + DSSP 4.5.8 environment"
  if command -v conda >/dev/null 2>&1; then
    ENV_TOOL="conda"
    ENV_TOOL_KIND="conda"
    echo "[SETUP] Using conda"
  elif [[ "$(uname -s)" == "Darwin" && "$(uname -m)" == "arm64" && \
          -x "${SCRIPT_DIR}/tools/micromamba-darwin-arm64" ]]; then
    ENV_TOOL="${SCRIPT_DIR}/tools/micromamba-darwin-arm64"
    ENV_TOOL_KIND="micromamba"
    export MAMBA_ROOT_PREFIX="${MAMBA_ROOT_PREFIX:-${SCRIPT_DIR}/.micromamba_root}"
    mkdir -p "${MAMBA_ROOT_PREFIX}"
    echo "[SETUP] conda was not found; using bundled micromamba"
  else
    echo "[ERROR] No supported environment manager was found." >&2
    echo "This package bundles micromamba for Apple-silicon Macs." >&2
    echo "On other systems, install conda or set both PYTHON_BIN and DSSP_EXE." >&2
    exit 1
  fi

  ENV_ACTION="create"
  if [[ -d "${ENV_PREFIX}/conda-meta" ]]; then
    ENV_ACTION="install"
  fi

  if [[ "${ENV_TOOL_KIND}" == "conda" ]]; then
    CONDA_PKGS_DIRS="${PACKAGE_CACHE}" "${ENV_TOOL}" "${ENV_ACTION}" \
      --prefix "${ENV_PREFIX}" \
      --channel conda-forge \
      python=3.12 dssp=4.5.8 libmcfp pip \
      biopython=1.88 numpy=2.5.2 pandas=3.0.5 requests=2.34.2 \
      scikit-learn=1.9.0 tqdm=4.70.0 \
      --yes
  else
    CONDA_PKGS_DIRS="${MAMBA_ROOT_PREFIX}/pkgs" \
    "${ENV_TOOL}" --no-rc "${ENV_ACTION}" \
      --prefix "${ENV_PREFIX}" \
      --channel conda-forge \
      python=3.12 dssp=4.5.8 libmcfp pip \
      biopython=1.88 numpy=2.5.2 pandas=3.0.5 requests=2.34.2 \
      scikit-learn=1.9.0 tqdm=4.70.0 \
      --yes
  fi
else
  echo "[SETUP] Reusing ${ENV_PREFIX}"
fi

export PYTHONNOUSERSITE=1
"${ENV_PREFIX}/bin/python" -c \
  "import importlib.metadata as m; expected={'biopython':'1.88','numpy':'2.5.2','pandas':'3.0.5','requests':'2.34.2','scikit-learn':'1.9.0','tqdm':'4.70.0'}; actual={k:m.version(k) for k in expected}; assert actual == expected, (actual, expected); import Bio,numpy,pandas,requests,sklearn,tqdm; print('[SETUP] Python dependencies OK:', actual)"

DSSP_ENV_PREFIX="${ENV_PREFIX}" "${SCRIPT_DIR}/mkdssp_wrapper.sh" --version

SMOKE_OUTPUT="$(mktemp "${TMPDIR:-/tmp}/selected8_dssp_smoke.XXXXXX")"
trap 'rm -f "${SMOKE_OUTPUT}"' EXIT
DSSP_ENV_PREFIX="${ENV_PREFIX}" "${SCRIPT_DIR}/mkdssp_wrapper.sh" \
  --output-format=dssp "${SCRIPT_DIR}/pdb_cache/5pti.cif" > "${SMOKE_OUTPUT}"
if [[ ! -s "${SMOKE_OUTPUT}" ]] || ! grep -q "Secondary Structure Definition" "${SMOKE_OUTPUT}"; then
  echo "[ERROR] DSSP could not parse the bundled 5PTI mmCIF structure." >&2
  exit 1
fi
echo "[SETUP] DSSP mmCIF smoke test OK"
touch "${ENV_MARKER}"
echo "[SETUP] Environment ready: ${ENV_PREFIX}"
