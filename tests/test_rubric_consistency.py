from __future__ import annotations

import hashlib
import json
from pathlib import Path

from careloop.evaluation import protocol_v7 as protocol

ROOT = Path(__file__).parents[1]
AUTHORITATIVE_JSON = ROOT / "rubrics" / "careloop_calibrated_v1.1_legacy_preserving_v7.json"
AUTHORITATIVE_MD = ROOT / "rubrics" / "careloop_calibrated_v1.1_legacy_preserving_v7.md"
JSON_ALIASES = [
    ROOT / "rubrics" / "careloop_trajectory_judge_v2_ordinal_1to5.json",
    ROOT / "rubrics" / "medical_trajectory_quality_rubric_v1.0.json",
]
MD_ALIASES = [
    ROOT / "rubrics" / "careloop_trajectory_judge_v2_ordinal_1to5.md",
    ROOT / "rubrics" / "medical_trajectory_quality_rubric_v1.0.md",
]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_authoritative_rubric_matches_executable_protocol_and_population():
    rubric = json.loads(AUTHORITATIVE_JSON.read_text(encoding="utf-8"))
    assert rubric["status"] == "sole_authoritative_canonical_rejudging_rubric"
    assert rubric["case_contract_rubric_version"] == protocol.CASE_RUBRIC_VERSION
    policy = rubric["case_contract_policy"]
    assert policy["source"] == "original_frozen_2026_09_04_case_files"
    assert policy["rc_count_allowed"] == [5, 6, 7, 8, 9]
    assert policy["high_order_count_required"] == 4
    assert policy["merge_or_drop_rc_items"] is False
    assert policy["infer_scalar_severity_or_closure_blocking_flags"] is False
    assert rubric["expected_population"] == {
        "cases": 120,
        "tested_models": 10,
        "trajectories": 1200,
        "judges": 4,
        "assessments": 4800,
    }
    assert rubric["score_scale"]["allowed_values"] == sorted(protocol.LABELS)
    assert rubric["labels"] == {str(score): label for score, label in protocol.LABELS.items()}
    assert rubric["dimensions"] == list(protocol.DIMENSIONS)
    assert set(rubric["dimension_values"]) == protocol.DIMENSION_VALUES
    assert rubric["executable_reference"] == "careloop/evaluation/protocol_v7.py"
    assert rubric["compatibility_alias_paths"] == [
        "rubrics/careloop_trajectory_judge_v2_ordinal_1to5.json",
        "rubrics/medical_trajectory_quality_rubric_v1.0.json",
    ]
    assert all((ROOT / path).is_file() for path in rubric["compatibility_alias_paths"])


def test_compatibility_rubric_paths_are_byte_identical_not_divergent():
    expected_json = sha(AUTHORITATIVE_JSON)
    expected_md = sha(AUTHORITATIVE_MD)
    assert all(sha(path) == expected_json for path in JSON_ALIASES)
    assert all(sha(path) == expected_md for path in MD_ALIASES)


def test_embedded_prompt_states_all_score_gates_without_omitting_closure():
    text = protocol.RUBRIC
    assert "Serious error mandates 1" in text
    assert "error caps the grade at 2" in text
    assert "closure is safe, executable, and traceable as valid_closure or valid_open" in text
    assert "at least two case-defined high-order points" in text
    assert "near-complete" in text
    assert "conditional guidance" in text
