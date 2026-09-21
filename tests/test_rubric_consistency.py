from __future__ import annotations

import json
from pathlib import Path

from careloop.evaluation import protocol_v7 as protocol

ROOT = Path(__file__).parents[1]
AUTHORITATIVE_JSON = ROOT / "rubrics" / "careloop_trajectory_judge_v2_ordinal_1to5.json"
AUTHORITATIVE_MD = ROOT / "rubrics" / "careloop_trajectory_judge_v2_ordinal_1to5.md"


def test_authoritative_rubric_matches_executable_protocol_and_population():
    rubric = json.loads(AUTHORITATIVE_JSON.read_text(encoding="utf-8"))
    assert rubric["status"] == "canonical_ordinal_comparator_rubric"
    assert rubric["case_contract_rubric_version"] == protocol.CASE_RUBRIC_VERSION
    policy = rubric["case_contract_policy"]
    assert policy["source"] == "selected_public_case_contract"
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
    assert AUTHORITATIVE_MD.is_file()


def test_embedded_prompt_states_all_score_gates_without_omitting_closure():
    text = protocol.RUBRIC
    assert "Serious error mandates 1" in text
    assert "error caps the grade at 2" in text
    assert "closure is safe, executable, and traceable as valid_closure or valid_open" in text
    assert "at least two case-defined high-order points" in text
    assert "near-complete" in text
    assert "conditional guidance" in text
