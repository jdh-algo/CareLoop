from __future__ import annotations

import copy

import pytest

from careloop.evaluation import protocol_v7 as p


GENERIC = "Ensure the current episode has a safe, specific, patient-executable next-step plan."


def source_case(*, rc_count: int = 6, placeholder: bool = False, nested: bool = False) -> dict:
    specific = [
        {
            "id": f"RC{i}_authored" if i <= 2 else f"R{i}",
            "requirement": f"Authored case-specific duty {i} with conditional safety and execution requirements.",
            "serious_failure_examples": [f"Serious consequence example {i}."],
            "minor_failure_examples": [f"Minor execution defect example {i}."],
        }
        for i in range(1, rc_count + 1)
    ]
    primary = [
        {
            "id": f"RC{i}",
            "category": "action_plan",
            "description": GENERIC,
            "why_required_for_current_episode": "template",
            "failure_severity_if_missed": "serious",
            "evidence_expected_in_trajectory": ["action", "receipt"],
            "closure_blocking_if_unmet": True,
        }
        for i in range(1, 6)
    ] if placeholder else [
        {
            "id": f"RC{i}",
            "description": f"Already-normalized authored case-specific duty {i}.",
        }
        for i in range(1, rc_count + 1)
    ]
    ho = [
        {"id": f"HO{i}", "point": f"Authored high-order opportunity {i}."}
        for i in range(1, 5)
    ]
    ec = {
        "rubric_version": p.CASE_RUBRIC_VERSION,
        "purpose": "frozen test contract",
        "responsibility_chain_required_items": primary,
        "high_order_test_points": ho,
        "responsibility_chain_gate": {"rule": "serious is 1; minor is 2"},
    }
    if placeholder:
        if nested:
            ec["responsibility_chain_gate"]["minimum_responsibility_chain_requirements"] = specific
        else:
            ec["responsibility_chain_requirements"] = specific
    return {"case_id": "case_X", "title": "test", "case_taxonomy": {"target_problem": "stale"}, "evaluation_contract_v2": ec}


def test_v7_uses_nonplaceholder_primary_without_rewriting_ids():
    case = source_case(rc_count=8)
    contract = p.normalize_frozen_case_contract(case, "a" * 64)
    ec = contract["evaluation_contract_v2"]
    assert ec["selected_responsibility_chain_path"] == "responsibility_chain_required_items"
    assert [x["id"] for x in ec["responsibility_chain_required_items"]] == [f"RC{i}" for i in range(1, 9)]
    assert p.validate_case_contract(contract, "case_X") == []


def test_v7_replaces_generic_placeholder_with_unique_nested_authored_list_and_keeps_nine():
    case = source_case(rc_count=9, placeholder=True, nested=True)
    contract = p.normalize_frozen_case_contract(case, "b" * 64)
    ec = contract["evaluation_contract_v2"]
    assert ec["selected_responsibility_chain_path"] == "responsibility_chain_gate.minimum_responsibility_chain_requirements"
    assert [x["id"] for x in ec["responsibility_chain_required_items"]] == ["RC1_authored", "RC2_authored", "R3", "R4", "R5", "R6", "R7", "R8", "R9"]
    assert all(x["source_item"] == case["evaluation_contract_v2"]["responsibility_chain_gate"]["minimum_responsibility_chain_requirements"][i] for i, x in enumerate(ec["responsibility_chain_required_items"]))
    assert p.validate_case_contract(contract, "case_X") == []


def test_v7_ambiguous_alternate_responsibility_chain_fails_closed():
    case = source_case(placeholder=True)
    case["evaluation_contract_v2"]["minimum_responsibility_chain_requirements"] = copy.deepcopy(
        case["evaluation_contract_v2"]["responsibility_chain_requirements"]
    )
    with pytest.raises(ValueError, match="candidate_count:2"):
        p.normalize_frozen_case_contract(case, "c" * 64)


def test_v7_judge_view_excludes_source_placeholder_taxonomy_and_provenance():
    contract = p.normalize_frozen_case_contract(source_case(placeholder=True), "d" * 64)
    view = p.judge_contract_view(contract)
    assert "case_taxonomy" not in view
    assert "source_evaluation_contract_v2" not in view
    assert "provenance" not in view
    assert GENERIC not in p.canonical(view)


def test_v7_contract_tampering_is_detected():
    contract = p.normalize_frozen_case_contract(source_case(placeholder=True), "e" * 64)
    contract["evaluation_contract_v2"]["responsibility_chain_required_items"][0]["source_item"]["requirement"] = "tampered"
    errors = p.validate_case_contract(contract, "case_X")
    assert "normalized_contract_payload_sha256_mismatch" in errors
    assert "normalized_responsibility_chain_mismatch" in errors
