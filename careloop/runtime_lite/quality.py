from __future__ import annotations

"""Lightweight trajectory quality checks for runtime_lite.

This module does not score the Doctor.  It flags simulator/runtime symptoms
that make a trajectory suspect before a human or LLM evaluator interprets the
medical performance.
"""

from dataclasses import asdict, dataclass, field
from collections import Counter
import json
import re
from typing import Any


@dataclass
class LiteTrajectoryQualityReport:
    case_id: str
    run_id: str
    status: str
    flags: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def analyze_lite_trajectory(payload: dict[str, Any]) -> LiteTrajectoryQualityReport:
    trajectory = payload.get("trajectory") or {}
    events = [event for event in trajectory.get("events") or [] if isinstance(event, dict)]
    transcript = [item for item in trajectory.get("transcript") or [] if isinstance(item, dict)]
    closure_status = (payload.get("closure") or {}).get("status")
    turns_completed = int(payload.get("turns_completed") or 0)
    max_turns = int((payload.get("metadata") or {}).get("max_turns") or turns_completed or 0)

    metrics = {
        "turns_completed": turns_completed,
        "max_turns": max_turns,
        "event_count": len(events),
        "transcript_count": len(transcript),
        "llm_call_count": len(trajectory.get("llm_calls") or []),
        "workspace_result_count": _count_events(events, "doctor_workspace_result"),
        "care_system_receipt_count": _count_events(events, "doctor_care_system_receipt"),
        "care_system_state_update_count": _count_events(events, "care_system_state_update"),
        "workspace_state_update_count": _count_events(events, "workspace_state_update"),
        "doctor_system_notification_count": _count_events(events, "doctor_system_notification"),
        "living_state_update_count": _count_events(events, "living_state_update"),
        "time_advance_count": _count_events(events, "time_advance"),
        "total_elapsed_minutes": _total_elapsed_minutes(events),
        "last_sim_time": _last_sim_time(events),
        "world_event_resolution_count": _count_events(events, "world_event_resolution"),
        "mainline_balance_judgement_count": _count_events(events, "mainline_balance_judgement"),
        "anti_retcon_guard_count": _count_events(events, "anti_retcon_world_state_guard"),
        "clinical_contingency_sampling_count": _count_events(events, "clinical_contingency_sampling"),
        "diagnostic_service_simulation_count": _count_events(events, "diagnostic_service_simulation"),
        "actor_realism_judgement_count": _count_events(events, "actor_realism_judgement"),
        "stability_horizon_plan_count": _count_events(events, "stability_horizon_plan"),
        "stability_horizon_verification_count": _count_events(events, "stability_horizon_verification"),
        "stability_horizon_closure_adjustment_count": _count_events(events, "stability_horizon_closure_adjustment"),
        "director_cut_request_count": _count_events(events, "director_cut_request"),
        "director_cut_approved_count": _count_events(events, "director_cut_approved"),
        "director_cut_rejected_count": _count_events(events, "director_cut_rejected"),
        "committed_world_event_count": _committed_world_event_count(events),
        "non_occurred_event_candidate_count": _non_occurred_event_count(events),
        "closure_status": closure_status,
        "evaluation_overall": (payload.get("evaluation") or {}).get("overall"),
        "unique_patient_or_family_texts": len(set(_actor_texts(transcript))),
        "patient_or_family_message_count": len(_actor_texts(transcript)),
        "actor_identity_missing_count": _actor_identity_missing_count(transcript),
    }
    care_loop = _care_loop_indicators(events, transcript)
    metrics["care_loop"] = care_loop
    metrics["care_loop_phase_count"] = care_loop["phase_count"]
    metrics["care_loop_shape"] = care_loop["shape"]
    longitudinal_care_process = _longitudinal_care_process_indicators(transcript, care_loop)
    metrics["longitudinal_care_process"] = longitudinal_care_process
    metrics["care_process_step_count"] = longitudinal_care_process["step_count"]
    clinical_core_loop = _clinical_core_loop_indicators(events, transcript, care_loop, longitudinal_care_process)
    metrics["clinical_core_loop"] = clinical_core_loop
    metrics["clinical_core_loop_status"] = clinical_core_loop["status"]
    metrics["clinical_core_stage_count"] = clinical_core_loop["stage_count"]
    real_world_friction = _real_world_friction_indicators(transcript, events)
    metrics["real_world_friction"] = real_world_friction
    metrics["real_world_friction_signal_count"] = real_world_friction["actor_friction_signal_count"]
    metrics["doctor_adaptation_after_friction_count"] = real_world_friction["doctor_adaptation_after_friction_count"]
    actor_cooperation_realism = _actor_cooperation_realism_indicators(transcript)
    metrics["actor_cooperation_realism"] = actor_cooperation_realism
    metrics["actor_overstructured_message_count"] = actor_cooperation_realism["overstructured_message_count"]
    metrics["actor_high_cooperation_risk_status"] = actor_cooperation_realism["status"]
    clinical_memory_integrity = _clinical_memory_integrity_indicators(events)
    metrics["clinical_memory_integrity"] = clinical_memory_integrity
    trajectory_progression = _trajectory_progression_indicators(
        events=events,
        transcript=transcript,
        turns_completed=turns_completed,
        max_turns=max_turns,
        closure_status=str(closure_status or ""),
        care_loop=care_loop,
    )
    metrics["trajectory_progression"] = trajectory_progression
    metrics["trajectory_progression_status"] = trajectory_progression["status"]
    metrics["material_progress_signal_count"] = trajectory_progression["material_progress_signal_count"]
    metrics["material_progress_turn_count"] = trajectory_progression["material_progress_turn_count"]
    external_progression_dependency = _external_progression_dependency_indicators(
        care_loop=care_loop,
        longitudinal_care_process=longitudinal_care_process,
        events=events,
        closure_status=str(closure_status or ""),
        turns_completed=turns_completed,
    )
    metrics["external_progression_dependency"] = external_progression_dependency
    metrics["external_progression_dependency_status"] = external_progression_dependency["status"]
    flags: list[str] = []
    notes: list[str] = []

    runtime_error = payload.get("runtime_error") if isinstance(payload.get("runtime_error"), dict) else None
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    interrupted_by_user = bool((metadata or {}).get("interrupted_by_user")) or (runtime_error or {}).get("type") == "KeyboardInterrupt"
    if runtime_error and interrupted_by_user:
        flags.append("user_interrupted_fragment")
        notes.append(
            "该 trajectory 是用户/操作者主动截断的非终局片段；应使用 fragment evaluator 评价片段中已经发生的诊疗能力，不应等同于运行时失败。"
        )
    elif runtime_error:
        flags.append("runtime_error")
        notes.append("运行时或基础设施错误导致该 trajectory 不完整，不能作为正常模拟轨迹评价。")

    actor_texts = _actor_texts(transcript)
    doctor_texts = _speaker_texts(transcript, {"doctor"})
    doctor_blank_message_count = sum(1 for text in doctor_texts if not str(text or "").strip())
    evaluation_payload = payload.get("evaluation") or {}
    evaluation_infrastructure = evaluation_payload.get("infrastructure_integrity") if isinstance(evaluation_payload, dict) else {}
    llm_calls = [item for item in trajectory.get("llm_calls") or [] if isinstance(item, dict)]
    llm_empty_output_rejected_count = sum(
        1
        for item in llm_calls
        if isinstance(item.get("metadata"), dict) and item["metadata"].get("runtime_empty_output_rejected")
    )
    metrics.update(
        {
            "doctor_message_count": len(doctor_texts),
            "doctor_blank_message_count": doctor_blank_message_count,
            "doctor_non_response_event_count": _count_events(events, "doctor_non_response"),
            "llm_empty_output_rejected_count": llm_empty_output_rejected_count,
            "evaluation_infrastructure_status": (evaluation_infrastructure or {}).get("status") if isinstance(evaluation_infrastructure, dict) else "",
        }
    )
    if turns_completed > 0 and not actor_texts:
        flags.append("no_patient_or_family_messages")
    if metrics["doctor_non_response_event_count"] or doctor_blank_message_count:
        flags.append("doctor_non_response_present")
        notes.append(
            "存在医生面向患者/家属的空白回复；这应作为被测医生非回复或 API 空内容污染证据，不能被 CareLoop 世界推进自动抵消。"
        )
    if evaluation_payload.get("overall") == "evaluation_failed" or metrics.get("evaluation_infrastructure_status") == "evaluation_failed":
        flags.append("final_evaluation_failed")
        notes.append("最终 trajectory evaluator 未产出有效结构化评价；本次运行不能作为完整评分结果，只能作为轨迹模拟证据。")
    if metrics["actor_identity_missing_count"]:
        flags.append("missing_actor_identity_in_patient_or_family_messages")
        notes.append("患者/家属消息缺少清晰 speaker_display 或 speaker_category；多人交互可读性需要复核。")
    if len(actor_texts) >= 2 and len(set(actor_texts)) <= max(1, len(actor_texts) // 2):
        flags.append("possible_actor_stasis_repeated_text")
        notes.append("患者/家属回复存在明显重复；真实 LLM 跑测时需确认是否卡住。")
    if len(doctor_texts) >= 2 and len(set(doctor_texts)) <= max(1, len(doctor_texts) // 2):
        flags.append("possible_doctor_stasis_repeated_text")

    if actor_cooperation_realism["status"] == "high_risk":
        flags.append("actor_overcooperation_high_risk")
        notes.append(
            "患者/家属消息呈现过度结构化或过高执行力；这可能是模拟端真实性问题，不应直接当成被测医生能力证据。"
        )
    elif actor_cooperation_realism["status"] == "review":
        flags.append("actor_overcooperation_review")

    if _workspace_result_leaked_to_actor(transcript):
        flags.append("workspace_result_leaked_into_patient_or_family_chat")
    if _internal_terms_leaked_to_actor(transcript):
        flags.append("internal_runtime_terms_leaked_to_actor")
    if _internal_terms_leaked_to_doctor_visible_events(events):
        flags.append("internal_runtime_terms_leaked_to_doctor_visible_content")
        notes.append("医生/患者可见事件内容包含 runtime 内部调度痕迹；需检查可见性边界。")
    if clinical_memory_integrity.get("missing_pending_receipt_count"):
        flags.append("clinical_memory_missing_pending_responsibilities")
        notes.append("临床记忆压缩审计显示仍有 pending 医生侧责任未被保留；需复核 ClinicalMemorySteward 或 runtime carry-forward。")
    if clinical_memory_integrity.get("missing_non_compressible_kernel_count"):
        flags.append("clinical_memory_missing_non_compressible_kernel")
        notes.append("临床记忆压缩审计显示不可压缩临床核心仍有缺失；需复核 ClinicalMemorySteward 或 deterministic carry-forward。")
    if clinical_memory_integrity.get("latest_status") == "review" and "clinical_memory_missing_pending_responsibilities" not in flags:
        flags.append("clinical_memory_integrity_review")

    if turns_completed >= 2 and metrics["committed_world_event_count"] == 0:
        flags.append("weak_world_progression_no_committed_events")
        notes.append("多轮后没有 committed world event；可能是真实暂缓，也可能是导演推进不足。")
    if _missing_time_advance_for_completed_turns(events, turns_completed, payload):
        flags.append("missing_time_advance_event")
    if _all_time_advances_zero(events) and turns_completed >= 2:
        flags.append("time_not_advancing")

    if trajectory_progression["status"] == "possible_stasis":
        flags.append("possible_trajectory_stasis")
        notes.append(
            "Trajectory progression audit suggests the run may be materially stuck rather than merely unfinished; inspect transcript and WorldDirector→ActorSituation→actor handoff."
        )

    if clinical_core_loop["status"] in {"handoff_or_advice_only", "minimal_or_not_yet_tested"} and closure_status in {"closed", "soft_closed"}:
        flags.append("clinical_core_loop_under_evidenced_at_closure")
        notes.append(
            "轨迹被判为闭环或阶段性闭环，但诊断—检查—结果—治疗—反应追踪等临床核心闭环证据不足；需避免把 handoff 或计划误判为完整临床能力。"
        )
    elif clinical_core_loop["status"] in {"handoff_or_advice_only", "minimal_or_not_yet_tested"} and turns_completed >= 3:
        flags.append("clinical_core_loop_under_evidenced_review")

    if external_progression_dependency["status"] == "high_risk":
        flags.append("external_or_world_progression_dominant_high_risk")
        notes.append(
            "轨迹推进或闭环可能主要由外部医疗系统、CareLoop 世界推进或阶段性 handoff 促成；需区分患者安全状态和 AI 医生真实临床贡献。"
        )
    elif external_progression_dependency["status"] == "review":
        flags.append("external_or_world_progression_dominant_review")

    if closure_status in {"closed", "soft_closed"} and turns_completed <= 1:
        flags.append("possible_premature_closure")
    if closure_status == "closed" and metrics["committed_world_event_count"] == 0:
        flags.append("closed_without_committed_world_progression")
    if closure_status == "open" and max_turns and turns_completed >= max_turns:
        flags.append("open_at_max_turns")
        if trajectory_progression.get("open_at_max_but_progressing"):
            notes.append(
                "Trajectory is open at max_turns but still shows material progression signals; treat as unfinished care, not automatic framework stasis."
            )
    if closure_status in {"closed", "soft_closed"} and care_loop["shape"] in {
        "conversation_only",
        "doctor_plan_or_receipt_only",
    }:
        notes.append(
            "闭环证据主要来自对话或未执行的医生侧 receipt；如非低风险自我管理 case，需重点复核是否只是表层闭环。"
        )

    status = _status_from_flags(flags)
    return LiteTrajectoryQualityReport(
        case_id=str(payload.get("case_id") or ""),
        run_id=str(payload.get("run_id") or ""),
        status=status,
        flags=flags,
        metrics=metrics,
        notes=notes,
    )


def quality_report_markdown(report: LiteTrajectoryQualityReport) -> str:
    lines = [
        "## Runtime quality report",
        "",
        f"- status: {report.status}",
        f"- flags: {', '.join(report.flags) if report.flags else 'none'}",
        "",
        "### Metrics",
        "",
    ]
    for key, value in report.metrics.items():
        lines.append(f"- {key}: {value}")
    if report.notes:
        lines.extend(["", "### Notes", ""])
        for note in report.notes:
            lines.append(f"- {note}")
    return "\n".join(lines) + "\n"


def _count_events(events: list[dict[str, Any]], event_type: str) -> int:
    return sum(1 for event in events if event.get("event_type") == event_type)


def _total_elapsed_minutes(events: list[dict[str, Any]]) -> float:
    total = 0.0
    for event in events:
        if event.get("event_type") != "time_advance":
            continue
        content = event.get("content") if isinstance(event.get("content"), dict) else {}
        try:
            total += max(0.0, float(content.get("elapsed_minutes") or 0))
        except (TypeError, ValueError):
            continue
    return round(total, 3)


def _last_sim_time(events: list[dict[str, Any]]) -> str:
    for event in reversed(events):
        sim_time = str(event.get("sim_time") or "").strip()
        if sim_time:
            return sim_time
    return ""


def _missing_time_advance_for_completed_turns(events: list[dict[str, Any]], turns_completed: int, payload: dict[str, Any]) -> bool:
    """Return whether a clinically completed turn lacks a time advance event.

    Stage checkpoints can be written in the middle of the current turn, for
    example after the doctor reply or world_director but before timekeeper.  A
    naive ``time_advance_count < turns_completed`` check then reports a false
    positive.  We still flag any older completed turn without time_advance, and
    we also flag the current turn once it has reached actor reply, closure, or
    memory snapshot without time advancing.
    """

    if turns_completed <= 0:
        return False
    time_advance_turns = {
        int(event.get("turn") or 0)
        for event in events
        if event.get("event_type") == "time_advance"
    }
    missing = [turn for turn in range(1, turns_completed + 1) if turn not in time_advance_turns]
    if not missing:
        return False
    current_turn = turns_completed
    if any(turn < current_turn for turn in missing):
        return True
    # If this is a stage checkpoint before the actor-facing half of the turn,
    # the missing current-turn time advance is expected and should not taint the
    # trajectory quality report.
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    if metadata.get("checkpoint"):
        current_events = [event for event in events if int(event.get("turn") or 0) == current_turn]
        completed_markers = {
            "patient_message",
            "family_message",
            "closure_assessment",
            "clinical_memory_snapshot",
        }
        if not any(event.get("event_type") in completed_markers for event in current_events):
            return False
    return True


def _committed_world_event_count(events: list[dict[str, Any]]) -> int:
    total = 0
    for event in events:
        if event.get("event_type") != "world_event_resolution":
            continue
        content = event.get("content") if isinstance(event.get("content"), dict) else {}
        total += len(content.get("committed_world_events") or [])
    return total


def _non_occurred_event_count(events: list[dict[str, Any]]) -> int:
    total = 0
    for event in events:
        if event.get("event_type") != "world_event_resolution":
            continue
        content = event.get("content") if isinstance(event.get("content"), dict) else {}
        total += len(content.get("non_occurred_event_candidates") or [])
    return total


def _clinical_memory_integrity_indicators(events: list[dict[str, Any]]) -> dict[str, Any]:
    snapshots = [
        event.get("content")
        for event in events
        if event.get("event_type") == "clinical_memory_snapshot" and isinstance(event.get("content"), dict)
    ]
    audits = [
        snapshot.get("memory_integrity_audit")
        for snapshot in snapshots
        if isinstance(snapshot.get("memory_integrity_audit"), dict)
    ]
    latest = audits[-1] if audits else {}
    carried_ids: list[str] = []
    missing_ids: list[str] = []
    carried_kernel_keys: list[str] = []
    missing_kernel_keys: list[str] = []
    kernel_item_count = 0
    high_impact_signal_count = 0
    review_count = 0
    for audit in audits:
        carried_ids.extend(str(item) for item in audit.get("carried_forward_pending_receipt_ids") or [] if item)
        missing_ids.extend(str(item) for item in audit.get("missing_pending_receipt_ids_after_carry_forward") or [] if item)
        carried_kernel_keys.extend(str(item) for item in audit.get("carried_forward_non_compressible_kernel_keys") or [] if item)
        missing_kernel_keys.extend(str(item) for item in audit.get("missing_non_compressible_kernel_keys_after_carry_forward") or [] if item)
        kernel_item_count += int(audit.get("non_compressible_kernel_item_count") or 0)
        high_impact_signal_count += int(audit.get("high_impact_recent_signal_count") or 0)
        if audit.get("status") == "review":
            review_count += 1
    return {
        "snapshot_count": len(snapshots),
        "audit_count": len(audits),
        "latest_status": latest.get("status", ""),
        "review_audit_count": review_count,
        "carried_forward_pending_receipt_count": len(carried_ids),
        "carried_forward_pending_receipt_ids": list(dict.fromkeys(carried_ids))[-12:],
        "missing_pending_receipt_count": len(missing_ids),
        "missing_pending_receipt_ids": list(dict.fromkeys(missing_ids))[-12:],
        "non_compressible_kernel_item_count": kernel_item_count,
        "carried_forward_non_compressible_kernel_count": len(carried_kernel_keys),
        "carried_forward_non_compressible_kernel_keys": list(dict.fromkeys(carried_kernel_keys))[-12:],
        "missing_non_compressible_kernel_count": len(missing_kernel_keys),
        "missing_non_compressible_kernel_keys": list(dict.fromkeys(missing_kernel_keys))[-12:],
        "high_impact_recent_signal_count": high_impact_signal_count,
    }


def _trajectory_progression_indicators(
    *,
    events: list[dict[str, Any]],
    transcript: list[dict[str, Any]],
    turns_completed: int,
    max_turns: int,
    closure_status: str,
    care_loop: dict[str, Any],
) -> dict[str, Any]:
    """Describe whether the simulated world is materially moving.

    This is a post-hoc audit aid, not a runtime rule and not a Doctor score.
    CareLoop_FCCT-1 deliberately allows long unfinished trajectories, so this helper
    separates "still open because long-course care is unfolding" from "open
    because the simulator/actors appear to be going in circles".
    """

    material_by_turn: dict[int, set[str]] = {}
    signal_counts: Counter[str] = Counter()

    def add_signal(turn: int, label: str, count: int = 1) -> None:
        if count <= 0:
            return
        normalized_turn = max(0, int(turn or 0))
        signal_counts[label] += count
        if normalized_turn:
            material_by_turn.setdefault(normalized_turn, set()).add(label)

    for event in events:
        event_type = str(event.get("event_type") or "")
        turn = _event_turn(event)
        content = event.get("content") if isinstance(event.get("content"), dict) else {}
        if event_type == "time_advance":
            try:
                elapsed = float(content.get("elapsed_minutes") or 0)
            except (TypeError, ValueError):
                elapsed = 0.0
            if elapsed > 0:
                add_signal(turn, "positive_time_advance")
        elif event_type == "world_event_resolution":
            committed = content.get("committed_world_events") or []
            if isinstance(committed, list):
                add_signal(turn, "committed_world_event", len(committed))
        elif event_type == "living_state_update":
            add_signal(turn, "living_state_update")
        elif event_type == "care_system_state_update":
            updates = content.get("updates") or []
            add_signal(turn, "care_system_state_update", len(updates) if isinstance(updates, list) and updates else 1)
        elif event_type == "workspace_state_update":
            add_signal(turn, "workspace_state_update")
        elif event_type == "doctor_care_system_receipt":
            add_signal(turn, "doctor_operation_registered")
        elif event_type == "doctor_workspace_result":
            add_signal(turn, "workspace_result_returned")
        elif event_type == "doctor_system_notification":
            add_signal(turn, "doctor_visible_world_notification")

    actor_texts_with_turns = [
        (_event_turn(item), _normalize_text(str(item.get("text") or "")))
        for item in transcript
        if _is_patient_or_family_item(item) and _normalize_text(str(item.get("text") or ""))
    ]
    doctor_texts = _speaker_texts(transcript, {"doctor"})
    actor_texts = [text for _, text in actor_texts_with_turns]
    actor_unique_text_count = len(set(actor_texts))
    doctor_unique_text_count = len(set(doctor_texts))
    actor_unique_ratio = round(actor_unique_text_count / len(actor_texts), 3) if actor_texts else None
    doctor_unique_ratio = round(doctor_unique_text_count / len(doctor_texts), 3) if doctor_texts else None
    actor_repetition_suspected = bool(
        len(actor_texts) >= 2 and actor_unique_text_count <= max(1, len(actor_texts) // 2)
    )
    doctor_repetition_suspected = bool(
        len(doctor_texts) >= 2 and doctor_unique_text_count <= max(1, len(doctor_texts) // 2)
    )
    actor_novel_turns = sorted({turn for turn, text in actor_texts_with_turns if actor_texts.count(text) == 1})

    progress_turns = sorted(turn for turn in material_by_turn if turn > 0)
    bounded_progress_turns = [turn for turn in progress_turns if not turns_completed or turn <= turns_completed]
    material_signal_count = int(sum(signal_counts.values()))
    material_turn_count = len(bounded_progress_turns)
    all_turns = list(range(1, max(0, turns_completed) + 1))
    stagnant_turns = [turn for turn in all_turns if turn not in set(bounded_progress_turns)]
    care_loop_phase_count = int(care_loop.get("phase_count") or 0)
    care_loop_shape = str(care_loop.get("shape") or "")
    positive_time_advance_count = int(signal_counts.get("positive_time_advance") or 0)
    committed_world_event_count = int(signal_counts.get("committed_world_event") or 0)
    world_time_or_state_signal_count = sum(
        int(signal_counts.get(label) or 0)
        for label in [
            "positive_time_advance",
            "committed_world_event",
            "living_state_update",
            "care_system_state_update",
            "workspace_state_update",
            "doctor_visible_world_notification",
        ]
    )
    substantive_progress_signal_count = sum(
        int(signal_counts.get(label) or 0)
        for label in [
            "committed_world_event",
            "living_state_update",
            "care_system_state_update",
            "workspace_state_update",
            "doctor_visible_world_notification",
            "doctor_operation_registered",
        ]
    )
    doctor_operation_or_tool_signal_count = sum(
        int(signal_counts.get(label) or 0)
        for label in ["doctor_operation_registered", "workspace_result_returned"]
    )
    open_at_max = bool(closure_status == "open" and max_turns and turns_completed >= max_turns)
    material_turn_ratio = round(material_turn_count / turns_completed, 3) if turns_completed else None

    stasis_risk_reasons: list[str] = []
    if turns_completed >= 3 and committed_world_event_count == 0:
        stasis_risk_reasons.append("no_committed_world_events")
    if turns_completed >= 3 and positive_time_advance_count == 0:
        stasis_risk_reasons.append("no_positive_virtual_time_advance")
    if turns_completed >= 4 and material_turn_count <= 1:
        stasis_risk_reasons.append("material_progress_on_zero_or_one_turn")
    if turns_completed >= 3 and care_loop_phase_count <= 1:
        stasis_risk_reasons.append("thin_care_loop_evidence")
    if actor_repetition_suspected:
        stasis_risk_reasons.append("actor_text_repetition")
    if doctor_repetition_suspected:
        stasis_risk_reasons.append("doctor_text_repetition")

    mechanical_signal_labels = {"positive_time_advance", "workspace_result_returned"}
    mechanical_progress_only = bool(signal_counts and set(signal_counts).issubset(mechanical_signal_labels))
    mechanical_repetition_loop = bool(
        turns_completed >= 4
        and mechanical_progress_only
        and care_loop_phase_count <= 1
        and (actor_repetition_suspected or doctor_repetition_suspected)
    )
    if mechanical_repetition_loop:
        stasis_risk_reasons.append("mechanical_time_or_workspace_progress_without_world_change")
    strong_progress = bool(
        substantive_progress_signal_count >= 2
        or care_loop_phase_count >= 3
        or (
            material_turn_count >= max(2, turns_completed // 3 if turns_completed >= 6 else 2)
            and substantive_progress_signal_count >= 1
        )
    )
    limited_progress = bool(material_signal_count > 0 or actor_unique_text_count >= 2 or care_loop_phase_count >= 1)
    low_conversation_novelty = bool(actor_repetition_suspected or doctor_repetition_suspected)
    likely_stasis = bool(
        mechanical_repetition_loop
        or (
            turns_completed >= 4
            and material_turn_count <= 1
            and care_loop_phase_count <= 1
            and (low_conversation_novelty or (material_signal_count == 0 and actor_unique_text_count <= 1))
        )
    )

    if turns_completed <= 1:
        status = "too_short_to_judge"
        status_reason = "One turn or less is insufficient to distinguish a deliberately slow start from stasis."
    elif likely_stasis:
        status = "possible_stasis"
        status_reason = "Few or no material world/care-system signals and repetitive or low-novelty dialogue."
    elif open_at_max and strong_progress:
        status = "unfinished_but_progressing"
        status_reason = "The run reached max_turns open, but material world/care-loop signals continued to accumulate."
    elif strong_progress:
        status = "progressing"
        status_reason = "Material world, time, care-system, workspace, or care-loop signals are present across the trajectory."
    elif limited_progress:
        status = "limited_progress"
        status_reason = "Some movement is visible, but material world/care execution evidence is sparse."
    else:
        status = "possible_stasis"
        status_reason = "No material movement or meaningful dialogue novelty was detected after multiple turns."

    return {
        "principle": "Post-hoc descriptive progression evidence only; not runtime flow control and not a doctor score by itself.",
        "status": status,
        "status_reason": status_reason,
        "open_at_max_turns": open_at_max,
        "open_at_max_but_progressing": bool(open_at_max and status == "unfinished_but_progressing"),
        "turns_completed": turns_completed,
        "max_turns": max_turns,
        "material_progress_signal_count": material_signal_count,
        "material_progress_turn_count": material_turn_count,
        "material_progress_turn_ratio": material_turn_ratio,
        "material_progress_turns": bounded_progress_turns[:40],
        "stagnant_turns": stagnant_turns[:40],
        "last_material_progress_turn": max(bounded_progress_turns) if bounded_progress_turns else 0,
        "progress_signal_counts": dict(sorted(signal_counts.items())),
        "progress_signals_by_turn": {
            str(turn): sorted(labels)
            for turn, labels in sorted(material_by_turn.items())
            if not turns_completed or turn <= turns_completed
        },
        "world_time_or_state_signal_count": int(world_time_or_state_signal_count),
        "substantive_progress_signal_count": int(substantive_progress_signal_count),
        "doctor_operation_or_tool_signal_count": int(doctor_operation_or_tool_signal_count),
        "mechanical_progress_only": mechanical_progress_only,
        "mechanical_repetition_loop": mechanical_repetition_loop,
        "care_loop_phase_count": care_loop_phase_count,
        "care_loop_shape": care_loop_shape,
        "actor_message_count": len(actor_texts),
        "actor_unique_text_count": actor_unique_text_count,
        "actor_unique_text_ratio": actor_unique_ratio,
        "doctor_message_count": len(doctor_texts),
        "doctor_unique_text_count": doctor_unique_text_count,
        "doctor_unique_text_ratio": doctor_unique_ratio,
        "actor_repetition_suspected": actor_repetition_suspected,
        "doctor_repetition_suspected": doctor_repetition_suspected,
        "actor_novel_turns": actor_novel_turns[:40],
        "stasis_risk_reasons": stasis_risk_reasons,
    }


def _care_loop_indicators(events: list[dict[str, Any]], transcript: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Summarize the shape of longitudinal care evidence without scoring it.

    This deliberately stays descriptive.  A low-risk self-care case may close
    well with conversation only, while a result-follow-up case usually needs
    workspace retrieval, action, execution/result feedback, and follow-up.
    The evaluator can interpret these indicators against the actual case.
    """

    queried_panels: set[str] = set()
    operation_counts: Counter[str] = Counter()
    receipt_status_counts: Counter[str] = Counter()
    update_status_counts: Counter[str] = Counter()
    result_available_turns: list[int] = []
    execution_update_turns: list[int] = []
    workspace_update_turns: list[int] = []
    committed_event_titles: list[str] = []
    workspace_result_review_turns: list[int] = []
    doctor_result_review_turns: list[int] = []
    doctor_result_action_language_turns: list[int] = []
    doctor_operation_after_result_turns: list[int] = []

    for event in events:
        event_type = str(event.get("event_type") or "")
        turn = _event_turn(event)
        content = event.get("content") if isinstance(event.get("content"), dict) else {}
        if event_type == "doctor_workspace_result":
            panels = [str(panel) for panel in content.get("requested_panels") or [] if str(panel).strip()]
            current_panels = set(panels)
            queried_panels.update(current_panels)
            if result_available_turns and any(previous_turn <= turn for previous_turn in result_available_turns):
                if current_panels.intersection({"test_results", "documents", "records", "timeline"}):
                    workspace_result_review_turns.append(turn)
        elif event_type == "doctor_care_system_receipt":
            operation = _normalize_operation(content.get("operation"))
            if operation:
                operation_counts[operation] += 1
            if operation in {"order_test", "prescribe", "schedule_followup", "track_result", "referral", "call_emergency"}:
                # Whether this was truly *because of* a new result is a soft
                # evaluator judgement.  Here we only preserve the temporal
                # evidence: a concrete doctor-side action was registered
                # after some result/new-record signal had appeared.
                doctor_operation_after_result_turns.append(turn)
            status = str(content.get("status") or "").strip()
            if status:
                receipt_status_counts[status] += 1
        elif event_type == "care_system_state_update":
            for update in content.get("updates") or []:
                if not isinstance(update, dict):
                    continue
                new_status = str(update.get("new_status") or "").strip()
                if new_status:
                    update_status_counts[new_status] += 1
                if new_status == "result_available":
                    result_available_turns.append(turn)
                if new_status in {"patient_executed", "completed", "arrived", "transferred", "refused_after_safety_net"}:
                    execution_update_turns.append(turn)
        elif event_type == "workspace_state_update":
            workspace_update_turns.append(turn)
        elif event_type == "world_event_resolution":
            for item in content.get("committed_world_events") or []:
                if isinstance(item, dict):
                    title = str(item.get("title") or item.get("event_id") or "").strip()
                    if title:
                        committed_event_titles.append(title)

    actor_reported_result_turns = _actor_reported_result_turns(transcript or [])
    result_signal_turns = sorted(set(result_available_turns + workspace_update_turns + actor_reported_result_turns))
    doctor_operation_after_result_turns = [
        turn for turn in doctor_operation_after_result_turns if _after_any(turn, result_signal_turns)
    ]
    doctor_result_review_turns = _doctor_result_review_turns(transcript or [], result_signal_turns)
    doctor_result_action_language_turns = _doctor_result_action_language_turns(transcript or [], result_signal_turns)
    result_reviewed_after_return = bool(workspace_result_review_turns or doctor_result_review_turns)
    doctor_actioned_after_result = bool(doctor_operation_after_result_turns or doctor_result_action_language_turns)
    patient_execution_after_result_action = bool(
        doctor_operation_after_result_turns
        and any(
            _after_any(execution_turn, doctor_operation_after_result_turns)
            for execution_turn in execution_update_turns
        )
    )

    has_workspace_query = bool(queried_panels)
    has_prior_context = bool(queried_panels.intersection({"records", "timeline"}))
    has_result_or_document_context = bool(queried_panels.intersection({"test_results", "documents"}))
    has_medication_context = "medications" in queried_panels
    has_access_context = "care_access" in queried_panels
    has_care_system_action = bool(operation_counts)
    has_order = bool(operation_counts.get("order_test"))
    has_prescription = bool(operation_counts.get("prescribe"))
    has_followup = bool(operation_counts.get("schedule_followup"))
    has_result_tracking = bool(operation_counts.get("track_result"))
    has_handoff_or_emergency = bool(operation_counts.get("referral") or operation_counts.get("call_emergency"))
    has_system_result_or_execution = bool(result_available_turns or execution_update_turns or workspace_update_turns)
    has_world_progression = bool(committed_event_titles)

    phases = {
        "evidence_acquisition": has_workspace_query,
        "doctor_action_registration": has_care_system_action,
        "world_or_system_execution_feedback": has_system_result_or_execution or has_world_progression,
        "result_review_after_return": result_reviewed_after_return,
        "result_action_after_return": doctor_actioned_after_result,
        "followup_or_monitoring_plan": has_followup or has_result_tracking,
    }
    phase_count = sum(1 for value in phases.values() if value)
    shape = _care_loop_shape(
        has_workspace_query=has_workspace_query,
        has_care_system_action=has_care_system_action,
        has_system_result_or_execution=has_system_result_or_execution,
        has_world_progression=has_world_progression,
        result_reviewed_after_return=result_reviewed_after_return,
        has_prescription=has_prescription,
        has_followup=has_followup,
        has_result_tracking=has_result_tracking,
        has_order=has_order,
        has_handoff_or_emergency=has_handoff_or_emergency,
        operation_counts=operation_counts,
        result_actioned_after_return=doctor_actioned_after_result,
    )
    return {
        "shape": shape,
        "phase_count": phase_count,
        "phases": phases,
        "queried_panels": sorted(queried_panels),
        "prior_records_or_timeline_queried": has_prior_context,
        "test_results_or_documents_queried": has_result_or_document_context,
        "medication_context_queried": has_medication_context,
        "care_access_context_queried": has_access_context,
        "operation_counts": dict(sorted(operation_counts.items())),
        "receipt_status_counts": dict(sorted(receipt_status_counts.items())),
        "care_system_update_status_counts": dict(sorted(update_status_counts.items())),
        "result_available_or_workspace_updated": bool(result_available_turns or workspace_update_turns),
        "actor_reported_result_or_record_turns": actor_reported_result_turns,
        "result_signal_turns": result_signal_turns,
        "workspace_result_review_turns": workspace_result_review_turns,
        "doctor_result_review_turns": doctor_result_review_turns,
        "doctor_result_action_language_turns": doctor_result_action_language_turns,
        "doctor_operation_after_result_turns": doctor_operation_after_result_turns,
        "patient_execution_or_transfer_updated": bool(execution_update_turns),
        "result_reviewed_after_return": result_reviewed_after_return,
        "doctor_actioned_after_result_return": doctor_actioned_after_result,
        "patient_execution_after_result_action": patient_execution_after_result_action,
        "followup_scheduled": has_followup,
        "result_tracking_registered": has_result_tracking,
        "handoff_or_emergency_registered": has_handoff_or_emergency,
        "committed_world_event_titles": committed_event_titles[:12],
    }


def _longitudinal_care_process_indicators(
    transcript: list[dict[str, Any]],
    care_loop: dict[str, Any],
) -> dict[str, Any]:
    """Describe the doctor-side care process without turning it into a score.

    CareLoop_FCCT-1's target is not just whether the story ended.  It is whether the
    tested doctor keeps adapting a real-world care plan while gathering enough
    context, using records/tools when available, checking feasibility, giving
    safety-net instructions, and arranging follow-through.  These indicators
    are deliberately post-hoc evidence for humans/LLM evaluators; they do not
    drive the simulator or define hard pass/fail gates.
    """

    doctor_items = [item for item in transcript if _is_doctor_item(item)]
    question_turns = _doctor_turns_matching(doctor_items, _looks_like_question)
    history_context_turns = _doctor_turns_matching(doctor_items, _looks_like_history_or_medication_context_request)
    symptom_or_risk_turns = _doctor_turns_matching(doctor_items, _looks_like_symptom_or_risk_context_request)
    execution_context_turns = _doctor_turns_matching(doctor_items, _looks_like_execution_context_request)
    record_or_report_turns = _doctor_turns_matching(doctor_items, _looks_like_record_or_report_request)
    explanation_turns = _doctor_turns_matching(doctor_items, _looks_like_clinical_explanation)
    action_plan_turns = _doctor_turns_matching(doctor_items, _looks_like_action_language)
    safety_net_turns = _doctor_turns_matching(doctor_items, _looks_like_safety_net_instruction)
    followup_language_turns = _doctor_turns_matching(doctor_items, _looks_like_followup_or_monitoring_language)
    understanding_check_turns = _doctor_turns_matching(doctor_items, _looks_like_understanding_or_feasibility_check)
    actor_response_after_question_turns = _actor_response_after_turns(transcript, question_turns)

    queried_panels = set(str(panel) for panel in care_loop.get("queried_panels") or [])
    operation_counts = care_loop.get("operation_counts") if isinstance(care_loop.get("operation_counts"), dict) else {}
    has_any_operation = bool(operation_counts)
    step_flags = {
        "doctor_asked_questions": bool(question_turns),
        "actor_responded_after_question": bool(actor_response_after_question_turns),
        "history_or_medication_context_explored": bool(
            history_context_turns or queried_panels.intersection({"records", "timeline", "medications"})
        ),
        "symptom_or_risk_context_explored": bool(symptom_or_risk_turns),
        "real_world_execution_context_explored": bool(
            execution_context_turns or "care_access" in queried_panels
        ),
        "records_or_reports_requested_or_queried": bool(
            record_or_report_turns or queried_panels.intersection({"records", "documents", "test_results", "timeline"})
        ),
        "clinical_explanation_present": bool(explanation_turns),
        "action_plan_or_operation_present": bool(action_plan_turns or has_any_operation),
        "safety_net_present": bool(safety_net_turns),
        "followup_or_monitoring_present": bool(
            followup_language_turns
            or care_loop.get("followup_scheduled")
            or care_loop.get("result_tracking_registered")
        ),
        "understanding_or_feasibility_checked": bool(understanding_check_turns),
    }
    examples = [
        {
            "turn": _event_turn(item),
            "text_preview": _preview_text(str(item.get("text") or "")),
        }
        for item in doctor_items
        if _event_turn(item) in question_turns[:3]
    ]
    return {
        "principle": "Post-hoc descriptive care-process evidence only; not runtime flow control and not a doctor score by itself.",
        "step_flags": step_flags,
        "step_count": sum(1 for value in step_flags.values() if value),
        "doctor_question_turns": question_turns,
        "actor_response_after_question_turns": actor_response_after_question_turns,
        "history_or_medication_context_turns": history_context_turns,
        "symptom_or_risk_context_turns": symptom_or_risk_turns,
        "execution_context_turns": execution_context_turns,
        "record_or_report_request_turns": record_or_report_turns,
        "clinical_explanation_turns": explanation_turns,
        "action_plan_language_turns": action_plan_turns,
        "safety_net_turns": safety_net_turns,
        "followup_or_monitoring_language_turns": followup_language_turns,
        "understanding_or_feasibility_check_turns": understanding_check_turns,
        "doctor_question_examples": examples,
    }


def _care_loop_shape(
    *,
    has_workspace_query: bool,
    has_care_system_action: bool,
    has_system_result_or_execution: bool,
    has_world_progression: bool,
    result_reviewed_after_return: bool,
    has_prescription: bool,
    has_followup: bool,
    has_result_tracking: bool,
    has_order: bool,
    has_handoff_or_emergency: bool,
    operation_counts: Counter[str],
    result_actioned_after_return: bool,
) -> str:
    if not has_workspace_query and not has_care_system_action:
        return "conversation_only"
    non_handoff_actions = sum(
        count
        for operation, count in operation_counts.items()
        if operation not in {"referral", "call_emergency"}
    )
    if has_handoff_or_emergency and non_handoff_actions == 0:
        return "handoff_or_emergency_only"
    if result_reviewed_after_return and result_actioned_after_return and (has_prescription or has_followup or has_result_tracking):
        return "result_to_action_followup_loop"
    if has_order and has_system_result_or_execution and has_workspace_query:
        return "ordered_result_returned_loop"
    if has_care_system_action and (has_system_result_or_execution or has_world_progression):
        return "action_execution_loop"
    if has_care_system_action:
        return "doctor_plan_or_receipt_only"
    return "workspace_assisted_conversation"


def _clinical_core_loop_indicators(
    events: list[dict[str, Any]],
    transcript: list[dict[str, Any]],
    care_loop: dict[str, Any],
    longitudinal_care_process: dict[str, Any],
) -> dict[str, Any]:
    """Describe whether the run exercised the narrow clinical core loop.

    CareLoop should not only test whether the doctor can send a patient to a
    hospital or translate a finished external plan.  This audit makes visible
    which parts of diagnosis → testing → results → treatment → response tracking
    → long-term management actually appeared in the trajectory.  It is evidence
    for the evaluator, not a scoring formula.
    """

    doctor_items = [item for item in transcript if _is_doctor_item(item)]
    operation_counts = care_loop.get("operation_counts") if isinstance(care_loop.get("operation_counts"), dict) else {}
    queried_panels = set(str(panel) for panel in care_loop.get("queried_panels") or [])
    step_flags = longitudinal_care_process.get("step_flags") if isinstance(longitudinal_care_process.get("step_flags"), dict) else {}

    diagnostic_reasoning_turns = _doctor_turns_matching(doctor_items, _looks_like_diagnostic_reasoning)
    test_selection_turns = sorted(
        set(_doctor_turns_matching(doctor_items, _looks_like_test_selection) + _operation_turns(events, {"order_test"}))
    )
    result_interpretation_turns = sorted(
        set(
            list(care_loop.get("doctor_result_review_turns") or [])
            + _doctor_turns_matching(doctor_items, _looks_like_result_review_text)
        )
    )
    treatment_decision_turns = sorted(
        set(_doctor_turns_matching(doctor_items, _looks_like_treatment_decision) + _operation_turns(events, {"prescribe"}))
    )
    response_tracking_turns = sorted(
        set(
            _doctor_turns_matching(doctor_items, _looks_like_response_tracking)
            + _operation_turns(events, {"schedule_followup", "track_result"})
        )
    )
    long_term_management_turns = _doctor_turns_matching(doctor_items, _looks_like_long_term_management)
    cross_institution_takeover_turns = sorted(
        set(
            _doctor_turns_matching(doctor_items, _looks_like_cross_institution_takeover)
            + ([1] if queried_panels.intersection({"records", "documents", "test_results", "medications", "timeline"}) else [])
        )
    )

    stage_flags = {
        "diagnostic_reasoning": bool(diagnostic_reasoning_turns),
        "test_selection_or_ordering": bool(test_selection_turns or operation_counts.get("order_test")),
        "result_interpretation": bool(result_interpretation_turns or care_loop.get("result_reviewed_after_return")),
        "treatment_decision_or_adjustment": bool(treatment_decision_turns or operation_counts.get("prescribe")),
        "response_or_adverse_effect_tracking": bool(response_tracking_turns or care_loop.get("followup_scheduled") or care_loop.get("result_tracking_registered")),
        "long_term_management": bool(long_term_management_turns),
        "cross_institution_discontinuity_takeover": bool(cross_institution_takeover_turns),
    }
    stage_count = sum(1 for value in stage_flags.values() if value)
    handoff_only = bool(care_loop.get("handoff_or_emergency_registered") and not operation_counts.get("order_test") and not operation_counts.get("prescribe"))
    has_result_to_treatment = bool(stage_flags["result_interpretation"] and stage_flags["treatment_decision_or_adjustment"])
    has_tracking_after_action = bool(
        stage_flags["response_or_adverse_effect_tracking"]
        or care_loop.get("patient_execution_after_result_action")
    )
    has_executed_or_registered_treatment_loop = bool(
        operation_counts.get("prescribe")
        or care_loop.get("patient_execution_after_result_action")
        or care_loop.get("shape") == "result_to_action_followup_loop"
    )
    if stage_count >= 5 and has_result_to_treatment and has_tracking_after_action and has_executed_or_registered_treatment_loop:
        status = "broad_clinical_loop_evidence"
    elif stage_count >= 4 and (has_result_to_treatment or stage_flags["test_selection_or_ordering"]):
        status = "partial_clinical_loop_evidence"
    elif stage_count >= 2 and not handoff_only:
        status = "early_clinical_reasoning_evidence"
    elif handoff_only:
        status = "handoff_or_advice_only"
    else:
        status = "minimal_or_not_yet_tested"

    return {
        "principle": "Post-hoc descriptive clinical-core audit only; not a closure formula and not a doctor score by itself.",
        "status": status,
        "stage_flags": stage_flags,
        "stage_count": stage_count,
        "diagnostic_reasoning_turns": diagnostic_reasoning_turns,
        "test_selection_or_ordering_turns": test_selection_turns,
        "result_interpretation_turns": result_interpretation_turns,
        "treatment_decision_or_adjustment_turns": treatment_decision_turns,
        "response_or_adverse_effect_tracking_turns": response_tracking_turns,
        "long_term_management_turns": long_term_management_turns,
        "cross_institution_takeover_turns": cross_institution_takeover_turns,
        "handoff_only_signal": handoff_only,
        "result_to_treatment_signal": has_result_to_treatment,
        "tracking_after_action_signal": has_tracking_after_action,
        "executed_or_registered_treatment_loop_signal": has_executed_or_registered_treatment_loop,
        "doctor_care_process_step_flags_used": {
            key: bool(step_flags.get(key))
            for key in [
                "history_or_medication_context_explored",
                "symptom_or_risk_context_explored",
                "records_or_reports_requested_or_queried",
                "clinical_explanation_present",
                "followup_or_monitoring_present",
            ]
        },
    }


def _actor_cooperation_realism_indicators(transcript: list[dict[str, Any]]) -> dict[str, Any]:
    """Flag patient/family messages that look unrealistically like perfect staff notes.

    This is a simulator-validity audit, not a doctor score.  Some real families
    are highly organized, so the metric is intentionally evidentiary and
    conservative: it asks the evaluator to review whether the actor became too
    complete, structured, compliant, or checklist-like for the authored profile.
    """

    actor_items = [item for item in transcript if _is_patient_or_family_item(item)]
    examples: list[dict[str, Any]] = []
    marker_counts: Counter[str] = Counter()
    overstructured_count = 0
    prompted_structured_count = 0
    for item in actor_items:
        text = str(item.get("text") or "")
        markers = _actor_overcooperation_markers(text)
        if _doctor_prompted_structured_actor_response(transcript, item, markers):
            markers.append("doctor_prompted_structured_response")
        marker_counts.update(markers)
        if _actor_message_is_overstructured(text, markers):
            overstructured_count += 1
            examples.append(
                {
                    "turn": _event_turn(item),
                    "speaker": str(item.get("speaker_display") or item.get("speaker") or item.get("speaker_category") or ""),
                    "markers": markers,
                    "text_preview": _preview_text(text, limit=220),
                }
            )
        elif "doctor_prompted_structured_response" in markers:
            prompted_structured_count += 1
    total = len(actor_items)
    ratio = round(overstructured_count / total, 3) if total else 0.0
    # Do not treat a small number of structured updates as high-risk by itself.
    # In long medical trajectories a doctor may repeatedly ask a caregiver to
    # report glucose, BP, medication execution, and alarm symptoms.  That can
    # legitimately produce a few checklist-like messages, especially from an
    # engaged family caregiver.  Reserve high_risk for pervasive or repeatedly
    # dominant over-organization; keep sparse signals as review evidence.
    if total >= 3 and (ratio >= 0.25 or (overstructured_count >= 6 and ratio >= 0.12)):
        status = "high_risk"
    elif total >= 2 and overstructured_count >= 1:
        status = "review"
    elif prompted_structured_count >= 2:
        # Doctor-prompted structured check-ins are common in long care loops:
        # e.g. “14:00 report chest tightness X/10, ideation X/10, red flags”.
        # They should remain visible in marker_counts, but they are not by
        # themselves evidence that the simulator actor became unrealistically
        # cooperative.  Escalate only when the message is independently
        # overstructured above.
        status = "ok_doctor_prompted_structured"
    elif total == 1 and overstructured_count >= 1:
        status = "single_message_review"
    else:
        status = "ok"
    return {
        "principle": "Simulator-validity audit only; high organization can be realistic for some caregivers but should not be assumed by default.",
        "status": status,
        "actor_message_count": total,
        "overstructured_message_count": overstructured_count,
        "doctor_prompted_structured_message_count": prompted_structured_count,
        "overstructured_ratio": ratio,
        "marker_counts": dict(sorted(marker_counts.items())),
        "examples": examples[:8],
    }


def _actor_overcooperation_markers(text: str) -> list[str]:
    source = str(text or "")
    markers: list[str] = []
    numbered_items = re.findall(r"(?:^|[\n\s:：;；,，])(?:\d+[\.、\)]|[一二三四五六七八九十][、.．]|[①②③④⑤⑥⑦⑧⑨])", source)
    if len(numbered_items) >= 3:
        markers.append("numbered_checklist")
    if len(re.findall(r"(没有|无|不发热|不咳|不痛|不晕|不吐|不拉|不胸闷|不气短)", source)) >= 5:
        markers.append("exhaustive_negative_symptom_list")
    echo_terms = [
        "按你说",
        "按医生说",
        "我已经",
        "都已经",
        "全部",
        "逐条",
        "一项一项",
        "上传了",
        "拍了",
        "记录了",
        "保存了",
        "设了闹钟",
        "做了表",
        "贴在墙",
        "红旗",
        "安全网",
        "理解得对不对",
        "我理解得对吗",
    ]
    echo_count = sum(1 for term in echo_terms if term in source)
    if echo_count >= 4:
        markers.append("comprehensive_instruction_echo")
    if re.search(r"(设了?闹钟|做了?表格|贴在墙上|打卡|每天记录|拍照保存|建了?群|清单)", source):
        markers.append("meticulous_tracking_behavior")
    if re.search(r"(我理解得对不对|我理解得对吗|这样理解对吗|请你确认我理解)", source):
        markers.append("frequent_understanding_confirmation")
    if len(source) >= 420 and len(numbered_items) >= 2 and echo_count >= 2:
        markers.append("long_complete_case_report_style")
    return sorted(set(markers))


def _actor_message_is_overstructured(text: str, markers: list[str]) -> bool:
    if "doctor_prompted_structured_response" in markers:
        hard_markers = {
            "comprehensive_instruction_echo",
            "meticulous_tracking_behavior",
            "frequent_understanding_confirmation",
        }
        # A long or numbered reply can be realistic when the doctor just gave a
        #查房/复核清单.  Keep it as a review signal, but do not count it as
        # overcooperation unless the actor also becomes unrealistically
        # meticulous or repeatedly asks for confirmation.
        if not hard_markers.intersection(markers):
            return False
    if "numbered_checklist" in markers and len(markers) >= 2:
        return True
    if "long_complete_case_report_style" in markers:
        return True
    if len(markers) >= 3:
        return True
    source = str(text or "")
    # A single meticulous-tracking sentence can be realistic.  It becomes a
    # review signal when paired with a long, complete, instruction-echoing reply.
    return len(source) >= 320 and "meticulous_tracking_behavior" in markers and "comprehensive_instruction_echo" in markers


def _doctor_prompted_structured_actor_response(
    transcript: list[dict[str, Any]], item: dict[str, Any], markers: list[str]
) -> bool:
    if "numbered_checklist" not in markers and "exhaustive_negative_symptom_list" not in markers:
        return False
    previous_doctor_text = _previous_doctor_text(transcript, item)
    if not previous_doctor_text:
        return False
    numbered_items = re.findall(
        r"(?:^|[\n\s:：;；,，])(?:\d+[\.、\)]|[一二三四五六七八九十][、.．]|[①②③④⑤⑥⑦⑧⑨])",
        previous_doctor_text,
    )
    checklist_terms = [
        "清单",
        "按顺序",
        "逐条",
        "这几个问题",
        "几个问题",
        "重点问",
        "请问清",
        "请把",
        "拍照发",
        "发给我",
        "告诉我",
        "核对",
        "查房",
    ]
    if len(numbered_items) >= 3 or sum(1 for term in checklist_terms if term in previous_doctor_text) >= 2:
        return True

    # Monitoring reports are often naturally semi-structured even when the
    # doctor did not use an explicit numbered checklist.  If the previous doctor
    # message asked the actor to send concrete home-monitoring data, do not turn
    # the next vitals/symptom report into a high-cooperation signal unless other
    # hard overcooperation markers are present.
    report_request_terms = ["发我", "告诉我", "回我", "记录", "测", "量", "复测", "观察", "反馈"]
    monitoring_terms = [
        "血糖",
        "血压",
        "坐位",
        "站立",
        "站起来",
        "空腹",
        "晚饭前",
        "睡前",
        "打针",
        "用药",
        "头晕",
        "口干",
        "尿痛",
        "尿烧",
        "发热",
        "呕吐",
        "低血糖",
    ]
    return (
        sum(1 for term in report_request_terms if term in previous_doctor_text) >= 1
        and sum(1 for term in monitoring_terms if term in previous_doctor_text) >= 3
    )


def _previous_doctor_text(transcript: list[dict[str, Any]], item: dict[str, Any]) -> str:
    try:
        index = transcript.index(item)
    except ValueError:
        return ""
    for previous in reversed(transcript[:index]):
        if _is_doctor_item(previous):
            return str(previous.get("text") or "")
    return ""


def _external_progression_dependency_indicators(
    *,
    care_loop: dict[str, Any],
    longitudinal_care_process: dict[str, Any],
    events: list[dict[str, Any]],
    closure_status: str,
    turns_completed: int,
) -> dict[str, Any]:
    """Describe when apparent progress may be dominated by external systems/world motion.

    This does not penalize appropriate collaboration with hospitals.  It makes
    the evaluator explicitly separate patient safety/progress from the tested AI
    doctor's own clinical agency.
    """

    operation_counts_raw = care_loop.get("operation_counts") if isinstance(care_loop.get("operation_counts"), dict) else {}
    operation_counts = {str(key): int(value or 0) for key, value in operation_counts_raw.items()}
    handoff_count = operation_counts.get("referral", 0) + operation_counts.get("call_emergency", 0)
    non_handoff_action_count = sum(
        count for operation, count in operation_counts.items() if operation not in {"referral", "call_emergency"}
    )
    doctor_action_count = sum(operation_counts.values())
    world_or_system_progress_count = int(care_loop.get("world_or_system_progress_event_count") or 0)
    if not world_or_system_progress_count:
        world_or_system_progress_count = (
            _count_events(events, "care_system_state_update")
            + _count_events(events, "workspace_state_update")
            + _count_events(events, "doctor_system_notification")
            + _committed_world_event_count(events)
        )
    step_flags = longitudinal_care_process.get("step_flags") if isinstance(longitudinal_care_process.get("step_flags"), dict) else {}
    doctor_clinical_agency_signal_count = sum(
        1
        for key in (
            "history_or_medication_context_explored",
            "symptom_or_risk_context_explored",
            "records_or_reports_requested_or_queried",
            "clinical_explanation_present",
            "action_plan_or_operation_present",
            "followup_or_monitoring_present",
        )
        if step_flags.get(key)
    )
    shape = str(care_loop.get("shape") or "")
    result_without_action = bool(
        care_loop.get("result_available_or_workspace_updated")
        and not care_loop.get("doctor_actioned_after_result_return")
    )
    handoff_only_at_closure = bool(
        closure_status in {"closed", "soft_closed"}
        and (shape == "handoff_or_emergency_only" or (handoff_count and non_handoff_action_count == 0))
    )
    closed_by_plan_only = bool(closure_status in {"closed", "soft_closed"} and shape == "doctor_plan_or_receipt_only")
    world_progress_dominates = bool(
        world_or_system_progress_count >= max(2, doctor_action_count + 1)
        and non_handoff_action_count == 0
        and doctor_clinical_agency_signal_count <= 3
        and turns_completed >= 2
    )
    reasons: list[str] = []
    if handoff_only_at_closure:
        reasons.append("handoff_or_emergency_only_at_closure")
    if closed_by_plan_only:
        reasons.append("closed_or_soft_closed_with_plan_only")
    if result_without_action:
        reasons.append("result_or_workspace_update_without_doctor_action_after_return")
    if world_progress_dominates:
        reasons.append("world_or_external_progress_events_exceed_doctor_actions")
    if doctor_clinical_agency_signal_count <= 2 and world_or_system_progress_count >= 2:
        reasons.append("low_doctor_clinical_agency_signals_with_external_progress")
    if handoff_only_at_closure or closed_by_plan_only:
        status = "high_risk"
    elif world_progress_dominates or result_without_action or (doctor_clinical_agency_signal_count <= 2 and world_or_system_progress_count >= 2):
        status = "review"
    else:
        status = "ok"
    return {
        "principle": "Simulator/evaluator aid only: distinguish patient progress/safety from tested AI doctor contribution; appropriate external collaboration is not a failure by itself.",
        "status": status,
        "care_loop_shape": shape,
        "doctor_action_count": doctor_action_count,
        "handoff_action_count": handoff_count,
        "non_handoff_doctor_action_count": non_handoff_action_count,
        "world_or_system_progress_event_count": world_or_system_progress_count,
        "doctor_clinical_agency_signal_count": doctor_clinical_agency_signal_count,
        "result_without_doctor_action_after_return": result_without_action,
        "handoff_only_at_closure": handoff_only_at_closure,
        "closed_by_plan_only": closed_by_plan_only,
        "world_progress_dominates_doctor_action": world_progress_dominates,
        "reasons": reasons,
    }


def _real_world_friction_indicators(transcript: list[dict[str, Any]], events: list[dict[str, Any]]) -> dict[str, Any]:
    """Describe real-world barriers/noise visible in the conversation.

    This is not a score.  It gives ClosureJudge/Evaluator and humans a compact
    read on whether the trajectory actually exercised CareLoop_FCCT-1's target
    capability: adapting care under messy human/system constraints.
    """

    actor_signals: list[dict[str, Any]] = []
    category_counts: Counter[str] = Counter()
    for item in transcript:
        if not _is_patient_or_family_item(item):
            continue
        text = str(item.get("text") or "")
        categories = _friction_categories(text)
        if not categories:
            continue
        for category in categories:
            category_counts[category] += 1
        actor_signals.append(
            {
                "turn": _event_turn(item),
                "speaker": item.get("speaker_display") or item.get("speaker"),
                "categories": categories,
                "text_preview": _preview_text(text),
            }
        )

    adaptation_examples: list[dict[str, Any]] = []
    for item in transcript:
        if not _is_doctor_item(item):
            continue
        turn = _event_turn(item)
        prior_signals = [signal for signal in actor_signals if 0 < turn - int(signal.get("turn") or 0) <= 2]
        if not prior_signals:
            continue
        text = str(item.get("text") or "")
        if not _looks_like_friction_adaptation(text):
            continue
        categories: list[str] = []
        for signal in prior_signals:
            for category in signal.get("categories") or []:
                if category not in categories:
                    categories.append(category)
        adaptation_examples.append(
            {
                "turn": turn,
                "responds_to_categories": categories,
                "text_preview": _preview_text(text),
            }
        )

    return {
        "actor_friction_signal_count": len(actor_signals),
        "friction_category_counts": dict(sorted(category_counts.items())),
        "actor_friction_examples": actor_signals[:8],
        "doctor_adaptation_after_friction_count": len(adaptation_examples),
        "doctor_adaptation_examples": adaptation_examples[:8],
        "barrier_to_adaptation_observed": bool(actor_signals and adaptation_examples),
    }


def _friction_categories(text: str) -> list[str]:
    source = str(text or "")
    categories: list[str] = []
    patterns = [
        ("cost_or_insurance", r"(钱|费用|太贵|贵|花钱|医保|报销|住院费|检查费|付不起|经济|收入)"),
        ("transport_or_access", r"(远|交通|打不到车|没车|救护车|120|排队|挂号|夜里|晚上|急诊太远|县医院|乡镇|本地做不了)"),
        ("family_resistance", r"(家里人|家属|我爸|我妈|老公|老婆|女儿|儿子|父母|家人).{0,18}(不让|不同意|反对|觉得|说不用|嫌|怕花钱)"),
        ("low_literacy_or_misunderstanding", r"(看不懂|不懂|不会|说不清|搞不清|弄不明白|不知道怎么|听不懂|字看不清|不会上传|上传不了)"),
        ("fear_emotion_or_stigma", r"(怕|害怕|担心|焦虑|紧张|丢人|隐私|不敢说|不好意思|被骂|不想让人知道)"),
        ("delay_refusal_or_adherence", r"(不想|不愿|不用|用不着|不去|不吃|没吃|忘了|自己停|先不|等等|拖|周末再|明天再|忍一忍|不叫|不查|不住院)"),
        ("tech_or_record_problem", r"(上传|照片|拍照|报告|报告单|药盒|网络|手机|看不清|原件|找不到|打不开).{0,18}(不了|不清|没有|找不到|不会|失败|丢了)?"),
        ("misinformation_or_prior_belief", r"(短视频|网上|百度|朋友说|邻居说|以前也这样|老毛病|偏方|土办法|先吃点药|网上说)"),
    ]
    for category, pattern in patterns:
        if re.search(pattern, source, re.IGNORECASE):
            categories.append(category)
    return categories


def _looks_like_friction_adaptation(text: str) -> bool:
    source = str(text or "")
    if not source.strip():
        return False
    adaptation_terms = [
        "如果",
        "可以先",
        "换个办法",
        "就近",
        "先做",
        "第一步",
        "一步一步",
        "我给你写清楚",
        "让家属",
        "联系家属",
        "叫120",
        "救护车",
        "急诊",
        "授权",
        "上传",
        "拍照",
        "药盒",
        "费用",
        "医保",
        "便宜",
        "社区",
        "县医院",
        "随访",
        "复查",
        "安全网",
        "不要等",
        "现在最要紧",
        "可执行",
        "做不到",
        "替代",
    ]
    return any(term in source for term in adaptation_terms)


def _preview_text(text: str, limit: int = 160) -> str:
    compact = " ".join(str(text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 13].rstrip() + " ...[truncated]"


def _event_turn(event: dict[str, Any]) -> int:
    try:
        return int(event.get("turn") or 0)
    except (TypeError, ValueError):
        return 0


def _normalize_operation(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    if "." in raw:
        raw = raw.split(".")[-1]
    aliases = {
        "order": "order_test",
        "test": "order_test",
        "lab": "order_test",
        "rx": "prescribe",
        "medication": "prescribe",
        "prescription": "prescribe",
        "followup": "schedule_followup",
        "follow_up": "schedule_followup",
        "emergency": "call_emergency",
        "ems": "call_emergency",
    }
    return aliases.get(raw, raw)


def _actor_texts(transcript: list[dict[str, Any]]) -> list[str]:
    return _speaker_texts(transcript, {"patient", "family", "caregiver", "relative", "家属", "患者"})


def _actor_identity_missing_count(transcript: list[dict[str, Any]]) -> int:
    missing = 0
    for item in transcript:
        if not _is_patient_or_family_item(item):
            continue
        has_display = bool(str(item.get("speaker_display") or item.get("speaker") or "").strip())
        has_category = bool(str(item.get("speaker_category") or "").strip())
        if not has_display or not has_category:
            missing += 1
    return missing


def _actor_reported_result_turns(transcript: list[dict[str, Any]]) -> list[int]:
    turns: list[int] = []
    for item in transcript:
        if not _is_patient_or_family_item(item):
            continue
        text = str(item.get("text") or "")
        if _looks_like_result_or_record_signal(text):
            turns.append(_event_turn(item))
    return turns


def _doctor_result_review_turns(transcript: list[dict[str, Any]], result_signal_turns: list[int]) -> list[int]:
    turns: list[int] = []
    for item in transcript:
        if not _is_doctor_item(item):
            continue
        turn = _event_turn(item)
        if not _after_any(turn, result_signal_turns):
            continue
        text = str(item.get("text") or "")
        if _looks_like_result_review_text(text):
            turns.append(turn)
    return turns


def _doctor_result_action_language_turns(transcript: list[dict[str, Any]], result_signal_turns: list[int]) -> list[int]:
    turns: list[int] = []
    for item in transcript:
        if not _is_doctor_item(item):
            continue
        turn = _event_turn(item)
        if not _after_any(turn, result_signal_turns):
            continue
        text = str(item.get("text") or "")
        if _looks_like_result_review_text(text) and _looks_like_action_language(text):
            turns.append(turn)
    return turns


def _after_any(turn: int, earlier_turns: list[int]) -> bool:
    return bool(earlier_turns) and any(previous_turn < turn for previous_turn in earlier_turns)


def _operation_turns(events: list[dict[str, Any]], operations: set[str]) -> list[int]:
    turns: list[int] = []
    for event in events:
        if event.get("event_type") != "doctor_care_system_receipt":
            continue
        content = event.get("content") if isinstance(event.get("content"), dict) else {}
        operation = _normalize_operation(content.get("operation"))
        if operation in operations:
            turns.append(_event_turn(event))
    return sorted(set(turns))


def _looks_like_diagnostic_reasoning(text: str) -> bool:
    source = str(text or "")
    if not source.strip():
        return False
    diagnostic_terms = [
        "考虑",
        "可能是",
        "也可能",
        "不能排除",
        "需要排除",
        "鉴别",
        "风险分层",
        "原因",
        "诊断",
        "疑似",
        "倾向",
        "符合",
        "不像",
        "要警惕",
        "红旗",
    ]
    clinical_terms = [
        "感染",
        "出血",
        "梗死",
        "卒中",
        "肿瘤",
        "癌",
        "贫血",
        "低血糖",
        "心衰",
        "肾",
        "肺",
        "炎症",
        "血栓",
        "异位妊娠",
        "药物",
        "副作用",
        "复发",
    ]
    return any(term in source for term in diagnostic_terms) and any(term in source for term in clinical_terms)


def _looks_like_test_selection(text: str) -> bool:
    source = str(text or "")
    if not source.strip():
        return False
    action_terms = ["做", "查", "复查", "安排", "预约", "开", "完善", "需要", "建议"]
    test_terms = [
        "血常规",
        "尿常规",
        "尿培养",
        "血培养",
        "肝肾功能",
        "电解质",
        "心电图",
        "CT",
        "ct",
        "MRI",
        "B超",
        "彩超",
        "肠镜",
        "胃镜",
        "病理",
        "培养",
        "药敏",
        "影像",
        "检查",
        "化验",
        "检验",
        "血糖",
        "血压",
    ]
    return any(term in source for term in action_terms) and any(term in source for term in test_terms)


def _looks_like_treatment_decision(text: str) -> bool:
    source = str(text or "")
    if not source.strip():
        return False
    treatment_terms = [
        "用药",
        "吃药",
        "换药",
        "停药",
        "暂停",
        "加用",
        "减量",
        "增量",
        "剂量",
        "处方",
        "抗生素",
        "胰岛素",
        "吸入",
        "雾化",
        "抗凝",
        "降压",
        "降糖",
        "补铁",
        "治疗",
        "手术",
        "住院治疗",
        "输液",
        "补液",
        "康复",
    ]
    decision_terms = ["建议", "需要", "改成", "调整", "开始", "继续", "不要", "先", "如果", "方案"]
    return any(term in source for term in treatment_terms) and any(term in source for term in decision_terms)


def _looks_like_response_tracking(text: str) -> bool:
    source = str(text or "")
    if not source.strip():
        return False
    tracking_terms = [
        "疗效",
        "有没有好转",
        "是否好转",
        "副作用",
        "不良反应",
        "复发",
        "无效",
        "加重",
        "观察",
        "监测",
        "记录",
        "复诊",
        "随访",
        "复查",
        "再反馈",
        "几天后",
        "24小时",
        "48小时",
        "一周后",
        "结果回来",
        "结果出来",
        "追踪",
    ]
    return any(term in source for term in tracking_terms)


def _looks_like_long_term_management(text: str) -> bool:
    source = str(text or "")
    if not source.strip():
        return False
    long_terms = [
        "长期",
        "慢病",
        "长期随访",
        "稳定后",
        "二级预防",
        "康复",
        "复发预防",
        "生活方式",
        "戒烟",
        "限盐",
        "运动",
        "血压目标",
        "血糖目标",
        "血脂",
        "长期管理",
        "家庭监测",
        "用药依从",
    ]
    return any(term in source for term in long_terms)


def _looks_like_cross_institution_takeover(text: str) -> bool:
    source = str(text or "")
    if not source.strip():
        return False
    institution_terms = ["外院", "本院", "社区", "区医院", "县医院", "急诊", "住院", "出院", "门诊", "药房", "体检", "上次", "既往"]
    takeover_terms = ["调取", "授权", "上传", "报告", "病历", "出院小结", "处方", "药盒", "记录", "时间线", "联网", "复核", "核对"]
    return any(term in source for term in institution_terms) and any(term in source for term in takeover_terms)


def _looks_like_result_or_record_signal(text: str) -> bool:
    source = str(text or "")
    if not source.strip():
        return False
    result_terms = [
        "结果",
        "报告",
        "报告单",
        "化验",
        "检验",
        "检查单",
        "片子",
        "CT",
        "ct",
        "B超",
        "彩超",
        "尿培养",
        "血培养",
        "病理",
        "心电图",
        "血糖",
        "血压",
        "肌酐",
        "白细胞",
        "CRP",
        "降钙素",
    ]
    return_terms = [
        "出来",
        "出了",
        "拿到",
        "拍了",
        "上传",
        "发你",
        "发给",
        "提示",
        "显示",
        "写着",
        "阳性",
        "阴性",
        "异常",
        "正常",
        "偏高",
        "偏低",
        "升高",
        "降低",
        "耐药",
        "敏感",
        "不敏感",
    ]
    return any(term in source for term in result_terms) and any(term in source for term in return_terms)


def _looks_like_result_review_text(text: str) -> bool:
    source = str(text or "")
    if not source.strip():
        return False
    review_terms = [
        "结果",
        "报告",
        "报告单",
        "化验",
        "检验",
        "检查",
        "片子",
        "提示",
        "显示",
        "说明",
        "根据",
        "从这个",
        "尿培养",
        "血培养",
        "CT",
        "ct",
        "心电图",
        "血糖",
        "血压",
        "肌酐",
        "白细胞",
        "CRP",
        "阳性",
        "阴性",
        "偏高",
        "偏低",
        "耐药",
        "敏感",
        "不敏感",
    ]
    return any(term in source for term in review_terms)


def _looks_like_action_language(text: str) -> bool:
    source = str(text or "")
    if not source.strip():
        return False
    action_terms = [
        "调整",
        "换药",
        "改成",
        "停用",
        "暂停",
        "加用",
        "减少",
        "增加",
        "开",
        "处方",
        "用药",
        "复查",
        "随访",
        "复诊",
        "转诊",
        "急诊",
        "120",
        "住院",
        "观察",
        "监测",
        "联系",
        "安排",
        "登记",
    ]
    return any(term in source for term in action_terms)


def _doctor_turns_matching(doctor_items: list[dict[str, Any]], predicate: Any) -> list[int]:
    turns: list[int] = []
    for item in doctor_items:
        text = str(item.get("text") or "")
        if predicate(text):
            turns.append(_event_turn(item))
    return sorted(set(turns))


def _contains_any(text: str, terms: list[str]) -> bool:
    source = str(text or "")
    return any(term in source for term in terms)


def _looks_like_question(text: str) -> bool:
    source = str(text or "")
    if not source.strip():
        return False
    question_terms = [
        "？",
        "?",
        "有没有",
        "是否",
        "多久",
        "几天",
        "什么时候",
        "哪里",
        "哪儿",
        "什么",
        "多少",
        "能不能",
        "可不可以",
        "方便不方便",
        "请问",
        "确认一下",
        "告诉我",
        "描述一下",
    ]
    return _contains_any(source, question_terms)


def _looks_like_history_or_medication_context_request(text: str) -> bool:
    source = str(text or "")
    context_terms = [
        "既往",
        "以前",
        "基础病",
        "病史",
        "手术",
        "过敏",
        "家族",
        "用药",
        "正在吃",
        "吃什么药",
        "药盒",
        "药名",
        "剂量",
        "孕",
        "哺乳",
        "月经",
    ]
    return _contains_any(source, context_terms) and (
        _looks_like_question(source) or _contains_any(source, ["上传", "拍照", "发给", "调取", "看一下"])
    )


def _looks_like_symptom_or_risk_context_request(text: str) -> bool:
    source = str(text or "")
    risk_terms = [
        "症状",
        "疼",
        "痛",
        "发热",
        "烧",
        "胸闷",
        "胸痛",
        "喘",
        "呼吸",
        "意识",
        "嗜睡",
        "抽搐",
        "麻",
        "无力",
        "说话",
        "出血",
        "呕吐",
        "尿",
        "便",
        "皮疹",
        "头痛",
        "加重",
        "危险信号",
        "红旗",
    ]
    return _looks_like_question(source) and _contains_any(source, risk_terms)


def _looks_like_execution_context_request(text: str) -> bool:
    source = str(text or "")
    execution_terms = [
        "能不能去",
        "能否去",
        "怎么去",
        "谁陪",
        "家属",
        "费用",
        "医保",
        "报销",
        "距离",
        "远不远",
        "打车",
        "救护车",
        "120",
        "上班",
        "请假",
        "孩子",
        "照顾",
        "做得到",
        "能做到",
        "可执行",
        "方便",
        "县医院",
        "社区",
        "本地",
        "挂号",
        "排队",
    ]
    return _contains_any(source, execution_terms) and (
        _looks_like_question(source) or _contains_any(source, ["如果做不到", "做不到的话", "替代办法"])
    )


def _looks_like_record_or_report_request(text: str) -> bool:
    source = str(text or "")
    record_terms = [
        "报告",
        "报告单",
        "病历",
        "既往记录",
        "检查结果",
        "化验",
        "检验",
        "片子",
        "CT",
        "B超",
        "彩超",
        "心电图",
        "处方",
        "药盒",
        "照片",
        "上传",
        "拍照",
        "发给我",
        "发来",
        "调取",
        "看一下",
    ]
    return _contains_any(source, record_terms)


def _looks_like_clinical_explanation(text: str) -> bool:
    source = str(text or "")
    explanation_terms = [
        "说明",
        "意味着",
        "提示",
        "考虑",
        "可能是",
        "因为",
        "原因",
        "风险",
        "不能排除",
        "不代表",
        "需要",
        "所以",
        "优先",
    ]
    return _contains_any(source, explanation_terms)


def _looks_like_safety_net_instruction(text: str) -> bool:
    source = str(text or "")
    trigger_terms = ["如果", "一旦", "出现", "加重", "恶化", "突然", "持续", "马上", "立即", "不要等"]
    action_terms = ["急诊", "120", "救护车", "立刻", "马上", "立即", "不要等", "就医", "复诊"]
    return _contains_any(source, trigger_terms) and _contains_any(source, action_terms)


def _looks_like_followup_or_monitoring_language(text: str) -> bool:
    source = str(text or "")
    terms = [
        "随访",
        "复查",
        "复诊",
        "回访",
        "追踪",
        "复测",
        "监测",
        "记录",
        "结果出来",
        "发给我",
        "告诉我",
        "明天",
        "24小时",
        "48小时",
        "一周后",
        "下次",
    ]
    return _contains_any(source, terms)


def _looks_like_understanding_or_feasibility_check(text: str) -> bool:
    source = str(text or "")
    terms = [
        "明白",
        "理解",
        "复述",
        "确认一下",
        "有没有哪里不清楚",
        "不清楚",
        "能做到",
        "做得到",
        "做不到",
        "可以做到",
        "方便吗",
        "可以吗",
        "有没有问题",
        "有问题",
        "可执行",
    ]
    return _contains_any(source, terms)


def _actor_response_after_turns(transcript: list[dict[str, Any]], earlier_turns: list[int]) -> list[int]:
    turns: list[int] = []
    if not earlier_turns:
        return turns
    for item in transcript:
        if not _is_patient_or_family_item(item):
            continue
        turn = _event_turn(item)
        if any(previous < turn <= previous + 2 for previous in earlier_turns):
            turns.append(turn)
    return sorted(set(turns))


def _speaker_texts(transcript: list[dict[str, Any]], speakers: set[str]) -> list[str]:
    texts: list[str] = []
    for item in transcript:
        speaker = str(item.get("speaker") or "").lower()
        speaker_category = str(item.get("speaker_category") or "").lower()
        speaker_role = str(item.get("speaker_role") or "").lower()
        relationship = str(item.get("relationship_to_patient") or "").lower()
        if speaker in speakers or speaker_category in speakers or speaker_role in speakers or relationship in speakers:
            text = _normalize_text(str(item.get("text") or ""))
            if text:
                texts.append(text)
    return texts


def _normalize_text(text: str) -> str:
    return " ".join(text.strip().split())


def _workspace_result_leaked_to_actor(transcript: list[dict[str, Any]]) -> bool:
    leak_tokens = ["[Clinical Workspace", "临床工作台 · 医生侧工具结果", "doctor_workspace_result"]
    for item in transcript:
        speaker = str(item.get("speaker") or "").lower()
        if not _is_patient_or_family_item(item):
            continue
        text = str(item.get("text") or "")
        if any(token in text for token in leak_tokens):
            return True
    return False


def _internal_terms_leaked_to_actor(transcript: list[dict[str, Any]]) -> bool:
    for item in transcript:
        speaker = str(item.get("speaker") or "").lower()
        if not _is_patient_or_family_item(item):
            continue
        text = str(item.get("text") or "")
        if _contains_internal_runtime_token(text):
            return True
    return False


def _internal_terms_leaked_to_doctor_visible_events(events: list[dict[str, Any]]) -> bool:
    visible_values = {"doctor_visible", "doctor_visible_tool", "patient_visible"}
    for event in events:
        if str(event.get("visibility") or "") not in visible_values:
            continue
        content = event.get("content")
        try:
            text = json.dumps(content, ensure_ascii=False)
        except TypeError:
            text = str(content)
        if _contains_internal_runtime_token(text):
            return True
    return False


def _contains_internal_runtime_token(text: str) -> bool:
    source = str(text or "")
    internal_tokens = [
        "WorldDirector",
        "Timekeeper",
        "ActorSituation",
        "ProbabilityKernel",
        "TrajectoryEvaluator",
        "benchmark",
        "roleplay",
        "角色扮演",
        "隐藏真相",
        "backstage_probability",
        "掷骰",
        "概率任务",
        "runtime_lite",
        "next_world_need",
    ]
    if any(token in source for token in internal_tokens):
        return True
    # A bare English "hidden" is too broad for doctor-visible workspace JSON:
    # normal access-policy fields can contain words like "withheld" or discuss
    # externally hidden/withheld records without exposing CareLoop hidden truth.
    # Treat it as runtime leakage only when paired with explicit backstage/case
    # truth/state terminology.
    if re.search(r"\bhidden\s+(?:truth|state|case|scenario|world|backstage)\b", source, flags=re.IGNORECASE):
        return True
    if re.search(r"\b(?:truth|state|case|scenario|world|backstage)\s+hidden\b", source, flags=re.IGNORECASE):
        return True
    return _contains_backstage_runtime_context(source)


def _contains_backstage_runtime_context(text: str) -> bool:
    """Detect true runtime/backstage leakage without flagging hospital UI language.

    This deterministic scanner is only a cheap guardrail.  It should catch strong
    CareLoop/runtime phrases such as “运行时后台”, “评分后台”, “后台概率任务” or
    “backstage probability task”, but it must not treat ordinary clinical/product
    language as leakage.  In real Chinese medical conversations, phrases such as
    “医院后台调病案”, “系统后台审核”, “大概率有病理”, or “复发概率不高” are normal
    patient-facing language and should be left to semantic audit if suspicious.
    """

    source = str(text or "")
    if "后台" not in source and "backstage" not in source:
        return False

    # Strong phrases: these are implementation/simulation semantics, not normal
    # hospital information-system language.  Keep them as hard deterministic hits.
    strong_patterns = [
        r"(?:CareLoop|runtime|运行时|框架|评测|评分|模拟|导演|世界模型|隐藏真相|内部流程).{0,12}(?:后台|backstage)",
        r"(?:后台|backstage).{0,12}(?:CareLoop|runtime|运行时|框架|评测|评分|模拟|导演|世界模型|隐藏真相|内部流程)",
        r"(?:后台|backstage).{0,12}(?:概率任务|掷骰|ProbabilityKernel|WorldDirector|TrajectoryEvaluator|ClosureJudge|closure[_ -]?judge)",
        r"(?:概率任务|掷骰|ProbabilityKernel|WorldDirector|TrajectoryEvaluator|ClosureJudge|closure[_ -]?judge).{0,12}(?:后台|backstage)",
        r"backstage[_ -]?probability",
    ]
    if any(re.search(pattern, source, flags=re.IGNORECASE) for pattern in strong_patterns):
        return True

    # A broad “后台 + 概率/probability” rule caused false positives such as
    # “医院从后台调病案” plus “大概率有病理”.  Do not escalate those by code.
    # Semantic visibility audit can still review the trajectory holistically.
    return False


def _is_patient_or_family_item(item: dict[str, Any]) -> bool:
    actor_terms = {"patient", "family", "caregiver", "relative", "家属", "患者"}
    fields = [
        str(item.get("speaker") or "").lower(),
        str(item.get("speaker_category") or "").lower(),
        str(item.get("speaker_role") or "").lower(),
        str(item.get("relationship_to_patient") or "").lower(),
    ]
    family_relationships = {"daughter", "son", "wife", "husband", "spouse", "mother", "father", "parent"}
    return any(value in actor_terms or value in family_relationships for value in fields)


def _is_doctor_item(item: dict[str, Any]) -> bool:
    fields = [
        str(item.get("speaker") or "").lower(),
        str(item.get("speaker_category") or "").lower(),
        str(item.get("speaker_role") or "").lower(),
        str(item.get("event_type") or "").lower(),
    ]
    return any(value in {"doctor", "ai_doctor", "clinician", "医生"} for value in fields) or "doctor_message" in fields


def _all_time_advances_zero(events: list[dict[str, Any]]) -> bool:
    advances = [event for event in events if event.get("event_type") == "time_advance"]
    if not advances:
        return False
    for event in advances:
        content = event.get("content") if isinstance(event.get("content"), dict) else {}
        try:
            if float(content.get("elapsed_minutes") or 0) > 0:
                return False
        except (TypeError, ValueError):
            return False
    return True


def _status_from_flags(flags: list[str]) -> str:
    hard = {
        "runtime_error",
        "workspace_result_leaked_into_patient_or_family_chat",
        "internal_runtime_terms_leaked_to_actor",
        "internal_runtime_terms_leaked_to_doctor_visible_content",
        "closed_without_committed_world_progression",
        "final_evaluation_failed",
    }
    if any(flag in hard for flag in flags):
        return "fail"
    if flags:
        return "review"
    return "pass"
