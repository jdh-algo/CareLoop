from __future__ import annotations

"""Anti-cheat visibility audits for runtime_lite.

The audit is intentionally lightweight and structural.  It checks that hidden
case-author/evaluator metadata is not present in doctor-visible openings or
workspace outputs, that formal cases use neutral workspace panels, and that the
opening does not provide a problem-oriented EHR summary.
"""

from typing import Any, Mapping

from careloop.runtime_lite.visibility_boundary import (
    audit_doctor_visible_provenance,
    audit_visible_payload,
    nonstandard_panel_names,
)


def audit_doctor_visible_opening(opening: Mapping[str, Any]) -> dict[str, Any]:
    opening = opening if isinstance(opening, Mapping) else {}
    structural = audit_visible_payload(
        opening,
        payload_name="doctor_visible_opening",
        include_opening_identity_keys=True,
    )
    care_summary = opening.get("care_network_history_summary") if isinstance(opening.get("care_network_history_summary"), Mapping) else {}
    legacy_summary = opening.get("current_institution_history_summary") if isinstance(opening.get("current_institution_history_summary"), Mapping) else {}
    available_workspace = opening.get("available_workspace") if isinstance(opening.get("available_workspace"), Mapping) else {}
    summary_text = str(care_summary.get("summary") or legacy_summary.get("summary") or "")
    panel_warnings = nonstandard_panel_names(available_workspace.get("available_panels") or [])
    title = str(opening.get("title") or "")
    session = opening.get("session") if isinstance(opening.get("session"), Mapping) else {}
    raw_case_id_visible = bool(opening.get("case_id"))
    return {
        "payload_name": "doctor_visible_opening",
        "structural_leak_found": bool(structural.get("structural_leak_found")),
        "forbidden_key_hits": structural.get("forbidden_key_hits") or [],
        "raw_case_id_visible": raw_case_id_visible,
        "neutral_session_present": bool(session.get("session_label") or session.get("session_id") or title),
        "problem_oriented_summary_present": bool(summary_text.strip()),
        "summary_length": len(summary_text.strip()),
        "nonstandard_workspace_panels": panel_warnings,
        "pass": not structural.get("structural_leak_found") and not raw_case_id_visible and not summary_text.strip() and not panel_warnings,
        "principle": "Doctor opening may expose neutral session, latest patient/family message and neutral workspace capabilities only.",
    }


def audit_workspace_result(result: Mapping[str, Any]) -> dict[str, Any]:
    result = result if isinstance(result, Mapping) else {}
    structural = audit_visible_payload(
        result,
        payload_name="doctor_visible_workspace_result",
        include_opening_identity_keys=False,
    )
    panels = result.get("requested_panels") if isinstance(result.get("requested_panels"), list) else []
    nonstandard = nonstandard_panel_names(panels)
    doctor_text = str(result.get("doctor_visible_text") or "")
    summary_like_hint = any(
        phrase in doctor_text
        for phrase in [
            "本case重点",
            "本 case 重点",
            "核心考点",
            "评分标准",
            "参考答案",
            "应重点关注",
            "关键线索是",
        ]
    )
    provenance = audit_doctor_visible_provenance(result, payload_name="doctor_visible_workspace_result")
    return {
        "payload_name": "doctor_visible_workspace_result",
        "structural_leak_found": bool(structural.get("structural_leak_found")),
        "forbidden_key_hits": structural.get("forbidden_key_hits") or [],
        "nonstandard_workspace_panels": nonstandard,
        "summary_like_hint_risk": bool(summary_like_hint),
        "provenance_audit": provenance,
        "pass": not structural.get("structural_leak_found") and not nonstandard and not summary_like_hint and provenance.get("pass", True),
        "principle": "Workspace may retrieve neutral source/index material with provenance, but must not disclose evaluator metadata or case-author hints.",
    }


def combine_audits(*audits: Mapping[str, Any]) -> dict[str, Any]:
    failures = [audit for audit in audits if isinstance(audit, Mapping) and not audit.get("pass", False)]
    return {
        "pass": not failures,
        "failure_count": len(failures),
        "failures": failures,
        "audits": list(audits),
        "principle": "Structural anti-cheat readiness signal; semantic audits can be layered later via LLM.",
    }


def audit_actor_visible_message(message_event: Mapping[str, Any]) -> dict[str, Any]:
    """Thin audit for patient/family messages shown to the doctor.

    A clinical diagnosis or sensitive private fact in patient/family speech is
    not automatically a leak; the audit is concerned with hidden/evaluator-only
    structural contamination and provenance metadata.
    """

    event = message_event if isinstance(message_event, Mapping) else {}
    structural = audit_visible_payload(event, payload_name="actor_message_doctor_visible")
    provenance = audit_doctor_visible_provenance(event, payload_name="actor_message_doctor_visible")
    metadata = event.get("metadata") if isinstance(event.get("metadata"), Mapping) else {}
    prov = metadata.get("provenance") if isinstance(metadata.get("provenance"), Mapping) else {}
    natural_actor_disclosure = bool(prov.get("natural_actor_disclosure"))
    return {
        "payload_name": "actor_message_doctor_visible",
        "structural_leak_found": bool(structural.get("structural_leak_found")),
        "forbidden_key_hits": structural.get("forbidden_key_hits") or [],
        "natural_actor_disclosure": natural_actor_disclosure,
        "provenance_audit": provenance,
        "pass": not structural.get("structural_leak_found"),
        "principle": "Patient/family disclosure is normal care-world evidence when plausibly actor-known; system/backstage contamination is the leak risk.",
    }
