#!/usr/bin/env bash
#SBATCH --job-name=ml100k-c3c6
#SBATCH --time=7-00:00:00
#SBATCH --mem=1024G
#SBATCH --cpus-per-task=8
#SBATCH --output=slurm-%x-%j.out
set -euo pipefail
cd "${SLURM_SUBMIT_DIR}"
export C6_TIMEOUT_SECONDS="${C6_TIMEOUT_SECONDS:-604800}"
bash scripts/run_full_experiment.sh
