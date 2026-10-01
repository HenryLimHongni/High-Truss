# Protein helix-core case study: selected 8 proteins

This package reproduces the two paper tables for exactly these eight PDB proteins:

```text
1A6M 2CI2 5PTI 1YCC 2RN2 4ICB 3GB1 4CPV
```

## Run everything locally

From the project directory, run:

```bash
bash run_all.sh
```

The first run creates an isolated local environment and installs pinned
dependencies without sudo. On Apple-silicon macOS, the package can use its
bundled micromamba executable, so a separate conda installation is not
required. Internet access is needed only when creating the environment.

The workflow is resumable per PDB. To recompute all eight proteins, run:

```bash
FORCE=1 bash run_all.sh
```

If a suitable Python environment and DSSP executable already exist, use:

```bash
PYTHON_BIN=/full/path/to/python \
DSSP_EXE=/full/path/to/mkdssp \
bash run_all.sh
```

## Outputs

```text
logs/selected8_local.log
selected8_outputs/helix_core_selected8_table.tex
selected8_outputs/selected8_metrics.csv
selected8_outputs/reference_comparison.txt
selected8_state/exploratory/per_pdb/<PDB_ID>/
```

The complete LaTeX table is saved separately and printed in the log. Its
format is `PDB`, `|V|`, `|E|`, followed by ROC-AUC and PR-AUC for
`C_3`, `C_4`, `C_5`, and `C_6`. Results at 6.5 and 7.0 Angstrom are kept in
separate tables and are never averaged.

Before the table is emitted, all 16 freshly computed PDB-by-cutoff rows are
checked against the full-precision values in
`reference_results/selected8_expected.csv`. A mismatch stops the run instead
of silently producing an unchecked table.

## Fixed experimental protocol

For every protein, the workflow uses exactly the same settings:

- cutoffs: 6.5 and 7.0 Angstrom;
- vertices: retained amino-acid residues;
- backbone edges: same-chain sequentially adjacent residues whose C-alpha
  distance is at most 4.5 Angstrom;
- contact edges: same-chain nonadjacent residues with sequence separation at
  least 2 and C-alpha distance at most the selected cutoff;
- no maximum sequence separation and no cross-chain edges;
- helix-core labels: minimum helix length 7, trim 2 residues at each end,
  exclude non-core DSSP `H`, and exclude DSSP `G/I` from evaluation;
- vertex score: maximum cycle-truss number among incident edges;
- methods: C3-, C4-, C5-, and C6-truss only;
- metrics: scikit-learn `roc_auc_score` and `average_precision_score`.

The batch driver fixes these values; it does not tune graph construction,
labels, cutoffs, scoring, or metrics for individual proteins.

## Spartan without sudo

The same local command can be used in an interactive Spartan shell:

```bash
bash run_all.sh
```

Alternatively, create the local environment first:

```bash
bash setup_spartan_env.sh
bash run_all.sh
```

No `sbatch` submission is required for this eight-protein run.
