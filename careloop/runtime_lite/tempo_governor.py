from __future__ import annotations

"""Phase-aware Tempo Governor shadow audit for CareLoop runtime_lite.

G4 scope: offline/shadow only.  This module does not change runtime flow,
prompts, closure decisions, scoring, world generation, or actor behavior.  It
uses completed trajectory/checkpoint payloads plus optional G1/G2/G3 audits to
separate ordinary friction from true new major events, check phase-aware event
policy, and expose whether world tempo is likely helping a natural clinical
closure or keeping the episode open through repeated/unrelated disturbances.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from careloop.runtime_lite.episode_governance import (
    TextEvidence,
    audit_episode_governance,
    collect_text_evidence,
    load_trajectory_payload,
)

Json = dict[str, Any]


EVENT_CLASSES = [
    "ordinary_friction",
    "minor_continuation",
    "pending_result_update",
    "patient_uncertainty",
    "patient_behavior_issue",
    "system_access_friction",
    "doctor_induced_consequence",
    "major_new_issue",
    "rare_unexpected_event",
    "external_takeover_event",
    "terminal_event",
    "closure_runway_support",
]

PHASE_ALLOWED: dict[str, set[str]] = {
    "initial_uncertainty": {
        "minor_continuation",
        "patient_uncertainty",
        "ordinary_friction",
        "system_access_friction",
        "pending_result_update",
        "major_new_issue",  # if it is part of the presenting syndrome / hidden truth.
    },
    "active_workup": {
        "minor_continuation",
        "pending_result_update",
        "patient_uncertainty",
        "ordinary_friction",
        "system_access_friction",
        "patient_behavior_issue",
        "major_new_issue",
        "external_takeover_event",
        "doctor_induced_consequence",
    },
    "treatment_execution": {
        "minor_continuation",
        "pending_result_update",
        "patient_uncertainty",
        "ordinary_friction",
        "system_access_friction",
        "patient_behavior_issue",
        "major_new_issue",
        "doctor_induced_consequence",
        "external_takeover_event",
    },
    "result_pending": {
        "minor_continuation",
        "pending_result_update",
        "patient_uncertainty",
        "ordinary_friction",
        "system_access_friction",
        "patient_behavior_issue",
        "doctor_induced_consequence",
        "external_takeover_event",
        "closure_runway_support",
    },
    "medication_reconciliation": {
        "minor_continuation",
        "pending_result_update",
        "patient_uncertainty",
        "ordinary_friction",
        "system_access_friction",
        "patient_behavior_issue",
        "doctor_induced_consequence",
        "external_takeover_event",
        "closure_runway_support",
    },
    "stabilization": {
        "minor_continuation",
        "pending_result_update",
        "patient_uncertainty",
        "ordinary_friction",
        "system_access_friction",
        "patient_behavior_issue",
        "closure_runway_support",
        "doctor_induced_consequence",
        "external_takeover_event",
    },
    "closure_runway": {
        "minor_continuation",
        "pending_result_update",
        "patient_uncertainty",
        "ordinary_friction",
        "system_access_friction",
        "closure_runway_support",
        "doctor_induced_consequence",
        "external_takeover_event",
    },
    "failure_escalation": {
        "doctor_induced_consequence",
        "external_takeover_event",
        "terminal_event",
        "pending_result_update",
        "minor_continuation",
    },
    "terminal_state": {"terminal_event", "external_takeover_event", "minor_continuation"},
}

MAJOR_SIGNATURE_PATTERNS: dict[str, str] = {
    "bleeding_or_gi_red_flag": r"黑便|便血|呕血|出血|消化道出血|柏油样便",
    "infection_or_sepsis": r"发热|寒战|感染|脓毒|败血|白细胞|CRP|降钙素原",
    "cardiopulmonary_red_flag": r"胸痛|胸闷|呼吸困难|喘|血氧|心梗|肺栓塞|心衰",
    "neurologic_red_flag": r"昏迷|意识不清|叫不醒|抽搐|偏瘫|言语不清|晕厥",
    "metabolic_decompensation": r"高血糖|低血糖|酮体|酮症|脱水|电解质|酸中毒|口干|尿多|站不起来",
    "acute_abdomen_or_pain": r"腹痛|剧痛|腹膜炎|穿孔|梗阻|疼痛加重",
    "malignancy_or_pathology_high_impact": r"癌|恶性|肿瘤|病理.*阳性|转移|淋巴结",
    "hospitalization_or_rescue": r"急诊|120|救护车|住院|ICU|抢救|手术|外院接管|转院",
    "death_or_terminal": r"死亡|去世|临终|尸检",
}

CLASS_PATTERNS: dict[str, str] = {
    "terminal_event": r"死亡|去世|临终|尸检|terminal_state|terminal event",
    "external_takeover_event": r"外部接管|急诊接管|急诊.*处理|120|救护车|住院|ICU|抢救|转院|外院|专科接管|护士.*分诊|线下医生.*接诊",
    "doctor_induced_consequence": r"医生.{0,30}(错误|延误|遗漏|误导|不安全)|错误建议|危险建议|不安全|unsafe|critical safety|critical_safety_event|triage_failure|doctor_failure|safety_event|停药.{0,20}(导致|造成|引起|恶化)|漏服.{0,20}(导致|造成|引起|恶化)|未.{0,12}(急诊|处理|转诊).{0,30}(导致|延误|恶化|不良)",
    "pending_result_update": r"结果|报告|病理|化验|检查|复查|未出|没出来|等待|pending|培养|影像|心电图|抽血|回报|取报告",
    "patient_behavior_issue": r"不想去|不愿|拒绝|自行|自己停|拖着|隐瞒|没说|骗|瞒|不配合|没按|怕花钱|怕耽误|不肯|失访|不敢乱点|忘记吃|漏服",
    "system_access_friction": r"挂号|排队|预约|窗口|医保|费用|请假|交通|床位|转诊单|病历|取号|电话打不通|系统|平台|上传|看不到",
    "ordinary_friction": r"担心|焦虑|不放心|家属|上班|请假|照护|门诊|复诊|医院|护士|医生号|路上|费用",
    "patient_uncertainty": r"不懂|不清楚|不知道|记不清|忘了|听不懂|看不明白|搞不清|糊涂|严重吗|靠谱吗|怎么办|要不要",
    "closure_runway_support": r"稳定|好转|没有.*(发热|腹痛|胸痛|黑便|便血|呼吸困难)|复诊|随访|追踪|谁负责|负责|电话|预约|时间|异常.*怎么办|红旗|警示|计划|交代清楚",
}

CAUSAL_MARKERS = r"因为|由于|导致|术后|用药|停药|漏服|感染|并发|已知|既往|原发|复发|进展|复查|结果提示|报告提示|doctor|医生|外部接管|急诊接管"
CONDITIONAL_SAFETY_NET = r"如果|一旦|若|出现.*就|需要警惕|红旗"
NEGATION_AROUND_MAJOR = r"(?:没有|未见|否认|无明显|暂不|并无|不是)"
REPEATED_MARKERS = r"仍|还是|继续|再次|反复|前面|之前|仍然|还在|持续|未见新|没有新|同前|复述"
TRUE_NEW_MARKERS = r"突然|新出现|刚刚|开始|加重|越来越|第一次|新发|当天|今晨|现在出现|这次出现"


@dataclass
class TempoEvent:
    turn: int | None
    event_class: str
    phase: str
    novelty: str
    source_label: str
    policy_status: str
    severity: str
    signature: str
    why: str
    evidence: list[Json] = field(default_factory=list)

    def to_dict(self) -> Json:
        return {
            "turn": self.turn,
            "event_class": self.event_class,
            "phase": self.phase,
            "novelty": self.novelty,
            "source_label": self.source_label,
            "policy_status": self.policy_status,
            "severity": self.severity,
            "signature": self.signature,
            "why": self.why,
            "evidence": self.evidence,
        }


def _short(text: Any, limit: int = 300) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    return value[:limit] + ("…" if len(value) > limit else "")


def _as_dict(value: Any) -> Json:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _evidence_dict(ev: TextEvidence) -> Json:
    return ev.to_dict()


def _phase_map_from_episode_audit(episode_audit: Mapping[str, Any]) -> tuple[dict[int, str], list[Json]]:
    phase_by_turn: dict[int, str] = {}
    for row in _as_list(episode_audit.get("phase_timeline_tail")):
        if not isinstance(row, dict):
            continue
        try:
            turn = int(row.get("turn"))
        except Exception:
            continue
        phase = str(row.get("episode_phase") or "unknown")
        if phase and phase != "unknown":
            phase_by_turn[turn] = phase
    windows: list[Json] = []
    for row in _as_list(episode_audit.get("phase_windows")):
        if isinstance(row, dict):
            windows.append(row)
    return phase_by_turn, windows


def _phase_for_turn(turn: int | None, phase_by_turn: Mapping[int, str], windows: Iterable[Mapping[str, Any]]) -> str:
    if turn is None:
        return "unknown"
    if turn in phase_by_turn:
        return phase_by_turn[turn]
    for row in windows:
        try:
            start = int(row.get("start_turn") or 0)
            end = int(row.get("end_turn") or 0)
        except Exception:
            continue
        if start <= turn <= end:
            return str(row.get("dominant_phase") or "unknown")
    return "unknown"




def _evidence_relevant_for_tempo(ev: TextEvidence) -> bool:
    """Keep live world/dialogue/safety evidence; drop evaluator summaries as per-turn tempo.

    ClosureJudge and fragment evaluator text often summarizes every historical
    blocker and can make all earlier turns look like fresh world events.  G4
    uses those artifacts only through upstream G1/G2/G3 audits, not as raw
    event-frequency evidence.
    """

    speaker = str(ev.speaker or "")
    event_type = str(ev.event_type or "")
    visibility = str(ev.visibility or "")
    if re.search(r"SafetyEvent|CriticalSafety|safety_ledger|critical_safety", speaker + " " + event_type, re.I):
        return True
    if re.search(r"ClosureJudge|FragmentTrajectoryEvaluator|TrajectoryEvaluator|QualityScanner|Judge", speaker + " " + event_type, re.I):
        return False
    if visibility == "evaluator_visible":
        return False
    return True

def _is_doctor_conditional_safety_net(ev: TextEvidence) -> bool:
    speaker = str(ev.speaker or "").lower()
    text = ev.text or ""
    return "doctor" in speaker and bool(re.search(CONDITIONAL_SAFETY_NET, text) and re.search("黑便|便血|发热|腹痛|胸痛|晕厥|出血|血氧|呼吸困难|加重|叫不醒|昏迷|休克", text))


def _major_signatures(text: str) -> list[str]:
    out: list[str] = []
    for name, pattern in MAJOR_SIGNATURE_PATTERNS.items():
        if re.search(pattern, text, re.I):
            # Negated red flags in a stability sentence are closure-runway evidence, not active major events.
            if re.search(NEGATION_AROUND_MAJOR + r".{0,8}(" + pattern + r")", text, re.I):
                continue
            out.append(name)
    return out


def _main_class_for_evidence(ev: TextEvidence) -> str:
    text = ev.text or ""
    if _is_doctor_conditional_safety_net(ev):
        if re.search(CLASS_PATTERNS["closure_runway_support"], text, re.I):
            return "closure_runway_support"
        return "minor_continuation"
    if re.search(CLASS_PATTERNS["terminal_event"], text, re.I):
        return "terminal_event"
    if re.search(CLASS_PATTERNS["doctor_induced_consequence"], text, re.I):
        return "doctor_induced_consequence"
    if re.search(CLASS_PATTERNS["external_takeover_event"], text, re.I):
        return "external_takeover_event"
    if _major_signatures(text):
        return "major_new_issue"
    for cls in (
        "pending_result_update",
        "patient_behavior_issue",
        "system_access_friction",
        "patient_uncertainty",
        "closure_runway_support",
        "ordinary_friction",
    ):
        if re.search(CLASS_PATTERNS[cls], text, re.I):
            return cls
    return "minor_continuation"


def _source_label(event_class: str, text: str) -> str:
    lowered = text.lower()
    if event_class == "doctor_induced_consequence":
        return "doctor_induced_consequence"
    if event_class == "external_takeover_event":
        return "external_care_event"
    if event_class == "terminal_event":
        return "terminal_event"
    if re.search(r"hidden_truth|隐藏|真实病情|潜在疾病", lowered, re.I):
        return "hidden_truth_progression"
    if re.search(r"并发|术后|感染|出血|血糖|酮|停药|漏服|药", text, re.I):
        return "known_complication_or_action_chain"
    if event_class == "patient_behavior_issue":
        return "patient_behavior_event"
    if event_class == "system_access_friction":
        return "system_friction"
    if event_class == "pending_result_update":
        return "legitimate_pending_process"
    if event_class == "major_new_issue":
        if re.search(CAUSAL_MARKERS, text, re.I):
            return "causally_labeled_major_issue"
        return "rare_unexpected_event"
    return "ordinary_episode_progression"


def _severity(event_class: str, source_label: str, text: str, phase: str, novelty: str) -> str:
    if event_class == "terminal_event":
        return "critical"
    if event_class == "doctor_induced_consequence":
        return "critical" if re.search(r"critical|不安全|危险|死亡|休克|昏迷|延误.*急诊", text, re.I) else "high"
    if event_class == "external_takeover_event":
        return "high"
    if event_class == "major_new_issue":
        if novelty == "true_new_event" and phase in {"closure_runway", "result_pending", "stabilization"} and source_label == "rare_unexpected_event":
            return "high"
        return "medium" if novelty == "repeated_summary" else "high"
    if event_class in {"patient_behavior_issue", "pending_result_update"}:
        return "medium"
    return "low"


def _policy_status(event_class: str, phase: str, novelty: str, source_label: str, text: str) -> tuple[str, str]:
    allowed = PHASE_ALLOWED.get(phase, set(EVENT_CLASSES))
    if event_class == "doctor_induced_consequence":
        return "route_to_failure_escalation", "doctor-induced consequence should be handled as safety/failure escalation, not endless ordinary questioning"
    if event_class == "terminal_event":
        return "allowed_terminal", "terminal events are allowed when clinically caused/labeled and should end or classify the episode"
    if event_class == "major_new_issue":
        if novelty == "repeated_summary":
            return "monitor_repeated_summary", "major-signal language appears repeated rather than a true new issue; avoid counting it as fresh world tempo expansion"
        if phase == "closure_runway" and source_label == "rare_unexpected_event":
            return "violation_candidate", "closure runway should not introduce a new unrelated major issue without explicit clinical causality"
        if phase in {"result_pending", "stabilization", "medication_reconciliation"} and source_label == "rare_unexpected_event":
            return "restricted_phase_mismatch", f"{phase} should focus on existing threads unless a major new issue has explicit causality or episode-level budget"
        if event_class not in allowed:
            return "restricted_phase_mismatch", f"{event_class} is not in the phase-aware allowed set for {phase}"
        return "allowed_with_causal_review", "major issue may be legitimate if tied to hidden truth, identified disease progression, complication, or doctor-induced consequence"
    if event_class not in allowed and phase in PHASE_ALLOWED:
        return "restricted_phase_mismatch", f"{event_class} is not normally allowed in {phase}"
    return "allowed", "event class is compatible with the current phase under G4 shadow policy"


def _signature_for_event(event_class: str, text: str) -> str:
    if event_class in {"major_new_issue", "external_takeover_event", "terminal_event", "doctor_induced_consequence"}:
        sigs = _major_signatures(text)
        if sigs:
            return "+".join(sorted(sigs))
        if event_class == "doctor_induced_consequence":
            return "doctor_induced_consequence"
        if event_class == "external_takeover_event":
            return "external_takeover_event"
        if event_class == "terminal_event":
            return "death_or_terminal"
    # For lower-risk classes, a coarse signature is enough for density summaries.
    return event_class


def classify_tempo_events(
    payload: Mapping[str, Any],
    *,
    episode_audit: Mapping[str, Any] | None = None,
) -> list[TempoEvent]:
    """Classify trajectory evidence into phase-aware tempo events.

    This is intentionally conservative: it labels candidate tempo problems for
    audit, but it never edits the trajectory or changes closure/runtime behavior.
    """

    if episode_audit is None:
        episode_audit = audit_episode_governance(payload)
    phase_by_turn, windows = _phase_map_from_episode_audit(episode_audit)
    evidences = [ev for ev in collect_text_evidence(payload) if _evidence_relevant_for_tempo(ev)]
    first_seen_signature: dict[str, int] = {}
    events: list[TempoEvent] = []
    for ev in evidences:
        text = ev.text or ""
        event_class = _main_class_for_evidence(ev)
        turn = ev.turn
        phase = _phase_for_turn(turn, phase_by_turn, windows)
        signature = _signature_for_event(event_class, text)
        source = _source_label(event_class, text)
        if event_class in {"major_new_issue", "rare_unexpected_event", "external_takeover_event", "terminal_event", "doctor_induced_consequence"}:
            first = first_seen_signature.get(signature)
            if first is None and turn is not None:
                first_seen_signature[signature] = turn
                first = turn
            if first is not None and turn is not None and turn > first and (re.search(REPEATED_MARKERS, text, re.I) or event_class == "major_new_issue"):
                novelty = "repeated_summary"
            elif re.search(TRUE_NEW_MARKERS, text, re.I) or first == turn:
                novelty = "true_new_event"
            else:
                novelty = "candidate_new_event"
        else:
            novelty = "routine_or_continuation"
        policy, why = _policy_status(event_class, phase, novelty, source, text)
        events.append(
            TempoEvent(
                turn=turn,
                event_class=event_class,
                phase=phase,
                novelty=novelty,
                source_label=source,
                policy_status=policy,
                severity=_severity(event_class, source, text, phase, novelty),
                signature=signature,
                why=why,
                evidence=[_evidence_dict(ev)],
            )
        )
    return events


def _turns_completed(payload: Mapping[str, Any], events: Iterable[TempoEvent]) -> int:
    explicit = payload.get("turns_completed")
    if isinstance(explicit, int):
        return explicit
    return max((event.turn or 0 for event in events), default=0)


def _event_counts(events: Iterable[TempoEvent]) -> Json:
    class_counts = Counter(event.event_class for event in events)
    novelty_counts = Counter(event.novelty for event in events)
    policy_counts = Counter(event.policy_status for event in events)
    source_counts = Counter(event.source_label for event in events)
    phase_counts = Counter(event.phase for event in events)
    return {
        "event_class_counts": class_counts.most_common(),
        "novelty_counts": novelty_counts.most_common(),
        "policy_status_counts": policy_counts.most_common(),
        "source_label_counts": source_counts.most_common(),
        "phase_counts": phase_counts.most_common(),
    }


def _window_metrics(events: list[TempoEvent], turns_completed: int, window_size: int = 30) -> list[Json]:
    if turns_completed <= 0:
        return []
    rows: list[Json] = []
    start = 1
    while start <= turns_completed:
        end = min(turns_completed, start + window_size - 1)
        subset = [e for e in events if e.turn is not None and start <= e.turn <= end]
        true_major = [e for e in subset if e.event_class == "major_new_issue" and e.novelty in {"true_new_event", "candidate_new_event"}]
        repeated_major = [e for e in subset if e.event_class == "major_new_issue" and e.novelty == "repeated_summary"]
        restricted = [e for e in subset if e.policy_status in {"restricted_phase_mismatch", "violation_candidate"}]
        doctor_induced = [e for e in subset if e.event_class == "doctor_induced_consequence"]
        dominant_phase = Counter(e.phase for e in subset).most_common(1)[0][0] if subset else "unknown"
        interpretation = "tempo compatible with phase-aware shadow policy"
        if restricted:
            interpretation = "contains phase-policy restricted/violation candidate events; needs semantic review before runtime integration"
        elif true_major and dominant_phase in {"result_pending", "closure_runway", "stabilization"}:
            interpretation = "late/result-pending window contains true major events; check whether clinically caused or world tempo overactive"
        elif repeated_major and not true_major:
            interpretation = "major-signal language mostly repeats existing thread; should not be treated as fresh tempo expansion"
        elif doctor_induced:
            interpretation = "doctor-induced consequence appears; should feed G5 failure/escape monitor rather than ordinary friction"
        rows.append(
            {
                "start_turn": start,
                "end_turn": end,
                "dominant_phase": dominant_phase,
                "event_class_counts": Counter(e.event_class for e in subset).most_common(),
                "true_major_event_turns": sorted({e.turn for e in true_major if e.turn is not None}),
                "repeated_major_summary_turns": sorted({e.turn for e in repeated_major if e.turn is not None}),
                "restricted_or_violation_turns": sorted({e.turn for e in restricted if e.turn is not None}),
                "doctor_induced_turns": sorted({e.turn for e in doctor_induced if e.turn is not None}),
                "interpretation": interpretation,
            }
        )
        start = end + 1
    return rows


def _rare_event_budget(events: list[TempoEvent], *, routine_major_budget: int = 0) -> Json:
    counted = [
        e
        for e in events
        if e.event_class == "major_new_issue"
        and e.novelty in {"true_new_event", "candidate_new_event"}
        and e.source_label == "rare_unexpected_event"
    ]
    status = "within_budget" if len(counted) <= routine_major_budget else "over_budget_needs_case_profile_or_causal_label"
    return {
        "budget_scope": "routine_case_default_shadow_budget; can be raised by case profile/high-risk hidden-truth configuration in later integration",
        "routine_major_unexpected_event_budget": routine_major_budget,
        "counted_major_unexpected_events": len(counted),
        "status": status,
        "events": [e.to_dict() for e in counted[:10]],
    }


def _closure_runway_discipline(events: list[TempoEvent], turns_completed: int, *, window: int = 12) -> Json:
    start = max(1, turns_completed - window + 1)
    subset = [e for e in events if e.turn is not None and start <= e.turn <= turns_completed]
    true_unrelated_major = [
        e
        for e in subset
        if e.event_class == "major_new_issue"
        and e.novelty in {"true_new_event", "candidate_new_event"}
        and e.source_label == "rare_unexpected_event"
    ]
    runway_support = [e for e in subset if e.event_class == "closure_runway_support"]
    doctor_induced = [e for e in subset if e.event_class == "doctor_induced_consequence"]
    status = "pass"
    if true_unrelated_major:
        status = "fail_or_semantic_review_required"
    elif doctor_induced:
        status = "not_safe_runway_doctor_failure_present"
    elif len(runway_support) >= max(1, window // 4):
        status = "possible_natural_runway"
    return {
        "window_start_turn": start,
        "window_end_turn": turns_completed,
        "status": status,
        "true_unrelated_major_event_turns": sorted({e.turn for e in true_unrelated_major if e.turn is not None}),
        "closure_support_turns": sorted({e.turn for e in runway_support if e.turn is not None}),
        "doctor_induced_turns": sorted({e.turn for e in doctor_induced if e.turn is not None}),
        "interpretation": (
            "closure runway should be protected from new unrelated major issues"
            if true_unrelated_major
            else "last-window tempo does not show deterministic unrelated major issue injection"
        ),
    }


def _phase_policy_findings(events: list[TempoEvent]) -> list[Json]:
    findings: list[Json] = []
    grouped: dict[str, list[TempoEvent]] = defaultdict(list)
    for event in events:
        if event.policy_status in {"restricted_phase_mismatch", "violation_candidate", "route_to_failure_escalation"}:
            grouped[event.policy_status].append(event)
    if grouped.get("violation_candidate"):
        findings.append(
            {
                "finding": "closure_runway_or_late_phase_unrelated_major_issue",
                "status": "fail_or_semantic_review_required",
                "why": "A true/candidate new major issue appears in closure runway or another restrictive phase without explicit causality.",
                "events": [e.to_dict() for e in grouped["violation_candidate"][:8]],
            }
        )
    if grouped.get("restricted_phase_mismatch"):
        findings.append(
            {
                "finding": "phase_policy_restricted_major_issue",
                "status": "review_required",
                "why": "Result-pending/stabilization/medication reconciliation should not frequently open unrelated major lines unless causally labeled or budgeted.",
                "events": [e.to_dict() for e in grouped["restricted_phase_mismatch"][:8]],
            }
        )
    if grouped.get("route_to_failure_escalation"):
        findings.append(
            {
                "finding": "doctor_induced_consequence_should_not_be_ordinary_friction",
                "status": "route_to_G5",
                "why": "Doctor-induced unsafe consequences should move toward failure escalation/external takeover classification rather than keeping the world in generic Q&A.",
                "events": [e.to_dict() for e in grouped["route_to_failure_escalation"][:8]],
            }
        )
    if not findings:
        findings.append(
            {
                "finding": "phase_policy_shadow_check",
                "status": "pass_with_notes",
                "why": "No deterministic phase-policy violation candidate found by code-driven G4 scanner; semantic review still required before runtime prompt changes.",
                "events": [],
            }
        )
    return findings


def _next_actions(findings: list[Json], budget: Mapping[str, Any], closure: Mapping[str, Any]) -> list[str]:
    actions: list[str] = []
    statuses = {str(f.get("status")) for f in findings}
    if "fail_or_semantic_review_required" in statuses or str(budget.get("status")) == "over_budget_needs_case_profile_or_causal_label":
        actions.append("G4/G7: review WorldDirector/contingency prompts for phase-aware event budgets before active integration")
    if any(f.get("status") == "route_to_G5" for f in findings):
        actions.append("G5: feed doctor-induced consequence turns into doctor-hostage escape and terminal outcome shadow classifiers")
    if closure.get("status") in {"possible_natural_runway", "pass"}:
        actions.append("G6: when residual-risk audit allows it, let closure judge inspect protected closure-runway evidence")
    actions.append("Manual calibration: verify whether flagged major events are truly new, clinically caused, or repeated summaries before changing runtime tempo")
    # De-duplicate preserving order.
    seen: set[str] = set()
    out: list[str] = []
    for action in actions:
        if action not in seen:
            out.append(action)
            seen.add(action)
    return out


def audit_tempo_governor(
    payload: Mapping[str, Any],
    *,
    episode_audit: Mapping[str, Any] | None = None,
    residual_audit: Mapping[str, Any] | None = None,
    world_audit: Mapping[str, Any] | None = None,
    name: str = "trajectory",
) -> Json:
    if episode_audit is None:
        episode_audit = audit_episode_governance(payload, name=name)
    events = classify_tempo_events(payload, episode_audit=episode_audit)
    turns_completed = _turns_completed(payload, events)
    windows = _window_metrics(events, turns_completed)
    budget = _rare_event_budget(events)
    closure = _closure_runway_discipline(events, turns_completed)
    findings = _phase_policy_findings(events)
    counts = _event_counts(events)
    restricted_count = sum(1 for e in events if e.policy_status in {"restricted_phase_mismatch", "violation_candidate"})
    true_major_count = sum(1 for e in events if e.event_class == "major_new_issue" and e.novelty in {"true_new_event", "candidate_new_event"})
    repeated_major_count = sum(1 for e in events if e.event_class == "major_new_issue" and e.novelty == "repeated_summary")
    doctor_induced_count = sum(1 for e in events if e.event_class == "doctor_induced_consequence")
    finding_statuses = {str(f.get("status")) for f in findings}
    overall_status = "pass_with_notes"
    if (
        "fail_or_semantic_review_required" in finding_statuses
        or "review_required" in finding_statuses
        or budget.get("status") == "over_budget_needs_case_profile_or_causal_label"
        or restricted_count > 0
    ):
        overall_status = "review_required"
    elif "route_to_G5" in finding_statuses:
        overall_status = "route_to_G5_with_tempo_notes"
    summary = {
        "case_id": payload.get("case_id"),
        "run_id": payload.get("run_id"),
        "turns_completed": turns_completed,
        "closure_status": _as_dict(payload.get("closure")).get("status"),
        "closure_kind": _as_dict(payload.get("closure")).get("closure_kind"),
        "true_or_candidate_major_event_count": true_major_count,
        "repeated_major_summary_count": repeated_major_count,
        "restricted_or_violation_event_count": restricted_count,
        "doctor_induced_consequence_count": doctor_induced_count,
    }
    audit = {
        "schema_version": "careloop.tempo_governor_audit.v1",
        "name": name,
        "scope": "G4 offline/shadow audit; no runtime/prompt/closure/scoring/world-generation changes",
        "summary": summary,
        "event_counts": counts,
        "phase_policy_findings": findings,
        "event_budget": budget,
        "closure_runway_discipline": closure,
        "window_metrics": windows,
        "events_tail": [e.to_dict() for e in events[-60:]],
        "events_flagged": [
            e.to_dict()
            for e in events
            if e.policy_status in {"restricted_phase_mismatch", "violation_candidate", "route_to_failure_escalation"}
        ][:80],
        "upstream_audit_refs": {
            "episode_schema_version": episode_audit.get("schema_version") if isinstance(episode_audit, Mapping) else None,
            "residual_schema_version": residual_audit.get("schema_version") if isinstance(residual_audit, Mapping) else None,
            "world_schema_version": world_audit.get("schema_version") if isinstance(world_audit, Mapping) else None,
        },
        "overall": {
            "status": overall_status,
            "tempo_safe_for_soft_integration": overall_status == "pass_with_notes",
            "interpretation": "G4 is a code-driven shadow screen; any runtime prompt change must be calibrated against semantic trajectory review.",
        },
        "next_recommended_actions": _next_actions(findings, budget, closure),
    }
    return audit


def render_markdown(audit: Mapping[str, Any]) -> str:
    summary = _as_dict(audit.get("summary"))
    overall = _as_dict(audit.get("overall"))
    lines = [
        "# Phase-aware Tempo Governor Audit",
        "",
        f"name: `{audit.get('name')}`  ",
        f"schema: `{audit.get('schema_version')}`  ",
        f"scope: {audit.get('scope')}",
        "",
        "## Summary",
        "",
        f"- case_id: `{summary.get('case_id')}`",
        f"- run_id: `{summary.get('run_id')}`",
        f"- turns_completed: {summary.get('turns_completed')}",
        f"- closure: `{summary.get('closure_status')}` / `{summary.get('closure_kind')}`",
        f"- true/candidate major events: {summary.get('true_or_candidate_major_event_count')}",
        f"- repeated major summaries: {summary.get('repeated_major_summary_count')}",
        f"- restricted/violation candidates: {summary.get('restricted_or_violation_event_count')}",
        f"- doctor-induced consequences: {summary.get('doctor_induced_consequence_count')}",
        f"- overall: `{overall.get('status')}`",
        "",
        "## Phase-policy findings",
        "",
    ]
    for finding in _as_list(audit.get("phase_policy_findings")):
        if not isinstance(finding, dict):
            continue
        lines += [
            f"### {finding.get('finding')}",
            "",
            f"- status: `{finding.get('status')}`",
            f"- why: {finding.get('why')}",
        ]
        for event in _as_list(finding.get("events"))[:5]:
            ev = _as_dict(event)
            evidence = _as_list(ev.get("evidence"))
            sample = _short(evidence[0].get("text") if evidence and isinstance(evidence[0], dict) else "")
            lines.append(f"  - turn {ev.get('turn')} `{ev.get('event_class')}` / `{ev.get('policy_status')}` / `{ev.get('novelty')}`: {sample}")
        lines.append("")
    budget = _as_dict(audit.get("event_budget"))
    lines += [
        "## Rare/unexpected event budget",
        "",
        f"- scope: {budget.get('budget_scope')}",
        f"- routine_major_unexpected_event_budget: {budget.get('routine_major_unexpected_event_budget')}",
        f"- counted_major_unexpected_events: {budget.get('counted_major_unexpected_events')}",
        f"- status: `{budget.get('status')}`",
        "",
        "## Closure runway discipline",
        "",
    ]
    closure = _as_dict(audit.get("closure_runway_discipline"))
    lines += [
        f"- window: {closure.get('window_start_turn')}-{closure.get('window_end_turn')}",
        f"- status: `{closure.get('status')}`",
        f"- true_unrelated_major_event_turns: {closure.get('true_unrelated_major_event_turns')}",
        f"- closure_support_turns: {closure.get('closure_support_turns')}",
        f"- doctor_induced_turns: {closure.get('doctor_induced_turns')}",
        f"- interpretation: {closure.get('interpretation')}",
        "",
        "## Window metrics",
        "",
        "| turns | phase | true major | repeated major | restricted | doctor-induced | interpretation |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for row in _as_list(audit.get("window_metrics")):
        if not isinstance(row, dict):
            continue
        lines.append(
            f"| {row.get('start_turn')}-{row.get('end_turn')} | `{row.get('dominant_phase')}` | "
            f"{len(_as_list(row.get('true_major_event_turns')))} | "
            f"{len(_as_list(row.get('repeated_major_summary_turns')))} | "
            f"{len(_as_list(row.get('restricted_or_violation_turns')))} | "
            f"{len(_as_list(row.get('doctor_induced_turns')))} | {row.get('interpretation')} |"
        )
    lines += ["", "## Next recommended actions", ""]
    for action in _as_list(audit.get("next_recommended_actions")):
        lines.append(f"- {action}")
    lines.append("")
    return "\n".join(lines)


__all__ = [
    "audit_tempo_governor",
    "classify_tempo_events",
    "load_trajectory_payload",
    "render_markdown",
]
