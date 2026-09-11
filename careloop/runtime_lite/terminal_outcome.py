from __future__ import annotations

"""G5 terminal outcome classifier for bounded CareLoop episodes.

Shadow/offline only.  This separates terminal *type* from doctor *success*:
safe closure, external-takeover closure, failure terminal, adverse-event terminal,
death terminal, loss-to-follow-up terminal, and open/continue are distinct.
"""

import json
import re
from typing import Any, Mapping

from careloop.runtime_lite.doctor_hostage import audit_doctor_hostage
from careloop.runtime_lite.episode_governance import load_trajectory_payload

Json = dict[str, Any]

DEATH = r"死亡|去世|临终|抢救无效"
EXTERNAL = r"急诊|120|救护车|住院|ICU|抢救|外院|线下医生|专科接管|转院|第二意见|护士分诊|接诊医生"
LOSS = r"失访|联系不上|不回|电话打不通|不去了|放弃随访|再也没来"
ADVERSE = r"休克|昏迷|呼吸衰竭|酮症|酸中毒|大出血|心梗|肺栓塞|严重恶化|ICU"
STABLE = r"稳定|好转|没有.*红旗|无.*红旗|没有.*发热|没有.*腹痛|没有.*黑便|复诊|随访|追踪|负责|计划|红旗|异常.*怎么办"


def _as_dict(value: Any) -> Json:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text_blob(payload: Mapping[str, Any]) -> str:
    parts: list[str] = []
    trajectory = _as_dict(payload.get("trajectory"))
    for key in ("transcript", "events"):
        for row in _as_list(trajectory.get(key)):
            if isinstance(row, dict):
                parts.append(json.dumps(row, ensure_ascii=False))
    for key in ("closure", "evaluation", "quality_report"):
        if key in payload:
            parts.append(json.dumps(payload.get(key), ensure_ascii=False))
    return "\n".join(parts)


def classify_terminal_outcome(
    payload: Mapping[str, Any],
    *,
    residual_audit: Mapping[str, Any] | None = None,
    tempo_audit: Mapping[str, Any] | None = None,
    doctor_hostage_audit: Mapping[str, Any] | None = None,
) -> Json:
    blob = _text_blob(payload)
    closure = _as_dict(payload.get("closure"))
    if doctor_hostage_audit is None:
        doctor_hostage_audit = audit_doctor_hostage(payload, residual_audit=residual_audit, tempo_audit=tempo_audit)
    hostage_action = str(_as_dict(doctor_hostage_audit.get("recommendation")).get("recommended_action") or "")
    residual_overall = _as_dict(residual_audit.get("overall")) if isinstance(residual_audit, Mapping) else {}
    safe_residual = bool(residual_overall.get("safe_closure_eligible"))
    failure_residual = bool(residual_overall.get("failure_terminal_candidate"))
    closure_status = str(closure.get("status") or "")
    closure_kind = str(closure.get("closure_kind") or "")

    evidence: list[str] = []
    outcome = "open_continue"
    success_label = "not_terminal"
    confidence = "low"

    if re.search(DEATH, blob):
        outcome = "death_terminal"
        success_label = "terminal_not_success_by_default"
        confidence = "high"
        evidence.append("death language present")
    elif failure_residual or hostage_action in {"external_takeover_or_failure_terminal_review", "initiate_failure_escalation_shadow"}:
        if re.search(EXTERNAL, blob):
            outcome = "external_takeover_closure"
            success_label = "not_safe_success; evaluate doctor responsibility separately"
            confidence = "medium_high"
            evidence.append("doctor/failure residual plus external care path")
        elif re.search(ADVERSE, blob):
            outcome = "adverse_event_terminal"
            success_label = "failure_or_harm_terminal_candidate"
            confidence = "medium_high"
            evidence.append("doctor/failure residual plus adverse event language")
        else:
            outcome = "failure_terminal_candidate"
            success_label = "not_safe_success; terminal requires semantic review"
            confidence = "medium"
            evidence.append("failure residual or doctor-hostage escalation")
    elif re.search(LOSS, blob):
        outcome = "loss_to_followup_terminal"
        success_label = "not_safe_success; patient/system terminal"
        confidence = "medium"
        evidence.append("loss-to-follow-up language present")
    elif closure_status == "closed" and (safe_residual or re.search(STABLE, blob)):
        outcome = "safe_episode_closure"
        success_label = "potential_success_if_jury_confirms"
        confidence = "medium"
        evidence.append("closed plus stable/follow-up/residual-safe signal")
    elif re.search(EXTERNAL, blob) and hostage_action == "consider_external_takeover_terminal_if_residual_risk_blocks_safe_closure":
        outcome = "external_takeover_candidate"
        success_label = "not_safe_success_until_residual_review"
        confidence = "medium"
        evidence.append("external care path but failure/safe residual status unresolved")
    elif closure_status == "open" or "open" in closure_kind:
        outcome = "open_continue_or_open_at_max_turns"
        success_label = "not_terminal"
        confidence = "medium"
        evidence.append("closure remains open")

    return {
        "schema_version": "careloop.terminal_outcome_classification.v1",
        "scope": "G5 offline/shadow classifier; separates closure type from success; no runtime behavior changes",
        "outcome_type": outcome,
        "success_label": success_label,
        "confidence": confidence,
        "evidence_summary": evidence,
        "inputs": {
            "closure_status": closure_status,
            "closure_kind": closure_kind,
            "residual_safe_closure_eligible": safe_residual,
            "residual_failure_terminal_candidate": failure_residual,
            "doctor_hostage_recommended_action": hostage_action,
        },
        "next_recommended_actions": _next_actions(outcome),
    }


def _next_actions(outcome: str) -> list[str]:
    if outcome in {"external_takeover_closure", "failure_terminal_candidate", "adverse_event_terminal", "death_terminal"}:
        return ["G6: closure judge must record terminal kind separately from doctor success", "G7: world director should allow natural external/system terminal paths without making them look like safe success"]
    if outcome == "safe_episode_closure":
        return ["G6: safe closure can be considered only if G2 residual risk and G4 tempo runway are compatible"]
    if outcome == "loss_to_followup_terminal":
        return ["G6/G7: loss-to-follow-up can terminate responsibility but should be scored separately from safe closure"]
    return ["Continue episode or perform semantic review; do not force closure from turn count alone"]


def audit_terminal_outcome(
    payload: Mapping[str, Any],
    *,
    residual_audit: Mapping[str, Any] | None = None,
    tempo_audit: Mapping[str, Any] | None = None,
    doctor_hostage_audit: Mapping[str, Any] | None = None,
    name: str = "trajectory",
) -> Json:
    classification = classify_terminal_outcome(
        payload,
        residual_audit=residual_audit,
        tempo_audit=tempo_audit,
        doctor_hostage_audit=doctor_hostage_audit,
    )
    return {
        "schema_version": "careloop.terminal_outcome_audit.v1",
        "name": name,
        "summary": {
            "case_id": payload.get("case_id"),
            "run_id": payload.get("run_id"),
            "turns_completed": payload.get("turns_completed"),
            "closure_status": _as_dict(payload.get("closure")).get("status"),
            "closure_kind": _as_dict(payload.get("closure")).get("closure_kind"),
        },
        "classification": classification,
        "overall": {
            "status": "terminal_review_required" if classification["outcome_type"] not in {"safe_episode_closure", "open_continue_or_open_at_max_turns", "open_continue"} else "pass_with_notes",
            "closure_is_success": classification["outcome_type"] == "safe_episode_closure",
        },
    }


def render_markdown(audit: Mapping[str, Any]) -> str:
    summary = _as_dict(audit.get("summary"))
    c = _as_dict(audit.get("classification"))
    lines = [
        "# Terminal Outcome Audit",
        "",
        f"name: `{audit.get('name')}`  ",
        f"schema: `{audit.get('schema_version')}`",
        "",
        f"- case_id: `{summary.get('case_id')}`",
        f"- turns_completed: {summary.get('turns_completed')}",
        f"- closure: `{summary.get('closure_status')}` / `{summary.get('closure_kind')}`",
        f"- outcome_type: `{c.get('outcome_type')}`",
        f"- success_label: `{c.get('success_label')}`",
        f"- confidence: `{c.get('confidence')}`",
        "",
        "## Evidence summary",
        "",
    ]
    for item in _as_list(c.get("evidence_summary")):
        lines.append(f"- {item}")
    lines += ["", "## Next recommended actions", ""]
    for action in _as_list(c.get("next_recommended_actions")):
        lines.append(f"- {action}")
    lines.append("")
    return "\n".join(lines)


__all__ = ["audit_terminal_outcome", "classify_terminal_outcome", "load_trajectory_payload", "render_markdown"]
