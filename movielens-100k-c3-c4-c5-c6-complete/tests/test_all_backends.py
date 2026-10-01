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
SHORT = ROOT / "bin" / "short_cycle_truss"
C6 = ROOT / "bin" / "streaming_c6_truss"


def normalize(edge: tuple[int, int]) -> tuple[int, int]:
    left, right = edge
    return (left, right) if left < right else (right, left)


def enumerate_cycles(
    edges: set[tuple[int, int]], length: int
) -> list[tuple[tuple[int, int], ...]]:
    adjacency: dict[int, set[int]] = {}
    for left, right in edges:
        adjacency.setdefault(left, set()).add(right)
        adjacency.setdefault(right, set()).add(left)
    output: list[tuple[tuple[int, int], ...]] = []
    for start in sorted(adjacency):
        path = [start]
        visited = {start}

        def dfs(current: int) -> None:
            if len(path) == length:
                if normalize((current, start)) in edges and path[1] < path[-1]:
                    output.append(
                        tuple(
                            normalize((path[index], path[(index + 1) % length]))
                            for index in range(length)
                        )
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
    return output


def direct_fixed_point(
    edges: set[tuple[int, int]], length: int
) -> tuple[dict[tuple[int, int], int], dict[tuple[int, int], int]]:
    initial_cycles = enumerate_cycles(edges, length)
    initial = Counter(edge for cycle in initial_cycles for edge in cycle)
    truss = {edge: 0 for edge in edges}
    for threshold in range(1, max(initial.values(), default=0) + 1):
        active = set(edges)
        while True:
            support = Counter(
                edge for cycle in enumerate_cycles(active, length) for edge in cycle
            )
            removed = {edge for edge in active if support[edge] < threshold}
            if not removed:
                break
            active.difference_update(removed)
        for edge in active:
            truss[edge] = threshold
    return ({edge: initial[edge] for edge in edges}, truss)


def run_backend(
    binary: Path, edge_list: list[tuple[int, int]], length: int | None = None
) -> list[dict[str, str]]:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        graph = root / "graph.txt"
        result = root / "result.tsv"
        graph.write_text(
            "".join(f"{left}\t{right}\n" for left, right in edge_list),
            encoding="ascii",
        )
        command = [str(binary)]
        if length is not None:
            command.extend(["--length", str(length)])
        command.extend([str(graph), str(result)])
        subprocess.run(
            command,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        with result.open(encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle, delimiter="\t"))


class AllBackendTests(unittest.TestCase):
    def test_isolated_cycles(self) -> None:
        for length, binary in ((3, SHORT), (4, SHORT), (6, C6)):
            edges = [
                normalize((index, (index + 1) % length))
                for index in range(length)
            ]
            rows = run_backend(binary, edges, length if length in {3, 4} else None)
            with self.subTest(length=length):
                self.assertEqual(
                    [int(row["initial_support"]) for row in rows], [1] * length
                )
                self.assertEqual(
                    [int(row["trussness"]) for row in rows], [1] * length
                )

    def test_random_small_c3_c4_c6_match_direct_fixed_point(self) -> None:
        generator = random.Random(3466)
        for length, binary, samples in ((3, SHORT, 10), (4, SHORT, 10), (6, C6, 6)):
            for sample in range(samples):
                n = max(length, 5 + sample % 3)
                probability = 0.20 + 0.55 * generator.random()
                edge_list = [
                    edge
                    for edge in itertools.combinations(range(n), 2)
                    if generator.random() < probability
                ]
                if not edge_list:
                    edge_list = [(0, 1)]
                generator.shuffle(edge_list)
                expected_support, expected_truss = direct_fixed_point(
                    set(map(normalize, edge_list)), length
                )
                rows = run_backend(
                    binary,
                    edge_list,
                    length if length in {3, 4} else None,
                )
                with self.subTest(length=length, sample=sample):
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
