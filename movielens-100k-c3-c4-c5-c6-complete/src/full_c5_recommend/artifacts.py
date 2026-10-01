from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from .utils import atomic_write_text, key_edge, sha256_file, write_tsv

EdgeKey = tuple[str, str]


def node_key(node_type: str, entity_id: str) -> str:
    prefix = {"user": "U", "item": "I"}.get(node_type)
    if prefix is None:
        raise ValueError(f"unknown node type: {node_type}")
    return f"{prefix}:{entity_id}"


@dataclass(frozen=True)
class NodeRecord:
    key: str
    node_type: str
    entity_id: str
    title: str = ""
    side_info: str = ""


@dataclass(frozen=True)
class EdgeRecord:
    edge_type: str
    reason: str = ""


@dataclass(frozen=True)
class CaseArtifact:
    root: Path
    nodes: tuple[NodeRecord, ...]
    node_to_id: Mapping[str, int]
    edges: tuple[EdgeKey, ...]
    edge_records: tuple[EdgeRecord, ...]
    truth: tuple[tuple[str, str], ...]
    metadata: Mapping[str, object]
    graph_sha256: str

    @property
    def graph_path(self) -> Path:
        return self.root / "graph.txt"

    @property
    def edge_count(self) -> int:
        return len(self.edges)

    @property
    def edge_types(self) -> tuple[str, ...]:
        return tuple(record.edge_type for record in self.edge_records)


def write_case(
    *,
    output_dir: Path,
    node_records: Mapping[str, NodeRecord],
    edge_records: Mapping[EdgeKey, EdgeRecord],
    truth: Iterable[tuple[str, str]],
    metadata: Mapping[str, object] | None = None,
) -> CaseArtifact:
    """Write a canonical full-graph case.

    The row order of ``graph.txt`` is the public zero-based edge-ID contract
    used by the exact C5 backend and every value file.
    """

    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    nodes = tuple(
        sorted(
            node_records.values(),
            key=lambda record: (
                0 if record.node_type == "user" else 1,
                record.entity_id,
            ),
        )
    )
    if not nodes:
        raise ValueError("cannot write a case without nodes")
    if len({record.key for record in nodes}) != len(nodes):
        raise ValueError("duplicate node key")
    if any(record.node_type not in {"user", "item"} for record in nodes):
        raise ValueError("invalid node type")

    node_to_id = {record.key: node_id for node_id, record in enumerate(nodes)}

    normalized: dict[EdgeKey, EdgeRecord] = {}
    for raw_edge, record in edge_records.items():
        edge = key_edge(*raw_edge)
        if edge[0] not in node_to_id or edge[1] not in node_to_id:
            raise ValueError(f"edge endpoint missing from nodes: {edge}")
        if record.edge_type not in {"UI", "II"}:
            raise ValueError(f"unsupported edge type: {record.edge_type}")
        previous = normalized.setdefault(edge, record)
        if previous != record:
            raise ValueError(f"conflicting edge records: {edge}")

    entries: list[tuple[int, int, EdgeRecord]] = []
    for edge, record in normalized.items():
        left = node_to_id[edge[0]]
        right = node_to_id[edge[1]]
        if left > right:
            left, right = right, left
        entries.append((left, right, record))
    entries.sort(key=lambda row: (row[0], row[1]))
    if not entries:
        raise ValueError("cannot write an empty graph")

    integer_edges = tuple((left, right) for left, right, _ in entries)
    edges = tuple((nodes[left].key, nodes[right].key) for left, right, _ in entries)
    records = tuple(record for _, _, record in entries)

    graph_payload = "".join(f"{left}\t{right}\n" for left, right in integer_edges)
    atomic_write_text(output_dir / "graph.txt", graph_payload, encoding="ascii")
    graph_hash = sha256_file(output_dir / "graph.txt")

    write_tsv(
        output_dir / "nodes.tsv",
        ["node_id", "node_key", "node_type", "entity_id", "title", "side_info"],
        (
            {
                "node_id": node_id,
                "node_key": record.key,
                "node_type": record.node_type,
                "entity_id": record.entity_id,
                "title": record.title,
                "side_info": record.side_info,
            }
            for node_id, record in enumerate(nodes)
        ),
    )

    write_tsv(
        output_dir / "edges.tsv",
        ["edge_id", "src_id", "dst_id", "src_key", "dst_key", "edge_type", "reason"],
        (
            {
                "edge_id": edge_id,
                "src_id": integer_edges[edge_id][0],
                "dst_id": integer_edges[edge_id][1],
                "src_key": edges[edge_id][0],
                "dst_key": edges[edge_id][1],
                "edge_type": records[edge_id].edge_type,
                "reason": records[edge_id].reason,
            }
            for edge_id in range(len(edges))
        ),
    )

    ui_rows: list[tuple[int, int]] = []
    ii_rows: list[tuple[int, int]] = []
    training_ui_keys: set[EdgeKey] = set()
    for edge, record in zip(edges, records):
        left, right = edge
        left_record = nodes[node_to_id[left]]
        right_record = nodes[node_to_id[right]]
        if record.edge_type == "UI":
            if {left_record.node_type, right_record.node_type} != {"user", "item"}:
                raise ValueError(f"UI edge has invalid endpoint types: {edge}")
            user = left if left_record.node_type == "user" else right
            item = right if right_record.node_type == "item" else left
            ui_rows.append((node_to_id[user], node_to_id[item]))
            training_ui_keys.add(key_edge(user, item))
        else:
            if left_record.node_type != "item" or right_record.node_type != "item":
                raise ValueError(f"II edge has invalid endpoint types: {edge}")
            ii_rows.append((node_to_id[left], node_to_id[right]))

    atomic_write_text(
        output_dir / "user_item_edges.txt",
        "".join(f"{user}\t{item}\n" for user, item in sorted(ui_rows)),
        encoding="ascii",
    )
    atomic_write_text(
        output_dir / "item_item_edges.txt",
        "".join(f"{left}\t{right}\n" for left, right in sorted(ii_rows)),
        encoding="ascii",
    )
    atomic_write_text(
        output_dir / "catalog_items.txt",
        "".join(
            f"{node_to_id[record.key]}\n"
            for record in nodes
            if record.node_type == "item"
        ),
        encoding="ascii",
    )
    atomic_write_text(
        output_dir / "users.txt",
        "".join(
            f"{node_to_id[record.key]}\n"
            for record in nodes
            if record.node_type == "user"
        ),
        encoding="ascii",
    )

    normalized_truth: set[tuple[str, str]] = set()
    for user, item in truth:
        if user not in node_to_id or item not in node_to_id:
            raise ValueError(f"truth endpoint missing from graph: {(user, item)}")
        if nodes[node_to_id[user]].node_type != "user":
            raise ValueError(f"truth user endpoint is invalid: {user}")
        if nodes[node_to_id[item]].node_type != "item":
            raise ValueError(f"truth item endpoint is invalid: {item}")
        if key_edge(user, item) in training_ui_keys:
            raise ValueError(f"test edge already exists in training graph: {(user, item)}")
        normalized_truth.add((user, item))

    truth_rows = tuple(sorted(normalized_truth))
    write_tsv(
        output_dir / "test_edges.tsv",
        ["user_node", "item_node", "user_id", "item_id"],
        (
            {
                "user_node": node_to_id[user],
                "item_node": node_to_id[item],
                "user_id": nodes[node_to_id[user]].entity_id,
                "item_id": nodes[node_to_id[item]].entity_id,
            }
            for user, item in truth_rows
        ),
    )
    evaluation_users = sorted({node_to_id[user] for user, _ in truth_rows})
    atomic_write_text(
        output_dir / "evaluation_users.txt",
        "".join(f"{node}\n" for node in evaluation_users),
        encoding="ascii",
    )

    case_metadata: dict[str, object] = dict(metadata or {})
    case_metadata.update(
        {
            "format_version": 1,
            "graph_sha256": graph_hash,
            "node_count": len(nodes),
            "user_count": sum(record.node_type == "user" for record in nodes),
            "item_count": sum(record.node_type == "item" for record in nodes),
            "edge_count": len(edges),
            "user_item_edge_count": len(ui_rows),
            "item_item_edge_count": len(ii_rows),
            "test_edge_count": len(truth_rows),
            "test_user_count": len(evaluation_users),
            "edge_id_contract": "zero-based graph.txt row order",
        }
    )
    atomic_write_text(
        output_dir / "case.json",
        json.dumps(case_metadata, indent=2, sort_keys=True) + "\n",
    )
    return load_case(output_dir)


def load_case(root: Path) -> CaseArtifact:
    root = Path(root).resolve()
    required = ["graph.txt", "nodes.tsv", "edges.tsv", "test_edges.tsv", "case.json"]
    missing = [name for name in required if not (root / name).is_file()]
    if missing:
        raise FileNotFoundError(f"case directory is missing: {missing}")

    metadata = json.loads((root / "case.json").read_text(encoding="utf-8"))
    graph_hash = sha256_file(root / "graph.txt")
    if metadata.get("graph_sha256") != graph_hash:
        raise ValueError("graph.txt SHA-256 differs from case.json")

    nodes_by_id: dict[int, NodeRecord] = {}
    with (root / "nodes.tsv").open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            node_id = int(row["node_id"])
            if node_id in nodes_by_id:
                raise ValueError(f"duplicate node ID: {node_id}")
            nodes_by_id[node_id] = NodeRecord(
                key=row["node_key"],
                node_type=row["node_type"],
                entity_id=row["entity_id"],
                title=row.get("title", ""),
                side_info=row.get("side_info", ""),
            )
    if set(nodes_by_id) != set(range(len(nodes_by_id))):
        raise ValueError("nodes.tsv IDs are not contiguous from zero")
    nodes = tuple(nodes_by_id[index] for index in range(len(nodes_by_id)))
    if len({record.key for record in nodes}) != len(nodes):
        raise ValueError("nodes.tsv contains duplicate node keys")
    node_to_id = {record.key: index for index, record in enumerate(nodes)}

    graph_rows: list[tuple[int, int]] = []
    with (root / "graph.txt").open(encoding="ascii") as handle:
        for line_number, line in enumerate(handle, 1):
            columns = line.split()
            if len(columns) != 2:
                raise ValueError(f"graph.txt line {line_number} is not two columns")
            left, right = map(int, columns)
            if not (0 <= left < right < len(nodes)):
                raise ValueError(f"graph.txt line {line_number} is not a canonical edge")
            graph_rows.append((left, right))
    if len(set(graph_rows)) != len(graph_rows):
        raise ValueError("graph.txt contains duplicate edges")

    edges_by_id: dict[int, EdgeKey] = {}
    records_by_id: dict[int, EdgeRecord] = {}
    with (root / "edges.tsv").open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            edge_id = int(row["edge_id"])
            if edge_id in edges_by_id or not 0 <= edge_id < len(graph_rows):
                raise ValueError(f"invalid or duplicate edge ID: {edge_id}")
            src_id, dst_id = int(row["src_id"]), int(row["dst_id"])
            if (src_id, dst_id) != graph_rows[edge_id]:
                raise ValueError(f"edges.tsv differs from graph.txt at edge {edge_id}")
            edge = (nodes[src_id].key, nodes[dst_id].key)
            if (row["src_key"], row["dst_key"]) != edge:
                raise ValueError(f"edges.tsv keys differ at edge {edge_id}")
            edges_by_id[edge_id] = edge
            records_by_id[edge_id] = EdgeRecord(
                edge_type=row["edge_type"],
                reason=row.get("reason", ""),
            )
    if set(edges_by_id) != set(range(len(graph_rows))):
        raise ValueError("edges.tsv does not cover every graph row")
    edges = tuple(edges_by_id[index] for index in range(len(graph_rows)))
    records = tuple(records_by_id[index] for index in range(len(graph_rows)))

    edge_set = {key_edge(*edge) for edge in edges}
    truth: list[tuple[str, str]] = []
    with (root / "test_edges.tsv").open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            user_node = int(row["user_node"])
            item_node = int(row["item_node"])
            if nodes[user_node].node_type != "user" or nodes[item_node].node_type != "item":
                raise ValueError("test_edges.tsv has invalid endpoint types")
            pair = (nodes[user_node].key, nodes[item_node].key)
            if key_edge(*pair) in edge_set:
                raise ValueError("test edge overlaps the training graph")
            truth.append(pair)

    expected_counts = {
        "node_count": len(nodes),
        "edge_count": len(edges),
        "test_edge_count": len(set(truth)),
    }
    for key, expected in expected_counts.items():
        if int(metadata.get(key, -1)) != expected:
            raise ValueError(f"case.json {key} differs from files")

    return CaseArtifact(
        root=root,
        nodes=nodes,
        node_to_id=node_to_id,
        edges=edges,
        edge_records=records,
        truth=tuple(sorted(set(truth))),
        metadata=metadata,
        graph_sha256=graph_hash,
    )


def write_value_file(
    case: CaseArtifact,
    values: Iterable[int],
    *,
    output: Path,
    algorithm: str,
    backend: str,
    elapsed_seconds: float,
) -> Path:
    parsed = tuple(int(value) for value in values)
    if len(parsed) != case.edge_count:
        raise ValueError(f"{algorithm} produced {len(parsed)} values, expected {case.edge_count}")
    if any(value < 0 for value in parsed):
        raise ValueError(f"{algorithm} produced a negative value")
    output = Path(output).resolve()
    atomic_write_text(output, "".join(f"{value}\n" for value in parsed), encoding="ascii")
    meta = {
        "format_version": 1,
        "algorithm": algorithm,
        "backend": backend,
        "graph_sha256": case.graph_sha256,
        "edge_count": case.edge_count,
        "line_contract": "line r contains the value for graph.txt line r",
        "elapsed_seconds": elapsed_seconds,
        "positive_edges": sum(value > 0 for value in parsed),
        "maximum_value": max(parsed, default=0),
        "values_sha256": sha256_file(output),
    }
    atomic_write_text(
        output.with_suffix(output.suffix + ".meta.json"),
        json.dumps(meta, indent=2, sort_keys=True) + "\n",
    )
    return output


def load_value_file(
    case: CaseArtifact,
    path: Path,
    *,
    expected_algorithm: str | None = None,
) -> tuple[int, ...]:
    path = Path(path).resolve()
    meta_path = path.with_suffix(path.suffix + ".meta.json")
    if not path.is_file() or not meta_path.is_file():
        raise FileNotFoundError(f"missing values or metadata: {path}")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("graph_sha256") != case.graph_sha256:
        raise ValueError(f"{path} belongs to a different graph")
    if int(meta.get("edge_count", -1)) != case.edge_count:
        raise ValueError(f"{path} edge count differs from graph")
    if meta.get("values_sha256") != sha256_file(path):
        raise ValueError(f"{path} SHA-256 differs from metadata")
    if expected_algorithm is not None and meta.get("algorithm") != expected_algorithm:
        raise ValueError(
            f"{path} algorithm is {meta.get('algorithm')}, expected {expected_algorithm}"
        )
    values: list[int] = []
    with path.open(encoding="ascii") as handle:
        for line_number, line in enumerate(handle, 1):
            text = line.strip()
            if not text.isdigit():
                raise ValueError(f"{path} line {line_number} is not a non-negative integer")
            values.append(int(text))
    if len(values) != case.edge_count:
        raise ValueError(f"{path} has {len(values)} lines, expected {case.edge_count}")
    return tuple(values)
