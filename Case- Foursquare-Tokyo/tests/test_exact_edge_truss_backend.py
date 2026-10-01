from __future__ import annotations

import csv
import subprocess
import tempfile
import unittest
from collections import Counter
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
BACKEND = PROJECT / "bin" / "streaming_cycle_truss"


def edge(left: str, right: str) -> tuple[str, str]:
    return (left, right) if left < right else (right, left)


def enumerate_cycles(
    active_edges: set[tuple[str, str]],
    length: int,
) -> list[tuple[tuple[str, str], ...]]:
    adjacency: dict[str, set[str]] = {}
    for left, right in active_edges:
        adjacency.setdefault(left, set()).add(right)
        adjacency.setdefault(right, set()).add(left)

    cycles: list[tuple[tuple[str, str], ...]] = []
    for start in sorted(adjacency):
        path = [start]
        visited = {start}

        def visit(current: str) -> None:
            if len(path) == length:
                if (
                    edge(current, start) in active_edges
                    and path[1] < path[-1]
                ):
                    cycle = tuple(
                        edge(path[index], path[(index + 1) % length])
                        for index in range(length)
                    )
                    cycles.append(cycle)
                return
            for neighbor in sorted(adjacency.get(current, ())):
                if neighbor <= start or neighbor in visited:
                    continue
                visited.add(neighbor)
                path.append(neighbor)
                visit(neighbor)
                path.pop()
                visited.remove(neighbor)

        visit(start)
    return cycles


def fixed_point_trussness(
    graph_edges: set[tuple[str, str]],
    length: int,
) -> tuple[dict[tuple[str, str], int], dict[tuple[str, str], int]]:
    initial_cycles = enumerate_cycles(graph_edges, length)
    initial_support = Counter(
        cycle_edge
        for cycle in initial_cycles
        for cycle_edge in cycle
    )
    trussness = {graph_edge: 0 for graph_edge in graph_edges}
    maximum = max(initial_support.values(), default=0)
    for threshold in range(1, maximum + 1):
        active = set(graph_edges)
        while True:
            support = Counter(
                cycle_edge
                for cycle in enumerate_cycles(active, length)
                for cycle_edge in cycle
            )
            remove = {
                graph_edge
                for graph_edge in active
                if support[graph_edge] < threshold
            }
            if not remove:
                break
            active.difference_update(remove)
        for graph_edge in active:
            trussness[graph_edge] = threshold
    return (
        {
            graph_edge: initial_support[graph_edge]
            for graph_edge in graph_edges
        },
        trussness,
    )


class ExactEdgeTrussBackendTests(unittest.TestCase):
    def test_backend_matches_direct_edge_fixed_point_for_c3_to_c6(self):
        self.assertTrue(BACKEND.is_file(), "bootstrap must compile the backend")
        # A six-node circulant graph has overlapping cycles of every requested
        # length and non-uniform edge supports, so this checks more than an
        # isolated-cycle sanity case.
        graph_edges = {
            edge("N0", "N1"),
            edge("N1", "N2"),
            edge("N2", "N3"),
            edge("N3", "N4"),
            edge("N4", "N5"),
            edge("N5", "N0"),
            edge("N0", "N2"),
            edge("N1", "N3"),
            edge("N2", "N4"),
            edge("N3", "N5"),
            edge("N4", "N0"),
            edge("N5", "N1"),
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            graph_path = root / "graph.tsv"
            graph_path.write_text(
                "".join(
                    f"{left}\t{right}\n"
                    for left, right in sorted(graph_edges)
                ),
                encoding="utf-8",
            )
            edges_path = root / "edges.csv"
            counts_path = root / "counts.csv"
            subprocess.run(
                [
                    str(BACKEND),
                    "--input",
                    str(graph_path),
                    "--edges-out",
                    str(edges_path),
                    "--counts-out",
                    str(counts_path),
                    "--lengths",
                    "3,4,5,6",
                    "--quiet",
                ],
                check=True,
            )
            with edges_path.open(encoding="utf-8", newline="") as handle:
                observed_rows = list(csv.DictReader(handle))

        observed = {
            edge(row["src"], row["dst"]): row
            for row in observed_rows
        }
        self.assertEqual(set(observed), graph_edges)
        for length in (3, 4, 5, 6):
            expected_support, expected_tau = fixed_point_trussness(
                graph_edges,
                length,
            )
            for graph_edge in graph_edges:
                with self.subTest(length=length, graph_edge=graph_edge):
                    row = observed[graph_edge]
                    self.assertEqual(
                        int(row[f"support_c{length}"]),
                        expected_support[graph_edge],
                    )
                    self.assertEqual(
                        int(row[f"trussness_c{length}"]),
                        expected_tau[graph_edge],
                    )


if __name__ == "__main__":
    unittest.main()
