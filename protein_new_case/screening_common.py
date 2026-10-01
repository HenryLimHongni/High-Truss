#!/usr/bin/env python3
"""Shared, protocol-locked helpers for exploratory and confirmation screening."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Dict, List, Sequence


PREVIOUSLY_USED_IDS = [
    "1K8U", "1JWE", "1C52", "1IKN", "1CNV", "1C1K", "1YPI", "1K9U",
]

# These values deliberately duplicate the established full-intrachain run.
# The screening CLI does not expose per-protein alternatives.
SCREENING_PROTOCOL: Dict[str, object] = {
    "protocol_version": "full-intrachain-c3456-per-cutoff-v2",
    "graph_mode": "full_intrachain",
    "same_chain_only": True,
    "backbone_ca_max_angstrom": 4.5,
    "contact_ca_cutoffs_angstrom": [6.5, 7.0],
    "minimum_contact_sequence_separation": 2,
    "maximum_contact_sequence_separation": None,
    "minimum_chain_length": 20,
    "label_mode": "helix-core",
    "minimum_helix_length": 7,
    "helix_core_trim_each_end": 2,
    "exclude_noncore_dssp_H": True,
    "excluded_dssp_secondary_structures": ["G", "I"],
    "cycle_lengths": [3, 4, 5, 6],
    "residue_score": "maximum incident edge cycle-truss level",
    "roc_metric": "sklearn.metrics.roc_auc_score",
    "pr_metric": "sklearn.metrics.average_precision_score",
    "protein_selection_metric": "per-cutoff PR-AUC; never averaged across cutoffs",
    "c5_exploratory_margin_threshold": 0.015,
    "c5_c6_crossover_metric": "PR-AUC",
    "c5_c6_crossover_rule": (
        "sign(C5_PR-C6_PR) is opposite at cutoffs 6.5 and 7.0"
    ),
}


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def protocol_sha256() -> str:
    return sha256_text(canonical_json(SCREENING_PROTOCOL))


def parse_candidate_file(path: Path) -> List[str]:
    """Read ordered, unique four-character PDB IDs; comments start with #."""
    if not path.exists():
        raise FileNotFoundError(path)

    values: List[str] = []
    seen = set()
    for line_number, raw_line in enumerate(path.read_text().splitlines(), start=1):
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        for token in re.split(r"[\s,]+", line):
            pdb_id = token.upper()
            if not re.fullmatch(r"[0-9][A-Z0-9]{3}", pdb_id):
                raise ValueError(
                    f"{path}:{line_number}: invalid four-character PDB ID {token!r}"
                )
            if pdb_id not in seen:
                values.append(pdb_id)
                seen.add(pdb_id)
    if not values:
        raise ValueError(f"candidate file is empty: {path}")
    return values


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def candidate_manifest(path: Path, candidates: Sequence[str], phase: str) -> Dict[str, object]:
    return {
        "phase": phase,
        "candidate_file": str(path.resolve()),
        "candidate_file_sha256": file_sha256(path),
        "candidate_count": len(candidates),
        "candidate_ids": list(candidates),
        "previously_used_ids": PREVIOUSLY_USED_IDS,
        "protocol": SCREENING_PROTOCOL,
        "protocol_sha256": protocol_sha256(),
    }
