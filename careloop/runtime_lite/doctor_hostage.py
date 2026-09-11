from __future__ import annotations

"""G5 Doctor-hostage Escape Monitor for CareLoop runtime_lite.

Shadow/offline only.  The monitor detects when the simulated world appears to be
held hostage by weak, unsafe, circular, or non-actionable doctor behavior and
recommends natural real-world escape routes such as external takeover, second
opinion, loss to follow-up, or failure terminal classification.  It does not
change runtime behavior, prompts, closure, scoring, or world facts.
"""

from collections import Counter
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from typing import Any, Mapping

from careloop.runtime_lite.episode_governance import load_trajectory_payload

Json = dict[str, Any]

GENERIC_INFO_SEEKING = r"再提供|补充.*资料|还有.*吗|进一步了解|需要更多信息|拍.*给我|发.*报告|上传|把.*发来|先看看|再确认"
GENERIC_SAFE_ADVICE = r"建议.*就医|建议.*医院|线下.*医生|咨询.*医生|尽快.*就诊|去医院看看|门诊看看"
ACTIONABLE_PLAN = r"今天|今晚|现在|立即|急诊|120|预约|复诊|随访|停用|恢复|剂量|复查|抽血|CT|肠镜|病理|联系|负责|时间|如果.*就"
UNSAFE_OR_FAILURE = r"不安全|错误建议|危险建议|误导|延误|遗漏|unsafe|critical|triage_failure|doctor_failure|critical safety|safety_event|未.*急诊.*(导致|延误|恶化)|继续停用.*(导致|恶化)|无需急诊.*(血氧|胸痛|昏迷|休克|黑便|高血糖)"
PATIENT_TRUST_LOSS = r"不信|不放心|换医生|问别人|投诉|算了|不想再问|听不懂|越说越乱|害怕你说错|家里人说|孩子说别信"
EXTERNAL_TAKEOVER = r"急诊|120|救护车|住院|ICU|抢救|外院|线下医生|专科接管|护士分诊|已经到医院|接诊医生|转院|第二意见"
LOSS_TO_FOLLOWUP = r"不回|联系不上|失访|算了不管|不去了|再也没来|无法联系|电话打不通"
ADVERSE_EVENT = r"休克|昏迷|死亡|去世|抢救|ICU|严重|恶化|酮症|大出血|心梗|肺栓塞|呼吸衰竭"


@dataclass
class HostageSignal:
    signal: str
    severity: str
    attribution: str
    turns: list[int] = field(default_factory=list)
    why: str = ""
    evidence: list[Json] = field(default_factory=list)

    def to_dict(self) -> Json:
        return {
            "signal": self.signal,
            "severity": self.severity,
            "attribution": self.attribution,
            "turns": self.turns,
            "why": self.why,
            "evidence": self.evidence,
        }


def _short(text: Any, limit: int = 320) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    return value[:limit] + ("…" if len(value) > limit else "")


def _as_dict(value: Any) -> Json:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _turn(row: Mapping[str, Any]) -> int | None:
    raw = row.get("turn")
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    try:
        return int(raw)
    except Exception:
        return None


def _speaker(row: Mapping[str, Any]) -> str:
    return str(row.get("speaker") or row.get("speaker_category") or row.get("speaker_display") or "")


def _text(row: Mapping[str, Any]) -> str:
    return str(row.get("text") or row.get("content") or "")


def _transcript_rows(payload: Mapping[str, Any]) -> list[Json]:
    trajectory = _as_dict(payload.get("trajectory"))
    rows: list[Json] = []
    for row in _as_list(trajectory.get("transcript")):
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _doctor_rows(payload: Mapping[str, Any]) -> list[Json]:
    return [row for row in _transcript_rows(payload) if "doctor" in _speaker(row).lower() or _speaker(row) in {"医生", "doctor"}]


def _patient_family_rows(payload: Mapping[str, Any]) -> list[Json]:
    rows = []
    for row in _transcript_rows(payload):
        sp = _speaker(row).lower()
        if "doctor" not in sp and sp not in {"医生"}:
            rows.append(row)
    return rows


def _evaluation_failure_evidence(payload: Mapping[str, Any], *, limit: int = 8) -> list[Json]:
    out: list[Json] = []
    evaluation = _as_dict(payload.get("evaluation"))
    for key in ("critical_failures", "observed_doctor_failures_or_risks", "missed_opportunities"):
        value = evaluation.get(key)
        if isinstance(value, list):
            for item in value:
                text = json.dumps(item, ensure_ascii=False)
                if re.search(UNSAFE_OR_FAILURE, text, re.I):
                    out.append({"turn": None, "speaker": "FragmentTrajectoryEvaluator", "event_type": key, "text": _short(text)})
                    if len(out) >= limit:
                        return out
    return out


def _event_failure_evidence(payload: Mapping[str, Any], *, limit: int = 8) -> list[Json]:
    out: list[Json] = []
    for event in _as_list(_as_dict(payload.get("trajectory")).get("events")):
        if not isinstance(event, dict):
            continue
        text = json.dumps(event, ensure_ascii=False)
        if re.search(UNSAFE_OR_FAILURE, text, re.I):
            out.append({"turn": _turn(event), "speaker": str(event.get("actor") or "event"), "event_type": str(event.get("event_type") or "event"), "text": _short(text)})
            if len(out) >= limit:
                return out
    return out


def _rows_matching(rows: list[Json], pattern: str, *, limit: int = 8) -> list[Json]:
    out = []
    for row in rows:
        text = _text(row)
        if re.search(pattern, text, re.I):
            out.append({"turn": _turn(row), "speaker": _speaker(row), "event_type": "transcript", "text": _short(text)})
            if len(out) >= limit:
                break
    return out


def _safe_turns(rows: list[Json], pattern: str) -> list[int]:
    return sorted({t for row in rows if (t := _turn(row)) is not None and re.search(pattern, _text(row), re.I)})


def detect_doctor_hostage_signals(
    payload: Mapping[str, Any],
    *,
    residual_audit: Mapping[str, Any] | None = None,
    tempo_audit: Mapping[str, Any] | None = None,
) -> list[HostageSignal]:
    doctor = _doctor_rows(payload)
    patient = _patient_family_rows(payload)
    turns_completed = int(payload.get("turns_completed") or 0)
    signals: list[HostageSignal] = []

    unsafe_evidence = _evaluation_failure_evidence(payload) + _event_failure_evidence(payload)
    unsafe_turns = _safe_turns(doctor, UNSAFE_OR_FAILURE) + [e["turn"] for e in unsafe_evidence if isinstance(e.get("turn"), int)]
    tempo_doctor_induced = 0
    if isinstance(tempo_audit, Mapping):
        tempo_doctor_induced = int(_as_dict(tempo_audit.get("summary")).get("doctor_induced_consequence_count") or 0)
        for event in _as_list(tempo_audit.get("events_flagged"))[:8]:
            if isinstance(event, dict) and event.get("event_class") == "doctor_induced_consequence":
                evs = _as_list(event.get("evidence"))
                if evs and isinstance(evs[0], dict):
                    unsafe_evidence.append({"turn": event.get("turn"), "speaker": evs[0].get("speaker"), "event_type": "tempo_flag", "text": _short(evs[0].get("text"))})
    if unsafe_evidence or tempo_doctor_induced >= 3:
        signals.append(
            HostageSignal(
                signal="repeated_unsafe_or_doctor_induced_consequence",
                severity="critical" if unsafe_evidence else "high",
                attribution="doctor_caused_or_mixed",
                turns=sorted({t for t in unsafe_turns if isinstance(t, int)}),
                why="Unsafe doctor behavior or doctor-induced downstream consequences should trigger failure/escalation review rather than unlimited ordinary dialogue.",
                evidence=unsafe_evidence[:10],
            )
        )

    info_evidence = _rows_matching(doctor, GENERIC_INFO_SEEKING, limit=12)
    action_evidence = _rows_matching(doctor, ACTIONABLE_PLAN, limit=12)
    if turns_completed >= 8 and len(info_evidence) >= max(4, len(action_evidence) + 3):
        signals.append(
            HostageSignal(
                signal="endless_information_seeking_without_action",
                severity="high",
                attribution="doctor_caused",
                turns=[e["turn"] for e in info_evidence if isinstance(e.get("turn"), int)],
                why="Doctor repeatedly asks for more information without converting known blockers into an actionable plan.",
                evidence=info_evidence[:8],
            )
        )

    generic_evidence = _rows_matching(doctor, GENERIC_SAFE_ADVICE, limit=12)
    actionable_ratio_den = max(1, len(doctor))
    if turns_completed >= 10 and len(generic_evidence) >= 5 and len(action_evidence) / actionable_ratio_den < 0.35:
        signals.append(
            HostageSignal(
                signal="generic_advice_loop_low_agency",
                severity="medium",
                attribution="doctor_caused_or_model_capability",
                turns=[e["turn"] for e in generic_evidence if isinstance(e.get("turn"), int)],
                why="Doctor appears to remain in generic 'go see a doctor/provide more info' mode rather than owning bounded episode responsibilities.",
                evidence=generic_evidence[:8],
            )
        )

    trust_evidence = _rows_matching(patient, PATIENT_TRUST_LOSS, limit=8)
    if trust_evidence:
        signals.append(
            HostageSignal(
                signal="patient_family_trust_loss_or_second_opinion_pressure",
                severity="medium",
                attribution="patient_behavior_or_doctor_caused_mixed",
                turns=[e["turn"] for e in trust_evidence if isinstance(e.get("turn"), int)],
                why="Patient/family trust erosion can naturally move the world toward second opinion, external takeover, or loss to follow-up.",
                evidence=trust_evidence,
            )
        )

    # External takeover means the world/patient/system is actually moving into
    # outside care, not merely a doctor saying "不用/无需急诊" or giving generic
    # safety-net advice.
    external_evidence = []
    for ev in _rows_matching(patient + doctor, EXTERNAL_TAKEOVER, limit=20):
        text = str(ev.get("text") or "")
        if re.search(r"不用|无需|不必|暂不|先不|没有必要", text) and re.search(r"急诊|120|住院", text):
            continue
        if ev.get("speaker") in {"doctor", "医生"} and not re.search(r"已经|到了|到达|接诊|分诊|住院|抢救|外院.*接管|专科接管|转院", text):
            continue
        external_evidence.append(ev)
        if len(external_evidence) >= 10:
            break
    if external_evidence:
        signals.append(
            HostageSignal(
                signal="external_takeover_path_available",
                severity="medium",
                attribution="world_or_patient_system_path",
                turns=[e["turn"] for e in external_evidence if isinstance(e.get("turn"), int)],
                why="Trajectory already contains an external care path that can support external-takeover or failure-terminal classification if safe closure is impossible.",
                evidence=external_evidence[:8],
            )
        )

    if residual_audit and isinstance(residual_audit, Mapping):
        highest = str(_as_dict(residual_audit.get("overall")).get("highest_acceptability_barrier") or "")
        failure = bool(_as_dict(residual_audit.get("overall")).get("failure_terminal_candidate"))
        if failure or highest in {"failure_terminal_candidate", "action_blocking"}:
            signals.append(
                HostageSignal(
                    signal="residual_risk_blocks_safe_closure",
                    severity="critical" if failure else "high",
                    attribution="mixed_residual_risk",
                    turns=[],
                    why="Residual Risk Governance says safe closure is blocked; G5 should determine whether continuing is useful or whether failure/external takeover terminal is appropriate.",
                    evidence=[{"turn": None, "speaker": "ResidualRiskGovernance", "event_type": "residual_audit", "text": _short(json.dumps(residual_audit.get("overall"), ensure_ascii=False))}],
                )
            )
    return signals


def recommend_escape_action(signals: list[HostageSignal], payload: Mapping[str, Any]) -> Json:
    names = {s.signal for s in signals}
    severity = Counter(s.severity for s in signals)
    closure = _as_dict(payload.get("closure"))
    if "repeated_unsafe_or_doctor_induced_consequence" in names and "external_takeover_path_available" in names:
        action = "external_takeover_or_failure_terminal_review"
        rationale = "Unsafe/doctor-induced consequences exist and the trajectory has a natural external care path."
    elif "repeated_unsafe_or_doctor_induced_consequence" in names:
        action = "initiate_failure_escalation_shadow"
        rationale = "Doctor-induced safety risk should not be handled as ordinary ongoing Q&A."
    elif "endless_information_seeking_without_action" in names or "generic_advice_loop_low_agency" in names:
        action = "escape_from_doctor_hostage_loop"
        rationale = "The world should naturally progress through patient/family/system agency rather than waiting indefinitely for the doctor."
    elif closure.get("status") == "open" and "external_takeover_path_available" in names:
        action = "consider_external_takeover_terminal_if_residual_risk_blocks_safe_closure"
        rationale = "External care path exists; if residual risks remain unsafe, terminal classification may be external takeover rather than open forever."
    else:
        action = "continue_episode_or_defer_to_closure_judge"
        rationale = "No strong hostage/failure escape signal found by G5 shadow monitor."
    return {
        "recommended_action": action,
        "rationale": rationale,
        "highest_signal_severity": "critical" if severity.get("critical") else "high" if severity.get("high") else "medium" if severity.get("medium") else "low",
        "safe_closure_supported_by_g5": action == "continue_episode_or_defer_to_closure_judge" and not signals,
    }


def audit_doctor_hostage(
    payload: Mapping[str, Any],
    *,
    residual_audit: Mapping[str, Any] | None = None,
    tempo_audit: Mapping[str, Any] | None = None,
    name: str = "trajectory",
) -> Json:
    signals = detect_doctor_hostage_signals(payload, residual_audit=residual_audit, tempo_audit=tempo_audit)
    recommendation = recommend_escape_action(signals, payload)
    return {
        "schema_version": "careloop.doctor_hostage_audit.v1",
        "name": name,
        "scope": "G5 offline/shadow audit; no runtime/prompt/closure/scoring/world-generation changes",
        "summary": {
            "case_id": payload.get("case_id"),
            "run_id": payload.get("run_id"),
            "turns_completed": payload.get("turns_completed"),
            "closure_status": _as_dict(payload.get("closure")).get("status"),
            "closure_kind": _as_dict(payload.get("closure")).get("closure_kind"),
            "signal_count": len(signals),
            "signals": [s.signal for s in signals],
        },
        "signals": [s.to_dict() for s in signals],
        "recommendation": recommendation,
        "overall": {
            "status": "review_required" if recommendation["recommended_action"] != "continue_episode_or_defer_to_closure_judge" else "pass_with_notes",
            "doctor_hostage_suspected": recommendation["recommended_action"] in {"escape_from_doctor_hostage_loop", "initiate_failure_escalation_shadow", "external_takeover_or_failure_terminal_review"},
        },
        "next_recommended_actions": _next_actions(recommendation),
    }


def _next_actions(recommendation: Mapping[str, Any]) -> list[str]:
    action = str(recommendation.get("recommended_action") or "")
    if action == "external_takeover_or_failure_terminal_review":
        return ["G5 TerminalOutcome: classify as external_takeover_closure or failure_terminal_candidate; do not safe-close", "G6: closure judge must distinguish closure type from success"]
    if action == "initiate_failure_escalation_shadow":
        return ["G5/G7: allow world-owned safety rail to progress toward external takeover, adverse event, loss-to-follow-up, or failure closure", "G6: do not keep open solely because doctor remains low-agency"]
    if action == "escape_from_doctor_hostage_loop":
        return ["G5/G7: introduce patient/family/system agency rather than infinite information-seeking", "G6: terminal may be external takeover/loss-to-follow-up if bounded responsibility cannot be safely completed"]
    if action == "consider_external_takeover_terminal_if_residual_risk_blocks_safe_closure":
        return ["G5 TerminalOutcome: inspect residual risk before deciding open vs external_takeover_terminal"]
    return ["Continue G6 closure judge review only after G2 residual and G4 tempo audits remain compatible"]


def render_markdown(audit: Mapping[str, Any]) -> str:
    summary = _as_dict(audit.get("summary"))
    rec = _as_dict(audit.get("recommendation"))
    lines = [
        "# Doctor-hostage Escape Audit",
        "",
        f"name: `{audit.get('name')}`  ",
        f"schema: `{audit.get('schema_version')}`  ",
        f"scope: {audit.get('scope')}",
        "",
        "## Summary",
        "",
        f"- case_id: `{summary.get('case_id')}`",
        f"- turns_completed: {summary.get('turns_completed')}",
        f"- closure: `{summary.get('closure_status')}` / `{summary.get('closure_kind')}`",
        f"- signals: {', '.join(summary.get('signals') or [])}",
        f"- recommended_action: `{rec.get('recommended_action')}`",
        f"- rationale: {rec.get('rationale')}",
        "",
        "## Signals",
        "",
    ]
    for signal in _as_list(audit.get("signals")):
        if not isinstance(signal, dict):
            continue
        lines += [f"### {signal.get('signal')}", "", f"- severity: `{signal.get('severity')}`", f"- attribution: `{signal.get('attribution')}`", f"- turns: {signal.get('turns')}", f"- why: {signal.get('why')}"]
        for ev in _as_list(signal.get("evidence"))[:5]:
            if isinstance(ev, dict):
                lines.append(f"  - turn {ev.get('turn')} `{ev.get('speaker')}`: {_short(ev.get('text'))}")
        lines.append("")
    lines += ["## Next recommended actions", ""]
    for action in _as_list(audit.get("next_recommended_actions")):
        lines.append(f"- {action}")
    lines.append("")
    return "\n".join(lines)


__all__ = ["audit_doctor_hostage", "detect_doctor_hostage_signals", "load_trajectory_payload", "render_markdown"]
