from __future__ import annotations

"""Offline Episode Governance audit helpers for runtime_lite trajectories.

G1 scope: descriptive/shadow-only audit.  This module deliberately does not
change runtime flow, prompts, scoring, closure decisions, or world generation.
It turns an existing trajectory/checkpoint payload into evidence about phase,
world tempo, residual blockers, closure runway and possible doctor-hostage
patterns so later tranches can be calibrated from real trajectories.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

Json = dict[str, Any]


EVENT_CLASS_PATTERNS: dict[str, str] = {
    "ordinary_friction": r"挂号|排队|预约|门诊|住院|急诊|窗口|护士|医保|费用|交通|请假|照护|病历|报告|检查单|取号|复诊|医院",
    "pending_result_update": r"结果|报告|病理|化验|复查|未出|没出来|等待|工作日|pending|培养|影像|心电图|抽血",
    "patient_uncertainty": r"不懂|不清楚|不知道|记不清|忘了|听不懂|看不明白|搞不清|糊涂|担心|害怕|焦虑|不放心|严重吗|靠谱吗",
    "medication_issue": r"药|阿司匹林|抗凝|抗血小板|降压|降糖|二甲双胍|胰岛素|激素|抗生素|停药|恢复|漏服|剂量|药盒|信必可|喷雾",
    "patient_behavior_issue": r"不想去|不愿|拒绝|自行|自己|拖|隐瞒|没说|骗|瞒|不配合|没按|怕花钱|怕耽误|不肯|失访",
    # This class is meant to capture live/active major clinical signals, not every
    # doctor safety-net sentence listing red flags.  Additional contextual filters
    # are applied in ``classify_evidence`` below.
    "symptom_or_major_signal": r"胸痛|喘|呼吸困难|头晕|乏力|黑便|便血|发热|腹痛|晕厥|出血|恶心|呕吐|加重|新出现|血氧|抢救|死亡|昏迷|休克|高血糖|血糖\s*(?:约|还是|仍|高|[0-9０-９])|血糖[0-9０-９]|口干|尿多|脱水|酮体|电解质|站不起来|叫不醒|精神.*蔫",
    "handoff_or_external_care": r"急诊|120|救护车|住院|专科|转诊|外院|线下|分诊|接诊|外部医生|接管|抢救",
    "followup_or_responsibility": r"随访|复诊|追踪|谁负责|负责|联系|电话|预约|时间|几天后|什么时候|异常.*怎么办|急诊.*条件|警示|红旗",
    # Keep this deliberately narrow. Generic 风险/危险/没有明确 are common in
    # appropriate clinical counseling and should not turn the whole episode into
    # failure_escalation; explicit critical/safety/failure markers are handled
    # separately by ``critical_safety_turns``.
    "doctor_failure_signal": r"不安全|错误建议|错误强化|误判|误导|延误|遗漏|critical|unsafe|safety event|critical safety|failure|triage_failure|危险建议",
    "safety_net_instruction": r"如果.*(黑便|便血|发热|腹痛|胸痛|晕厥|出血|血氧|呼吸困难|加重|叫不醒|昏迷|休克)|一旦.*(黑便|便血|发热|腹痛|胸痛|晕厥|出血|呼吸困难|加重|叫不醒|昏迷|休克)",
}

PHASES = [
    "initial_uncertainty",
    "active_workup",
    "treatment_execution",
    "result_pending",
    "medication_reconciliation",
    "stabilization",
    "closure_runway",
    "failure_escalation",
    "terminal_state",
]

BLOCKER_SEVERITY_ORDER = {"low": 1, "medium": 2, "high": 3, "critical": 4}


def load_trajectory_payload(path: str | Path) -> Json:
    p = Path(path)
    return json.loads(p.read_text(encoding="utf-8"))


def _short(text: Any, limit: int = 260) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    return value[:limit] + ("…" if len(value) > limit else "")


def _as_dict(value: Any) -> Json:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _event_text(event: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for key in ("content", "text", "summary", "rationale", "result_preview", "message", "title", "description"):
        value = event.get(key)
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, dict):
            parts.append(json.dumps(value, ensure_ascii=False)[:2000])
    metadata = event.get("metadata")
    if isinstance(metadata, dict):
        for key in (
            "content",
            "text",
            "summary",
            "rationale",
            "doctor_visible_summary",
            "visible_content",
            "patient_visible_text",
            "world_effect_instruction",
            "why_unsafe",
        ):
            value = metadata.get(key)
            if isinstance(value, str):
                parts.append(value)
            elif isinstance(value, dict):
                parts.append(json.dumps(value, ensure_ascii=False)[:2000])
    return "\n".join(parts)


def _transcript_text(item: Mapping[str, Any]) -> str:
    return str(item.get("text") or item.get("content") or "")


def _turn_of(item: Mapping[str, Any]) -> int | None:
    raw = item.get("turn")
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    try:
        return int(raw)
    except Exception:
        return None


@dataclass
class TextEvidence:
    turn: int | None
    speaker: str
    event_type: str
    visibility: str
    text: str
    source: str

    def to_dict(self) -> Json:
        return {
            "turn": self.turn,
            "speaker": self.speaker,
            "event_type": self.event_type,
            "visibility": self.visibility,
            "source": self.source,
            "text": _short(self.text, 360),
        }


@dataclass
class TurnSignals:
    turn: int
    class_counts: Counter[str] = field(default_factory=Counter)
    text_samples: list[TextEvidence] = field(default_factory=list)
    event_types: Counter[str] = field(default_factory=Counter)
    llm_purposes: Counter[str] = field(default_factory=Counter)

    def score(self, *classes: str) -> int:
        return sum(self.class_counts.get(cls, 0) for cls in classes)

    def to_dict(self) -> Json:
        return {
            "turn": self.turn,
            "class_counts": dict(self.class_counts),
            "event_types": self.event_types.most_common(12),
            "llm_purposes": self.llm_purposes.most_common(12),
            "samples": [sample.to_dict() for sample in self.text_samples[:6]],
        }


def _event_relevant_for_episode_audit(event: Mapping[str, Any]) -> bool:
    """Filter event text used by turn-level heuristics.

    Full trajectories contain large internal memory snapshots and evaluator notes
    that repeat the whole case. Counting those as per-turn world events inflates
    phase/tempo signals. G1 should primarily inspect visible transcript, visible
    or doctor-facing world/workspace material, plus explicit closure/evaluation/
    safety markers needed for failure attribution.
    """

    visibility = str(event.get("visibility") or "")
    actor = str(event.get("actor") or "")
    event_type = str(event.get("event_type") or "")
    if visibility in {"doctor_visible", "patient_visible", "public"}:
        return True
    if re.search(r"critical_safety|safety_event|unsafe|triage_failure|doctor_failure", event_type, re.I):
        return True
    if re.search(r"closure|fragment_trajectory_evaluation|trajectory_evaluation", event_type, re.I):
        return True
    if visibility == "evaluator_visible" and re.search(r"Evaluator|Judge|Closure", actor + " " + event_type, re.I):
        return True
    return False


def collect_text_evidence(payload: Mapping[str, Any]) -> list[TextEvidence]:
    trajectory = _as_dict(payload.get("trajectory"))
    items: list[TextEvidence] = []
    for row in _as_list(trajectory.get("transcript")):
        if not isinstance(row, dict):
            continue
        text = _transcript_text(row)
        if not text.strip():
            continue
        items.append(
            TextEvidence(
                turn=_turn_of(row),
                speaker=str(row.get("speaker") or row.get("speaker_display") or "transcript"),
                event_type=str(row.get("event_type") or "transcript"),
                visibility="patient_doctor_dialogue" if row.get("patient_visible", True) else "transcript",
                text=text,
                source="transcript",
            )
        )
    for event in _as_list(trajectory.get("events")):
        if not isinstance(event, dict) or not _event_relevant_for_episode_audit(event):
            continue
        text = _event_text(event)
        if not text.strip():
            continue
        actor = str(event.get("actor") or "")
        event_type = str(event.get("event_type") or "")
        visibility = str(event.get("visibility") or "")
        items.append(
            TextEvidence(
                turn=_turn_of(event),
                speaker=actor or "event",
                event_type=event_type,
                visibility=visibility,
                text=text,
                source="event",
            )
        )
    return items


def classify_text(text: str) -> Counter[str]:
    counts: Counter[str] = Counter()
    for label, pattern in EVENT_CLASS_PATTERNS.items():
        if re.search(pattern, text or "", re.I):
            counts[label] += 1
    return counts


def _is_doctor_safety_net_only(evidence: TextEvidence, text: str) -> bool:
    """Return True when a red-flag string is merely conditional safety-net advice.

    G1 initially counted every doctor sentence such as "如果黑便/发热就去急诊"
    as a live major signal.  That made late turns look overactive even when the
    doctor was just giving an appropriate escalation rule.  This helper only
    suppresses symptom_or_major_signal for doctor-authored conditional advice;
    patient/family reports and committed world facts remain eligible signals.
    """

    speaker = (evidence.speaker or "").lower()
    if "doctor" not in speaker and "医生" not in evidence.speaker:
        return False
    has_red_flag = re.search(EVENT_CLASS_PATTERNS["symptom_or_major_signal"], text or "", re.I)
    has_conditional = re.search(EVENT_CLASS_PATTERNS["safety_net_instruction"], text or "", re.I)
    has_active_state = re.search(
        r"现在|已经|仍然|持续|越来越|刚才|测了|血糖|口干|尿多|站不起来|叫不醒|精神.*蔫|已到急诊|抽血|扎针|补液|留观",
        text or "",
        re.I,
    )
    return bool(has_red_flag and has_conditional and not has_active_state)


def _is_negated_red_flag_report(text: str) -> bool:
    """Suppress pure negative red-flag checklists from major-signal counts."""

    red_flags = r"黑便|便血|发热|腹痛|胸痛|晕厥|出血|血氧|呼吸困难|喘不上气|昏迷|休克"
    if not re.search(red_flags, text or ""):
        return False
    # If the same text contains active metabolic/progression language, keep it.
    if re.search(r"血糖|口干|尿多|站不起来|叫不醒|精神.*蔫|越来越|加重|脱水|酮体|电解质", text or ""):
        return False
    return bool(re.search(r"没有|没|未|不是|无", text or "") and not re.search(r"出现|发生|持续|加重|越来越", text or ""))


def classify_evidence(evidence: TextEvidence) -> Counter[str]:
    counts = classify_text(evidence.text)
    if counts.get("symptom_or_major_signal") and (
        _is_doctor_safety_net_only(evidence, evidence.text) or _is_negated_red_flag_report(evidence.text)
    ):
        counts["safety_net_instruction"] += 1
        counts.pop("symptom_or_major_signal", None)
    return counts


def turn_signals(payload: Mapping[str, Any]) -> dict[int, TurnSignals]:
    trajectory = _as_dict(payload.get("trajectory"))
    signals: dict[int, TurnSignals] = {}

    def get(turn: int) -> TurnSignals:
        if turn not in signals:
            signals[turn] = TurnSignals(turn=turn)
        return signals[turn]

    for evidence in collect_text_evidence(payload):
        turn = evidence.turn
        if turn is None:
            continue
        sig = get(turn)
        classes = classify_evidence(evidence)
        sig.class_counts.update(classes)
        if classes and len(sig.text_samples) < 12:
            sig.text_samples.append(evidence)
    for event in _as_list(trajectory.get("events")):
        if isinstance(event, dict) and _turn_of(event) is not None:
            get(int(_turn_of(event))).event_types.update([str(event.get("event_type") or "")])
    for call in _as_list(trajectory.get("llm_calls")):
        if isinstance(call, dict) and _turn_of(call) is not None:
            get(int(_turn_of(call))).llm_purposes.update([str(call.get("purpose") or "")])
    return signals


def _dominant_phase_for_turn(
    turn: int,
    sig: TurnSignals,
    max_turn: int,
    closure_status: str,
    critical_turns: set[int],
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if closure_status and closure_status != "open" and turn >= max_turn - 2:
        return "terminal_state", ["closure_status_non_open_near_end"]
    if turn in critical_turns:
        return "failure_escalation", ["explicit_critical_safety_turn"]
    # Do not let generic safety-critique wording dominate phase inference.
    # Failure phase is reserved for explicit severe/critical safety turns extracted
    # by critical_safety_turns(); moderate reversible safety notes remain evidence
    # for blockers/evaluation but the clinical phase can still be result_pending,
    # medication_reconciliation, etc.
    if turn <= 2 and sig.score("pending_result_update", "handoff_or_external_care") == 0:
        return "initial_uncertainty", ["early_turn_with_uncertainty"]
    if sig.score("medication_issue") >= max(2, sig.score("pending_result_update")) and sig.score("medication_issue") >= 2:
        return "medication_reconciliation", ["medication_issue_dominates"]
    if sig.score("pending_result_update") >= 2:
        return "result_pending", ["pending_result_or_report_signal"]
    if sig.score("handoff_or_external_care", "ordinary_friction") >= 2:
        return "active_workup", ["workup_or_external_care_signal"]
    if sig.score("followup_or_responsibility") >= 2 and sig.score("symptom_or_major_signal") <= 1:
        return "stabilization", ["followup_responsibility_without_many_major_signals"]
    if turn > max(5, int(max_turn * 0.75)) and sig.score("symptom_or_major_signal") <= 1 and sig.score("followup_or_responsibility") >= 1:
        return "closure_runway", ["late_turn_with_responsibility_verification_and_low_major_signal"]
    if sig.score("symptom_or_major_signal") >= 2:
        return "treatment_execution", ["active_symptom_or_major_signal"]
    return "active_workup" if turn <= max(5, int(max_turn * 0.4)) else "stabilization", ["fallback_by_turn_position"]


def phase_timeline(payload: Mapping[str, Any], signals: dict[int, TurnSignals]) -> list[Json]:
    turns_completed = int(payload.get("turns_completed") or max(signals.keys(), default=0) or 0)
    closure = _as_dict(payload.get("closure"))
    closure_status = str(closure.get("status") or "")
    critical_turns = set(critical_safety_turns(payload))
    timeline: list[Json] = []
    for turn in range(0, turns_completed + 1):
        sig = signals.get(turn, TurnSignals(turn=turn))
        phase, reasons = _dominant_phase_for_turn(turn, sig, turns_completed, closure_status, critical_turns)
        timeline.append(
            {
                "turn": turn,
                "episode_phase": phase,
                "phase_reasons": reasons,
                "class_counts": dict(sig.class_counts),
            }
        )
    return timeline


def summarize_phase_windows(timeline: list[Json], window_size: int = 30) -> list[Json]:
    if not timeline:
        return []
    max_turn = max(int(row.get("turn") or 0) for row in timeline)
    windows: list[Json] = []
    start = 1 if max_turn >= 1 else 0
    while start <= max_turn:
        end = min(max_turn, start + window_size - 1)
        rows = [row for row in timeline if start <= int(row.get("turn") or 0) <= end]
        phases = Counter(str(row.get("episode_phase")) for row in rows)
        class_counts: Counter[str] = Counter()
        for row in rows:
            class_counts.update(_as_dict(row.get("class_counts")))
        dominant = phases.most_common(1)[0][0] if phases else "unknown"
        windows.append(
            {
                "start_turn": start,
                "end_turn": end,
                "dominant_phase": dominant,
                "phase_counts": phases.most_common(),
                "class_counts": class_counts.most_common(),
                "interpretation": _window_interpretation(dominant, class_counts, start, end),
            }
        )
        start = end + 1
    return windows


def _window_interpretation(dominant: str, class_counts: Counter[str], start: int, end: int) -> str:
    if dominant == "result_pending":
        return "以检查/病理/报告等待与追踪为主；应观察是否有明确责任主体和等待期间安全策略。"
    if dominant == "medication_reconciliation":
        return "以药物停用/恢复/核对为主；若持续到后期，可能是 action-blocking residual。"
    if dominant == "failure_escalation":
        return "出现安全失败或外部接管信号；应考虑 failure terminal 而非无限 open。"
    if dominant == "closure_runway":
        return "可能进入闭环跑道；应核验是否仍有高风险 blocker。"
    if class_counts.get("symptom_or_major_signal", 0) >= max(3, (end - start + 1) // 3):
        return "症状/重大信号密集；若在后期仍持续，提示 tempo 或未解决主线问题。"
    return "常规推进窗口；需要结合 blocker attribution 判断是否自然收束。"


def event_class_counts(signals: dict[int, TurnSignals]) -> Json:
    total: Counter[str] = Counter()
    turns_by_class: dict[str, set[int]] = defaultdict(set)
    for turn, sig in signals.items():
        for cls, count in sig.class_counts.items():
            total[cls] += count
            if count:
                turns_by_class[cls].add(turn)
    return {
        "counts": total.most_common(),
        "turn_counts": {cls: len(turns) for cls, turns in sorted(turns_by_class.items())},
        "turns_by_class": {cls: sorted(turns)[:300] for cls, turns in sorted(turns_by_class.items())},
    }


def _latest_text(payload: Mapping[str, Any], last_n_turns: int = 30) -> str:
    turns_completed = int(payload.get("turns_completed") or 0)
    threshold = max(0, turns_completed - last_n_turns + 1)
    chunks: list[str] = []
    for evidence in collect_text_evidence(payload):
        if evidence.turn is not None and evidence.turn >= threshold:
            chunks.append(evidence.text)
    closure = _as_dict(payload.get("closure"))
    evaluation = _as_dict(payload.get("evaluation"))
    chunks.append(json.dumps(closure, ensure_ascii=False))
    chunks.append(json.dumps(evaluation, ensure_ascii=False)[:4000])
    return "\n".join(chunks)


def residual_issues(payload: Mapping[str, Any], signals: dict[int, TurnSignals]) -> list[Json]:
    latest = _latest_text(payload, 30)
    issues: list[Json] = []

    def add(issue_type: str, risk_class: str, evidence_pattern: str, why: str) -> None:
        samples = _samples_for_pattern(payload, evidence_pattern, last_n_turns=30, limit=4)
        if not samples:
            return
        issues.append(
            {
                "issue_type": issue_type,
                "risk_class": risk_class,
                "evidence": samples,
                "why_it_matters": why,
            }
        )

    add(
        "pending_result_or_report",
        "result_dependent_high_impact_residual_issue",
        r"病理|结果|报告|化验|复查|未出|没出来|等待|培养|影像",
        "结果可能影响诊断、随访或治疗决策；是否可 residual close 取决于风险、责任主体和异常结果处理路径。",
    )
    add(
        "medication_plan",
        "action_blocking_or_time_sensitive_residual_issue",
        r"阿司匹林|抗凝|抗血小板|降压|降糖|二甲双胍|胰岛素|停药|恢复|药",
        "药物停用/恢复/监测若不明确，常会阻断安全 episode closure。",
    )
    add(
        "red_flag_or_symptom_monitoring",
        "time_sensitive_residual_issue",
        r"黑便|便血|发热|腹痛|胸痛|晕厥|出血|血氧|呼吸困难|加重",
        "红旗症状或症状演化需要明确即时处理与升级条件。",
    )
    add(
        "patient_family_executability",
        "patient_capacity_dependent_residual_issue",
        r"不懂|不清楚|不知道|担心|害怕|不放心|不愿|不肯|怕花钱|家属",
        "患者/家属理解和执行能力会决定随访/等待计划是否真实可执行。",
    )
    return issues


def _samples_for_pattern(payload: Mapping[str, Any], pattern: str, *, last_n_turns: int = 30, limit: int = 4) -> list[Json]:
    turns_completed = int(payload.get("turns_completed") or 0)
    threshold = max(0, turns_completed - last_n_turns + 1)
    rg = re.compile(pattern, re.I)
    samples: list[Json] = []
    seen: set[tuple[Any, str]] = set()
    for evidence in collect_text_evidence(payload):
        if evidence.turn is not None and evidence.turn < threshold:
            continue
        if rg.search(evidence.text):
            key = (evidence.turn, _short(evidence.text, 80))
            if key in seen:
                continue
            seen.add(key)
            samples.append(evidence.to_dict())
            if len(samples) >= limit:
                break
    return samples



def _extract_turn_numbers_from_text(text: str) -> set[int]:
    turns: set[int] = set()
    value = str(text or "")
    for start, end in re.findall(r"(?:turn|第)\s*(\d{1,4})\s*(?:-|–|—|到|至)\s*(\d{1,4})\s*(?:轮)?", value, re.I):
        a, b = int(start), int(end)
        if 0 <= a <= b <= 5000 and b - a <= 50:
            turns.update(range(a, b + 1))
    for raw in re.findall(r"(?:turn|第)\s*(\d{1,4})\s*(?:轮)?", value, re.I):
        n = int(raw)
        if 0 <= n <= 5000:
            turns.add(n)
    return turns


def critical_safety_turns(payload: Mapping[str, Any]) -> list[int]:
    """Extract explicit severe/critical safety-failure turns without future leakage.

    Do not extract every ``turn`` reference from evaluator summaries.  A strong
    open/progressing fragment may mention many historical turns without any
    terminal doctor-failure implication.  Phase ``failure_escalation`` should be
    driven by critical failures, severe/critical structured safety events, or a
    closure kind that explicitly says critical safety.
    """

    turns: set[int] = set()
    evaluation = _as_dict(payload.get("evaluation"))
    critical_failures = evaluation.get("critical_failures")
    if isinstance(critical_failures, list):
        for item in critical_failures:
            turns.update(_extract_turn_numbers_from_text(json.dumps(item, ensure_ascii=False)))
    observed = evaluation.get("observed_doctor_failures_or_risks")
    if isinstance(observed, list):
        for item in observed:
            row = _as_dict(item)
            severity = str(row.get("severity") or "").lower()
            text = json.dumps(item, ensure_ascii=False)
            if severity in {"severe", "critical"} or re.search(r"critical safety|triage_failure|unsafe_fragment", text, re.I):
                turns.update(_extract_turn_numbers_from_text(text))
    closure = _as_dict(payload.get("closure"))
    if "critical_safety" in str(closure.get("closure_kind") or "").lower():
        turns.update(_extract_turn_numbers_from_text(json.dumps(closure, ensure_ascii=False)))
    trajectory = _as_dict(payload.get("trajectory"))
    for event in _as_list(trajectory.get("events")):
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("event_type") or "")
        text = _event_text(event)
        metadata = _as_dict(event.get("metadata"))
        content = _as_dict(event.get("content"))
        severity = str(content.get("severity") or metadata.get("severity") or "").lower()
        critical_list = metadata.get("critical_safety_events")
        severe_structured_marker = bool(
            re.search(r"unsafe_doctor|triage_failure|doctor_failure", event_type, re.I)
            or (re.search(r"critical_safety|safety_event", event_type, re.I) and severity in {"severe", "critical"})
            or isinstance(critical_list, list)
            and any(str(_as_dict(item).get("severity") or "").lower() in {"severe", "critical"} for item in critical_list)
        )
        if severe_structured_marker:
            turn = _turn_of(event)
            if turn is not None:
                turns.add(turn)
            turns.update(_extract_turn_numbers_from_text(text))
    turns_completed = int(payload.get("turns_completed") or max(turns, default=0) or 0)
    return sorted(t for t in turns if 0 <= t <= max(turns_completed, t))


def critical_safety_evidence(payload: Mapping[str, Any], *, limit: int = 6) -> list[Json]:
    evidence: list[Json] = []
    evaluation = _as_dict(payload.get("evaluation"))
    for key in ("critical_failures", "observed_doctor_failures_or_risks"):
        value = evaluation.get(key)
        if not isinstance(value, list):
            continue
        for item in value:
            text = json.dumps(item, ensure_ascii=False)
            if re.search(r"critical|unsafe|严重|错误|延误|遗漏|triage_failure|三类药|三药", text, re.I):
                evidence.append(
                    {
                        "turn": sorted(_extract_turn_numbers_from_text(text)) or None,
                        "speaker": "FragmentTrajectoryEvaluator",
                        "event_type": key,
                        "visibility": "evaluator_visible",
                        "source": "evaluation",
                        "text": _short(text, 420),
                    }
                )
                if len(evidence) >= limit:
                    return evidence
    closure = _as_dict(payload.get("closure"))
    for text in closure.get("evidence") or []:
        if isinstance(text, str) and re.search(r"critical|严重|错误|延误|三类药|三药", text, re.I):
            evidence.append(
                {
                    "turn": sorted(_extract_turn_numbers_from_text(text)) or None,
                    "speaker": "ClosureJudge",
                    "event_type": "closure_evidence",
                    "visibility": "evaluator_visible",
                    "source": "closure",
                    "text": _short(text, 420),
                }
            )
            if len(evidence) >= limit:
                return evidence
    return evidence

def _has_critical_safety_event(payload: Mapping[str, Any]) -> bool:
    """Return whether the trajectory contains an explicit doctor-safety failure marker.

    Do not treat ordinary clinical risk/red-flag language as doctor failure.
    Emergency cases naturally contain words such as 风险/危险/急诊; those are
    evidence of acuity, not necessarily an unsafe doctor action.  G1 only marks
    critical safety when the runtime/evaluator used explicit critical/unsafe
    fields or a non-empty critical_safety_events ledger.
    """

    if critical_safety_turns(payload):
        return True
    closure = _as_dict(payload.get("closure"))
    if "critical_safety" in str(closure.get("closure_kind") or "").lower():
        return True
    evaluation = _as_dict(payload.get("evaluation"))
    if re.search(r"unsafe|critical", str(evaluation.get("overall") or ""), re.I):
        return True
    critical_failures = evaluation.get("critical_failures")
    if isinstance(critical_failures, list) and critical_failures:
        return True
    trajectory = _as_dict(payload.get("trajectory"))
    for event in _as_list(trajectory.get("events")):
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("event_type") or "")
        if re.search(r"critical_safety|doctor_failure|unsafe_doctor", event_type, re.I):
            return True
        metadata = _as_dict(event.get("metadata"))
        cse = metadata.get("critical_safety_events") or event.get("critical_safety_events")
        if isinstance(cse, list) and len(cse) > 0:
            return True
    return False


def blocker_attribution(payload: Mapping[str, Any], signals: dict[int, TurnSignals], timeline: list[Json]) -> list[Json]:
    latest = _latest_text(payload, 30)
    turns_completed = int(payload.get("turns_completed") or 0)
    blockers: list[Json] = []

    def add(blocker: str, attribution: str, severity: str, evidence_pattern: str, why: str, action: str) -> None:
        evidence = _samples_for_pattern(payload, evidence_pattern, last_n_turns=30, limit=4)
        if not evidence and not re.search(evidence_pattern, latest, re.I):
            return
        blockers.append(
            {
                "blocker": blocker,
                "attribution": attribution,
                "severity": severity,
                "why_it_blocks_or_delays_closure": why,
                "suggested_governance_action": action,
                "evidence": evidence,
            }
        )

    add(
        "medication_plan_unclear",
        "mixed_or_doctor_caused",
        "high",
        r"阿司匹林|抗凝|抗血小板|降压|降糖|二甲双胍|胰岛素|停药|恢复|药",
        "后期仍存在药物停用/恢复/核对语义；若没有清晰 med-by-med 计划，通常不能 safe close。",
        "residual_risk_governance_required; do_not_safe_close_until_action_blocker_resolved_or_failure_terminal",
    )
    add(
        "pending_result_without_safe_tracking_or_high_impact_result_pending",
        "case_intrinsic_or_world_caused",
        "medium",
        r"病理|结果|报告|化验|复查|未出|没出来|等待|培养|影像",
        "后期仍有结果/报告/病理语义；需判断是否有责任主体、时间窗、异常处理和等待安全性。",
        "residual_risk_governance_required",
    )
    add(
        "patient_family_misunderstanding_or_executability_gap",
        "patient_behavior_or_communication_mixed",
        "medium",
        r"不懂|不清楚|不知道|担心|害怕|不放心|不愿|不肯|怕花钱|家属",
        "患者/家属仍有理解、焦虑或执行障碍，可能使随访计划不可执行。",
        "actor_realism_and_executability_check_required",
    )
    add(
        "red_flag_or_symptom_thread_unresolved",
        "mixed",
        "high",
        r"黑便|便血|发热|腹痛|胸痛|晕厥|出血|血氧|呼吸困难|加重",
        "后期仍出现红旗症状/重大症状语义；需确认是复述、已处理风险，还是新/未解决风险。",
        "world_coherence_and_residual_risk_review_required",
    )
    if _has_critical_safety_event(payload):
        blockers.append(
            {
                "blocker": "doctor_performance_failure_or_critical_safety_event",
                "attribution": "doctor_caused_or_mixed",
                "severity": "critical",
                "why_it_blocks_or_delays_closure": "trajectory/closure/evaluation contains critical or unsafe signals; safe closure should not be granted without failure/external-takeover classification.",
                "suggested_governance_action": "consider_failure_escalation_or_failure_terminal_outcome; do_not_convert_to_safe_closure",
                "evidence": critical_safety_evidence(payload, limit=6),
            }
        )
    # Tempo blocker: many late turns with major/new-signal class.
    late_threshold = max(0, turns_completed - 29)
    late_major_turns = [turn for turn, sig in signals.items() if turn >= late_threshold and sig.score("symptom_or_major_signal") > 0]
    if turns_completed >= 30 and len(set(late_major_turns)) >= 10:
        blockers.append(
            {
                "blocker": "world_tempo_or_unresolved_mainline_too_active_late",
                "attribution": "world_caused_or_legitimate_unresolved_disease",
                "severity": "medium",
                "why_it_blocks_or_delays_closure": "last 30 turns still contain frequent symptom/major-signal text. Keyword evidence must be manually separated into true new events vs repeated summaries.",
                "suggested_governance_action": "run_deep_tempo_review; if true_new_events_are_frequent_add_phase_aware_tempo_governor",
                "evidence": [signals[t].text_samples[0].to_dict() for t in sorted(set(late_major_turns)) if signals[t].text_samples][:4],
            }
        )
    if not blockers and str(_as_dict(payload.get("closure")).get("status") or "") == "open":
        blockers.append(
            {
                "blocker": "open_without_strong_deterministic_blocker",
                "attribution": "closure_policy_issue_or_hidden_semantic_blocker",
                "severity": "medium",
                "why_it_blocks_or_delays_closure": "heuristic audit did not find strong blockers but closure remains open; requires semantic closure judge review.",
                "suggested_governance_action": "manual_deep_audit; consider_closure_criteria_too_strict_if_semantic_review_confirms",
                "evidence": [],
            }
        )
    return sorted(blockers, key=lambda row: -BLOCKER_SEVERITY_ORDER.get(str(row.get("severity")), 0))


def closure_runway(payload: Mapping[str, Any], signals: dict[int, TurnSignals], blockers: list[Json], window: int = 12) -> Json:
    turns_completed = int(payload.get("turns_completed") or 0)
    start = max(0, turns_completed - window + 1)
    rows = [signals.get(turn, TurnSignals(turn=turn)) for turn in range(start, turns_completed + 1)]
    major_turns = [sig.turn for sig in rows if sig.score("symptom_or_major_signal") > 0]
    responsibility_turns = [sig.turn for sig in rows if sig.score("followup_or_responsibility") > 0]
    high_blockers = [b for b in blockers if BLOCKER_SEVERITY_ORDER.get(str(b.get("severity")), 0) >= 3]
    critical_blockers = [b for b in blockers if str(b.get("severity")) == "critical"]
    detected = bool(rows) and len(set(major_turns)) <= max(1, window // 4) and len(set(responsibility_turns)) >= max(1, window // 4) and not high_blockers
    return {
        "window_start_turn": start,
        "window_end_turn": turns_completed,
        "detected": detected,
        "major_signal_turns": sorted(set(major_turns)),
        "responsibility_signal_turns": sorted(set(responsibility_turns)),
        "high_or_critical_blockers": [b.get("blocker") for b in high_blockers + critical_blockers],
        "interpretation": (
            "possible natural closure runway" if detected else "not a clean closure runway under heuristic audit; review blockers and late major-signal density"
        ),
    }


def doctor_hostage_signals(payload: Mapping[str, Any], signals: dict[int, TurnSignals], blockers: list[Json]) -> Json:
    trajectory = _as_dict(payload.get("trajectory"))
    transcript = _as_list(trajectory.get("transcript"))
    doctor_texts = [str(row.get("text") or "") for row in transcript if isinstance(row, dict) and "doctor" in str(row.get("speaker") or row.get("speaker_category") or "").lower()]
    patient_texts = [str(row.get("text") or "") for row in transcript if isinstance(row, dict) and "doctor" not in str(row.get("speaker") or row.get("speaker_category") or "").lower()]
    generic_advice_count = sum(1 for text in doctor_texts if re.search(r"建议.*医院|建议.*门诊|咨询.*医生|尽快.*就医|需要.*检查", text))
    doctor_turns = len(doctor_texts)
    unsafe = any(str(b.get("severity")) == "critical" for b in blockers)
    late_no_progress = False
    turns_completed = int(payload.get("turns_completed") or 0)
    if turns_completed >= 60:
        last_30 = [signals.get(turn, TurnSignals(turn=turn)) for turn in range(turns_completed - 29, turns_completed + 1)]
        # Many uncertainty/friction turns with little responsibility signal may indicate looping.
        uncertainty = sum(1 for sig in last_30 if sig.score("patient_uncertainty", "ordinary_friction") > 0)
        responsibility = sum(1 for sig in last_30 if sig.score("followup_or_responsibility") > 0)
        late_no_progress = uncertainty >= 15 and responsibility < 8
    suspected = unsafe or (doctor_turns >= 20 and generic_advice_count / max(1, doctor_turns) >= 0.45) or late_no_progress
    return {
        "suspected": suspected,
        "doctor_turn_count": doctor_turns,
        "patient_or_family_turn_count": len(patient_texts),
        "generic_escalation_or_information_advice_count": generic_advice_count,
        "generic_advice_ratio": round(generic_advice_count / max(1, doctor_turns), 3),
        "late_no_progress_pattern": late_no_progress,
        "critical_safety_or_failure_blocker_present": unsafe,
        "interpretation": (
            "consider external takeover / failure terminal pathway if semantic review confirms repeated low-agency or unsafe behavior"
            if suspected
            else "no strong deterministic doctor-hostage signal; still requires semantic review for long open runs"
        ),
    }


def open_at_max_turns_attribution(payload: Mapping[str, Any], blockers: list[Json], runway: Mapping[str, Any], hostage: Mapping[str, Any]) -> Json:
    closure = _as_dict(payload.get("closure"))
    quality = _as_dict(payload.get("quality_report"))
    flags = quality.get("flags") if isinstance(quality.get("flags"), list) else []
    if closure.get("status") != "open" and "open_at_max_turns" not in flags:
        return {"status": "not_open_at_max", "primary_attribution": "not_applicable", "supporting_reasons": []}
    critical = [b for b in blockers if str(b.get("severity")) == "critical"]
    high = [b for b in blockers if str(b.get("severity")) == "high"]
    tempo = [b for b in blockers if b.get("blocker") == "world_tempo_or_unresolved_mainline_too_active_late"]
    reasons: list[str] = []
    if critical:
        primary = "doctor_failure_or_critical_safety_event"
        reasons.append("critical safety/failure blocker present")
    elif high:
        primary = "high_risk_residual_or_action_blocker"
        reasons.extend(str(b.get("blocker")) for b in high[:3])
    elif tempo:
        primary = "world_tempo_overactive_or_legitimate_unresolved_mainline"
        reasons.append("late major-signal density remains high")
    elif runway.get("detected"):
        primary = "closure_criteria_too_strict_or_semantic_residual_review_needed"
        reasons.append("closure runway appears possible but closure remains open")
    elif hostage.get("suspected"):
        primary = "doctor_hostage_loop_or_failure_escalation_needed"
        reasons.append("doctor-hostage monitor suspected loop/failure")
    else:
        primary = "mixed_or_insufficient_evidence"
        reasons.append("heuristic audit cannot confidently attribute open state")
    return {"status": "open_at_max_or_open_fragment", "primary_attribution": primary, "supporting_reasons": reasons}


def audit_episode_governance(payload: Mapping[str, Any], *, name: str = "trajectory") -> Json:
    signals = turn_signals(payload)
    timeline = phase_timeline(payload, signals)
    windows = summarize_phase_windows(timeline, window_size=30)
    residual = residual_issues(payload, signals)
    blockers = blocker_attribution(payload, signals, timeline)
    runway = closure_runway(payload, signals, blockers)
    hostage = doctor_hostage_signals(payload, signals, blockers)
    attribution = open_at_max_turns_attribution(payload, blockers, runway, hostage)
    turns_completed = int(payload.get("turns_completed") or 0)
    trajectory = _as_dict(payload.get("trajectory"))
    quality = _as_dict(payload.get("quality_report"))
    try:
        from careloop.runtime_lite.quality import analyze_lite_trajectory

        quality = analyze_lite_trajectory(dict(payload)).to_dict()
    except Exception:
        quality = dict(quality)
    event_classes = event_class_counts(signals)
    return {
        "schema_version": "careloop.episode_governance_audit.v1",
        "name": name,
        "scope": "G1 offline/shadow audit; does not alter runtime, closure, scoring, prompts, or world generation",
        "summary": {
            "case_id": payload.get("case_id"),
            "run_id": payload.get("run_id"),
            "turns_completed": turns_completed,
            "closure_status": _as_dict(payload.get("closure")).get("status"),
            "closure_kind": _as_dict(payload.get("closure")).get("closure_kind"),
            "quality_status": quality.get("status"),
            "quality_flags": quality.get("flags"),
            "event_count": len(_as_list(trajectory.get("events"))),
            "transcript_count": len(_as_list(trajectory.get("transcript"))),
            "llm_call_count": len(_as_list(trajectory.get("llm_calls"))),
        },
        "phase_windows": windows,
        "phase_timeline_tail": timeline[-40:],
        "event_class_summary": event_classes,
        "residual_issues": residual,
        "open_blockers": blockers,
        "closure_runway": runway,
        "doctor_hostage_signals": hostage,
        "open_at_max_turns_attribution": attribution,
        "next_recommended_actions": recommended_actions(attribution, blockers, runway, hostage),
    }


def recommended_actions(attribution: Mapping[str, Any], blockers: list[Json], runway: Mapping[str, Any], hostage: Mapping[str, Any]) -> list[str]:
    actions: list[str] = []
    primary = str(attribution.get("primary_attribution") or "")
    if "doctor_failure" in primary or hostage.get("suspected"):
        actions.append("G5: add doctor-hostage/failure-terminal shadow review before relaxing safe closure")
    if any("medication" in str(b.get("blocker")) or "pending_result" in str(b.get("blocker")) for b in blockers):
        actions.append("G2: run Residual Risk Governance on medication and pending-result blockers")
    if "world_tempo" in primary or any(b.get("blocker") == "world_tempo_or_unresolved_mainline_too_active_late" for b in blockers):
        actions.append("G4: run phase-aware Tempo Governor shadow to separate true new events from repeated summaries")
    if runway.get("detected"):
        actions.append("G6: review closure judge criteria if semantic audit confirms acceptable residual risk")
    actions.append("Manual L06 deep audit: inspect 1-30/31-60/61-90/91-120 windows before runtime prompt changes")
    # Preserve order while de-duplicating.
    seen: set[str] = set()
    unique: list[str] = []
    for action in actions:
        if action not in seen:
            seen.add(action)
            unique.append(action)
    return unique


def render_markdown(audit: Mapping[str, Any]) -> str:
    summary = _as_dict(audit.get("summary"))
    lines: list[str] = [
        f"# Episode Governance Audit: {audit.get('name')}",
        "",
        "> G1 offline/shadow audit. 本报告不改变 runtime、closure、scoring、prompt 或世界生成，仅为后续施工提供证据。",
        "",
        "## Summary",
        "",
        f"- case_id: `{summary.get('case_id')}`",
        f"- run_id: `{summary.get('run_id')}`",
        f"- turns_completed: **{summary.get('turns_completed')}**",
        f"- closure: `{summary.get('closure_status')}` / `{summary.get('closure_kind')}`",
        f"- quality: `{summary.get('quality_status')}` flags={summary.get('quality_flags')}",
        f"- events/transcript/llm_calls: {summary.get('event_count')}/{summary.get('transcript_count')}/{summary.get('llm_call_count')}",
        "",
        "## 30-turn Phase Windows",
        "",
        "| turns | dominant phase | interpretation | top classes |",
        "|---|---|---|---|",
    ]
    for row in _as_list(audit.get("phase_windows")):
        classes = ", ".join(f"{k}:{v}" for k, v in _as_list(row.get("class_counts"))[:6])
        lines.append(
            f"| {row.get('start_turn')}-{row.get('end_turn')} | `{row.get('dominant_phase')}` | {row.get('interpretation')} | {classes} |"
        )
    lines += ["", "## Open Blockers", ""]
    blockers = _as_list(audit.get("open_blockers"))
    if not blockers:
        lines.append("No deterministic blockers found by G1 heuristics.")
    for blocker in blockers:
        lines += [
            f"### {blocker.get('blocker')} ({blocker.get('severity')}, {blocker.get('attribution')})",
            "",
            f"- why: {blocker.get('why_it_blocks_or_delays_closure')}",
            f"- suggested action: `{blocker.get('suggested_governance_action')}`",
            "",
        ]
        for ev in _as_list(blocker.get("evidence"))[:3]:
            lines.append(f"  - turn {ev.get('turn')} {ev.get('speaker')}/{ev.get('event_type')}: {ev.get('text')}")
        lines.append("")
    lines += ["## Residual Issues", ""]
    for issue in _as_list(audit.get("residual_issues")):
        lines += [f"- **{issue.get('issue_type')}** `{issue.get('risk_class')}`: {issue.get('why_it_matters')}"]
    lines += ["", "## Closure Runway", "", "```json", json.dumps(audit.get("closure_runway"), ensure_ascii=False, indent=2), "```", ""]
    lines += ["## Doctor-hostage Signals", "", "```json", json.dumps(audit.get("doctor_hostage_signals"), ensure_ascii=False, indent=2), "```", ""]
    lines += ["## Open-at-max Attribution", "", "```json", json.dumps(audit.get("open_at_max_turns_attribution"), ensure_ascii=False, indent=2), "```", ""]
    lines += ["## Next Recommended Actions", ""]
    for action in _as_list(audit.get("next_recommended_actions")):
        lines.append(f"- {action}")
    lines.append("")
    return "\n".join(lines)


__all__ = [
    "audit_episode_governance",
    "collect_text_evidence",
    "event_class_counts",
    "load_trajectory_payload",
    "render_markdown",
    "turn_signals",
]
