#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from full_c5_recommend.c4_typed_methods import (  # noqa: E402
    evaluate_c3_c4typed_c5_itemknn,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate C3 direct, typed C4 u-a-b-c-u, C5 MAX/AVG, and "
            "full-catalog ItemKNN without C6"
        )
    )
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--top-users", type=int, default=500)
    parser.add_argument("--cutoff", type=int, default=5)
    parser.add_argument("--ranking-limit", type=int, default=5)
    args = parser.parse_args()
    try:
        evaluate_c3_c4typed_c5_itemknn(
            case_dir=args.case,
            output_dir=args.output_dir,
            top_users=args.top_users,
            cutoff=args.cutoff,
            ranking_limit=args.ranking_limit,
        )
        return 0
    except Exception as error:
        print(f"error: {type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
