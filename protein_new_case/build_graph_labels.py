#!/usr/bin/env python3
"""
Step 1: build full intrachain residue contact graphs and DSSP labels.

For every PDB entry and C-alpha cutoff r, the graph contains:

1. backbone edges between consecutive usable residues in the same chain when
   their C-alpha distance is at most --backbone-ca-max;
2. every same-chain C-alpha contact (i, j) satisfying

       |chain_index(i) - chain_index(j)| >= --min-contact-seq-sep
       and distance(CA_i, CA_j) <= r.

The default --min-contact-seq-sep is 2. Therefore the contact graph is not
restricted to sequence separations {2, 3, 4, 5}; it includes all short-,
medium-, and long-range intrachain contacts that satisfy the spatial cutoff.
Cross-chain contacts are deliberately excluded.
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

import pandas as pd
import requests
from tqdm import tqdm

try:
    from Bio.PDB import MMCIFParser
    from Bio.PDB.DSSP import DSSP
    from Bio.PDB.Polypeptide import is_aa
except Exception as exc:  # pragma: no cover
    raise SystemExit(
        "Missing Biopython. Install dependencies with:\n"
        "  python3 -m pip install -r requirements.txt\n"
        f"Original import error: {exc}"
    )

Edge = Tuple[int, int]
Adj = List[Set[int]]

DEFAULT_PDB_IDS = ["1UBQ"]

AA3_TO_1 = {
    "ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C",
    "GLN": "Q", "GLU": "E", "GLY": "G", "HIS": "H", "ILE": "I",
    "LEU": "L", "LYS": "K", "MET": "M", "PHE": "F", "PRO": "P",
    "SER": "S", "THR": "T", "TRP": "W", "TYR": "Y", "VAL": "V",
    "SEC": "U", "PYL": "O",
}


def norm_edge(u: int, v: int) -> Edge:
    return (u, v) if u < v else (v, u)


def residue_uid(residue) -> str:
    het, resseq, icode = residue.id
    return f"{het.strip() or '*'}:{resseq}:{icode.strip() or '*'}"


@dataclass
class ResidueRecord:
    node_id: int
    pdb_id: str
    chain_id: str
    chain_index: int
    residue_uid: str
    resname: str
    aa: str
    dssp_ss: str
    is_alpha_helix_raw: int
    ca_x: float
    ca_y: float
    ca_z: float


@dataclass
class GraphBundle:
    adj: Adj
    edges: Set[Edge]
    residues: List[ResidueRecord]
    edge_types: Dict[Edge, Set[str]]
    edge_seq_sep: Dict[Edge, Optional[int]]
    edge_ca_distance: Dict[Edge, float]


def parse_pdb_ids(arg: str) -> List[str]:
    ids: List[str] = []
    for value in arg.replace("\n", ",").split(","):
        value = value.strip()
        if value:
            ids.append(value.upper())
    return ids


def parse_float_list(arg: str) -> List[float]:
    return [float(value.strip()) for value in arg.split(",") if value.strip()]


def parse_exclude_ss(arg: str) -> Set[str]:
    return {value.strip() for value in arg.split(",") if value.strip()}


def cutoff_tag(cutoff: float) -> str:
    return str(cutoff).replace(".", "p")


def check_executable(exe: str) -> str:
    path = shutil.which(exe)
    if path is None:
        raise SystemExit(
            f"Cannot find DSSP executable '{exe}'.\n"
            "Use --dssp-exe /full/path/to/mkdssp or install DSSP."
        )
    try:
        out = subprocess.check_output(
            [exe, "--version"],
            stderr=subprocess.STDOUT,
            text=True,
            timeout=15,
        )
        first = out.strip().splitlines()[0] if out.strip() else path
        print(f"[INFO] DSSP found: {first}")
        return first
    except Exception:
        print(f"[INFO] DSSP found at {path}; version command failed but continuing.")
        return "unknown"


def download_cif(pdb_id: str, cache_dir: Path, overwrite: bool = False) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    out = cache_dir / f"{pdb_id.lower()}.cif"
    if out.exists() and out.stat().st_size > 0 and not overwrite:
        return out

    url = f"https://files.rcsb.org/download/{pdb_id.upper()}.cif"
    print(f"[INFO] Downloading {pdb_id.upper()} from {url}")
    response = requests.get(url, timeout=60)
    if response.status_code != 200 or not response.text.lstrip().startswith("data_"):
        raise RuntimeError(
            f"Failed to download {pdb_id.upper()} from {url}; HTTP {response.status_code}"
        )
    out.write_text(response.text)
    return out


def run_dssp(model, cif_path: Path, dssp_exe: str):
    return DSSP(model, str(cif_path), dssp=dssp_exe, file_type="MMCIF")


def load_entry_records(
    pdb_id: str,
    cif_path: Path,
    dssp_exe: str,
    min_chain_len: int,
) -> List[ResidueRecord]:
    parser = MMCIFParser(QUIET=True)
    structure = parser.get_structure(pdb_id.lower(), str(cif_path))
    model = structure[0]
    dssp = run_dssp(model, cif_path, dssp_exe)

    # Keep the original filtering behavior so that labels are directly
    # comparable with the preceding local-graph experiment.
    chain_lengths: Dict[str, int] = {}
    for chain in model:
        count = 0
        for residue in chain:
            if is_aa(residue, standard=False) and "CA" in residue:
                if (chain.id, residue.id) in dssp:
                    count += 1
        chain_lengths[chain.id] = count

    records: List[ResidueRecord] = []
    for chain in model:
        if chain_lengths.get(chain.id, 0) < min_chain_len:
            continue

        chain_index = 0
        for residue in chain:
            if not (is_aa(residue, standard=False) and "CA" in residue):
                continue
            key = (chain.id, residue.id)
            if key not in dssp:
                continue

            dssp_tuple = dssp[key]
            ss = dssp_tuple[2]
            if ss == " ":
                ss = "-"
            resname = residue.get_resname().strip().upper()
            aa = dssp_tuple[1] if dssp_tuple[1] != "!" else AA3_TO_1.get(resname, "X")
            ca = residue["CA"].coord.astype(float)

            records.append(
                ResidueRecord(
                    node_id=-1,
                    pdb_id=pdb_id.upper(),
                    chain_id=chain.id,
                    chain_index=chain_index,
                    residue_uid=residue_uid(residue),
                    resname=resname,
                    aa=aa,
                    dssp_ss=ss,
                    is_alpha_helix_raw=1 if ss == "H" else 0,
                    ca_x=float(ca[0]),
                    ca_y=float(ca[1]),
                    ca_z=float(ca[2]),
                )
            )
            chain_index += 1

    for node_id, record in enumerate(records):
        record.node_id = node_id
    return records


def chain_groups(records: Sequence[ResidueRecord]) -> Dict[Tuple[str, str], List[ResidueRecord]]:
    by_chain: Dict[Tuple[str, str], List[ResidueRecord]] = defaultdict(list)
    for record in records:
        by_chain[(record.pdb_id, record.chain_id)].append(record)
    for chain_records in by_chain.values():
        chain_records.sort(key=lambda record: record.chain_index)
    return by_chain


def compute_eval_labels(
    records: List[ResidueRecord],
    label_mode: str,
    min_helix_len: int,
    helix_core_trim: int,
    exclude_noncore_H: bool,
    exclude_ss: Set[str],
) -> Tuple[pd.DataFrame, Dict[str, int]]:
    n = len(records)
    y = [0] * n
    eval_mask = [True] * n

    if label_mode == "dssp-H":
        for record in records:
            y[record.node_id] = 1 if record.dssp_ss == "H" else 0
    elif label_mode == "helix-core":
        for chain_records in chain_groups(records).values():
            i = 0
            while i < len(chain_records):
                if chain_records[i].dssp_ss != "H":
                    i += 1
                    continue
                j = i
                while j < len(chain_records) and chain_records[j].dssp_ss == "H":
                    j += 1
                if j - i >= min_helix_len:
                    start = i + helix_core_trim
                    end = j - helix_core_trim
                    if start < end:
                        for k in range(start, end):
                            y[chain_records[k].node_id] = 1
                i = j
    else:  # pragma: no cover - argparse prevents this
        raise ValueError("label_mode must be 'dssp-H' or 'helix-core'")

    for record in records:
        if record.dssp_ss in exclude_ss:
            eval_mask[record.node_id] = False
        if label_mode == "helix-core" and exclude_noncore_H:
            if record.dssp_ss == "H" and y[record.node_id] == 0:
                eval_mask[record.node_id] = False

    # A positive label is always retained even if another exclusion rule also
    # matches it.
    for node_id, value in enumerate(y):
        if value == 1:
            eval_mask[node_id] = True

    rows: List[Dict[str, object]] = []
    for record in records:
        rows.append({
            "pdb_id": record.pdb_id,
            "node_id": record.node_id,
            "chain_id": record.chain_id,
            "chain_index": record.chain_index,
            "residue_uid": record.residue_uid,
            "resname": record.resname,
            "aa": record.aa,
            "dssp_ss": record.dssp_ss,
            "is_alpha_helix_raw_H": record.is_alpha_helix_raw,
            "is_eval_residue": int(eval_mask[record.node_id]),
            "eval_label": int(y[record.node_id]) if eval_mask[record.node_id] else "",
        })

    eval_y = [y[node_id] for node_id in range(n) if eval_mask[node_id]]
    stats = {
        "eval_n": int(sum(eval_mask)),
        "positive_n": int(sum(eval_y)),
        "excluded_n": int(n - sum(eval_mask)),
        "raw_H_n": int(sum(record.dssp_ss == "H" for record in records)),
    }
    return pd.DataFrame(rows), stats


def dist_ca(a: ResidueRecord, b: ResidueRecord) -> float:
    dx = a.ca_x - b.ca_x
    dy = a.ca_y - b.ca_y
    dz = a.ca_z - b.ca_z
    return math.sqrt(dx * dx + dy * dy + dz * dz)


def add_typed_edge(
    edges: Set[Edge],
    edge_types: Dict[Edge, Set[str]],
    edge_seq_sep: Dict[Edge, Optional[int]],
    edge_ca_distance: Dict[Edge, float],
    u: int,
    v: int,
    typ: str,
    seq_sep: Optional[int],
    ca_distance: float,
) -> None:
    if u == v:
        return
    edge = norm_edge(u, v)
    edges.add(edge)
    edge_types[edge].add(typ)

    if edge not in edge_seq_sep:
        edge_seq_sep[edge] = seq_sep
    elif edge_seq_sep[edge] != seq_sep:
        edge_seq_sep[edge] = None

    # An undirected residue pair has a unique C-alpha distance. min() makes the
    # function robust if the same edge is inserted through multiple types.
    edge_ca_distance[edge] = min(edge_ca_distance.get(edge, ca_distance), ca_distance)


def build_full_intrachain_graph(
    records: List[ResidueRecord],
    ca_cutoff: float,
    min_contact_seq_sep: int,
    backbone_ca_max: float,
) -> GraphBundle:
    """Build a full same-chain C-alpha contact graph.

    All residue pairs in a chain are considered. A non-backbone contact is
    retained when its sequence separation is at least min_contact_seq_sep and
    its C-alpha distance does not exceed ca_cutoff.
    """
    if min_contact_seq_sep < 1:
        raise ValueError("--min-contact-seq-sep must be at least 1")
    if ca_cutoff <= 0 or backbone_ca_max <= 0:
        raise ValueError("distance cutoffs must be positive")

    edges: Set[Edge] = set()
    edge_types: Dict[Edge, Set[str]] = defaultdict(set)
    edge_seq_sep: Dict[Edge, Optional[int]] = {}
    edge_ca_distance: Dict[Edge, float] = {}

    by_chain = chain_groups(records)

    # Explicit backbone edges.
    for chain_records in by_chain.values():
        for a, b in zip(chain_records, chain_records[1:]):
            if b.chain_index != a.chain_index + 1:
                continue
            distance = dist_ca(a, b)
            if distance <= backbone_ca_max:
                add_typed_edge(
                    edges,
                    edge_types,
                    edge_seq_sep,
                    edge_ca_distance,
                    a.node_id,
                    b.node_id,
                    "backbone",
                    1,
                    distance,
                )

    # Full intrachain contacts: no upper bound on sequence separation.
    for chain_records in by_chain.values():
        for left in range(len(chain_records)):
            a = chain_records[left]
            for right in range(left + 1, len(chain_records)):
                b = chain_records[right]
                seq_sep = b.chain_index - a.chain_index
                if seq_sep < min_contact_seq_sep:
                    continue
                distance = dist_ca(a, b)
                if distance <= ca_cutoff:
                    add_typed_edge(
                        edges,
                        edge_types,
                        edge_seq_sep,
                        edge_ca_distance,
                        a.node_id,
                        b.node_id,
                        "contact",
                        seq_sep,
                        distance,
                    )

    adj: Adj = [set() for _ in range(len(records))]
    for u, v in edges:
        adj[u].add(v)
        adj[v].add(u)

    return GraphBundle(
        adj=adj,
        edges=edges,
        residues=records,
        edge_types=dict(edge_types),
        edge_seq_sep=edge_seq_sep,
        edge_ca_distance=edge_ca_distance,
    )


def graph_stats(bundle: GraphBundle) -> Dict[str, object]:
    contact_edges = [
        edge for edge, types in bundle.edge_types.items() if "contact" in types
    ]
    backbone_edges = [
        edge for edge, types in bundle.edge_types.items() if "backbone" in types
    ]
    both_edges = [
        edge
        for edge, types in bundle.edge_types.items()
        if "contact" in types and "backbone" in types
    ]

    sep_2_5 = sep_6_11 = sep_12_23 = sep_24_plus = 0
    for edge in contact_edges:
        sep = bundle.edge_seq_sep.get(edge)
        if sep is None:
            continue
        if 2 <= sep <= 5:
            sep_2_5 += 1
        elif 6 <= sep <= 11:
            sep_6_11 += 1
        elif 12 <= sep <= 23:
            sep_12_23 += 1
        elif sep >= 24:
            sep_24_plus += 1

    degrees = [len(neighbors) for neighbors in bundle.adj]
    return {
        "n_vertices": len(bundle.residues),
        "n_edges": len(bundle.edges),
        "n_backbone_edges": len(backbone_edges),
        "n_contact_edges": len(contact_edges),
        "n_backbone_and_contact_edges": len(both_edges),
        "mean_degree": (sum(degrees) / len(degrees)) if degrees else 0.0,
        "max_degree": max(degrees) if degrees else 0,
        "contact_seqsep_2_5": sep_2_5,
        "contact_seqsep_6_11": sep_6_11,
        "contact_seqsep_12_23": sep_12_23,
        "contact_seqsep_24_plus": sep_24_plus,
    }


def bundle_to_node_rows(bundle: GraphBundle) -> List[Dict[str, object]]:
    return [asdict(record) for record in bundle.residues]


def bundle_to_edge_rows(bundle: GraphBundle, cutoff: float) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    for u, v in sorted(bundle.edges):
        rows.append({
            "pdb_id": bundle.residues[u].pdb_id,
            "cutoff": cutoff,
            "u": u,
            "v": v,
            "edge_types": ";".join(sorted(bundle.edge_types.get((u, v), set()))),
            "seq_sep": bundle.edge_seq_sep.get((u, v)),
            "ca_distance": bundle.edge_ca_distance[(u, v)],
        })
    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build full intrachain C-alpha contact graphs and DSSP helix labels."
    )
    parser.add_argument("--pdb-ids", default=",".join(DEFAULT_PDB_IDS))
    parser.add_argument("--cutoffs", default="6.5,7.0")
    parser.add_argument("--cache-dir", default="./pdb_cache")
    parser.add_argument("--out-dir", default="./data_full_intrachain")
    parser.add_argument("--dssp-exe", default="mkdssp")
    parser.add_argument(
        "--min-contact-seq-sep",
        type=int,
        default=2,
        help=(
            "Minimum same-chain sequence separation for spatial contact edges. "
            "Default 2 excludes adjacent residues from the contact rule; they "
            "are represented by backbone edges. There is no maximum separation."
        ),
    )
    parser.add_argument("--backbone-ca-max", type=float, default=4.5)
    parser.add_argument("--min-chain-len", type=int, default=20)
    parser.add_argument("--label-mode", choices=["dssp-H", "helix-core"], default="helix-core")
    parser.add_argument("--min-helix-len", type=int, default=7)
    parser.add_argument("--helix-core-trim", type=int, default=2)

    noncore_group = parser.add_mutually_exclusive_group()
    noncore_group.add_argument(
        "--exclude-noncore-H",
        dest="exclude_noncore_H",
        action="store_true",
        help="Exclude H residues outside the trimmed helix core (default).",
    )
    noncore_group.add_argument(
        "--include-noncore-H",
        dest="exclude_noncore_H",
        action="store_false",
        help="Keep H residues outside the trimmed helix core as evaluated negatives.",
    )
    parser.set_defaults(exclude_noncore_H=True)

    parser.add_argument("--exclude-ss", default="G,I")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    dssp_version = check_executable(args.dssp_exe)

    pdb_ids = parse_pdb_ids(args.pdb_ids)
    cutoffs = parse_float_list(args.cutoffs)
    exclude_ss = parse_exclude_ss(args.exclude_ss)
    if not pdb_ids:
        raise ValueError("--pdb-ids is empty")
    if not cutoffs:
        raise ValueError("--cutoffs is empty")

    out_dir = Path(args.out_dir)
    cache_dir = Path(args.cache_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    all_nodes: List[Dict[str, object]] = []
    all_labels: List[Dict[str, object]] = []
    all_graph_summary: List[Dict[str, object]] = []
    all_edges_by_cutoff: Dict[float, List[Dict[str, object]]] = {
        cutoff: [] for cutoff in cutoffs
    }
    failed: List[Tuple[str, str]] = []

    print(f"[INFO] PDB IDs: {','.join(pdb_ids)}")
    print(f"[INFO] C-alpha cutoffs: {cutoffs}")
    print("[INFO] graph_mode=full_intrachain")
    print(
        "[INFO] contact rule: same chain, "
        f"seq_sep >= {args.min_contact_seq_sep}, no maximum seq_sep"
    )
    print("[INFO] cross-chain contacts are excluded")

    for pdb_id in tqdm(pdb_ids, desc="build full intrachain graphs"):
        try:
            cif_path = download_cif(
                pdb_id,
                cache_dir=cache_dir,
                overwrite=args.overwrite,
            )
            records = load_entry_records(
                pdb_id,
                cif_path,
                args.dssp_exe,
                min_chain_len=args.min_chain_len,
            )
            if not records:
                failed.append((pdb_id, "no usable residues/chains after filtering"))
                continue

            label_df, label_stats = compute_eval_labels(
                records,
                label_mode=args.label_mode,
                min_helix_len=args.min_helix_len,
                helix_core_trim=args.helix_core_trim,
                exclude_noncore_H=args.exclude_noncore_H,
                exclude_ss=exclude_ss,
            )

            all_nodes.extend(asdict(record) for record in records)
            all_labels.extend(label_df.to_dict("records"))

            for cutoff in cutoffs:
                bundle = build_full_intrachain_graph(
                    records=records,
                    ca_cutoff=cutoff,
                    min_contact_seq_sep=args.min_contact_seq_sep,
                    backbone_ca_max=args.backbone_ca_max,
                )
                stats = graph_stats(bundle)
                all_edges_by_cutoff[cutoff].extend(bundle_to_edge_rows(bundle, cutoff))
                all_graph_summary.append({
                    "pdb_id": pdb_id,
                    "cutoff": cutoff,
                    "graph_mode": "full_intrachain",
                    "min_contact_seq_sep": args.min_contact_seq_sep,
                    "same_chain_only": 1,
                    **stats,
                    **label_stats,
                })

                print(
                    f"{pdb_id:5s} cutoff={cutoff:>3.1f} "
                    f"|V|={int(stats['n_vertices']):4d} "
                    f"|E|={int(stats['n_edges']):5d} "
                    f"contacts={int(stats['n_contact_edges']):5d} "
                    f"long(>=24)={int(stats['contact_seqsep_24_plus']):4d}"
                )

        except Exception as exc:
            failed.append((pdb_id, str(exc)))
            print(f"[WARN] {pdb_id} failed: {exc}")

    pd.DataFrame(all_nodes).to_csv(out_dir / "nodes.csv", index=False)
    pd.DataFrame(all_labels).to_csv(out_dir / "labels.csv", index=False)
    pd.DataFrame(all_graph_summary).to_csv(out_dir / "graph_summary.csv", index=False)

    for cutoff, rows in all_edges_by_cutoff.items():
        pd.DataFrame(rows).to_csv(
            out_dir / f"edges_ca{cutoff_tag(cutoff)}.csv",
            index=False,
        )

    failed_path = out_dir / "failed_entries.csv"
    if failed:
        pd.DataFrame(failed, columns=["pdb_id", "error"]).to_csv(failed_path, index=False)
    elif failed_path.exists():
        failed_path.unlink()

    params = vars(args).copy()
    params["pdb_ids"] = pdb_ids
    params["cutoffs"] = cutoffs
    params["exclude_ss"] = sorted(exclude_ss)
    params["graph_mode"] = "full_intrachain"
    params["same_chain_only"] = True
    params["dssp_version"] = dssp_version
    params["maximum_contact_seq_sep"] = None
    params["score_mode_for_step2"] = "incident_max"
    with open(out_dir / "run_parameters.json", "w") as handle:
        json.dump(params, handle, indent=2)

    print(f"\n[OK] Saved graph data to: {out_dir.resolve()}")
    print("  - nodes.csv")
    print("  - labels.csv")
    print("  - graph_summary.csv")
    print("  - run_parameters.json")
    for cutoff in cutoffs:
        print(f"  - edges_ca{cutoff_tag(cutoff)}.csv")
    if failed:
        print("  - failed_entries.csv")


if __name__ == "__main__":
    main()
