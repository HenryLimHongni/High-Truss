from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .all_decompose import compute_cycle_decompositions
from .all_methods import evaluate_all_methods
from .artifacts import load_case
from .dataset import BuildOptions, build_case
from .decompose import DEFAULT_BIN_DIR, compute_c5
from .preflight import graph_preflight
from .recommend import evaluate_top_users


def _csv(value: str) -> list[str]:
    return [token.strip() for token in value.split(",") if token.strip()]


def _print_case(case_dir: Path) -> None:
    case = load_case(case_dir)
    print(
        json.dumps(
            {
                "case": str(case.root),
                "dataset": case.metadata.get("dataset"),
                "graph_sha256": case.graph_sha256,
                "vertices": len(case.nodes),
                "users": sum(n.node_type == "user" for n in case.nodes),
                "items": sum(n.node_type == "item" for n in case.nodes),
                "edges": case.edge_count,
                "user_item_edges": sum(r.edge_type == "UI" for r in case.edge_records),
                "item_item_edges": sum(r.edge_type == "II" for r in case.edge_records),
                "test_edges": len(case.truth),
                "test_users": len({u for u, _ in case.truth}),
                "cutoff_timestamp": case.metadata.get("cutoff_timestamp"),
            },
            indent=2,
            ensure_ascii=False,
        )
    )


def _add_build_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--ratings", type=Path, required=True)
    parser.add_argument("--movies", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--train-quantile", type=float, default=0.80)
    parser.add_argument("--fixed-cutoff", type=int)
    parser.add_argument("--minimum-rating", type=float, default=0.0)
    parser.add_argument("--ii-top-k", type=int, default=20)
    parser.add_argument("--genre-weight", type=float, default=0.8)
    parser.add_argument("--year-weight", type=float, default=0.2)
    parser.add_argument("--ii-similarity-threshold", type=float, default=0.4)


def _options(args: argparse.Namespace) -> BuildOptions:
    return BuildOptions(
        train_quantile=args.train_quantile,
        fixed_cutoff=args.fixed_cutoff,
        minimum_rating=args.minimum_rating,
        ii_top_k=args.ii_top_k,
        genre_weight=args.genre_weight,
        year_weight=args.year_weight,
        ii_similarity_threshold=args.ii_similarity_threshold,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cyclecase",
        description=(
            "MovieLens 100K full temporal graph with exact C3/C4/C5/C6 "
            "decomposition and recommendation comparisons"
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)

    build = commands.add_parser("build", help="build the full temporal MovieLens graph")
    _add_build_args(build)

    inspect = commands.add_parser("inspect", help="validate and summarize a case")
    inspect.add_argument("--case", type=Path, required=True)

    preflight = commands.add_parser("preflight", help="estimate graph resource pressure")
    preflight.add_argument("--case", type=Path, required=True)

    decompose = commands.add_parser(
        "decompose", help="preserved original exact simple-C5 support/trussness"
    )
    decompose.add_argument("--case", type=Path, required=True)
    decompose.add_argument("--output-dir", type=Path)
    decompose.add_argument("--bin-dir", type=Path, default=DEFAULT_BIN_DIR)
    decompose.add_argument("--timeout-seconds", type=float)
    decompose.add_argument("--force", action="store_true")

    decompose_all = commands.add_parser(
        "decompose-all", help="compute exact C3, C4, C5, and C6 support/trussness"
    )
    decompose_all.add_argument("--case", type=Path, required=True)
    decompose_all.add_argument("--lengths", default="3,4,5,6")
    decompose_all.add_argument("--output-dir", type=Path)
    decompose_all.add_argument("--bin-dir", type=Path, default=DEFAULT_BIN_DIR)
    decompose_all.add_argument("--timeout-seconds", type=float)
    decompose_all.add_argument("--force", action="store_true")

    evaluate = commands.add_parser(
        "evaluate-top-users",
        help="preserved original C5-MAX, C5-AVG, and full-catalog ItemKNN",
    )
    evaluate.add_argument("--case", type=Path, required=True)
    evaluate.add_argument("--output-dir", type=Path, required=True)
    evaluate.add_argument("--values", type=Path)
    evaluate.add_argument("--top-users", type=int, default=500)
    evaluate.add_argument("--cutoffs", default="5,10,20")
    evaluate.add_argument("--skip-itemknn", action="store_true")
    evaluate.add_argument("--ranking-limit", type=int)

    evaluate_all = commands.add_parser(
        "evaluate-all",
        help=(
            "evaluate C3 direct, preserved C4 connectivity, C5 MAX/AVG, "
            "typed C6 MAX, and ItemKNN with corrected Precision/HR"
        ),
    )
    evaluate_all.add_argument("--case", type=Path, required=True)
    evaluate_all.add_argument("--output-dir", type=Path, required=True)
    evaluate_all.add_argument("--top-users", type=int, default=500)
    evaluate_all.add_argument("--cutoffs", default="5,10")
    evaluate_all.add_argument("--ranking-limit", type=int)
    evaluate_all.add_argument("--skip-itemknn", action="store_true")
    evaluate_all.add_argument(
        "--skip-c6",
        action="store_true",
        help="evaluate C3/C4/C5/ItemKNN without requiring C6 values",
    )

    run = commands.add_parser("run-full", help="preserved original C5-only pipeline")
    _add_build_args(run)
    run.add_argument("--case", type=Path, required=True)
    run.add_argument("--results", type=Path, required=True)
    run.add_argument("--bin-dir", type=Path, default=DEFAULT_BIN_DIR)
    run.add_argument("--timeout-seconds", type=float)
    run.add_argument("--top-users", type=int, default=500)
    run.add_argument("--cutoffs", default="5,10,20")
    run.add_argument("--skip-itemknn", action="store_true")

    run_all = commands.add_parser(
        "run-all", help="build, decompose C3-C6, and run the complete comparison"
    )
    _add_build_args(run_all)
    run_all.add_argument("--case", type=Path, required=True)
    run_all.add_argument("--results", type=Path, required=True)
    run_all.add_argument("--bin-dir", type=Path, default=DEFAULT_BIN_DIR)
    run_all.add_argument("--timeout-seconds", type=float)
    run_all.add_argument("--top-users", type=int, default=500)
    run_all.add_argument("--cutoffs", default="5,10")
    run_all.add_argument("--ranking-limit", type=int)
    run_all.add_argument("--skip-itemknn", action="store_true")
    run_all.add_argument("--skip-c6", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            case = build_case(
                ratings_path=args.ratings,
                movies_path=args.movies,
                output_dir=args.output,
                options=_options(args),
            )
            _print_case(case.root)
        elif args.command == "inspect":
            _print_case(args.case)
        elif args.command == "preflight":
            graph_preflight(case_dir=args.case)
        elif args.command == "decompose":
            result = compute_c5(
                case_dir=args.case,
                output_dir=args.output_dir,
                bin_dir=args.bin_dir,
                timeout_seconds=args.timeout_seconds,
                force=args.force,
            )
            print(json.dumps({k: str(v) for k, v in result.items()}, indent=2))
        elif args.command == "decompose-all":
            lengths = [int(value) for value in _csv(args.lengths)]
            result = compute_cycle_decompositions(
                case_dir=args.case,
                lengths=lengths,
                output_dir=args.output_dir,
                bin_dir=args.bin_dir,
                timeout_seconds=args.timeout_seconds,
                force=args.force,
            )
            print(json.dumps({k: str(v) for k, v in result.items()}, indent=2))
        elif args.command == "evaluate-top-users":
            evaluate_top_users(
                case_dir=args.case,
                output_dir=args.output_dir,
                values_path=args.values,
                top_users=args.top_users,
                cutoffs=[int(v) for v in _csv(args.cutoffs)],
                include_itemknn=not args.skip_itemknn,
                ranking_limit=args.ranking_limit,
            )
        elif args.command == "evaluate-all":
            evaluate_all_methods(
                case_dir=args.case,
                output_dir=args.output_dir,
                top_users=args.top_users,
                cutoffs=[int(v) for v in _csv(args.cutoffs)],
                ranking_limit=args.ranking_limit,
                include_itemknn=not args.skip_itemknn,
                skip_c6=args.skip_c6,
            )
        elif args.command == "run-full":
            case = build_case(
                ratings_path=args.ratings,
                movies_path=args.movies,
                output_dir=args.case,
                options=_options(args),
            )
            _print_case(case.root)
            graph_preflight(case_dir=case.root)
            compute_c5(
                case_dir=case.root,
                bin_dir=args.bin_dir,
                timeout_seconds=args.timeout_seconds,
                force=True,
            )
            evaluate_top_users(
                case_dir=case.root,
                output_dir=args.results,
                top_users=args.top_users,
                cutoffs=[int(v) for v in _csv(args.cutoffs)],
                include_itemknn=not args.skip_itemknn,
            )
        elif args.command == "run-all":
            case = build_case(
                ratings_path=args.ratings,
                movies_path=args.movies,
                output_dir=args.case,
                options=_options(args),
            )
            _print_case(case.root)
            graph_preflight(case_dir=case.root)
            lengths = (3, 4, 5) if args.skip_c6 else (3, 4, 5, 6)
            compute_cycle_decompositions(
                case_dir=case.root,
                lengths=lengths,
                bin_dir=args.bin_dir,
                timeout_seconds=args.timeout_seconds,
                force=True,
            )
            if args.skip_c6:
                raise RuntimeError(
                    "run-all --skip-c6 builds only C3-C5 values; use evaluate-all "
                    "only after c6_truss.txt is available"
                )
            evaluate_all_methods(
                case_dir=case.root,
                output_dir=args.results,
                top_users=args.top_users,
                cutoffs=[int(v) for v in _csv(args.cutoffs)],
                ranking_limit=args.ranking_limit,
                include_itemknn=not args.skip_itemknn,
            )
        else:
            parser.error(f"unsupported command: {args.command}")
        return 0
    except Exception as error:
        print(f"error: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
