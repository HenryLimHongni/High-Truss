#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${CYCLECASE_PYTHON:-python3}"
"$PY" -c 'import sys; assert sys.version_info >= (3,10), "Python >=3.10 required"; print("Python:",sys.version.split()[0])'
CXX="${CYCLECASE_CXX:-}"
if [[ -z "$CXX" ]]; then
  for compiler in c++ g++ clang++; do
    if command -v "$compiler" >/dev/null 2>&1; then CXX="$compiler"; break; fi
  done
fi
[[ -n "$CXX" ]] || { echo 'Install/load GCC or Clang with C++17 support.' >&2; exit 1; }
mkdir -p "$ROOT/bin"
for name in short_cycle_truss c5_truss_sparse streaming_c6_truss; do
  if [[ ! -x "$ROOT/bin/$name" || "$ROOT/cpp/$name.cpp" -nt "$ROOT/bin/$name" ]]; then
    tmp="$ROOT/bin/$name.tmp.$$"
    trap 'rm -f "$tmp"' EXIT
    "$CXX" -std=c++17 -O3 -DNDEBUG "$ROOT/cpp/$name.cpp" -o "$tmp"
    mv "$tmp" "$ROOT/bin/$name"
    trap - EXIT
    echo "Built $ROOT/bin/$name"
  else
    echo "[resume] compiled $name"
  fi
done
