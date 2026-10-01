# Foursquare Tokyo Cycle-Truss Case Study

This is a standalone reproduction package for the Foursquare Tokyo Department
Store Cross-Recommendation experiment. It downloads the official TSMC2014
Tokyo data, constructs a temporal User--Venue plus Venue--Venue graph, computes
exact edge-level C3--C6 support and trussness, and compares five recommenders:

- ItemKNN
- C3-truss only
- C4-truss only
- C5-truss only
- C6-truss only


## Run

Python 3.10+, a C++17 compiler, and `curl` or `wget` are required. No `sudo`,
Python package installation, or graph database server is needed.

```bash
cd "Case- Foursquare-Tokyo"
bash scripts/run_foursquare.sh
```

The command writes a fresh run to `results/reproduction`. The bundled verified
reference result remains in `results/run`.

Show the saved result or run the tests:

```bash
bash scripts/show_results.sh
bash scripts/run_tests.sh
```
