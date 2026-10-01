#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

STATE_DIR="${STATE_DIR:-${SCRIPT_DIR}/selected8_state}"
OUTPUT_DIR="${OUTPUT_DIR:-${SCRIPT_DIR}/selected8_outputs}"
CANDIDATE_FILE="${SCRIPT_DIR}/candidate_lists/selected8.txt"
REFERENCE_FILE="${SCRIPT_DIR}/reference_results/selected8_expected.csv"
ENV_PREFIX="${ENV_PREFIX:-${SCRIPT_DIR}/.selected8_env}"
LOG_FILE="${LOG_FILE:-${SCRIPT_DIR}/logs/selected8_local.log}"

mkdir -p "$(dirname "${LOG_FILE}")"

run_selected8() {
echo
echo "================================================================"
echo "[RUN] Selected-8 local run started at $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
echo "[RUN] Project: ${SCRIPT_DIR}"
echo "[RUN] State: ${STATE_DIR}"
echo "[RUN] Output: ${OUTPUT_DIR}"
echo "[RUN] Log: ${LOG_FILE}"

if [[ -z "${PYTHON_BIN:-}" || -z "${DSSP_EXE:-}" ]]; then
  ENV_PREFIX="${ENV_PREFIX}" bash "${SCRIPT_DIR}/setup_selected8_env.sh"
  PYTHON_BIN="${ENV_PREFIX}/bin/python"
  DSSP_EXE="${SCRIPT_DIR}/mkdssp_wrapper.sh"
  export DSSP_ENV_PREFIX="${ENV_PREFIX}"
else
  echo "[RUN] Using supplied PYTHON_BIN=${PYTHON_BIN}"
  echo "[RUN] Using supplied DSSP_EXE=${DSSP_EXE}"
fi

export PYTHONNOUSERSITE=1
"${PYTHON_BIN}" -c "import Bio,numpy,pandas,requests,sklearn,tqdm; print('[RUN] Python dependencies OK')"
"${DSSP_EXE}" --version

SMOKE_OUTPUT="$(mktemp "${TMPDIR:-/tmp}/selected8_dssp_run_smoke.XXXXXX")"
trap 'rm -f "${SMOKE_OUTPUT}"' EXIT
"${DSSP_EXE}" --output-format=dssp "${SCRIPT_DIR}/pdb_cache/5pti.cif" > "${SMOKE_OUTPUT}"
if [[ ! -s "${SMOKE_OUTPUT}" ]] || ! grep -q "Secondary Structure Definition" "${SMOKE_OUTPUT}"; then
  echo "[ERROR] DSSP could not parse the bundled 5PTI mmCIF structure." >&2
  exit 1
fi
echo "[RUN] DSSP mmCIF smoke test OK"

mkdir -p "${STATE_DIR}/pdb_cache" "${OUTPUT_DIR}"
while IFS= read -r pdb_id; do
  [[ -z "${pdb_id}" ]] && continue
  lower_id="$(printf '%s' "${pdb_id}" | tr '[:upper:]' '[:lower:]')"
  source_cif="${SCRIPT_DIR}/pdb_cache/${lower_id}.cif"
  target_cif="${STATE_DIR}/pdb_cache/${lower_id}.cif"
  if [[ ! -s "${source_cif}" ]]; then
    echo "[ERROR] Bundled PDB snapshot is missing: ${source_cif}" >&2
    exit 1
  fi
  if [[ ! -s "${target_cif}" ]]; then
    cp "${source_cif}" "${target_cif}"
  fi
done < "${CANDIDATE_FILE}"

SCREEN_FORCE_ARG=""
if [[ "${FORCE:-0}" == "1" ]]; then
  SCREEN_FORCE_ARG="--force"
fi

"${PYTHON_BIN}" "${SCRIPT_DIR}/screen_candidates.py" \
  --candidate-file "${CANDIDATE_FILE}" \
  --phase exploratory \
  --state-dir "${STATE_DIR}" \
  --python-bin "${PYTHON_BIN}" \
  --dssp-exe "${DSSP_EXE}" \
  ${SCREEN_FORCE_ARG}

"${PYTHON_BIN}" "${SCRIPT_DIR}/make_selected8_table.py" \
  --candidate-file "${CANDIDATE_FILE}" \
  --reference "${REFERENCE_FILE}" \
  --state-dir "${STATE_DIR}" \
  --phase exploratory \
  --out-dir "${OUTPUT_DIR}"

echo "[OK] Complete table is stored in the log: ${LOG_FILE}"
echo "[OK] LaTeX: ${OUTPUT_DIR}/helix_core_selected8_table.tex"
echo "[OK] CSV: ${OUTPUT_DIR}/selected8_metrics.csv"
echo "[RUN] Finished at $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
}

run_selected8 2>&1 | tee -a "${LOG_FILE}"
