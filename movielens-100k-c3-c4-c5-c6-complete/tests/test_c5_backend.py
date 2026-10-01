from __future__ import annotations

import csv
import itertools
import random
import subprocess
import tempfile
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BINARY = ROOT / "bin" / "c5_truss_sparse"


def normalize(edge: tuple[int, int]) -> tuple[int, int]:
    a, b = edge
    return (a, b) if a < b else (b, a)


def enumerate_c5(edges: set[tuple[int, int]]) -> list[tuple[tuple[int, int], ...]]:
    adjacency: dict[int, set[int]] = {}
    for a, b in edges:
        adjacency.setdefault(a, set()).add(b)
        adjacency.setdefault(b, set()).add(a)
    cycles: list[tuple[tuple[int, int], ...]] = []
    for start in sorted(adjacency):
        path = [start]
        visited = {start}

        def dfs(current: int) -> None:
            if len(path) == 5:
                if normalize((current, start)) in edges and path[1] < path[-1]:
                    cycles.append(
                        tuple(normalize((path[i], path[(i + 1) % 5])) for i in range(5))
                    )
                return
            for neighbor in sorted(adjacency.get(current, ())):
                if neighbor <= start or neighbor in visited:
                    continue
                visited.add(neighbor)
                path.append(neighbor)
                dfs(neighbor)
                path.pop()
                visited.remove(neighbor)

        dfs(start)
    return cycles


def direct_fixed_point(
    edges: set[tuple[int, int]],
) -> tuple[dict[tuple[int, int], int], dict[tuple[int, int], int]]:
    initial_cycles = enumerate_c5(edges)
    initial = Counter(edge for cycle in initial_cycles for edge in cycle)
    truss = {edge: 0 for edge in edges}
    for threshold in range(1, max(initial.values(), default=0) + 1):
        active = set(edges)
        while True:
            support = Counter(edge for cycle in enumerate_c5(active) for edge in cycle)
            removed = {edge for edge in active if support[edge] < threshold}
            if not removed:
                break
            active.difference_update(removed)
        for edge in active:
            truss[edge] = threshold
    return ({edge: initial[edge] for edge in edges}, truss)


def run_backend(edge_list: list[tuple[int, int]]) -> list[dict[str, str]]:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        graph = root / "graph.txt"
        output = root / "result.tsv"
        graph.write_text("".join(f"{a}\t{b}\n" for a, b in edge_list), encoding="ascii")
        subprocess.run(
            [str(BINARY), str(graph), str(output)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        with output.open(encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle, delimiter="\t"))


class C5BackendTests(unittest.TestCase):
    def test_isolated_c5(self) -> None:
        edges = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 4)]
        rows = run_backend(edges)
        self.assertEqual([int(row["initial_support"]) for row in rows], [1] * 5)
        self.assertEqual([int(row["trussness"]) for row in rows], [1] * 5)

    def test_k5(self) -> None:
        edges = list(itertools.combinations(range(5), 2))
        rows = run_backend(edges)
        self.assertEqual([int(row["initial_support"]) for row in rows], [6] * 10)
        self.assertEqual([int(row["trussness"]) for row in rows], [6] * 10)

    def test_random_small_graphs_match_direct_fixed_point(self) -> None:
        generator = random.Random(5105)
        for sample in range(12):
            n = 5 + sample % 4
            probability = 0.20 + 0.65 * generator.random()
            edge_list = [
                edge
                for edge in itertools.combinations(range(n), 2)
                if generator.random() < probability
            ]
            if not edge_list:
                edge_list = [(0, 1)]
            generator.shuffle(edge_list)
            expected_support, expected_truss = direct_fixed_point(set(edge_list))
            rows = run_backend(edge_list)
            with self.subTest(sample=sample):
                self.assertEqual(
                    [int(row["initial_support"]) for row in rows],
                    [expected_support[normalize(edge)] for edge in edge_list],
                )
                self.assertEqual(
                    [int(row["trussness"]) for row in rows],
                    [expected_truss[normalize(edge)] for edge in edge_list],
                )


if __name__ == "__main__":
    unittest.main()
