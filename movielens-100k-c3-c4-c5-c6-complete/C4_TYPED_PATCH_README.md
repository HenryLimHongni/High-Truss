# Typed C4 recommendation patch

This additive patch does not overwrite the original `cyclecase.py` or
`all_methods.py`. It adds a separate evaluator for these methods only:

- `c3-truss-direct`
- `c4-truss-typed`
- `c5-max`
- `c5-avg`
- `itemknn-full-catalog`

For a typed cycle `(u,a,b,c,u)`, where `u` is a user and `a,b,c` are
items, candidate `b` is recommended to `u`. The cycle score is
`max(tau4(a,b), tau4(b,c))`; multiple cycles for the same pair are
aggregated by maximum.

The experiment evaluates fixed-denominator Precision@5, binary HR@5 and
binary-relevance NDCG@5. C3 and C5 preserve the existing degree-based padding
to 5; typed C4 is not padded.
