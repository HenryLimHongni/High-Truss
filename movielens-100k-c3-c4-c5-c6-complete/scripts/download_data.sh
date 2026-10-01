#!/usr/bin/env bash
set -euo pipefail
project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
data_dir="${project_root}/data/movielens_100k"
archive="${data_dir}/ml-100k.zip"
mkdir -p "${data_dir}"
if [[ -f "$archive" ]]; then
  observed="$(md5sum "$archive" | awk '{print $1}')"
  [[ "$observed" == "0e33842e24a9c977be4e0107933c0723" ]] || {
    echo "Existing archive has wrong MD5: $archive" >&2; exit 1;
  }
else
  temporary="${archive}.part.$$"
  trap 'rm -f "${temporary:-}"' EXIT
  if command -v curl >/dev/null 2>&1; then
    curl --fail --location --retry 3 \
      https://files.grouplens.org/datasets/movielens/ml-100k.zip \
      --output "$temporary"
  elif command -v wget >/dev/null 2>&1; then
    wget --tries=3 --output-document="$temporary" \
      https://files.grouplens.org/datasets/movielens/ml-100k.zip
  else
    echo "curl or wget is required" >&2
    exit 1
  fi
  observed="$(md5sum "$temporary" | awk '{print $1}')"
  [[ "$observed" == "0e33842e24a9c977be4e0107933c0723" ]] || {
    echo "Downloaded archive has wrong MD5" >&2; exit 1;
  }
  mv "$temporary" "$archive"
  trap - EXIT
fi
unzip -tq "$archive" >/dev/null
rm -rf "${data_dir}/ml-100k"
unzip -q "$archive" -d "$data_dir"
echo "MovieLens files ready in ${data_dir}/ml-100k"
