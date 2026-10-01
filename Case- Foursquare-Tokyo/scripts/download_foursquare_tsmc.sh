#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
data_dir="${project_root}/data/foursquare_tsmc"
destination="${data_dir}/dataset_TSMC2014_TKY.csv"
archive="${data_dir}/dataset_tsmc2014.zip"
python_bin="${CROSSREC_PYTHON:-python3}"
source_url="https://www-public.imtbs-tsp.eu/~zhang_da/pub/dataset_tsmc2014.zip"

mkdir -p "${data_dir}"

validate() {
  "${python_bin}" - "$1" <<'PY'
import csv
import itertools
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
if not path.is_file():
    raise SystemExit(1)
with path.open(encoding="latin-1", newline="") as handle:
    first = handle.readline()
    delimiter = "\t" if first.count("\t") > first.count(",") else ","
    handle.seek(0)
    rows = csv.DictReader(handle, delimiter=delimiter)
    required = {
        "userId", "venueId", "venueCategoryId", "venueCategory",
        "latitude", "longitude", "timezoneOffset", "utcTimestamp",
    }
    if not required.issubset(rows.fieldnames or ()):
        raise SystemExit(1)
    count = sum(1 for _ in rows)
if count != 573703:
    raise SystemExit(f"expected 573703 Tokyo check-ins, found {count}")
PY
}

if validate "${destination}"; then
  echo "Verified: ${destination}"
  exit 0
fi
if [[ -e "${destination}" ]]; then
  echo "Existing Tokyo file failed schema/row-count validation: ${destination}" >&2
  exit 1
fi

temporary="${archive}.part.$$"
trap 'rm -f "${temporary:-}"' EXIT
if command -v curl >/dev/null 2>&1; then
  curl --fail --location --retry 3 "${source_url}" --output "${temporary}"
elif command -v wget >/dev/null 2>&1; then
  wget --tries=3 --output-document="${temporary}" "${source_url}"
else
  echo "Neither curl nor wget is available." >&2
  exit 1
fi
mv "${temporary}" "${archive}"
trap - EXIT

"${python_bin}" - "${archive}" "${destination}" <<'PY'
import csv
import itertools
import pathlib
import sys
import zipfile

archive = pathlib.Path(sys.argv[1])
destination = pathlib.Path(sys.argv[2])
columns = [
    "userId", "venueId", "venueCategoryId", "venueCategory",
    "latitude", "longitude", "timezoneOffset", "utcTimestamp",
]
with zipfile.ZipFile(archive) as bundle:
    matches = [
        name for name in bundle.namelist()
        if pathlib.PurePosixPath(name).name.lower() in {
            "dataset_tsmc2014_tky.csv", "dataset_tsmc2014_tky.txt"
        }
    ]
    if len(matches) != 1:
        raise SystemExit(f"expected one Tokyo file in archive, found {matches}")
    with bundle.open(matches[0]) as raw:
        lines = (line.decode("latin-1") for line in raw)
        first = next(lines)
        delimiter = "\t" if first.count("\t") > first.count(",") else ","
        source = itertools.chain((first,), lines)
        reader = csv.reader(source, delimiter=delimiter)
        first_row = next(reader)
        has_header = first_row[:2] == columns[:2]
        data_rows = reader if has_header else itertools.chain((first_row,), reader)
        temporary = destination.with_suffix(destination.suffix + ".part")
        with temporary.open("w", encoding="latin-1", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(columns)
            count = 0
            for row in data_rows:
                if len(row) != len(columns):
                    raise SystemExit(f"malformed Tokyo row {count + 1}")
                writer.writerow(row)
                count += 1
        if count != 573703:
            temporary.unlink(missing_ok=True)
            raise SystemExit(f"expected 573703 Tokyo rows, found {count}")
        temporary.replace(destination)
PY

validate "${destination}"
echo "Foursquare TSMC2014 Tokyo file is ready: ${destination}"
