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

The public result contains only Precision@5 and binary NDCG@5. Every method
ranks the same complete warm-unseen Item catalog. Zero-score Items are retained,
so every evaluated User receives five recommendations. Ties are resolved by:

```text
primary method score descending
training mixed-graph Item degree descending
Item ID ascending
```

Item degree counts incident training User--Item and Item--Item edges. Test edges
are never used to compute it.

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

## Official result

| Method | NDCG@5 | Precision@5 |
|---|---:|---:|
| ItemKNN | 0.1342 | 0.0633 |
| C3-truss only | 0.2171 | **0.0918** |
| C4-truss only | 0.2192 | 0.0878 |
| **C5-truss only** | **0.2243** | **0.0918** |
| C6-truss only | 0.2067 | 0.0878 |

C5-truss obtains the highest NDCG@5 and ties C3-truss for the highest
Precision@5.

## Data pipeline

The script downloads the official TSMC2014 archive from:

```text
https://www-public.imtbs-tsp.eu/~zhang_da/pub/dataset_tsmc2014.zip
```

It verifies the Tokyo file schema and its 573,703 raw visit records. The fixed
cutoff is `2012-12-01 00:00:00 UTC`:

- distinct User--Venue visits before the cutoff form training edges;
- a relation first observed after the cutoff is a test positive only when its
  User and Venue are already present in the training graph; and
- a training User--Venue edge is never reused as a test positive.

Items are training-period Tokyo venues whose category is exactly
`Department Store`. A Venue's category and coordinates are fixed using its
earliest pre-cutoff record. Two Items receive an undirected Item--Item edge when
their geographic distance is at most 2 km; every qualifying edge is retained.

The resulting experiment has:

```text
User vertices                         916
Venue/Item vertices                   172
Training User--Venue edges          1,679
Training Venue--Venue edges           765
Total training edges                2,444
Test users                              98
Future positive User--Venue edges     160
Scored User--Venue pairs           16,519
```

## Exact decomposition and scoring

The C++ backend enumerates generic simple C3, C4, C5, and C6 cycles in the same
undirected training graph and performs exact edge peeling. Support and
trussness are edge properties, not node properties. No predicted User--Item
edge is temporarily inserted before decomposition.

- `itemknn` uses cosine ItemKNN on the training User--Item graph.
- `c3_truss_only` scores an unseen `(u,c)` relation through a training path
  `u--b--c`, using the largest C3-trussness of a corresponding Item--Item edge
  `b--c`.
- `c4_truss_only` uses typed simple C4 cycles `U-I-I-I`. For
  `u-a-b-c-u`, where `u-b` is the unseen relation, the cycle score is the larger
  C4-trussness of `a-b` and `b-c`; the largest score over supporting cycles is
  used.
- `c5_truss_only` uses typed simple C5 cycles `U-I-U-I-I` and the largest
  C5-trussness of the corresponding Item--Item role edge.
- `c6_truss_only` uses typed simple C6 cycles `U-I-I-U-I-I` and the largest
  C6-trussness of the corresponding Item--Item role edge.

Generic cycles determine edge trussness; the typed patterns are applied only
when those edge properties are used for recommendation.

## Evaluation and outputs

For each of the 98 test Users, each method scores every training Item not linked
to that User in the training graph. It then returns exactly five Items using the
shared tie-breaking rule above. Precision@5 is the number of future positives
in the five recommendations divided by five, averaged over all 98 Users.
Binary NDCG@5 rewards future positives placed nearer the top and is normalized
per User before being averaged over the same Users.

Bundled verified output files (a fresh run uses `results/reproduction`):

```text
results/run/HEADLINE_METRICS.csv
results/run/METRICS.csv
results/run/PER_USER_METRICS.csv
results/run/AUDIT.json
results/run/decomposition/MIXED_GRAPH_EDGES.tsv
results/run/decomposition/EDGE_TRUSSNESS.csv
results/run/decomposition/CYCLE_COUNTS.csv
results/run/graphdb/users.csv
results/run/graphdb/items.csv
results/run/graphdb/interactions.csv
results/run/graphdb/item_relations.csv
results/run/graphdb/cross_recommendations.csv
results/run/graphdb/queries.cypher
```

`EDGE_TRUSSNESS.csv` stores exact C3--C6 support and trussness for every
training edge. The headline CSV has exactly three columns:
`method,ndcg_at_5,precision_at_5`.

## Scope

- A visit is not an e-commerce purchase.
- Department Store, 2 km, the temporal cutoff, and the degree tie-break are
  fixed retrospective case-study settings.
- The degree tie-break is shared by all methods and is computed from training
  data only, but it materially orders zero-score Items.
- C5-truss is best on NDCG@5 and ties for best Precision@5; it is not strictly
  best on both reported measures.

See `README_ZH.md` for the Chinese documentation.
