#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python_bin="${CYCLECASE_PYTHON:-python3}"

"${python_bin}" -c '
import sys
if sys.version_info < (3, 10):
    raise SystemExit("Python 3.10 or newer is required")
print("Python:", sys.version.split()[0])
'

compiler_bin="${CYCLECASE_CXX:-}"
if [[ -z "${compiler_bin}" ]]; then
  for candidate in c++ g++ clang++; do
    if command -v "${candidate}" >/dev/null 2>&1; then
      compiler_bin="${candidate}"
      break
    fi
  done
fi
if [[ -z "${compiler_bin}" ]]; then
  echo "A C++17 compiler is required (c++, g++, or clang++)." >&2
  exit 1
fi

mkdir -p "${project_root}/bin"

"${compiler_bin}" -std=c++17 -O3 -DNDEBUG \
  "${project_root}/cpp/short_cycle_truss.cpp" \
  -o "${project_root}/bin/short_cycle_truss"

"${compiler_bin}" -std=c++17 -O3 -DNDEBUG \
  "${project_root}/cpp/c5_truss_sparse.cpp" \
  -o "${project_root}/bin/c5_truss_sparse"

"${compiler_bin}" -std=c++17 -O3 -DNDEBUG \
  "${project_root}/cpp/streaming_c6_truss.cpp" \
  -o "${project_root}/bin/streaming_c6_truss"

echo "Compiler: ${compiler_bin}"
echo "Built exact C3/C4, sparse C5, and streaming C6 backends."
