from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil

import pytest

from test_strict_judge_runner import make_direct_canonical, write_packet_and_manifest

SCRIPT = Path(__file__).parents[1] / "scripts" / "aggregate_canonical_judge_results.py"
SPEC = importlib.util.spec_from_file_location("canonical_aggregator_v7", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


def install_cell(root: Path, result_root: Path):
    packet, row, manifest = write_packet_and_manifest(root)
    cell = result_root / "results" / "judge-X" / row["packet_id"]
    cell.mkdir(parents=True)
    canonical = make_direct_canonical(cell, packet, row, model="judge-X")
    return packet, row, manifest, canonical


def test_aggregator_requires_and_emits_exact_validated_cartesian_matrix(tmp_path: Path):
    result_root = tmp_path / "run"
    _, _, manifest, _ = install_cell(tmp_path, result_root)
    audit = m.aggregate(
        root=tmp_path,
        packet_manifest=manifest,
        result_roots=[result_root],
        output_dir=tmp_path / "aggregate",
        judges=["judge-X"],
        expected_packets=1,
        expected_judges=1,
    )
    assert audit["result"] == "PASS"
    assert audit["validated_unique_cell_count"] == 1
    assert audit["missing_cell_count"] == 0
    scores = (tmp_path / "aggregate" / "canonical_scores.csv").read_text(encoding="utf-8")
    assert "minor_or_moderate_error_present" in scores
    assert "tested-model-X" in scores
    record = json.loads((tmp_path / "aggregate" / "canonical_records.jsonl").read_text(encoding="utf-8"))
    assert record["canonical"]["trajectory_grade"] == 4


def test_aggregator_fails_on_missing_duplicate_or_tampered_cell(tmp_path: Path):
    result_root = tmp_path / "run"
    _, _, manifest, canonical = install_cell(tmp_path, result_root)
    empty_root = tmp_path / "empty"
    empty_root.mkdir()
    with pytest.raises(ValueError, match="canonical_candidate_count"):
        m.aggregate(
            root=tmp_path, packet_manifest=manifest, result_roots=[empty_root],
            output_dir=tmp_path / "out-missing", judges=["judge-X"],
            expected_packets=1, expected_judges=1,
        )

    duplicate_root = tmp_path / "duplicate"
    duplicate_cell = duplicate_root / "results" / "judge-X" / "packet_1"
    duplicate_cell.parent.mkdir(parents=True)
    shutil.copytree(canonical.parent, duplicate_cell)
    with pytest.raises(ValueError, match="canonical_candidate_count"):
        m.aggregate(
            root=tmp_path, packet_manifest=manifest, result_roots=[result_root, duplicate_root],
            output_dir=tmp_path / "out-duplicate", judges=["judge-X"],
            expected_packets=1, expected_judges=1,
        )

    canonical_data = json.loads(canonical.read_text(encoding="utf-8"))
    canonical_data["trajectory_grade"] = 5
    canonical.write_text(json.dumps(canonical_data), encoding="utf-8")
    with pytest.raises(ValueError, match="canonical_validation_failed"):
        m.aggregate(
            root=tmp_path, packet_manifest=manifest, result_roots=[result_root],
            output_dir=tmp_path / "out-tampered", judges=["judge-X"],
            expected_packets=1, expected_judges=1,
        )
