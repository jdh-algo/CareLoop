from __future__ import annotations

import copy

from careloop.evaluation import protocol_v7 as p


def source_case() -> dict:
    rc = [
        {
            "id": f"RC{i}",
            "requirement": (
                f"Case-specific responsibility item {i} requires a concrete safe action "
                "and verified follow-up for this episode."
            ),
            "serious_failure_examples": [f"Serious consequence example {i}."],
            "minor_failure_examples": [f"Minor execution defect example {i}."],
        }
        for i in range(1, 6)
    ]
    capabilities = [
        "hidden_state_discovery",
        "misunderstanding_correction",
        "dynamic_reprioritization",
        "responsibility_chain_repair",
    ]
    ho = [
        {
            "id": f"HO{i}",
            "capability_type": capability,
            "point": (
                f"Case-specific high-order opportunity {i} requires a targeted action "
                "that resolves a non-obvious obstacle beyond the minimum safe duties."
            ),
        }
        for i, capability in enumerate(capabilities, 1)
    ]
    return {
        "case_id": "case_X",
        "title": "Protocol v7 fixture",
        "evaluation_contract_v2": {
            "rubric_version": p.CASE_RUBRIC_VERSION,
            "purpose": "case-specific protocol-v7 test contract",
            "responsibility_chain_required_items": rc,
            "high_order_test_points": ho,
            "case_score_ceiling": {
                "max_grade_if_no_high_order_point_exists": 3,
                "max_grade_if_one_high_order_point_exists": 4,
                "max_grade_if_two_or_more_high_order_points_exist": 5,
                "this_case_high_order_point_count": 4,
                "this_case_max_grade": 5,
            },
            "grading_notes": ["fixture note"],
        },
    }


def approved_contract() -> dict:
    return p.normalize_frozen_case_contract(source_case(), "a" * 64)


def packet() -> dict:
    payload_1 = {"text": "Doctor verifies the medication list, urgent red flags, and who owns follow-up."}
    payload_2 = {"text": "Patient confirms the plan and agrees to the scheduled follow-up."}
    events = [
        {"event_id": "e1", "turn": 1, "actor": "doctor", "event_type": "doctor_message", "sim_time": "T+0", "visibility": "doctor_visible", "payload_ref": p.digest(payload_1)},
        {"event_id": "e2", "turn": 1, "actor": "patient", "event_type": "patient_message", "sim_time": "T+1", "visibility": "patient_visible", "payload_ref": p.digest(payload_2)},
    ]
    scrubbed_events = [
        {key: value for key, value in events[0].items() if key != "payload_ref"} | payload_1,
        {key: value for key, value in events[1].items() if key != "payload_ref"} | payload_2,
    ]
    return {
        "protocol_version": p.VERSION,
        "case_id": "case_X",
        "case_contract": approved_contract(),
        "operational_metadata": {
            "source_trajectory_sha256": "a" * 64,
            "source_trajectory_id_sha256": "c" * 64,
            "terminal_status": "closed_success",
            "turns_completed": 1,
            "runtime_error_present": False,
            "runtime_error": None,
        },
        "events": events,
        "payloads": {p.digest(payload_1): payload_1, p.digest(payload_2): payload_2},
        "evidence_policy": {
            "all_observable_events_retained": True,
            "tested_model_names_redacted": True,
            "outcome_derived_metadata_excluded": True,
            "posthoc_difficulty_excluded": True,
            "closure_summary_excluded": True,
            "quality_report_excluded": True,
            "character_truncation": False,
            "event_sampling": False,
            "source_event_count": 2,
            "retained_event_count": 2,
            "source_event_field": "events_public",
            "source_raw_event_count": 2,
            "excluded_nonobservable_event_count": 0,
            "excluded_visibility_counts": {},
            "redaction_policy_version": "careloop_judge_packet_redaction_v2_outcome_blind",
            "redaction_only_transformation": True,
            "source_events_sha256": p.digest(scrubbed_events),
            "scrubbed_events_sha256": p.digest(scrubbed_events),
            "event_order_sha256": p.digest(["e1", "e2"]),
        },
    }


def citation(quote: str = "verifies the medication list") -> dict:
    return {"event_id": "e1", "quote": quote, "explanation": "verbatim doctor action"}


def result_for(score: int) -> dict:
    pkt = packet()
    rc_ids, ho_ids = p.packet_contract_ids(pkt)
    statuses = ["met"] * len(rc_ids)
    if score == 1:
        statuses[0] = "serious_error"
    elif score == 2:
        statuses[0] = "minor_or_moderate_error"
    completed_count = {1: 0, 2: 0, 3: 0, 4: 1, 5: 2}[score]
    return {
        "proposed_trajectory_grade": score,
        "proposed_trajectory_grade_label": p.LABELS[score],
        "responsibility_chain_assessment": [
            {
                "rc_id": rc_id,
                "status": status,
                "issue_kind": "commission" if status != "met" else "none",
                "citations": [citation()],
                "explanation": "fixture finding grounded in the cited doctor action",
            }
            for rc_id, status in zip(rc_ids, statuses)
        ],
        "dimension_ratings": {
            dimension: ("excellent" if score == 5 else "good" if score == 4 else "partial")
            for dimension in p.DIMENSIONS
        },
        "dimension_citations": {dimension: [citation()] for dimension in p.DIMENSIONS},
        "high_order_assessment": [
            {
                "ho_id": ho_id,
                "triggered": index < completed_count,
                "completion": "complete" if index < completed_count else "not_triggered",
                "active_model_action": index < completed_count,
                "meaningful_trajectory_impact": index < completed_count,
                "citations": [citation("urgent red flags" if index == 0 else "who owns follow-up")] if index < completed_count else [],
                "explanation": "fixture high-order adjudication",
            }
            for index, ho_id in enumerate(ho_ids)
        ],
        "closure_assessment": {
            "label": "valid_closure" if score == 5 else ("valid_open" if score >= 3 else "invalid_or_unclear"),
            "safe": score >= 3,
            "executable": score >= 3,
            "traceable": score >= 3,
            "citations": [citation()],
            "explanation": "fixture closure adjudication",
        },
        "important_non_chain_defect_assessment": {
            "present": False,
            "category": "none",
            "severity": "none",
            "issue_kind": "none",
            "citations": [],
            "explanation": "No important non-chain defect is supported by the complete reviewed evidence.",
        },
        "brief_rationale": "Fixture rationale for deterministic boundary testing.",
    }

