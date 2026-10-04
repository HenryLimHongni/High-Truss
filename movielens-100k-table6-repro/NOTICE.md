# Data, attribution and provenance

MovieLens-100K source:
https://grouplens.org/datasets/movielens/100k/

Official archive:
https://files.grouplens.org/datasets/movielens/ml-100k.zip

Official data description and usage conditions:
https://files.grouplens.org/datasets/movielens/ml-100k/README

The official README requires acknowledgement and does not permit data
redistribution without separate permission. Therefore this source-code ZIP does
not contain ratings, movie metadata, or real per-user recommendation results.
The downloader retrieves the official archive into the user's own environment.
The accompanying unit-test data are synthetic.

Dataset citation:
F. Maxwell Harper and Joseph A. Konstan. 2015. The MovieLens Datasets: History
and Context. ACM Transactions on Interactive Intelligent Systems 5(4), Article
19, 19 pages. DOI: 10.1145/2827872.

Project lineage (source files supplied in the conversation):
- movielens_100k_c3_c4_c5_c6_complete.zip
- movielens_100k_typed_c4_recommend_patch.zip, plus the final pad=True correction.

Those archives are not runtime dependencies. The corresponding required
implementation is included directly in this project. See the SHA-256 provenance
and synthetic regression report in validation/legacy_parity.json.

No proprietary/compiler binaries or raw benchmark data are bundled. The
project does not imply endorsement by GroupLens or the University of Minnesota.
