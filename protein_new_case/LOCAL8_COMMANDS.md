# Selected-8 local commands

Unzip and enter the project:

```bash
unzip protein_new_case_8proteins.zip
cd protein_new_case
```

Run all eight proteins and generate the verified LaTeX table:

```bash
bash run_all.sh
```

Inspect the table, log, and exact-value comparison:

```bash
cat selected8_outputs/helix_core_selected8_table.tex
cat logs/selected8_local.log
cat selected8_outputs/reference_comparison.txt
```

The run is resumable. To recompute all eight proteins:

```bash
FORCE=1 bash run_all.sh
```

To reuse an existing Python environment and DSSP executable:

```bash
PYTHON_BIN=/full/path/to/python \
DSSP_EXE=/full/path/to/mkdssp \
bash run_all.sh
```
