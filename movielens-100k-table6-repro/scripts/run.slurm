#!/usr/bin/env bash
#SBATCH --job-name=ml-table6
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=64G
#SBATCH --time=24:00:00
#SBATCH --output=table6-%j.out
#SBATCH --error=table6-%j.err
set -euo pipefail
# Submit from the clean PROJECT ROOT: sbatch scripts/run.slurm
# Resource requests are starting points, not measurements or guarantees.
cd "${SLURM_SUBMIT_DIR:?Submit this job from the project root}"
export CYCLECASE_PYTHON="${CYCLECASE_PYTHON:-python3}"
# By default only four completed numeric rows are reproduced.
# To attempt C6 too: C6_MODE=run C6_TIMEOUT_SECONDS=36400 sbatch scripts/run.slurm
bash scripts/reproduce.sh
