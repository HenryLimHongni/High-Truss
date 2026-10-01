#!/bin/bash
#SBATCH --job-name=ml-c6
#SBATCH --time=2-00:00:00
#SBATCH --mem=1024G
#SBATCH --cpus-per-task=4
#SBATCH --output=ml-c6-%j.out
#SBATCH --error=ml-c6-%j.err

set -euo pipefail

cd /data/gpfs/projects/punim2835/movielens-100k-c3-c4-c5-c6-complete

CASE=/data/gpfs/projects/punim2835/movielens-100k-weighted-08-02-threshold04/work/movielens_case
OUT=/data/gpfs/projects/punim2835/movielens-100k-weighted-08-02-threshold04/work/c3_c4_c5_c6_all107_results

CYCLECASE_PYTHON=/apps/easybuild-2022/easybuild/software/Core/Anaconda3/2024.02-1/bin/python3 \
C6_TIMEOUT_SECONDS=172800 \
TOP_USERS=500 \
CUTOFFS=5,10 \
RANKING_LIMIT=10 \
bash scripts/run_on_existing_case.sh "$CASE" "$OUT"
