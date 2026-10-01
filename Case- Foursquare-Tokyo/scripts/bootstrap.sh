#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python_bin="${CROSSREC_PYTHON:-python3}"

"${python_bin}" -c '
import sys
if sys.version_info < (3, 10):
    raise SystemExit("Python 3.10 or newer is required")
print("Python:", sys.version.split()[0])
'

compiler="${CROSSREC_CXX:-}"
if [[ -z "${compiler}" ]]; then
  for candidate in c++ g++ clang++; do
    if command -v "${candidate}" >/dev/null 2>&1; then
      compiler="${candidate}"
      break
    fi
  done
fi
if [[ -z "${compiler}" ]]; then
  echo "No C++17 compiler found (tried c++, g++, clang++)." >&2
  exit 1
fi

mkdir -p "${project_root}/bin"
temporary="${project_root}/bin/.streaming_cycle_truss.tmp"
"${compiler}" -std=c++17 -O3 -DNDEBUG \
  "${project_root}/cpp/streaming_cycle_truss.cpp" \
  -o "${temporary}"
mv "${temporary}" "${project_root}/bin/streaming_cycle_truss"

echo "Compiler: ${compiler}"
echo "Built exact edge-level C3/C4/C5/C6 support and truss backend."
