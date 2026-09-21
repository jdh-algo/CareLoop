from __future__ import annotations

"""Minimal executable CareLoop_FCCT-1 runtime_lite chain."""

from copy import deepcopy
from dataclasses import dataclass, field, replace
from hashlib import sha256
import json
from pathlib import Path
import re
import time
from typing import Any, Callable, Mapping, Sequence

from careloop.runtime_lite.care_system import LiteCareSystem
from careloop.runtime_lite.closure_governance import build_closure_governance_advisory
from careloop.runtime_lite.case_loader import LiteCase, load_lite_case
from careloop.runtime_lite.evaluation_dimensions import evaluation_dimension_catalog_for_prompt
from careloop.runtime_lite.json_utils import extract_json_array, extract_json_object
from careloop.runtime_lite.interface_observation import attach_interface_observation, interface_observation_from_events
from careloop.runtime_lite.llm import LiteLLMClient, ScriptedLiteLLMClient
from careloop.runtime_lite.long_context_memory import LongContextConfig, LongContextMemoryStore
from careloop.runtime_lite.models import (
    ActorSituation,
    ActorUtterance,
    ClosureAssessmentLite,
    DirectorBeat,
    DoctorOperationRequest,
    LiteEventCandidate,
    LiteProbabilityTask,
)
from careloop.runtime_lite.prompt_loader import load_prompt
from careloop.runtime_lite.probability import decide_probability_task
from careloop.runtime_lite.quality import analyze_lite_trajectory
from careloop.runtime_lite.trajectory import LiteTrajectory
from careloop.runtime_lite.visibility_boundary import (
    audit_doctor_visible_provenance,
    redact_for_actor,
    visibility_provenance,
)
from careloop.runtime_lite.workspace import LiteClinicalWorkspace


@dataclass
class LiteRuntimeConfig:
    max_turns: int = 10
    run_id: str = "runtime_lite_run"
    seed_scope: str = "runtime_lite"
    doctor_temperature: float = 0.2
    director_temperature: float = 0.5
    actor_temperature: float = 0.8
    closure_temperature: float = 0.2
    stop_on_soft_closed: bool = False
    evaluate_at_end: bool = True
    enable_clinical_memory_steward: bool = True
    clinical_memory_steward_mode: str = "every_turn"
    clinical_memory_steward_max_interval_turns: int = 3
    # Eventful mode should not call the ClinicalMemorySteward LLM on every
    # routine turn.  Raw ledgers still persist every turn; this is only a
    # minimum spacing for expensive semantic memory refreshes.
    clinical_memory_eventful_min_interval_turns: int = 3
    clinical_memory_urgent_update_enabled: bool = True
    # 1000+ turn long-context persistence. These switches only control
    # evidence storage, compression layers and audit sidecars; clinical
    # interpretation remains LLM-led.
    long_context_1000_plus: bool = False
    long_context_output_dir: str = ""
    episode_turn_span: int = 20
    chapter_episode_span: int = 5
    working_memory_mode: str = "eventful"
    working_memory_forced_interval_turns: int = 3
    memory_integrity_audit_interval_turns: int = 50
    fragment_eval_interval_turns: int = 100
    append_only_ledger: bool = True
    externalize_memory_snapshots: bool = True
    recent_raw_turn_window: int = 6
    # Long-context guardrails.  These are infrastructure budgets, not clinical
    # rules: LLM subagents still decide what matters, while runtime prevents a
    # 100-300 turn care trajectory from re-sending unbounded ledgers.
    clinical_memory_prompt_char_budget: int = 10000
    doctor_self_context_prompt_char_budget: int = 9000
    doctor_memory_prompt_char_budget: int = 9000
    actor_memory_prompt_char_budget: int = 7000
    care_system_prompt_char_budget: int = 9000
    workspace_prompt_char_budget: int = 11000
    trajectory_prompt_char_budget: int = 6500
    source_evidence_prompt_char_budget: int = 4500
    runtime_quality_prompt_char_budget: int = 4500
    case_context_prompt_char_budget: int = 9000
    memory_prompt_text_limit: int = 650
    memory_prompt_list_limit: int = 12
    memory_kernel_item_limit: int = 14
    memory_problem_item_limit: int = 8
    memory_responsibility_item_limit: int = 16
    memory_constraint_item_limit: int = 10
    memory_thread_item_limit: int = 10
    memory_resolved_thread_item_limit: int = 6
    memory_uncertainty_item_limit: int = 10
    care_system_prompt_pending_limit: int = 20
    # Empty LLM content is an infrastructure boundary condition, not a
    # clinical judgement.  The runtime retries it for nodes where a blank answer
    # would otherwise contaminate the benchmark, then preserves any remaining
    # non-response as evidence instead of silently inventing a reply.
    doctor_empty_response_retries: int = 2
    doctor_empty_response_rescue_retries: int = 1
    stop_on_unrescued_doctor_non_response: bool = True
    # Experimental compatibility switch only.  Formal CareLoop runs should keep
    # this off: the tested doctor, not CareLoop, decides whether and how to
    # compress long context (free text, own notes, transcript review, or no
    # explicit memory at all).
    enable_doctor_self_context: bool = False
    doctor_self_context_empty_retries: int = 1
    doctor_same_turn_tool_round_safety_cap: int = 4
    # FCCT-2 dual-route protocol: the tested doctor chooses either patient/family-visible
    # care dialogue or a private clinical workspace using a tiny envelope:
    # {"to":"care|workspace","content":"..."}.  This remains lightweight;
    # malformed/non-JSON output is tolerated as legacy care text and audited.
    enable_doctor_dual_route: bool = True
    doctor_workspace_same_turn_cap: int = 4
    enable_patient_facing_boundary_check: bool = True
    enable_actor_cooperation_sampler: bool = True
    evaluator_empty_response_retries: int = 2
    evaluator_repair_empty_response_retries: int = 1
    enable_longitudinal_stability_horizon: bool = True
    enable_diagnostic_service_simulator: bool = True
    enable_anti_retcon_world_state_guard: bool = True
    enable_clinical_contingency_sampler: bool = True
    enable_mainline_balance_judge: bool = True
    enable_actor_realism_degrader: bool = True
    # Formal FCCT-2 runtime advisories are deliberately opt-in.  They ask
    # backstage LLMs for semantic guidance, but never impose deterministic
    # clinical pathway gates or patient-visible keyword rules.
    enable_actor_realism_controller_v2: bool = False
    enable_friction_coverage_planner: bool = False
    enable_closure_thread_advisory: bool = False
    enable_receipt_lifecycle_advisory: bool = False
    enable_episode_governance_closure_advisory: bool = False
    mainline_balance_min_turns: int = 18
    mainline_balance_check_interval_turns: int = 6
    actor_realism_check_interval_turns: int = 4
    actor_realism_v2_check_interval_turns: int = 4
    friction_coverage_check_interval_turns: int = 6
    closure_thread_advisory_interval_turns: int = 4
    receipt_lifecycle_advisory_interval_turns: int = 4
    emit_stage_checkpoints: bool = False
    checkpoint_callback: Callable[["LiteRunResult"], None] | None = None
    runtime_status_output_dir: str = ""


@dataclass
class LiteRunResult:
    case_id: str
    run_id: str
    closure: ClosureAssessmentLite
    trajectory: LiteTrajectory
    turns_completed: int
    metadata: dict[str, Any] = field(default_factory=dict)
    evaluation: dict[str, Any] = field(default_factory=dict)
    shadow_high_impact_event_lifecycle: dict[str, Any] = field(default_factory=dict)
    shadow_action_receipt_tracker: dict[str, Any] = field(default_factory=dict)
    shadow_critical_safety_thread_summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "run_id": self.run_id,
            "turns_completed": self.turns_completed,
            "closure": self.closure.to_dict(),
            "trajectory": self.trajectory.to_dict(),
            "evaluation": self.evaluation,
            "metadata": self.metadata,
            "shadow_high_impact_event_lifecycle": self.shadow_high_impact_event_lifecycle,
            "shadow_action_receipt_tracker": self.shadow_action_receipt_tracker,
            "shadow_critical_safety_thread_summary": self.shadow_critical_safety_thread_summary,
        }


class LiteCareLoopRunner:
    """Thin simulator runtime.

    The chain is:
    Doctor -> WorldDirector -> ProbabilityKernel/Timekeeper ->
    ActorSituationMessenger -> Patient/Family Actor -> ClosureJudge.
    """

    def __init__(
        self,
        *,
        case: LiteCase | str,
        simulator_llm: LiteLLMClient | None = None,
        doctor_llm: LiteLLMClient | None = None,
        config: LiteRuntimeConfig | None = None,
    ) -> None:
        self.case = load_lite_case(case) if isinstance(case, str) else case
        self.simulator_llm = simulator_llm or ScriptedLiteLLMClient()
        self.doctor_llm = doctor_llm or self.simulator_llm
        self.config = config or LiteRuntimeConfig()
        self.trajectory = LiteTrajectory(case_id=self.case.case_id, run_id=self.config.run_id)
        self.workspace = LiteClinicalWorkspace(self.case)
        self.care_system = LiteCareSystem(self.case)
        self.current_sim_time = "T+0min"
        self.living_state_memory: list[dict[str, Any]] = []
        self.clinical_memory_snapshots: list[dict[str, Any]] = []
        self.doctor_owned_notes: list[dict[str, Any]] = []
        self.doctor_self_context: dict[str, Any] = {}
        self.stability_horizon_plan: dict[str, Any] = {}
        self.diagnostic_service_ledger: list[dict[str, Any]] = []
        self.anti_retcon_guard_ledger: list[dict[str, Any]] = []
        self.clinical_contingency_ledger: list[dict[str, Any]] = []
        self.mainline_balance_ledger: list[dict[str, Any]] = []
        self.actor_realism_ledger: list[dict[str, Any]] = []
        self.actor_realism_v2_ledger: list[dict[str, Any]] = []
        self.actor_cooperation_ledger: list[dict[str, Any]] = []
        self.critical_safety_event_ledger: list[dict[str, Any]] = []
        self.friction_coverage_ledger: list[dict[str, Any]] = []
        self.closure_thread_advisory_ledger: list[dict[str, Any]] = []
        self.receipt_lifecycle_advisory_ledger: list[dict[str, Any]] = []
        self.episode_governance_closure_advisory_ledger: list[dict[str, Any]] = []
        self._case_evaluation_material_cache: dict[str, Any] | None = None
        self._resume_start_turn = 1
        self._resume_latest_actor_text: str | None = None
        self._resume_initial_closure: ClosureAssessmentLite | None = None
        self._resume_source_metadata: dict[str, Any] = {}
        self.shadow_high_impact_event_lifecycle: dict[str, Any] = {}
        self.shadow_action_receipt_tracker: dict[str, Any] = {}
        self.shadow_critical_safety_thread_summary: dict[str, Any] = {}
        self.long_context_store: LongContextMemoryStore | None = self._build_long_context_store()
        self._runtime_phase_last_payload: dict[str, Any] = {}

    def _runtime_status_output_dir(self) -> Path | None:
        output_dir = str(getattr(self.config, "runtime_status_output_dir", "") or "").strip()
        if not output_dir:
            return None
        return Path(output_dir)

    def _utc_timestamp(self) -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def _write_runtime_phase(
        self,
        *,
        phase: str,
        turn: int | None = None,
        current_stage: str | None = None,
        closure: ClosureAssessmentLite | None = None,
        final_file_written: bool = False,
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        """Write a non-sensitive runtime progress sidecar for launcher heartbeat.

        This is observability only: it must not alter trajectory semantics and it
        must never include prompts, model responses, API keys, or provider payloads.
        """

        output_dir = self._runtime_status_output_dir()
        if output_dir is None:
            return
        try:
            turns_completed = max(0, int(turn or 0))
        except (TypeError, ValueError):
            turns_completed = 0
        payload: dict[str, Any] = {
            "protocol": "careloop.runtime_phase.v1",
            "updated_at": self._utc_timestamp(),
            "phase": str(phase or "running"),
            "current_stage": str(current_stage or phase or "running"),
            "current_turn": turns_completed,
            "turns_completed": turns_completed,
            "max_turns": int(getattr(self.config, "max_turns", 0) or 0),
            "case_id": self.case.case_id,
            "run_id": self.config.run_id,
            "final_file_written": bool(final_file_written),
            "safe_sidecar": True,
            "redaction_policy": "no_prompt_no_response_no_api_key_no_authorization_header",
        }
        if isinstance(closure, ClosureAssessmentLite):
            payload["closure_status"] = closure.status
            payload["closure_terminal"] = bool(closure.is_terminal)
        if extra:
            safe_extra = {k: v for k, v in dict(extra).items() if k not in {"prompt", "system", "user", "response", "api_key", "authorization"}}
            payload.update(safe_extra)
        self._runtime_phase_last_payload = dict(payload)
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            path = output_dir / "runtime_phase.json"
            tmp_path = path.with_name(f".{path.name}.tmp")
            tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp_path.replace(path)
        except Exception:
            return

    def _build_long_context_store(self) -> LongContextMemoryStore | None:
        if not bool(getattr(self.config, "long_context_1000_plus", False)):
            return None
        output_dir = str(getattr(self.config, "long_context_output_dir", "") or "").strip()
        if not output_dir:
            return None
        return LongContextMemoryStore(
            output_dir=output_dir,
            case_id=self.case.case_id,
            run_id=self.config.run_id,
            config=LongContextConfig(
                enabled=True,
                episode_turn_span=max(1, int(getattr(self.config, "episode_turn_span", 20) or 20)),
                chapter_episode_span=max(1, int(getattr(self.config, "chapter_episode_span", 5) or 5)),
                working_memory_mode=str(getattr(self.config, "working_memory_mode", "eventful") or "eventful"),
                working_memory_forced_interval_turns=max(1, int(getattr(self.config, "working_memory_forced_interval_turns", 3) or 3)),
                memory_integrity_audit_interval_turns=max(1, int(getattr(self.config, "memory_integrity_audit_interval_turns", 50) or 50)),
                fragment_eval_interval_turns=max(1, int(getattr(self.config, "fragment_eval_interval_turns", 100) or 100)),
                append_only_ledger=bool(getattr(self.config, "append_only_ledger", True)),
                externalize_memory_snapshots=bool(getattr(self.config, "externalize_memory_snapshots", True)),
            ),
        )

    def _sync_long_context(
        self,
        turn: int,
        *,
        closure: ClosureAssessmentLite | None = None,
        latest_snapshot: Mapping[str, Any] | None = None,
        final_evaluation: Mapping[str, Any] | None = None,
        reason: str = "sync",
        rollup: bool = False,
        force_audit: bool = False,
    ) -> dict[str, Any]:
        if self.long_context_store is None:
            return {}
        closure_payload = closure.to_dict() if isinstance(closure, ClosureAssessmentLite) else {}
        snapshot_for_store = latest_snapshot
        if snapshot_for_store is None and not rollup and not force_audit and final_evaluation is None:
            # Plain checkpoints should append the raw ledger cheaply without
            # re-rolling hierarchical memory from the latest prior snapshot.
            # ClinicalMemorySteward calls and final/max-turn syncs pass either
            # an explicit snapshot or rollup=True.
            snapshot_for_store = {}
        status = self.long_context_store.sync_from_trajectory(
            self.trajectory,
            turn=turn,
            closure=closure_payload,
            latest_snapshot=snapshot_for_store,
            reason=reason,
            rollup=rollup,
            force_audit=force_audit,
        )
        if final_evaluation is not None:
            self.long_context_store.build_final_evidence_pack(
                turn=turn,
                closure=closure_payload,
                evaluation_mode=str(final_evaluation.get("evaluation_mode") or ""),
                evaluation=final_evaluation,
                reason=reason or "final_evaluation",
            )
            status = self.long_context_store.write_status(
                turn=turn,
                reason=f"{reason}:final_evidence_pack",
                closure=closure_payload,
                integrity_audit={},
            )
        return status

    def _long_context_status_metadata(self) -> dict[str, Any]:
        if self.long_context_store is None:
            return {
                "enabled": bool(getattr(self.config, "long_context_1000_plus", False)),
                "available": False,
                "reason": "disabled_or_missing_output_dir",
            }
        return {
            "enabled": True,
            "available": True,
            "output_dir": str(self.long_context_store.output_dir),
            "manifest": "memory/memory_store_manifest.json",
            "status": "memory/long_context_status.json",
            "final_evidence_pack": "memory/final_long_context_evidence_pack.json",
            "doctor_visibility_boundary": "CareLoop long-context sidecars are backstage only and are never auto-injected into tested doctor prompts.",
        }

    def _long_context_evidence_pack_for_prompt(
        self, turn: int, closure: ClosureAssessmentLite, evaluation_mode: str
    ) -> dict[str, Any]:
        if self.long_context_store is None:
            return {
                "available": False,
                "reason": "long_context_1000_plus_disabled_or_no_output_dir",
            }
        return self.long_context_store.prompt_evidence_pack(
            turn=turn,
            closure=closure.to_dict(),
            evaluation_mode=evaluation_mode,
        )

    def run(self) -> LiteRunResult:
        self._write_runtime_phase(phase="starting", turn=max(0, self._resume_start_turn - 1), current_stage="run_start")
        if self._resume_start_turn <= 1:
            self._record_opening()
            closure = ClosureAssessmentLite(status="open", rationale="not assessed yet")
            self._sync_long_context(0, closure=closure, reason="opening", rollup=False)
            latest_actor_text = self.case.initial_message
            turns_completed = 0
            start_turn = 1
        else:
            closure = self._resume_initial_closure or ClosureAssessmentLite(status="open", rationale="resumed from checkpoint")
            latest_actor_text = self._resume_latest_actor_text or self.case.initial_message
            turns_completed = self._resume_start_turn - 1
            start_turn = self._resume_start_turn
            self.trajectory.add_event(
                turn=turns_completed,
                actor="RuntimeLite",
                event_type="checkpoint_resume",
                content={
                    "protocol": "careloop.runtime_lite.checkpoint_resume.v1",
                    "resume_start_turn": start_turn,
                    "target_max_turns": self.config.max_turns,
                    "source": self._resume_source_metadata,
                    "principle": (
                        "This trajectory is a continuation from a runtime_lite checkpoint. "
                        "Max-turns is an absolute run horizon, not additional turns."
                    ),
                },
                sim_time=self.current_sim_time,
                visibility="internal_audit",
            )
            self._sync_long_context(turns_completed, closure=closure, reason="checkpoint_resume", rollup=False)
        self._write_runtime_phase(phase="running", turn=start_turn - 1, current_stage="before_turn_loop", closure=closure)
        for turn in range(start_turn, self.config.max_turns + 1):
            turns_completed = turn
            self._write_runtime_phase(phase="turn_running", turn=turn, current_stage="turn_start", closure=closure)
            doctor_text = self._call_doctor(turn, latest_actor_text)
            if not doctor_text.strip() and self.config.stop_on_unrescued_doctor_non_response:
                closure = self._runtime_stop_for_doctor_non_response(turn)
                self._emit_checkpoint(turn, closure, trigger="after_unrescued_doctor_non_response")
                self._maybe_update_clinical_memory(turn, trigger="after_unrescued_doctor_non_response")
                self._emit_checkpoint(turn, closure, trigger="after_unrescued_doctor_non_response_memory")
                break
            self._emit_stage_checkpoint(turn, "stage_after_doctor_message", stage="doctor_message_ready")
            mainline_balance = self._maybe_call_mainline_balance_judge(turn, doctor_text)
            self._maybe_call_friction_coverage_planner(turn, doctor_text)
            beat = self._call_world_director(turn, doctor_text)
            self._emit_stage_checkpoint(turn, "stage_after_world_director", stage="world_director_ready")
            anti_retcon_review = self._call_anti_retcon_world_state_guard(turn, doctor_text, beat)
            beat = self._apply_anti_retcon_review_to_beat(turn, beat, anti_retcon_review)
            contingency_packet = self._call_clinical_contingency_event_sampler(turn, doctor_text, beat)
            beat = self._merge_candidate_events_into_beat(beat, contingency_packet.get("candidate_events") or [])
            diagnostic_packet = self._call_diagnostic_service_simulator(turn, doctor_text, beat)
            beat = self._merge_candidate_events_into_beat(beat, diagnostic_packet.get("candidate_events") or diagnostic_packet.get("service_events") or [])
            probability_records = self._resolve_probability_tasks(turn, beat)
            world_event_resolution = self._resolve_world_events_after_probability(turn, beat, probability_records)
            world_event_resolution = self._merge_diagnostic_service_packet_into_world_resolution(turn, world_event_resolution, diagnostic_packet)
            self._record_critical_safety_events(turn, beat, world_event_resolution, source="world_director")
            self._emit_stage_checkpoint(
                turn,
                "stage_after_world_event_resolution",
                stage="world_event_resolution_ready",
                probability_record_count=len(probability_records),
                committed_world_event_count=len(world_event_resolution.get("committed_world_events") or []),
            )
            time_summary = self._call_timekeeper(turn, doctor_text, beat, probability_records, world_event_resolution)
            self._emit_stage_checkpoint(turn, "stage_after_timekeeper", stage="timekeeper_ready")
            self._record_living_state_update(turn, beat)
            self._apply_workspace_updates_from_world_events(turn, world_event_resolution)
            self._apply_care_system_updates_from_world_events(turn, world_event_resolution)
            self._publish_doctor_visible_world_notifications(turn, world_event_resolution)
            cut_closure = self._handle_director_cut_request(turn, beat, world_event_resolution)
            if cut_closure is not None:
                closure = cut_closure
                self._emit_checkpoint(turn, closure, trigger="before_memory_after_director_cut_closure")
                self._maybe_update_clinical_memory(turn, trigger="after_director_cut_closure")
                self._emit_checkpoint(turn, closure, trigger="after_director_cut_closure")
                if self._should_stop_for_closure(closure):
                    break
            self._maybe_call_actor_realism_degrader(turn, doctor_text, beat, world_event_resolution)
            self._maybe_call_actor_realism_controller_v2(turn, doctor_text, beat, world_event_resolution)
            situation = self._call_actor_situation_messenger(turn, doctor_text, beat, time_summary, probability_records, world_event_resolution)
            self._emit_stage_checkpoint(turn, "stage_after_actor_situation_messenger", stage="actor_situation_ready")
            utterance = self._call_actor(turn, doctor_text, situation)
            latest_actor_text = utterance.text
            self._emit_stage_checkpoint(turn, "stage_after_actor_reply", stage="actor_reply_ready")
            self._maybe_call_receipt_lifecycle_advisory(turn, trigger="before_closure_judge")
            self._maybe_call_closure_thread_advisory(turn, trigger="before_closure_judge")
            closure = self._call_closure_judge(turn, trigger="routine_after_actor_reply")
            closure = self._convert_clinical_unsafe_closure_to_open(turn, closure, trigger="routine_after_actor_reply")
            closure = self._apply_stability_horizon_if_needed(turn, closure)
            closure = self._normalize_closure_kind_for_p03h(closure)
            closure = self._apply_case_level_terminality_gate(turn, closure, trigger="routine_after_actor_reply")
            self._emit_checkpoint(turn, closure, trigger="before_memory_after_closure_judge")
            self._maybe_update_clinical_memory(turn, trigger="after_closure_judge")
            self._emit_checkpoint(turn, closure, trigger="after_closure_judge")
            if self._should_stop_for_closure(closure):
                break
        if self.config.max_turns and turns_completed >= self.config.max_turns and not closure.is_terminal:
            self._write_runtime_phase(phase="max_turns_reached", turn=turns_completed, current_stage="max_turns_reached", closure=closure)
            self.trajectory.add_event(
                turn=turns_completed,
                actor="RuntimeLite",
                event_type="max_turns_reached",
                content={
                    "max_turns": self.config.max_turns,
                    "closure_status_at_limit": closure.status,
                    "principle": "Max-turns is an evaluation/runtime limit, not a medical closure condition.",
                    "if_continued_next_focus": closure.if_continued_next_focus,
                },
                sim_time=self.current_sim_time,
                visibility="evaluator_visible",
            )
            self._sync_long_context(turns_completed, closure=closure, reason="max_turns_reached", rollup=True)
        closure = self._normalize_closure_kind_for_p03h(closure)
        shadow_high_impact_event_lifecycle, shadow_action_receipt_tracker, shadow_critical_safety_thread_summary = self._finalize_p03h_shadow_sidecars(turns_completed, closure)
        evaluation: dict[str, Any] = {}
        if self.config.evaluate_at_end:
            self._write_runtime_phase(phase="final_audit", turn=turns_completed, current_stage="final_evaluator", closure=closure)
            try:
                evaluation = self._call_final_evaluator(turns_completed, closure)
            except Exception as exc:
                evaluation = self._final_evaluation_failure_from_exception(turns_completed, closure, exc)
        self._write_runtime_phase(phase="finalizing", turn=turns_completed, current_stage="long_context_final_sync", closure=closure, extra={"evaluation_overall": evaluation.get("overall") if isinstance(evaluation, Mapping) else None})
        self._sync_long_context(
            turns_completed,
            closure=closure,
            final_evaluation=evaluation if isinstance(evaluation, Mapping) else {},
            reason="run_complete",
            rollup=True,
            force_audit=True,
        )
        self._write_runtime_phase(phase="final_ready_for_write", turn=turns_completed, current_stage="runner_return", closure=closure, extra={"evaluation_overall": evaluation.get("overall") if isinstance(evaluation, Mapping) else None})
        return LiteRunResult(
            case_id=self.case.case_id,
            run_id=self.config.run_id,
            closure=closure,
            trajectory=self.trajectory,
            turns_completed=turns_completed,
            evaluation=evaluation,
            metadata={
                "max_turns": self.config.max_turns,
                "runtime": "runtime_lite",
                "principle": "thin rules, LLM-native soft judgement, hard boundaries only",
                "final_evaluation_mode": evaluation.get("evaluation_mode") if isinstance(evaluation, dict) else "disabled",
                "resumed_from_checkpoint": bool(self._resume_source_metadata),
                "resume_source": self._resume_source_metadata,
                "runtime_advisory": self._runtime_advisory_metadata(),
                "long_context": self._long_context_status_metadata(),
            },
            shadow_high_impact_event_lifecycle=shadow_high_impact_event_lifecycle,
            shadow_action_receipt_tracker=shadow_action_receipt_tracker,
            shadow_critical_safety_thread_summary=shadow_critical_safety_thread_summary,
        )

    def resume_from_payload(self, payload: Mapping[str, Any], *, source_path: str = "") -> None:
        """Restore enough runtime state to continue a checkpointed trajectory.

        This is intentionally a state replay, not a summary injection.  The
        tested doctor should continue facing the same trajectory, workspace,
        care-system receipts, living-state continuity and clinical memory that
        existed at the checkpoint.  If a future checkpoint contains a dedicated
        resume_state, this method can prefer it; for older checkpoints it
        reconstructs state from the append-only trajectory events.
        """

        trajectory_payload = payload.get("trajectory") if isinstance(payload.get("trajectory"), Mapping) else {}
        events = [dict(item) for item in (trajectory_payload.get("events") or []) if isinstance(item, Mapping)]
        transcript = [dict(item) for item in (trajectory_payload.get("transcript") or []) if isinstance(item, Mapping)]
        llm_calls = [dict(item) for item in (trajectory_payload.get("llm_calls") or []) if isinstance(item, Mapping)]
        probability_ledger = [dict(item) for item in (trajectory_payload.get("probability_ledger") or []) if isinstance(item, Mapping)]
        previous_run_id = str(trajectory_payload.get("run_id") or payload.get("run_id") or self.config.run_id)
        self.trajectory = LiteTrajectory(
            case_id=str(trajectory_payload.get("case_id") or payload.get("case_id") or self.case.case_id),
            run_id=self.config.run_id or previous_run_id,
            events=events,
            transcript=transcript,
            llm_calls=llm_calls,
            probability_ledger=probability_ledger,
            created_at=str(trajectory_payload.get("created_at") or ""),
        )
        self.current_sim_time = self._latest_nonempty_sim_time(events) or "T+0min"
        completed_turn = self._safe_int(payload.get("turns_completed")) or self._last_completed_turn()
        self._resume_start_turn = max(1, int(completed_turn) + 1)
        self._resume_latest_actor_text = self._latest_actor_text_from_transcript(transcript)
        self._resume_initial_closure = ClosureAssessmentLite.from_mapping(payload.get("closure") or {"status": "open"})
        self._resume_source_metadata = {
            "source_path": source_path,
            "previous_run_id": previous_run_id,
            "turns_completed": completed_turn,
            "checkpoint_trigger": (payload.get("metadata") or {}).get("checkpoint_trigger") if isinstance(payload.get("metadata"), Mapping) else "",
        }
        self._replay_runtime_state_from_events(events)

    def _replay_runtime_state_from_events(self, events: list[dict[str, Any]]) -> None:
        self.workspace = LiteClinicalWorkspace(self.case)
        self.care_system = LiteCareSystem(self.case)
        self.living_state_memory = []
        self.clinical_memory_snapshots = []
        self.doctor_owned_notes = []
        self.doctor_self_context = {}
        self.stability_horizon_plan = {}
        self.diagnostic_service_ledger = []
        self.anti_retcon_guard_ledger = []
        self.clinical_contingency_ledger = []
        self.mainline_balance_ledger = []
        self.actor_realism_ledger = []
        self.actor_realism_v2_ledger = []
        self.critical_safety_event_ledger = []
        self.friction_coverage_ledger = []
        self.closure_thread_advisory_ledger = []
        self.receipt_lifecycle_advisory_ledger = []
        for event in events:
            event_type = str(event.get("event_type") or "")
            content = event.get("content") if isinstance(event.get("content"), Mapping) else {}
            if event_type == "doctor_care_system_receipt" and isinstance(content, Mapping):
                receipt_id = str(content.get("receipt_id") or "").strip()
                if receipt_id and not any(item.get("receipt_id") == receipt_id for item in self.care_system.receipts):
                    self.care_system.receipts.append(deepcopy(dict(content)))
            elif event_type == "world_event_resolution" and isinstance(content, Mapping):
                committed = [dict(item) for item in (content.get("committed_world_events") or []) if isinstance(item, Mapping)]
                if committed:
                    self.workspace.apply_world_events(committed)
                    updates = self.care_system.apply_world_events(
                        committed,
                        current_sim_time=str(event.get("sim_time") or self.current_sim_time),
                        turn=self._safe_int(event.get("turn")) or 0,
                    )
                    self.workspace.ingest_care_system_updates(updates)
            elif event_type == "living_state_update" and isinstance(content, Mapping):
                self.living_state_memory.append(deepcopy(dict(content)))
            elif event_type == "clinical_memory_snapshot" and isinstance(content, Mapping):
                self.clinical_memory_snapshots.append(deepcopy(dict(content)))
            elif event_type == "doctor_self_context_update" and isinstance(content, Mapping):
                context = content.get("doctor_self_context")
                if isinstance(context, Mapping):
                    self.doctor_self_context = deepcopy(dict(context))
            elif event_type == "doctor_owned_note_saved" and isinstance(content, Mapping):
                note = content.get("saved_note")
                if isinstance(note, Mapping):
                    self.doctor_owned_notes.append(deepcopy(dict(note)))
            elif event_type == "stability_horizon_plan" and isinstance(content, Mapping):
                self.stability_horizon_plan = deepcopy(dict(content))
            elif event_type == "diagnostic_service_simulation" and isinstance(content, Mapping):
                self.diagnostic_service_ledger.append(deepcopy(dict(content)))
            elif event_type == "anti_retcon_world_state_guard" and isinstance(content, Mapping):
                self.anti_retcon_guard_ledger.append(deepcopy(dict(content)))
            elif event_type == "clinical_contingency_sampling" and isinstance(content, Mapping):
                self.clinical_contingency_ledger.append(deepcopy(dict(content)))
            elif event_type == "mainline_balance_judgement" and isinstance(content, Mapping):
                self.mainline_balance_ledger.append(deepcopy(dict(content)))
            elif event_type == "actor_realism_judgement" and isinstance(content, Mapping):
                self.actor_realism_ledger.append(deepcopy(dict(content)))
            elif event_type == "actor_realism_v2_advisory" and isinstance(content, Mapping):
                self.actor_realism_v2_ledger.append(deepcopy(dict(content)))
            elif event_type == "critical_safety_event" and isinstance(content, Mapping):
                self.critical_safety_event_ledger.append(deepcopy(dict(content)))
            elif event_type == "friction_coverage_advisory" and isinstance(content, Mapping):
                self.friction_coverage_ledger.append(deepcopy(dict(content)))
            elif event_type == "closure_thread_advisory" and isinstance(content, Mapping):
                self.closure_thread_advisory_ledger.append(deepcopy(dict(content)))
            elif event_type == "receipt_lifecycle_advisory" and isinstance(content, Mapping):
                self.receipt_lifecycle_advisory_ledger.append(deepcopy(dict(content)))
            elif event_type == "episode_governance_closure_advisory" and isinstance(content, Mapping):
                self.episode_governance_closure_advisory_ledger.append(deepcopy(dict(content)))

    def _runtime_advisory_metadata(self) -> dict[str, Any]:
        return {
            "mode": "llm_led_soft_advisory",
            "principle": (
                "Runtime advisory nodes may suggest realism, friction, receipt and closure attention, "
                "but they do not create hard clinical pathway gates, keyword bans, or patient-visible runtime language."
            ),
            "enabled": {
                "actor_realism_controller_v2": bool(self.config.enable_actor_realism_controller_v2),
                "friction_coverage_planner": bool(self.config.enable_friction_coverage_planner),
                "closure_thread_advisory": bool(self.config.enable_closure_thread_advisory),
                "receipt_lifecycle_advisory": bool(self.config.enable_receipt_lifecycle_advisory),
                "episode_governance_closure_advisory": bool(getattr(self.config, "enable_episode_governance_closure_advisory", False)),
                "doctor_dual_route": bool(getattr(self.config, "enable_doctor_dual_route", True)),
                "patient_facing_boundary_check": bool(getattr(self.config, "enable_patient_facing_boundary_check", True)),
                "actor_cooperation_sampler": bool(getattr(self.config, "enable_actor_cooperation_sampler", True)),
            },
            "ledger_counts": {
                "actor_realism_v1": len(self.actor_realism_ledger),
                "actor_realism_v2": len(self.actor_realism_v2_ledger),
                "actor_cooperation": len(self.actor_cooperation_ledger),
                "friction_coverage": len(self.friction_coverage_ledger),
                "closure_thread_advisory": len(self.closure_thread_advisory_ledger),
                "receipt_lifecycle_advisory": len(self.receipt_lifecycle_advisory_ledger),
                "episode_governance_closure_advisory": len(self.episode_governance_closure_advisory_ledger),
            },
        }

    def _latest_actor_text_from_transcript(self, transcript: list[dict[str, Any]]) -> str:
        for item in reversed(transcript):
            event_type = str(item.get("event_type") or "")
            category = str(item.get("speaker_category") or "")
            if event_type in {"patient_message", "family_message"} or category in {"patient", "family", "caregiver"}:
                return str(item.get("text") or "")
        return self.case.initial_message

    def _latest_nonempty_sim_time(self, events: list[dict[str, Any]]) -> str:
        for event in reversed(events):
            sim_time = str(event.get("sim_time") or "").strip()
            if sim_time:
                return sim_time
        return ""

    def _final_evaluation_failure_from_exception(
        self,
        turn: int,
        closure: ClosureAssessmentLite,
        exc: Exception,
    ) -> dict[str, Any]:
        """Preserve a completed/interrupted trajectory when final evaluation fails.

        The post-hoc evaluator is part of the benchmark report, but it is not the
        simulated medical world itself.  A timeout/parse/provider failure in this
        final backstage step should be auditable as evaluator failure, not erase
        or reclassify the whole trajectory as a runtime failure.
        """

        failure = {
            "evaluation_mode": self._final_evaluation_mode(closure),
            "overall": "evaluation_failed",
            "summary": "Final trajectory evaluation failed after the simulated trajectory had reached the evaluation stage.",
            "simulation_validity": {
                "status": "not_assessed",
                "rationale": "The trajectory is preserved, but the final evaluator failed and did not produce a clinical performance judgement.",
                "limitations": ["final_evaluator_exception"],
            },
            "infrastructure_integrity": {
                "status": "evaluation_failed",
                "failure_type": type(exc).__name__,
                "requires_rerun_or_repair": True,
            },
            "critical_failures": [],
            "evidence": [],
            "raw_evaluator_output": "",
        }
        self.trajectory.add_event(
            turn=turn,
            actor="RuntimeLite",
            event_type="final_evaluation_failed",
            content={
                "failure_type": type(exc).__name__,
                "failure_preview": self._compact_text(str(exc) or type(exc).__name__, limit=500),
                "principle": "Final evaluator failure is preserved as report infrastructure failure; the trajectory remains available for later analysis.",
            },
            sim_time=self.current_sim_time,
            visibility="evaluator_visible",
        )
        failure = self._attach_non_weighted_interface_observations(failure, turn=turn)
        return failure

    def _emit_checkpoint(self, turn: int, closure: ClosureAssessmentLite, *, trigger: str) -> None:
        self._write_runtime_phase(phase="checkpointing", turn=turn, current_stage=trigger, closure=closure)
        self._sync_long_context(
            turn,
            closure=closure,
            reason=f"checkpoint:{trigger}",
            rollup=False,
            force_audit=False,
        )
        callback = self.config.checkpoint_callback
        if callback is None:
            return
        result = LiteRunResult(
            case_id=self.case.case_id,
            run_id=self.config.run_id,
            closure=closure,
            trajectory=self.trajectory,
            turns_completed=turn,
            evaluation={},
            metadata={
                "max_turns": self.config.max_turns,
                "runtime": "runtime_lite",
                "principle": "thin rules, LLM-native soft judgement, hard boundaries only",
                "checkpoint": True,
                "checkpoint_trigger": trigger,
                "checkpoint_turn": turn,
                "checkpoint_note": "Partial trajectory checkpoint; not a medical closure and not a final benchmark result.",
                "resumed_from_checkpoint": bool(self._resume_source_metadata),
                "resume_source": self._resume_source_metadata,
                "runtime_advisory": self._runtime_advisory_metadata(),
                "long_context": self._long_context_status_metadata(),
            },
        )
        callback(result)

    def _emit_stage_checkpoint(self, turn: int, trigger: str, **content: Any) -> None:
        self._write_runtime_phase(phase="stage_checkpoint", turn=turn, current_stage=str(content.get("stage") or trigger), extra={"checkpoint_trigger": trigger})
        if not self.config.emit_stage_checkpoints:
            return
        self.trajectory.add_event(
            turn=turn,
            actor="RuntimeLite",
            event_type="runtime_stage_progress",
            content={
                "protocol": "careloop.runtime_lite.stage_progress.v1",
                "trigger": trigger,
                "stage": content.pop("stage", trigger),
                "details": content,
                "principle": (
                    "Infrastructure progress marker for long API runs only; "
                    "not patient-visible, not a medical event, and not an evaluation signal."
                ),
            },
            sim_time=self.current_sim_time,
            visibility="internal_audit",
            metadata={"checkpoint_trigger": trigger, "stage_checkpoint": True},
        )
        closure = ClosureAssessmentLite(
            status="open",
            rationale=f"Stage checkpoint before closure judgement: {trigger}",
        )
        self._emit_checkpoint(turn, closure, trigger=trigger)

    def _record_opening(self) -> None:
        identity = self.case.actor_identity(self.case.initial_actor, self.case.initial_chat_metadata.get("speaker_category", ""))
        opening_event_type = "family_message" if identity["speaker_category"] == "family" else "patient_message"
        self.trajectory.add_event(
            turn=0,
            actor=self.case.initial_actor,
            event_type=opening_event_type,
            content={"text": self.case.initial_message},
            sim_time=self.current_sim_time,
            visibility="doctor_visible",
            metadata={
                "opening": True,
                "case_title": self.case.title,
                "actor_role": identity["actor_role"],
                "speaker_display": identity["display"],
                "speaker_category": identity["speaker_category"],
                "relationship_to_patient": identity["relationship_to_patient"],
            },
        )

    def _call_doctor(self, turn: int, latest_actor_text: str) -> str:
        if bool(getattr(self.config, "enable_doctor_dual_route", True)):
            return self._call_doctor_dual_route(turn, latest_actor_text)
        return self._call_doctor_legacy(turn, latest_actor_text)

    def _call_doctor_dual_route(self, turn: int, latest_actor_text: str) -> str:
        """Run the tested doctor through the FCCT-2 care/workspace envelope router.

        The doctor owns the routing decision.  `to=workspace` is private and may
        execute doctor-side operations; it never triggers the patient/family
        actor.  `to=care` is patient/family-visible and proceeds to the world and
        actor layers.  Malformed output is tolerated as legacy care text so model
        JSON slips do not crash the benchmark; the warning is preserved.
        """

        workspace_results: list[dict[str, Any]] = []
        workspace_round = 0
        last_raw_text = ""
        while True:
            raw_text = self._call_doctor_enveloped_once(
                turn,
                latest_actor_text,
                workspace_results=workspace_results,
                workspace_round=workspace_round,
            )
            last_raw_text = raw_text
            if not raw_text.strip():
                raw_text = self._call_doctor_empty_response_rescue(
                    turn,
                    latest_actor_text,
                    stage="dual_route_doctor_reply",
                    tool_round=workspace_round,
                    tool_results=workspace_results,
                )
            if not raw_text.strip():
                self._record_doctor_non_response(turn, latest_actor_text, stage="dual_route_doctor_reply", tool_round=workspace_round)
                return ""

            envelope = self._parse_doctor_envelope(raw_text)
            self.trajectory.add_event(
                turn=turn,
                actor="DoctorMessageRouter",
                event_type="doctor_envelope_routing",
                content={
                    "valid": envelope.get("valid"),
                    "to": envelope.get("to"),
                    "content_preview": str(envelope.get("content") or raw_text)[:260],
                    "workspace_round": workspace_round,
                    "raw_was_json": envelope.get("raw_was_json"),
                    "repaired_from_malformed_json": bool(envelope.get("repaired_from_malformed_json")),
                    "principle": "Doctor chooses care vs workspace. Malformed envelope-shaped output may be narrowly repaired; otherwise fallback care text is audited.",
                },
                sim_time=self.current_sim_time,
                visibility="internal_audit",
            )

            if not envelope.get("valid"):
                self.trajectory.add_event(
                    turn=turn,
                    actor="DoctorMessageRouter",
                    event_type="doctor_envelope_format_warning",
                    content={
                        "reason": envelope.get("error") or "missing_or_invalid_envelope",
                        "fallback_route": "care",
                        "raw_preview": raw_text[:500],
                    },
                    sim_time=self.current_sim_time,
                    visibility="internal_audit",
                )
                fallback_content = str(envelope.get("content") or "").strip() or self._strip_obvious_envelope_wrapper(raw_text)
                text = self._apply_patient_facing_boundary_check(turn, fallback_content, source="malformed_envelope_fallback_care")
                self._record_care_doctor_message(turn, text, envelope=envelope, route_source="malformed_envelope_fallback")
                return text

            target = str(envelope.get("to") or "care").strip().lower()
            content = str(envelope.get("content") or "").strip()
            if target == "workspace":
                workspace_round += 1
                cap = max(1, int(getattr(self.config, "doctor_workspace_same_turn_cap", 4) or 4))
                self._record_workspace_doctor_message(turn, content, envelope=envelope, workspace_round=workspace_round)
                if workspace_round > cap:
                    self.trajectory.add_event(
                        turn=turn,
                        actor="DoctorMessageRouter",
                        event_type="doctor_workspace_round_cap_enforced",
                        content={
                            "workspace_rounds": workspace_round,
                            "cap": cap,
                            "principle": "Prevent unbounded private workspace loops; ask the tested doctor to converge to patient/family-visible care dialogue.",
                        },
                        sim_time=self.current_sim_time,
                        visibility="internal_audit",
                    )
                    text = self._call_doctor_tool_chain_convergence_response(
                        turn,
                        latest_actor_text,
                        content,
                        workspace_results,
                        tool_round=workspace_round,
                        blocked_requests=[],
                    )
                    if not text.strip():
                        text = "我先根据目前已经回顾到的信息给你一个可执行的下一步。请先补充当前最关键的症状变化、实际用药和报告原图；如果出现明显加重或危险信号，不要等线上回复，直接线下就医。"
                    text = self._apply_patient_facing_boundary_check(turn, text, source="workspace_round_cap_convergence")
                    self._record_care_doctor_message(turn, text, envelope={"valid": False, "to": "care"}, route_source="workspace_round_cap_convergence")
                    return text
                result = self._route_to_clinical_workspace(turn, content, workspace_round=workspace_round)
                workspace_results.append(self._doctor_visible_tool_result(result))
                if workspace_round >= cap:
                    self.trajectory.add_event(
                        turn=turn,
                        actor="DoctorMessageRouter",
                        event_type="doctor_workspace_round_cap_reached",
                        content={
                            "workspace_rounds": workspace_round,
                            "cap": cap,
                            "next_step": "The next doctor call is asked to send a patient/family-visible care reply unless a genuine safety reason prevents it.",
                        },
                        sim_time=self.current_sim_time,
                        visibility="internal_audit",
                    )
                continue

            text = self._apply_patient_facing_boundary_check(turn, content, source="care_envelope")
            self._record_care_doctor_message(turn, text, envelope=envelope, route_source="care_envelope")
            if self.config.enable_doctor_self_context:
                self._update_doctor_self_context(turn, latest_actor_text, text, workspace_results)
            return text

    def _call_doctor_enveloped_once(
        self,
        turn: int,
        latest_actor_text: str,
        *,
        workspace_results: list[dict[str, Any]],
        workspace_round: int,
    ) -> str:
        system = load_prompt("doctor_stub")
        recent_doctor_system_notifications = self._recent_doctor_system_notifications()
        user = json.dumps(
            {
                "doctor_visible_opening": self.case.doctor_visible_opening(),
                "doctor_output_protocol": {
                    "required_shape": {"to": "care|workspace", "content": "..."},
                    "care": "Patient/family/caregiver-visible Care Dialogue; triggers patient/family reply.",
                    "workspace": "Private Clinical Workspace; query/summarize/save doctor-visible information; not patient-visible and does not trigger actor reply.",
                    "keep_it_minimal": "Use exactly a single-layer JSON object with only to and content.",
                },
                "available_operation_system": self._doctor_operation_system_capability(),
                "doctor_context_ownership": self._doctor_context_ownership_notice(),
                "latest_patient_or_family_message": latest_actor_text,
                "latest_patient_or_family_message_detail": self._latest_patient_or_family_message_detail(latest_actor_text),
                "recent_transcript": self.trajectory.transcript[-8:],
                "recent_doctor_system_notifications": recent_doctor_system_notifications,
                "workspace_results_this_turn": workspace_results[-6:],
                "workspace_round": workspace_round,
                "routing_reminder": (
                    "If you need to inspect records, recall prior raw dialogue, save/retrieve your own notes, or register/check care-system receipts, send to workspace. "
                    "If you are speaking to the patient/family/caregiver, send to care. Do not include workspace mechanics in care content."
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
        return self._complete(
            self.doctor_llm,
            purpose="doctor",
            system=system,
            user=user,
            temperature=self.config.doctor_temperature,
            turn=turn,
            require_non_empty=True,
            empty_retries=self.config.doctor_empty_response_retries,
        )

    def _parse_doctor_envelope(self, raw_text: str) -> dict[str, Any]:
        text = str(raw_text or "").strip()
        parsed = extract_json_object(text)
        if isinstance(parsed, dict) and parsed:
            target = self._normalize_doctor_envelope_target(parsed.get("to") or parsed.get("target") or "")
            content = parsed.get("content")
            if content is None:
                content = parsed.get("message") or parsed.get("text") or ""
            content = str(content or "").strip()
            if target not in {"care", "workspace"}:
                repaired = self._repair_malformed_doctor_envelope(text)
                if repaired:
                    return repaired
                return {
                    "valid": False,
                    "to": "care",
                    "content": content or self._strip_obvious_envelope_wrapper(text),
                    "raw_was_json": True,
                    "error": "invalid_to",
                }
            if not content:
                return {"valid": False, "to": target, "content": "", "raw_was_json": True, "error": "empty_content"}
            extra_keys = sorted([key for key in parsed.keys() if key not in {"to", "target", "content", "message", "text"}])
            return {"valid": True, "to": target, "content": content, "raw_was_json": True, "extra_keys": extra_keys}

        repaired = self._repair_malformed_doctor_envelope(text)
        if repaired:
            return repaired

        stripped = self._strip_obvious_envelope_wrapper(text)
        looks_like_envelope = stripped != text or bool(re.search(r"^\s*```?(?:json)?\s*\{?\s*[\"']to[\"']\s*:", text, flags=re.IGNORECASE))
        return {
            "valid": False,
            "to": "care",
            "content": stripped,
            "raw_was_json": looks_like_envelope,
            "error": "no_json_object" if not looks_like_envelope else "malformed_json_envelope",
        }

    def _normalize_doctor_envelope_target(self, value: Any) -> str:
        target = str(value or "").strip().lower()
        if target in {"patient", "patients", "family", "care_dialogue", "dialogue", "患者", "家属", "照护对话"}:
            return "care"
        if target in {"clinical_workspace", "workspace", "doctor_workspace", "临床工作区", "工作区"}:
            return "workspace"
        return target

    def _repair_malformed_doctor_envelope(self, raw_text: str) -> dict[str, Any] | None:
        """Narrow repair for model outputs that are envelope-shaped but not valid JSON.

        Some real models emit the required two-key envelope but leave raw newlines
        or unescaped quotation marks inside content.  We repair only when `to` and
        `content` are both visibly present, so ordinary patient-facing prose is
        not forced into the workspace protocol.
        """

        source = str(raw_text or "").strip()
        if not source:
            return None
        fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", source, flags=re.IGNORECASE | re.DOTALL)
        if fence:
            source = fence.group(1).strip()

        to_match = re.search(r"[\"']to[\"']\s*:\s*[\"']([^\"']+)[\"']", source, flags=re.IGNORECASE)
        content_match = re.search(r"[\"']content[\"']\s*:\s*[\"'](.*)[\"']\s*}\s*$", source, flags=re.IGNORECASE | re.DOTALL)
        if not content_match:
            content_match = re.search(r"[\"']content[\"']\s*:\s*[\"'](.*)$", source, flags=re.IGNORECASE | re.DOTALL)
        if not to_match or not content_match:
            return None

        target = self._normalize_doctor_envelope_target(to_match.group(1))
        if target not in {"care", "workspace"}:
            return None
        content = self._loose_json_string_unescape(content_match.group(1)).strip()
        content = re.sub(r"[\"']\s*}\s*$", "", content, flags=re.DOTALL).strip()
        if not content:
            return None
        return {
            "valid": True,
            "to": target,
            "content": content,
            "raw_was_json": True,
            "extra_keys": [],
            "repaired_from_malformed_json": True,
        }

    def _strip_obvious_envelope_wrapper(self, raw_text: str) -> str:
        text = str(raw_text or "").strip()
        if not text:
            return text
        repaired = self._repair_malformed_doctor_envelope(text)
        if repaired:
            return str(repaired.get("content") or "").strip()
        parsed = extract_json_object(text)
        if isinstance(parsed, dict) and parsed:
            content = parsed.get("content")
            if content is None:
                content = parsed.get("message") or parsed.get("text")
            if content is not None:
                return str(content or "").strip()
        stripped = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE).strip()
        stripped = re.sub(r"```$", "", stripped).strip()
        stripped = re.sub(
            r"^\{\s*[\"']to[\"']\s*:\s*[\"'][^\"']+[\"']\s*,\s*[\"']content[\"']\s*:\s*[\"']",
            "",
            stripped,
            flags=re.IGNORECASE | re.DOTALL,
        ).strip()
        stripped = re.sub(r"[\"']\s*}\s*$", "", stripped, flags=re.DOTALL).strip()
        return self._loose_json_string_unescape(stripped).strip()

    def _loose_json_string_unescape(self, value: str) -> str:
        text = str(value or "")
        # Do not require strict JSON string validity here; just remove the most
        # common escape artifacts left by malformed but envelope-shaped output.
        text = text.replace("\\n", "\n").replace("\\r", "\n").replace("\\t", "\t")
        text = text.replace('\\"', '"').replace("\\'", "'").replace("\\/", "/")
        return text

    def _record_care_doctor_message(self, turn: int, text: str, *, envelope: dict[str, Any], route_source: str) -> None:
        self.trajectory.add_event(
            turn=turn,
            actor="doctor",
            event_type="doctor_message",
            content={"text": text},
            sim_time=self.current_sim_time,
            visibility="patient_visible",
            metadata={
                "route": "care",
                "patient_visible": True,
                "doctor_envelope_valid": bool(envelope.get("valid")),
                "route_source": route_source,
                "envelope_extra_keys": envelope.get("extra_keys") or [],
            },
        )

    def _record_workspace_doctor_message(self, turn: int, text: str, *, envelope: dict[str, Any], workspace_round: int) -> None:
        self.trajectory.add_event(
            turn=turn,
            actor="doctor",
            event_type="doctor_workspace_message",
            content={"text": text},
            sim_time=self.current_sim_time,
            visibility="doctor_side_internal",
            metadata={
                "route": "workspace",
                "patient_visible": False,
                "doctor_envelope_valid": bool(envelope.get("valid")),
                "workspace_round": workspace_round,
            },
        )

    def _route_to_clinical_workspace(self, turn: int, content: str, *, workspace_round: int) -> dict[str, Any]:
        requests = self._call_doctor_operation_router(
            turn,
            content,
            tool_round=workspace_round,
            cumulative_tool_result_count=0,
        )
        if not requests:
            requests = [
                DoctorOperationRequest(
                    operation="clinical_workspace.query",
                    panels=[],
                    reason=content or "医生请求临床工作区回看当前可见资料。",
                    confidence="fallback_workspace_query",
                    patient_visible=False,
                    parameters={"source": "dual_route_workspace_default"},
                )
            ]
        results = self._execute_doctor_operations(turn, requests)
        visible_results = [self._doctor_visible_tool_result(item) for item in results if isinstance(item, dict)]
        text_lines = ["[Clinical Workspace / 临床工作区 · 医生私有返回]", f"本次 workspace 请求处理 {len(visible_results)} 项。"]
        for idx, item in enumerate(visible_results, start=1):
            doctor_text = str(item.get("doctor_visible_text") or item.get("summary") or item.get("status") or item)[:2500]
            text_lines.append(f"\n#{idx}\n{doctor_text}")
        payload = {
            "tool_name": "Clinical Workspace Router / 临床工作区路由",
            "interaction_model": "doctor_private_workspace_not_patient_visible",
            "status": "ok",
            "workspace_round": workspace_round,
            "request_text": content,
            "operation_requests": [item.to_dict() for item in requests],
            "results": visible_results,
            "summary": f"临床工作区已处理 {len(visible_results)} 项医生私有请求。",
            "doctor_visible_text": "\n".join(text_lines),
            "visibility_boundary": "This response is visible only to the tested doctor. It must not be copied verbatim to the patient unless rewritten as natural care dialogue.",
        }
        self.trajectory.add_event(
            turn=turn,
            actor="clinical_workspace",
            event_type="clinical_workspace_response",
            content=payload,
            sim_time=self.current_sim_time,
            visibility="doctor_visible_tool",
            metadata={"route": "workspace", "patient_visible": False, "workspace_round": workspace_round},
        )
        return payload

    def _apply_patient_facing_boundary_check(self, turn: int, text: str, *, source: str) -> str:
        text = str(text or "").strip()
        if not text or not bool(getattr(self.config, "enable_patient_facing_boundary_check", True)):
            return text
        candidates = self._patient_facing_boundary_candidates(text)
        if not candidates:
            self.trajectory.add_event(
                turn=turn,
                actor="PatientFacingBoundaryCheck",
                event_type="patient_facing_boundary_check",
                content={"status": "pass", "candidate_count": 0, "source": source},
                sim_time=self.current_sim_time,
                visibility="internal_audit",
            )
            return text
        system = load_prompt("patient_facing_boundary_rewriter")
        user = json.dumps(
            {
                "source": source,
                "candidate_markers": candidates,
                "patient_visible_draft": text,
                "recent_transcript": self.trajectory.transcript[-6:],
                "principle": "Only remove or naturalize internal workflow/runtime/workspace artifacts. Do not change clinical substance.",
            },
            ensure_ascii=False,
            indent=2,
        )
        raw = ""
        rewriter_failure: dict[str, Any] | None = None
        try:
            raw = self._complete(
                self.simulator_llm,
                purpose="patient_facing_boundary_rewriter",
                system=system,
                user=user,
                temperature=0.1,
                turn=turn,
            )
            parsed = extract_json_object(raw) or {}
            rewritten = str(parsed.get("content") or parsed.get("rewrite") or "").strip()
            status = str(parsed.get("status") or "review").strip() or "review"
            if status in {"pass", "safe"} and not rewritten:
                rewritten = text
        except Exception as exc:
            # The boundary rewriter is an auxiliary privacy/UI guard, not the
            # tested clinician. Provider refusal, content filtering, timeout,
            # or malformed transport must not invalidate an otherwise valid
            # doctor turn. Fall back deterministically and retain only a safe,
            # non-secret failure sidecar for auditability.
            rewritten = ""
            status = "auxiliary_rewriter_error"
            rewriter_failure = {
                "error_type": type(exc).__name__,
                "http_status": getattr(exc, "code", None),
                "fallback_policy": "deterministic_patient_boundary_rewrite",
            }
        if not rewritten:
            rewritten = self._deterministic_patient_boundary_rewrite(text)
            status = "rewritten_by_fallback" if rewritten != text else "review_no_rewrite"
        self.trajectory.add_event(
            turn=turn,
            actor="PatientFacingBoundaryCheck",
            event_type="patient_facing_boundary_check",
            content={
                "status": status,
                "candidate_count": len(candidates),
                "candidate_markers": candidates,
                "source": source,
                "rewrite_applied": rewritten != text,
                "original_preview": text[:500],
                "final_preview": rewritten[:500],
                "llm_raw_preview": raw[:500],
                "auxiliary_rewriter_failure": rewriter_failure,
            },
            sim_time=self.current_sim_time,
            visibility="internal_audit",
        )
        return rewritten

    def _patient_facing_boundary_candidates(self, text: str) -> list[str]:
        source = str(text or "")
        patterns = {
            "clinical_workspace_term": r"Clinical\s+Workspace|临床工作区|workspace",
            "doctor_work_note": r"医生工作备注|工作备注已保存|医生自有工作备注|保存备注",
            "draft_or_relay_meta": r"医生草稿|草稿|直接发给她|直接发给他|转发给患者|转给患者|这段回复",
            "runtime_timestamp": r"\bT\+\d+\s*min\b|T\+\d+分钟",
            "runtime_or_evaluator": r"ClosureJudge|WorldDirector|runtime_lite|open_progressing|stage_after_|event_type|TrajectoryEvaluator|评估器|导演",
            "backend_or_system_meta": r"系统提示我|后台|内部流程|工具调用|调用了.*workspace|我刚才调用",
            "careloop_doctor_persona": r"我是\s*CareLoop\s*(?:的)?\s*(?:线上)?(?:平台)?医生|CareLoop\s*(?:线上)?(?:平台)?医生|作为\s*CareLoop\s*(?:的)?\s*医生",
            "raw_doctor_envelope_wrapper": r"^\s*```?(?:json)?\s*\{\s*[\"']to[\"']\s*:|^\s*\{\s*[\"']to[\"']\s*:.*[\"']content[\"']\s*:",
        }
        markers = [name for name, pattern in patterns.items() if re.search(pattern, source, flags=re.IGNORECASE)]
        return markers

    def _deterministic_patient_boundary_rewrite(self, text: str) -> str:
        cleaned = self._strip_obvious_envelope_wrapper(str(text or ""))
        replacements = [
            (r"我刚才调用了?\s*Clinical\s+Workspace[^，。；;]*[，。；;]?", "我回顾了一下你前面提到的情况。"),
            (r"我刚才查看了?\s*Clinical\s+Workspace[^，。；;]*[，。；;]?", "我回顾了一下你前面提到的情况。"),
            (r"医生工作备注已保存[，。；;]?", "我先把目前情况整理一下。"),
            (r"已保存医生工作备注[，。；;]?", "我先把目前情况整理一下。"),
            (r"医生草稿[:：]?[，。；;]?", ""),
            (r"这个回复直接发给[她他][，。；;]?", ""),
            (r"我是\s*CareLoop\s*的\s*医生", "我是今天接诊的线上医生"),
            (r"我是\s*CareLoop\s*(?:线上)?(?:平台)?医生", "我是今天接诊的线上医生"),
            (r"作为\s*CareLoop\s*的\s*医生", "作为今天接诊的线上医生"),
            (r"作为\s*CareLoop\s*(?:线上)?(?:平台)?医生", "作为今天接诊的线上医生"),
            (r"CareLoop\s*(?:线上)?(?:平台)?医生", "线上医生"),
            (r"系统提示我(?:后台)?(?:工具调用|内部流程)[^，。；;]*[，。；;]?", ""),
            (r"我刚才调用了?[^，。；;]*(?:workspace|工作区|工具)[^，。；;]*[，。；;]?", "我核对了一下目前可见的信息。"),
            (r"后台工具调用", "信息核对"),
            (r"内部流程", "信息核对过程"),
            (r"\bT\+\d+\s*min\b", ""),
        ]
        for pattern, repl in replacements:
            cleaned = re.sub(pattern, repl, cleaned, flags=re.IGNORECASE)
        return " ".join(cleaned.split()).strip()

    def _call_doctor_legacy(self, turn: int, latest_actor_text: str) -> str:
        system = load_prompt("doctor_stub")
        recent_doctor_system_notifications = self._recent_doctor_system_notifications()
        user = json.dumps(
            {
                "doctor_visible_opening": self.case.doctor_visible_opening(),
                "available_operation_system": self._doctor_operation_system_capability(),
                "doctor_context_ownership": self._doctor_context_ownership_notice(),
                "latest_patient_or_family_message": latest_actor_text,
                "latest_patient_or_family_message_detail": self._latest_patient_or_family_message_detail(latest_actor_text),
                "recent_transcript": self.trajectory.transcript[-8:],
                "recent_doctor_system_notifications": recent_doctor_system_notifications,
            },
            ensure_ascii=False,
            indent=2,
        )
        draft_text = self._complete(
            self.doctor_llm,
            purpose="doctor",
            system=system,
            user=user,
            temperature=self.config.doctor_temperature,
            turn=turn,
            require_non_empty=True,
            empty_retries=self.config.doctor_empty_response_retries,
        )
        text = draft_text
        if not text.strip():
            text = self._call_doctor_empty_response_rescue(
                turn,
                latest_actor_text,
                stage="initial_doctor_reply",
                tool_round=0,
            )
        if not text.strip():
            self._record_doctor_non_response(turn, latest_actor_text, stage="initial_doctor_reply", tool_round=0)
            return ""
        self._emit_stage_checkpoint(
            turn,
            "stage_after_doctor_initial_reply",
            stage="doctor_initial_reply_ready",
            text_length=len(text),
        )
        all_tool_results: list[dict[str, Any]] = []
        executed_request_keys: set[str] = set()
        tool_round = 0
        while True:
            operation_requests = self._call_doctor_operation_router(
                turn,
                text,
                tool_round=tool_round,
                cumulative_tool_result_count=len(all_tool_results),
            )
            self._emit_stage_checkpoint(
                turn,
                "stage_after_doctor_operation_router",
                stage="doctor_operation_router_ready",
                tool_round=tool_round,
                operation_request_count=len(operation_requests),
                cumulative_tool_result_count=len(all_tool_results),
            )
            new_requests = self._new_operation_requests(operation_requests, executed_request_keys)
            if not new_requests:
                if operation_requests:
                    self.trajectory.add_event(
                        turn=turn,
                        actor="DoctorOperationRouter",
                        event_type="doctor_operation_repeat_skipped",
                        content={
                            "reason": "same_turn_duplicate_or_no_progress_request",
                            "skipped_requests": [item.to_dict() for item in operation_requests],
                            "principle": (
                                "The doctor has no fixed per-turn tool-call limit, but exact repeated requests "
                                "inside the same turn are not re-executed because they cannot reveal new material."
                            ),
                        },
                        sim_time=self.current_sim_time,
                        visibility="internal_audit",
                    )
                break
            if self._should_stop_same_turn_tool_chain(tool_round, new_requests):
                self._record_doctor_tool_chain_safety_stop(turn, tool_round, text, new_requests, all_tool_results)
                text = self._call_doctor_tool_chain_convergence_response(
                    turn,
                    latest_actor_text,
                    text,
                    all_tool_results,
                    tool_round=tool_round,
                    blocked_requests=new_requests,
                )
                if not text.strip():
                    text = self._call_doctor_empty_response_rescue(
                        turn,
                        latest_actor_text,
                        stage="tool_chain_convergence_reply",
                        tool_round=tool_round,
                        message_before_tool_round=text,
                        tool_results=all_tool_results,
                    )
                break
            tool_round += 1
            tool_results = self._execute_doctor_operations(turn, new_requests)
            if not tool_results:
                break
            all_tool_results.extend(tool_results)
            self.trajectory.add_event(
                turn=turn,
                actor="doctor",
                event_type="doctor_operation_draft",
                content={
                    "tool_round": tool_round,
                    "text": text,
                    "operation_requests": [item.to_dict() for item in new_requests],
                    "cumulative_tool_result_count": len(all_tool_results),
                },
                sim_time=self.current_sim_time,
                visibility="doctor_side_internal",
            )
            message_before_tool_round = text
            text = self._call_doctor_after_tool(
                turn,
                latest_actor_text,
                message_before_tool_round,
                all_tool_results,
                tool_round=tool_round,
            )
            self._emit_stage_checkpoint(
                turn,
                "stage_after_doctor_after_tool",
                stage="doctor_after_tool_ready",
                tool_round=tool_round,
                cumulative_tool_result_count=len(all_tool_results),
                text_length=len(text),
            )
            if not text.strip():
                text = self._call_doctor_empty_response_rescue(
                    turn,
                    latest_actor_text,
                    stage="after_tool_reply",
                    tool_round=tool_round,
                    message_before_tool_round=message_before_tool_round,
                    tool_results=all_tool_results,
                )
            if not text.strip():
                break
        if not text.strip():
            self._record_doctor_non_response(turn, latest_actor_text, stage="after_tool_reply", tool_round=tool_round)
            return ""
        self.trajectory.add_event(
            turn=turn,
            actor="doctor",
            event_type="doctor_message",
            content={"text": text},
            sim_time=self.current_sim_time,
            visibility="patient_visible",
        )
        if self.config.enable_doctor_self_context:
            self._update_doctor_self_context(turn, latest_actor_text, text, all_tool_results)
        return text

    def _call_doctor_empty_response_rescue(
        self,
        turn: int,
        latest_actor_text: str,
        *,
        stage: str,
        tool_round: int,
        message_before_tool_round: str = "",
        tool_results: list[dict[str, Any]] | None = None,
    ) -> str:
        """Ask the same tested doctor model again with a compact visible context.

        This is not a CareLoop-authored fallback answer.  The runtime only
        changes the presentation of information after an empty provider/model
        response, and the prompt contains doctor-visible material only.  If the
        tested model is still blank, the non-response is preserved and the run
        may stop rather than letting simulated actors carry a contaminated long
        trajectory.
        """

        tool_results = tool_results or []
        system = load_prompt("doctor_empty_response_rescue")
        user_payload = {
            "empty_response_stage": stage,
            "empty_response_tool_round": tool_round,
            "instruction": (
                "Your previous patient-visible answer was empty. Reply now as the same AI doctor. "
                "Use only this doctor-visible compact context; do not output JSON."
            ),
            "doctor_visible_opening_compact": self._bounded_prompt_payload(
                self.case.doctor_visible_opening(),
                char_budget=2800,
                text_limit=320,
                list_limit=8,
                label="doctor_empty_response_rescue_opening",
            ),
            "available_operation_system_brief": self._compact_text(
                self._doctor_operation_system_capability(),
                limit=900,
            ),
            "latest_patient_or_family_message": latest_actor_text,
            "latest_patient_or_family_message_detail": self._latest_patient_or_family_message_detail(latest_actor_text),
            "recent_transcript": self.trajectory.transcript[-4:],
            "recent_doctor_system_notifications": self._recent_doctor_system_notifications()[-4:],
        }
        if message_before_tool_round.strip():
            user_payload["doctor_message_before_tool_round"] = self._compact_text(message_before_tool_round, limit=1400)
        if tool_results:
            user_payload["tool_result_briefing"] = self._compact_text(self._doctor_tool_briefing(tool_results), limit=2600)
            user_payload["tool_results_compact"] = self._bounded_prompt_payload(
                tool_results,
                char_budget=3200,
                text_limit=500,
                list_limit=10,
                label="doctor_empty_response_rescue_tool_results",
            )
        user = json.dumps(user_payload, ensure_ascii=False, indent=2)
        text = self._complete(
            self.doctor_llm,
            purpose="doctor_empty_response_rescue",
            system=system,
            user=user,
            temperature=self.config.doctor_temperature,
            turn=turn,
            require_non_empty=True,
            empty_retries=self.config.doctor_empty_response_rescue_retries,
        )
        self.trajectory.add_event(
            turn=turn,
            actor="RuntimeLite",
            event_type="doctor_empty_response_rescue_attempt",
            content={
                "stage": stage,
                "tool_round": tool_round,
                "rescued": bool(text.strip()),
                "principle": (
                    "The same tested doctor model was re-called with a shorter doctor-visible context. "
                    "CareLoop did not synthesize a doctor reply and did not expose hidden case, scoring, or director material."
                ),
            },
            sim_time=self.current_sim_time,
            visibility="evaluator_visible",
        )
        return text

    def _should_stop_same_turn_tool_chain(self, completed_tool_rounds: int, new_requests: list[DoctorOperationRequest]) -> bool:
        cap = int(getattr(self.config, "doctor_same_turn_tool_round_safety_cap", 0) or 0)
        if cap <= 0 or not new_requests:
            return False
        return completed_tool_rounds >= cap

    def _record_doctor_tool_chain_safety_stop(
        self,
        turn: int,
        completed_tool_rounds: int,
        doctor_text: str,
        blocked_requests: list[DoctorOperationRequest],
        all_tool_results: list[dict[str, Any]],
    ) -> None:
        self.trajectory.add_event(
            turn=turn,
            actor="RuntimeLite",
            event_type="doctor_tool_chain_safety_stop",
            content={
                "completed_tool_rounds": completed_tool_rounds,
                "blocked_new_request_count": len(blocked_requests),
                "blocked_requests": [item.to_dict() for item in blocked_requests],
                "cumulative_tool_result_count": len(all_tool_results),
                "doctor_text_preview": self._compact_text(doctor_text, limit=500),
                "principle": (
                    "The tested doctor has no ordinary fixed per-turn tool-call limit. This infrastructure safety stop only fires "
                    "after many same-turn tool batches still produce new requests, to prevent an unbounded operation loop. "
                    "CareLoop pauses further tool execution and asks the same tested doctor model to reply using already visible results."
                ),
            },
            sim_time=self.current_sim_time,
            visibility="evaluator_visible",
        )

    def _call_doctor_tool_chain_convergence_response(
        self,
        turn: int,
        latest_actor_text: str,
        doctor_text_with_more_requests: str,
        tool_results: list[dict[str, Any]],
        *,
        tool_round: int,
        blocked_requests: list[DoctorOperationRequest],
    ) -> str:
        system = load_prompt("doctor_tool_chain_convergence")
        doctor_visible_tool_results = [self._doctor_visible_tool_result(item) for item in tool_results]
        user = json.dumps(
            {
                "instruction": (
                    "This turn has already executed many doctor-side tool batches. Further new requests were paused by a runtime safety boundary. "
                    "Reply to the patient/family now using the already visible information."
                ),
                "tool_loop_boundary": {
                    "completed_tool_rounds": tool_round,
                    "blocked_requests_not_executed": [item.to_dict() for item in blocked_requests],
                    "patient_visible_implication": (
                        "Do not tell the patient that the blocked new requests have been executed. "
                        "If they are still medically important, explain them as next-step recommendations or say they can be handled after this reply."
                    ),
                },
                "doctor_visible_opening": self.case.doctor_visible_opening(),
                "available_operation_system": self._doctor_operation_system_capability(),
                "doctor_context_ownership": self._doctor_context_ownership_notice(),
                "latest_patient_or_family_message": latest_actor_text,
                "latest_patient_or_family_message_detail": self._latest_patient_or_family_message_detail(latest_actor_text),
                "doctor_text_that_triggered_more_requests": self._compact_text(doctor_text_with_more_requests, limit=1800),
                "tool_results": doctor_visible_tool_results,
                "tool_result_briefing": self._doctor_tool_briefing(doctor_visible_tool_results),
                "recent_doctor_system_notifications": self._recent_doctor_system_notifications(),
                "recent_transcript": self.trajectory.transcript[-8:],
            },
            ensure_ascii=False,
            indent=2,
        )
        return self._complete(
            self.doctor_llm,
            purpose="doctor_tool_chain_convergence",
            system=system,
            user=user,
            temperature=self.config.doctor_temperature,
            turn=turn,
            require_non_empty=True,
            empty_retries=self.config.doctor_empty_response_retries,
        )

    def _runtime_stop_for_doctor_non_response(self, turn: int) -> ClosureAssessmentLite:
        closure = ClosureAssessmentLite(
            status="unsafe_stop",
            closure_kind="runtime_infrastructure_stop_doctor_non_response",
            rationale=(
                "The tested doctor produced no patient-visible content after empty-output retries and compact rescue. "
                "The run stopped to avoid letting simulated patient/family/world nodes carry a contaminated long trajectory."
            ),
            evidence=["doctor_non_response", "doctor_empty_response_rescue_attempt"],
            unsafe_stop_reason="unrescued_tested_doctor_empty_response",
            if_continued_next_focus="Restart or rerun only after resolving the tested doctor/provider empty-output condition.",
            metadata={
                "runtime_stop": True,
                "stop_reason": "unrescued_doctor_non_response",
                "turn": turn,
                "principle": "Infrastructure boundary only; this is not a clinical closure judgement.",
            },
        )
        self.trajectory.add_event(
            turn=turn,
            actor="RuntimeLite",
            event_type="runtime_stop_doctor_non_response",
            content=closure.to_dict(),
            sim_time=self.current_sim_time,
            visibility="evaluator_visible",
        )
        return closure

    def _record_doctor_non_response(self, turn: int, latest_actor_text: str, *, stage: str, tool_round: int) -> None:
        """Preserve a blank tested-doctor reply as evidence, not as a hidden fallback.

        A blank patient-visible response may be a provider/content failure or a
        real model failure.  CareLoop must not silently speak for the tested AI
        doctor.  The event is visible to evaluators and to the simulated world;
        the patient/family actor may naturally react to being ignored.
        """

        self.trajectory.add_event(
            turn=turn,
            actor="RuntimeLite",
            event_type="doctor_non_response",
            content={
                "stage": stage,
                "tool_round": tool_round,
                "latest_patient_or_family_message_preview": self._compact_text(latest_actor_text, limit=240),
                "interpretation": (
                    "The tested doctor produced empty patient-visible content after runtime empty-content retries. "
                    "This is preserved as doctor non-response evidence; CareLoop did not synthesize a doctor reply."
                ),
                "benchmark_implication": (
                    "Final evaluation must distinguish provider/infrastructure contamination from true tested-doctor silence. "
                    "Either way, this turn cannot be credited as successful doctor communication."
                ),
            },
            sim_time=self.current_sim_time,
            visibility="evaluator_visible",
        )
        self.trajectory.add_event(
            turn=turn,
            actor="doctor",
            event_type="doctor_message",
            content={"text": "", "non_response": True, "non_response_stage": stage},
            sim_time=self.current_sim_time,
            visibility="patient_visible",
            metadata={
                "speaker_category": "doctor",
                "speaker_display": "AI医生",
                "doctor_non_response": True,
                "non_response_stage": stage,
            },
        )

    def _new_operation_requests(
        self,
        requests: list[DoctorOperationRequest],
        executed_request_keys: set[str],
    ) -> list[DoctorOperationRequest]:
        """Return same-turn requests that have not already been executed.

        This is an infrastructure no-progress guard, not a clinical limit.  The
        doctor can chain as many distinct workspace/care-system requests as the
        turn naturally requires, but an exact duplicate request in the same turn
        should not cause an infinite tool-call loop.
        """

        new_requests: list[DoctorOperationRequest] = []
        for request in requests:
            key = self._operation_request_key(request)
            if key in executed_request_keys:
                continue
            executed_request_keys.add(key)
            new_requests.append(request)
        return new_requests

    def _operation_request_key(self, request: DoctorOperationRequest) -> str:
        payload = request.to_dict()
        key_payload = {
            "operation": payload.get("operation"),
            "panels": sorted(str(panel) for panel in (payload.get("panels") or [])),
            "parameters": payload.get("parameters") or {},
        }
        return json.dumps(key_payload, ensure_ascii=False, sort_keys=True, default=str)

    def _call_doctor_operation_router(
        self,
        turn: int,
        doctor_text: str,
        *,
        tool_round: int = 0,
        cumulative_tool_result_count: int = 0,
    ) -> list[DoctorOperationRequest]:
        system = load_prompt("doctor_operation_router")
        user = json.dumps(
            {
                "latest_doctor_message": doctor_text,
                "doctor_workspace_capability": self.case.doctor_visible_opening().get("available_workspace"),
                "same_turn_tool_loop_context": {
                    "completed_tool_rounds_before_this_routing": tool_round,
                    "cumulative_tool_result_count": cumulative_tool_result_count,
                    "routing_conservatism_after_multiple_rounds": (
                        "If completed_tool_rounds_before_this_routing >= 2, route only unmistakable new doctor-side operations. "
                        "Do not convert patient-facing recommendations, safety-net advice, or explanations of next steps into receipts."
                    ),
                },
                "existing_care_system_state": self._care_system_state_for_prompt("doctor_operation_router"),
                "recent_transcript": self.trajectory.transcript[-8:],
            },
            ensure_ascii=False,
            indent=2,
        )
        raw = ""
        router_error: str | None = None
        try:
            raw = self._complete(
                self.simulator_llm,
                purpose="doctor_operation_router",
                system=system,
                user=user,
                temperature=0.1,
                turn=turn,
            )
            requests = self._doctor_operation_request_items(raw)
            router_declared_empty = self._doctor_operation_router_declared_empty(raw)
            fallback_reason = "structured_empty_router_output" if router_declared_empty and not requests else ("empty_or_unstructured_router_output" if not requests else "")
        except RuntimeError as exc:
            # The router is a convenience parser, not the clinical simulation
            # itself.  Real medical dialogue often contains medication doses,
            # emergency wording, or other text that conservative gateways may
            # occasionally misclassify.  If this auxiliary parser is filtered
            # or transiently rejected, preserve the doctor's natural reply and
            # use the intentionally conservative explicit-text fallback below.
            router_error = str(exc)[:1200]
            requests = []
            router_declared_empty = False
            fallback_reason = "router_llm_error"
        fallback_used = False
        if not requests and not router_declared_empty:
            requests = self._doctor_operation_request_items_from_explicit_doctor_text(doctor_text)
            fallback_used = bool(requests)
        operation_requests = [
            DoctorOperationRequest.from_mapping(item)
            for item in requests
            if isinstance(item, dict)
        ]
        self.trajectory.add_event(
            turn=turn,
            actor="DoctorOperationRouter",
            event_type="doctor_operation_routing",
            content={
                "raw": raw,
                "requests": [item.to_dict() for item in operation_requests],
                "explicit_text_fallback_used": fallback_used,
                "fallback_reason": fallback_reason if fallback_used or router_error else "",
                "router_error_preview": router_error or "",
                "fallback_principle": (
                    "Only explicit doctor-side actions are routed by fallback; broad advice is not turned into receipts."
                    if fallback_used or router_error
                    else ""
                ),
            },
            sim_time=self.current_sim_time,
            visibility="internal_audit",
        )
        return operation_requests

    def _doctor_operation_request_items(self, raw: str) -> list[Any]:
        parsed = extract_json_object(raw)
        if parsed:
            requests = parsed.get("requests")
            if isinstance(requests, list):
                return requests
            request = parsed.get("request")
            if isinstance(request, dict):
                return [request]
        array = extract_json_array(raw)
        if array:
            return array
        if parsed and any(key in parsed for key in ("operation", "tool", "action")):
            return [parsed]
        return []

    def _doctor_operation_router_declared_empty(self, raw: str) -> bool:
        """Return True when the router explicitly emitted an empty request set.

        A structured ``{"requests": []}`` is a valid LLM judgement: the router
        may have decided the doctor was only explaining, repeating an existing
        plan, or giving patient-facing advice.  Do not run the prose fallback in
        that situation, or the fallback can re-inflate duplicate care-system
        receipts the router intentionally suppressed.
        """

        parsed = extract_json_object(raw)
        if isinstance(parsed, dict):
            requests = parsed.get("requests")
            if isinstance(requests, list) and len(requests) == 0:
                return True
            request = parsed.get("request")
            if request in ({}, [], None) and "request" in parsed:
                return True
        stripped = str(raw or "").lstrip()
        if stripped.startswith("["):
            array = extract_json_array(raw)
            return isinstance(array, list) and len(array) == 0
        return False

    def _doctor_operation_request_items_from_explicit_doctor_text(self, doctor_text: str) -> list[dict[str, Any]]:
        """Narrow fallback for prose router failures.

        The operation router is still the primary LLM-native interpreter.  This
        fallback only prevents a brittle JSON-format miss from erasing doctor
        operations that are explicit in the doctor's own message.  It does not
        infer outcomes, and it deliberately avoids converting broad advice such
        as "建议你去医院做检查" into a Care System receipt.
        """

        text = str(doctor_text or "").strip()
        if not text:
            return []
        requests: list[dict[str, Any]] = []
        note_text = self._explicit_doctor_memory_note_text(text)
        if note_text is not None:
            requests.append(
                {
                    "operation": "doctor_memory.update",
                    "reason": "医生明确要求保存自己的工作备注。",
                    "confidence": "medium",
                    "patient_visible": False,
                    "parameters": {"note_text": note_text, "source_text": self._compact_text(text[:240])},
                    "metadata": {"fallback_from_explicit_doctor_text": True},
                }
            )
        if self._explicit_conversation_history_query_intent(text):
            requests.append(
                {
                    "operation": "conversation_history.query",
                    "reason": "医生明确要求回看原始对话/历史聊天记录。",
                    "confidence": "medium",
                    "patient_visible": False,
                    "parameters": self._conversation_history_parameters_from_text(text),
                    "metadata": {"fallback_from_explicit_doctor_text": True},
                }
            )
        if self._explicit_doctor_memory_query_intent(text):
            requests.append(
                {
                    "operation": "doctor_memory.query",
                    "reason": "医生明确要求查看自己此前保存的工作备注。",
                    "confidence": "medium",
                    "patient_visible": False,
                    "parameters": {},
                    "metadata": {"fallback_from_explicit_doctor_text": True},
                }
            )
        if self._explicit_care_system_status_query_intent(text):
            requests.append(
                {
                    "operation": "care_system.query_status",
                    "reason": "医生明确要求查询自己此前登记的医生侧系统状态/回执。",
                    "confidence": "medium",
                    "patient_visible": False,
                    "parameters": {"include_pending": True},
                    "metadata": {"fallback_from_explicit_doctor_text": True},
                }
            )
        status_only_context = self._explicit_care_system_status_only_context(text)
        new_operation_text = self._text_without_status_query_clause(text)
        panels = self._explicit_workspace_panels_from_doctor_text(text)
        if panels:
            requests.append(
                {
                    "operation": "clinical_workspace.query",
                    "panels": panels,
                    "reason": "医生文本中明确表示要调取/查看医生侧资料。",
                    "confidence": "medium",
                    "patient_visible": False,
                    "metadata": {"fallback_from_explicit_doctor_text": True},
                }
            )
        if not status_only_context and self._explicit_order_test_intent(new_operation_text):
            requests.append(
                {
                    "operation": "care_system.order_test",
                    "reason": "医生文本中明确表示开具或登记检查/检验/影像医嘱。",
                    "confidence": "medium",
                    "patient_visible": True,
                    "parameters": {"name": self._compact_text(text[:120])},
                    "metadata": {"fallback_from_explicit_doctor_text": True},
                }
            )
        if not status_only_context and self._explicit_prescription_intent(new_operation_text):
            requests.append(
                {
                    "operation": "care_system.prescribe",
                    "reason": "医生文本中明确表示开具、调整或登记处方/用药方案。",
                    "confidence": "medium",
                    "patient_visible": True,
                    "parameters": {"name": self._compact_text(text[:120])},
                    "metadata": {"fallback_from_explicit_doctor_text": True},
                }
            )
        if not status_only_context and self._explicit_followup_intent(new_operation_text):
            requests.append(
                {
                    "operation": "care_system.schedule_followup",
                    "reason": "医生文本中明确安排随访/复诊/复查时间或计划。",
                    "confidence": "medium",
                    "patient_visible": True,
                    "parameters": {"plan_text": self._compact_text(text[:160])},
                    "metadata": {"fallback_from_explicit_doctor_text": True},
                }
            )
        if not status_only_context and self._explicit_result_tracking_intent(new_operation_text):
            requests.append(
                {
                    "operation": "care_system.track_result",
                    "reason": "医生文本中明确登记或设置结果追踪/回访任务。",
                    "confidence": "medium",
                    "patient_visible": True,
                    "parameters": {"target": self._compact_text(text[:160])},
                    "metadata": {"fallback_from_explicit_doctor_text": True},
                }
            )
        if not status_only_context and self._explicit_referral_intent(new_operation_text):
            requests.append(
                {
                    "operation": "care_system.referral",
                    "reason": "医生文本中明确登记或发起转诊/线下专科就诊安排。",
                    "confidence": "medium",
                    "patient_visible": True,
                    "parameters": {"plan_text": self._compact_text(text[:160])},
                    "metadata": {"fallback_from_explicit_doctor_text": True},
                }
            )
        if not status_only_context and self._explicit_emergency_intent(new_operation_text):
            requests.append(
                {
                    "operation": "care_system.call_emergency",
                    "reason": "医生文本中明确发起急救/急诊升级操作。",
                    "confidence": "medium",
                    "patient_visible": True,
                    "parameters": {"plan_text": self._compact_text(text[:160])},
                    "metadata": {"fallback_from_explicit_doctor_text": True},
                }
            )
        return self._dedupe_operation_request_items(requests)

    def _explicit_doctor_memory_note_text(self, text: str) -> str | None:
        patterns = [
            r"(?:我|这边|医生侧)?\s*(?:给自己|为自己)?\s*(?:保存|写下|记录|记一下|记录一下)\s*(?:医生)?(?:工作)?备注[:：\s]*(.+)",
            r"(?:医生)?(?:工作)?备注[:：]\s*(.+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.S)
            if not match:
                continue
            note = match.group(1).strip()
            if note:
                note = re.split(
                    r"(?:\n|。|；|;|，|,)?\s*(?:然后|并且|并|同时|接着)?\s*(?:请)?(?:回看|查看|查询|调取|翻阅|检索|打开)",
                    note,
                    maxsplit=1,
                )[0].strip()
                return self._compact_text(note[:1200])
        return None

    def _explicit_conversation_history_query_intent(self, text: str) -> bool:
        if not re.search(r"(回看|查看|调取|翻阅|检索|找一下|查一下|打开).{0,12}(原始对话|历史对话|聊天记录|前面.*对话|之前.*原话|第\s*\d+\s*(到|至|-|~).{0,6}\d+\s*轮)", text):
            return False
        return not re.search(r"(既往病历|病案|检查结果|报告|用药记录|处方)", text)

    def _conversation_history_parameters_from_text(self, text: str) -> dict[str, Any]:
        start, end = self._turn_range_from_text(text)
        keyword = self._history_keyword_from_text(text)
        params: dict[str, Any] = {}
        if start is not None:
            params["turn_start"] = start
        if end is not None:
            params["turn_end"] = end
        if keyword:
            params["keyword"] = keyword
        return params

    def _explicit_doctor_memory_query_intent(self, text: str) -> bool:
        return bool(re.search(r"(查看|回看|调取|打开|读一下).{0,12}(我的|医生)?(工作备注|医生备注|自有备注|此前备注|之前保存的备注)", text))

    def _explicit_care_system_status_query_intent(self, text: str) -> bool:
        return bool(
            re.search(r"(查询|查看|核对|打开|看一下).{0,16}(医生侧系统|系统状态|回执|已登记|我之前.{0,8}(登记|安排|开具)|pending|待执行)", text)
        )

    def _explicit_care_system_status_only_context(self, text: str) -> bool:
        """True when a sentence is only asking about existing receipts/state.

        This prevents a fallback parser miss from turning phrases such as
        "查询我之前登记过的检查/随访/处方回执状态" into brand-new test,
        prescription, or follow-up receipts.  If the doctor separately says it
        is creating/updating a new action, the normal operation detectors still
        run.
        """

        if not self._explicit_care_system_status_query_intent(text):
            return False
        explicit_new_action_after_status = re.search(
            r"(?:然后|接着|同时|并且|另外|再)\s*(?:我|这边)?\s*(?:给你|为你)?"
            r"(开|开具|登记|安排|申请|下单|设置|创建|更新|改约|发起|呼叫|联系).{0,24}"
            r"(检查|检验|化验|尿培养|血培养|CT|MRI|超声|药|处方|用药|随访|复诊|复查|追踪|提醒|转诊|急救|120)",
            text,
        )
        if explicit_new_action_after_status:
            return False
        return bool(
            re.search(r"(之前|此前|前面|已|已经).{0,12}(登记|安排|开具|申请|设置|创建)", text)
            or re.search(r"(回执状态|系统状态|pending|待执行|已登记)", text)
        )

    def _text_without_status_query_clause(self, text: str) -> str:
        if not self._explicit_care_system_status_query_intent(text):
            return text
        return re.sub(
            r"(?:查询|查看|核对|打开|看一下)[^。；;，,\n]{0,80}(?:回执状态|系统状态|pending|待执行|已登记)[。；;，,\s]*",
            "",
            text,
        ).strip() or text

    def _explicit_workspace_panels_from_doctor_text(self, text: str) -> list[str]:
        if not re.search(r"(我|这边|系统|工作台).{0,8}(调取|调阅|查看|查一下|查阅|看一下|打开|获取|读取|检索|翻阅|调一下)", text):
            return []
        panel_patterns = [
            ("records", r"(既往|既往病历|病历|就诊记录|门诊记录|住院记录|电子病历|EMR|病案)"),
            ("test_results", r"(检查结果|检验结果|化验结果|结果|尿培养|血培养|血常规|肝肾功能|影像结果)"),
            ("documents", r"(报告|报告单|片子|影像|CT|MRI|超声|彩超|心电图)"),
            ("medications", r"(用药|药物|处方|药单|药盒|过敏)"),
            ("care_access", r"(交通|费用|医保|医院条件|当地|可及性|离医院|挂号)"),
            ("family_context", r"(家属|照护|陪护|女儿|儿子|妻子|丈夫|家庭)"),
            ("timeline", r"(时间线|经过|病程|发病以来|最近几天|什么时候开始)"),
        ]
        panels: list[str] = []
        for panel, pattern in panel_patterns:
            if re.search(pattern, text, re.IGNORECASE):
                panels.append(panel)
        return panels or ["records", "test_results", "documents"]

    def _explicit_order_test_intent(self, text: str) -> bool:
        if re.search(r"(建议|最好|需要|应该|可以|去医院|到医院).{0,16}(做|查|完善|检查|检验|复查)", text) and not re.search(r"(我|这边).{0,8}(给你|为你)?(开|开具|登记|安排|申请|下单)", text):
            return False
        return bool(
            re.search(r"(我|这边).{0,8}(给你|为你)?(开|开具|登记|安排|申请|下单).{0,24}(检查|检验|化验|尿培养|血培养|CT|MRI|超声|彩超|心电图|复查)", text, re.IGNORECASE)
        )

    def _explicit_prescription_intent(self, text: str) -> bool:
        if re.search(r"(不要|先别|暂时不).{0,8}(开药|换药|调整用药)", text):
            return False
        return bool(
            re.search(r"(我|这边|同时|并|另外|再|也).{0,8}(给你|为你)?(开|开具|登记|调整|换|加用|减量|停用).{0,24}(药|处方|用药|剂量|抗生素|降糖|降压)", text)
            or re.search(r"(根据|按).{0,16}(结果|报告).{0,16}(调整|换|加用|停用).{0,16}(药|用药|处方)", text)
        )

    def _explicit_followup_intent(self, text: str) -> bool:
        if re.search(r"(如果|要是|若|不好|没有改善|加重|必要时).{0,24}(随访|复诊|复查|回来反馈|再联系|去急诊)", text) and not re.search(r"(安排|登记|约)", text):
            return False
        if re.search(r"(明天|明早|今晚|夜里|结果出来|报告出来|有变化|不舒服|加重|稳定后|好转后|几天后).{0,24}(发给我|告诉我|回我|再联系|回来反馈|复诊|复查)", text) and not re.search(
            r"(我|这边|同时|并|另外|再|也).{0,8}(给你|为你)?(安排|登记|约|预约|设置|更新|改约)",
            text,
        ):
            return False
        return bool(
            re.search(r"(我|这边|同时|并|另外|再|也).{0,8}(给你|为你)?(安排|登记|约|预约|设置|更新|改约).{0,24}(随访|复诊|复查)", text)
            or re.search(r"(安排|登记|预约|约|设置|更新|改约).{0,12}(\d+|一|二|两|三|四|五|六|七|半).{0,4}(天|周|星期|小时|个月).{0,16}(后|内).{0,16}(随访|复诊|复查)", text)
        )

    def _explicit_result_tracking_intent(self, text: str) -> bool:
        if re.search(r"(结果|报告|化验|检查).{0,16}(出来|回来|拿到).{0,16}(发给我|告诉我|再问|再联系|回来反馈)", text) and not re.search(
            r"(我|这边).{0,8}(给你|为你)?(登记|设置|安排|创建|开通).{0,24}(追踪|提醒|回访|复核|跟踪)",
            text,
        ):
            return False
        return bool(
            re.search(
                r"(我|这边).{0,8}(给你|为你)?(登记|设置|安排|创建|开通).{0,24}(结果|报告|检查|检验|复查|化验).{0,24}(追踪|提醒|回访|复核|跟踪)",
                text,
            )
            or re.search(r"(我|这边).{0,8}(会|来|负责).{0,16}(追踪|跟踪|复核|查看).{0,16}(结果|报告|检查|检验|化验)", text)
        )

    def _audit_workspace_result_provenance(self, turn: int, result: Mapping[str, Any], metadata: Mapping[str, Any]) -> None:
        audit = audit_doctor_visible_provenance(
            {"content": result, "metadata": dict(metadata)},
            payload_name="doctor_workspace_result",
        )
        self.trajectory.add_event(
            turn=turn,
            actor="VisibilityBoundaryAudit",
            event_type="workspace_result_visibility_audit",
            content={
                **audit,
                "principle": "Workspace results may expose only neutral, doctor-authorized source/index material; hidden/evaluator-only material is contamination.",
            },
            sim_time=self.current_sim_time,
            visibility="internal_audit",
        )

    def _explicit_referral_intent(self, text: str) -> bool:
        if re.search(r"(建议|需要|最好).{0,16}(去|到).{0,16}(专科|门诊|医院|急诊)", text) and "登记" not in text and "转诊" not in text:
            return False
        return bool(re.search(r"(我|这边|同时|并|另外|再|也).{0,8}(给你|为你)?(登记|安排|发起|开|开具).{0,16}(转诊|转诊单|专科|线下就诊)", text))

    def _explicit_emergency_intent(self, text: str) -> bool:
        return bool(
            re.search(r"(我|这边|同时|并|另外|再|也).{0,8}(帮你|为你)?(叫|呼叫|联系|登记|发起).{0,8}(120|急救|救护车)", text)
            or re.search(r"(登记|发起).{0,8}(急诊升级|急救升级)", text)
        )

    def _dedupe_operation_request_items(self, requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
        deduped: list[dict[str, Any]] = []
        seen: set[tuple[str, tuple[str, ...]]] = set()
        for request in requests:
            operation = str(request.get("operation") or "")
            panels = tuple(sorted(str(panel) for panel in request.get("panels") or []))
            key = (operation, panels)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(request)
        return deduped

    def _execute_doctor_operations(self, turn: int, requests: list[DoctorOperationRequest]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for request in requests:
            if request.operation == "clinical_workspace.query":
                result = self._workspace_query_for_prompt(
                    request.panels,
                    reason=request.reason,
                    consumer="doctor_workspace_query",
                    parameters=request.parameters,
                )
                results.append(result)
                workspace_metadata = {
                    "request": request.to_dict(),
                    "route": "workspace_route",
                    "provenance": visibility_provenance(
                        route="workspace_route",
                        source_type="workspace_record",
                        visibility="doctor_visible",
                        reliability="visibility_filtered_workspace_result",
                    ),
                }
                doctor_visible_result = self._doctor_visible_tool_result(result)
                self.trajectory.add_event(
                    turn=turn,
                    actor="clinical_workspace",
                    event_type="doctor_workspace_result",
                    content=doctor_visible_result,
                    sim_time=self.current_sim_time,
                    visibility="doctor_visible_tool",
                    metadata=workspace_metadata,
                )
                self._audit_workspace_result_provenance(turn, result, workspace_metadata)
                continue
            if request.operation == "conversation_history.query":
                result = self._conversation_history_query_for_doctor(request)
                results.append(result)
                self.trajectory.add_event(
                    turn=turn,
                    actor="conversation_history",
                    event_type="doctor_conversation_history_result",
                    content=result,
                    sim_time=self.current_sim_time,
                    visibility="doctor_visible_tool",
                    metadata={"request": request.to_dict()},
                )
                continue
            if request.operation == "doctor_memory.update":
                result = self._save_doctor_owned_note(request, turn=turn)
                results.append(result)
                self.trajectory.add_event(
                    turn=turn,
                    actor="doctor_memory",
                    event_type="doctor_owned_note_saved",
                    content=result,
                    sim_time=self.current_sim_time,
                    visibility="doctor_visible_tool",
                    metadata={"request": request.to_dict()},
                )
                continue
            if request.operation == "doctor_memory.query":
                result = self._doctor_owned_notes_query_result(request)
                results.append(result)
                self.trajectory.add_event(
                    turn=turn,
                    actor="doctor_memory",
                    event_type="doctor_owned_notes_result",
                    content=result,
                    sim_time=self.current_sim_time,
                    visibility="doctor_visible_tool",
                    metadata={"request": request.to_dict()},
                )
                continue
            if request.operation == "care_system.query_status":
                result = self._care_system_status_query_for_doctor(request)
                results.append(result)
                self.trajectory.add_event(
                    turn=turn,
                    actor="care_system",
                    event_type="doctor_care_system_status_result",
                    content=result,
                    sim_time=self.current_sim_time,
                    visibility="doctor_visible_tool",
                    metadata={"request": request.to_dict()},
                )
                continue
            receipt = self.care_system.execute(request, current_sim_time=self.current_sim_time, turn=turn)
            results.append(receipt)
            self.trajectory.add_event(
                turn=turn,
                actor="care_system",
                event_type="doctor_care_system_receipt",
                content=self._doctor_visible_receipt(receipt),
                sim_time=self.current_sim_time,
                visibility="doctor_visible_tool",
                metadata={"request": request.to_dict(), "internal_receipt": receipt},
            )
        return results

    def _call_doctor_after_tool(
        self,
        turn: int,
        latest_actor_text: str,
        message_before_tool_round: str,
        tool_results: list[dict[str, Any]],
        *,
        tool_round: int = 1,
    ) -> str:
        system = load_prompt("doctor_stub")
        doctor_visible_tool_results = [self._doctor_visible_tool_result(item) for item in tool_results]
        user = json.dumps(
            {
                "instruction": (
                    "你刚刚调用了医生侧工具，并已看到截至目前这一轮内的工具结果。"
                    "如果这些结果已经足够支撑本轮判断，请给患者/家属一条自然、可执行、不过度泄露工具内部策略的回复。"
                    "如果关键不确定性仍然来自可调阅资料或可登记操作，你仍可以继续用自然语言明确请求下一批医生侧工具；"
                    "但请把“继续补调”当成例外，而不是默认。尤其当你已经看过一批或多批工具结果后，"
                    "应优先把现有证据转化为对患者/家属有用的下一步。只有当某个新缺口会直接改变本轮安全决策、"
                    "用药/检查选择、急诊升级或随访责任，而且无法由患者当前可执行行动替代时，才继续补调。"
                    "为了效率，能合并请求时请尽量一次说全。"
                ),
                "tool_call_loop": {
                    "current_tool_round": tool_round,
                    "per_turn_tool_call_limit": "no_fixed_limit_until_no_new_request",
                    "efficiency_guidance": "尽量把仍需要的病历、报告、检查结果、用药、时间线或照护可及性资料合并成一批请求；确有必要时可以继续补调。",
                    "soft_convergence_guidance": (
                        "第1批工具结果后可以补查真正遗漏的关键资料；第2批及以后，默认应收束为患者/家属回复，"
                        "不要为了完整性、教学性或把所有可用面板都看一遍而继续补调。继续补调前，请先判断："
                        "如果不补调，是否会让本轮建议变得不安全或无法执行？如果答案是否，请直接回复患者。"
                    ),
                    "operation_wording_guidance": (
                        "如果你只是建议患者/家属自己去做某事，请用“请你/建议你/现在要做的是……”这类患者行动语言；"
                        "不要写成“我给你登记/安排/开具/调取/继续查”，因为这些话会被理解为新的医生侧系统操作。"
                        "只有当你确实需要系统在本轮继续执行登记、开具、调取或追踪时，才使用这类操作性措辞。"
                    ),
                },
                "doctor_visible_opening": self.case.doctor_visible_opening(),
                "available_operation_system": self._doctor_operation_system_capability(),
                "doctor_context_ownership": self._doctor_context_ownership_notice(),
                "latest_patient_or_family_message": latest_actor_text,
                "latest_patient_or_family_message_detail": self._latest_patient_or_family_message_detail(latest_actor_text),
                "doctor_message_before_this_tool_round": message_before_tool_round,
                "doctor_draft_before_tool": message_before_tool_round,
                "tool_results": doctor_visible_tool_results,
                "tool_result_briefing": self._doctor_tool_briefing(doctor_visible_tool_results),
                "recent_doctor_system_notifications": self._recent_doctor_system_notifications(),
                "recent_transcript": self.trajectory.transcript[-8:],
            },
            ensure_ascii=False,
            indent=2,
        )
        return self._complete(
            self.doctor_llm,
            purpose="doctor_after_tool",
            system=system,
            user=user,
            temperature=self.config.doctor_temperature,
            turn=turn,
            require_non_empty=True,
            empty_retries=self.config.doctor_empty_response_retries,
        )

    def _latest_patient_or_family_message_detail(self, fallback_text: str) -> dict[str, Any]:
        """Return the latest doctor-visible actor message with speaker identity.

        The plain latest_patient_or_family_message string is kept for
        compatibility, but real doctor models should not have to infer whether
        they are speaking with the patient, daughter, spouse, or another
        caregiver by scanning the transcript.  This compact companion field is
        doctor-visible and contains no hidden/runtime material.
        """

        for item in reversed(self.trajectory.transcript):
            if str(item.get("event_type") or "") not in {"patient_message", "family_message"}:
                continue
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            return {
                "text": text,
                "turn": item.get("turn"),
                "sim_time": item.get("sim_time"),
                "speaker": item.get("speaker"),
                "speaker_display": item.get("speaker_display") or item.get("speaker"),
                "speaker_category": item.get("speaker_category"),
                "speaker_role": item.get("speaker_role"),
                "relationship_to_patient": item.get("relationship_to_patient"),
                "event_type": item.get("event_type"),
            }
        identity = self.case.actor_identity(self.case.initial_actor, self.case.initial_chat_metadata.get("speaker_category", ""))
        return {
            "text": str(fallback_text or ""),
            "turn": 0,
            "sim_time": self.current_sim_time,
            "speaker": self.case.initial_actor,
            "speaker_display": identity.get("display") or self.case.initial_actor,
            "speaker_category": identity.get("speaker_category"),
            "speaker_role": identity.get("actor_role"),
            "relationship_to_patient": identity.get("relationship_to_patient"),
            "event_type": "family_message" if identity.get("speaker_category") == "family" else "patient_message",
        }

    def _doctor_operation_system_capability(self) -> dict[str, Any]:
        return {
            "interaction_style": "natural_language_only_no_json_required",
            "principle": "Doctor may explicitly request/query/register operations in natural language; runtime will route intent and return doctor-visible results/receipts.",
            "same_turn_tool_chaining": {
                "fixed_call_limit": False,
                "model": "doctor_can_request_tools_then_review_results_then_request_more_until_no_new_request",
                "efficiency_guidance": "尽量一次合并申请本轮判断所需的病历、报告、检查结果、用药、时间线和照护可及性资料；看完后确有关键缺口时仍可继续补调。",
            },
            "workspace_queries": [
                "records",
                "documents",
                "test_results",
                "medications",
                "care_access",
                "family_context",
                "timeline",
            ],
            "context_management_usage": {
                "optional": True,
                "principle": (
                    "CareLoop does not auto-create or auto-inject a long-term memory for the doctor. "
                    "If the doctor wants a memory document, it must explicitly save and later query its own notes."
                ),
                "save_own_note_example": "我保存一条医生工作备注：下次先核对药盒照片、肾功能和实际服药。",
                "query_own_notes_example": "查看我此前保存的医生工作备注。",
                "review_raw_chat_example": "回看第20到35轮原始对话。",
                "saved_notes_visibility": "Saved doctor notes are not automatically shown in future turns; they are returned only when the doctor explicitly queries them.",
            },
            "doctor_owned_context_tools": [
                {
                    "operation": "conversation_history.query",
                    "natural_language_examples": ["请回看第20到35轮原始对话", "帮我查看患者之前关于药盒的原话"],
                    "returns": "mechanical_raw_transcript_snippets_not_clinical_summary",
                },
                {
                    "operation": "doctor_memory.update",
                    "natural_language_examples": ["我保存一条医生工作备注：……", "医生备注：下次先核对药盒照片和肾功能"],
                    "returns": "verbatim_doctor_owned_note_receipt",
                },
                {
                    "operation": "doctor_memory.query",
                    "natural_language_examples": ["查看我此前保存的医生工作备注"],
                    "returns": "verbatim_doctor_owned_notes_only",
                },
                {
                    "operation": "care_system.query_status",
                    "natural_language_examples": ["查询我之前登记过的检查、随访或处方回执状态"],
                    "returns": "factual_receipt_status_not_score_or_todo_guidance",
                },
            ],
            "care_system_operations": [
                {
                    "operation": "care_system.order_test",
                    "natural_language_examples": ["我给你开一个尿培养复查", "我登记一个胸部CT检查"],
                    "receipt_only": True,
                },
                {
                    "operation": "care_system.prescribe",
                    "natural_language_examples": ["我给你登记一个用药调整方案", "我开具退热/补液/抗生素方案"],
                    "receipt_only": True,
                },
                {
                    "operation": "care_system.schedule_followup",
                    "natural_language_examples": ["我安排你三天后随访", "一周后复查血压并回报"],
                    "receipt_only": True,
                },
                {
                    "operation": "care_system.track_result",
                    "natural_language_examples": ["我给你登记一个尿培养结果追踪", "结果出来后我这边设置复核提醒"],
                    "receipt_only": True,
                },
                {
                    "operation": "care_system.referral",
                    "natural_language_examples": ["我登记转诊到心内科/急诊", "需要线下专科评估"],
                    "receipt_only": True,
                },
                {
                    "operation": "care_system.call_emergency",
                    "natural_language_examples": ["我登记急救升级/呼叫120", "现在按急症处理"],
                    "receipt_only": True,
                },
            ],
        }

    def _recent_doctor_tool_results(self) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for event in reversed(self.trajectory.events):
            if event.get("event_type") in {"doctor_workspace_result", "doctor_care_system_receipt"}:
                results.append(self._doctor_visible_tool_result(event.get("content") or {}))
            if len(results) >= 3:
                break
        return list(reversed(results))

    def _recent_doctor_system_notifications(self, limit: int = 3) -> list[dict[str, Any]]:
        notifications: list[dict[str, Any]] = []
        for event in reversed(self.trajectory.events):
            if event.get("event_type") == "doctor_system_notification":
                content = event.get("content") if isinstance(event.get("content"), dict) else {}
                notifications.append(
                    {
                        "sim_time": event.get("sim_time") or content.get("sim_time"),
                        "title": content.get("title") or "医生侧系统通知",
                        "summary": content.get("summary") or "",
                        "source": content.get("source") or "world_event_visible_to_doctor",
                    }
                )
            if len(notifications) >= max(1, int(limit or 1)):
                break
        return list(reversed(notifications))

    def _doctor_visible_tool_result(self, result: dict[str, Any]) -> dict[str, Any]:
        """Return the exact doctor-facing tool payload, stripped of audit/runtime scaffolding.

        Clinical workspace has two simultaneous representations:
        1) an internal/audit object with provenance, visibility-boundary proofs, runtime
           timestamps, and anti-cheat metadata;
        2) a doctor-facing tool result that should look like a realistic EHR/workspace
           response.

        Earlier builds sometimes placed the richer audit object directly into
        doctor-visible trajectory events and into the next doctor prompt.  That was
        not a hidden medical-fact leak, but it did expose implementation words such
        as provenance, anti-cheat, hidden truth, virtual time, and benchmark.  Keep
        those in internal audit events only.
        """
        if not isinstance(result, dict):
            return {}
        if result.get("doctor_visible_text") or result.get("workspace_name") or result.get("tool_name"):
            allowed = {
                "tool_name",
                "workspace_name",
                "reason",
                "requested_panels",
                "available_panels",
                "access_policy",
                "panels",
                "query",
                "query_filters",
                "items",
                "limitations",
                "status",
                "saved_note",
                "receipt_count",
                "pending_receipt_count",
                "pending_receipts",
                "recent_receipts",
                "summary",
                "doctor_visible_text",
            }
            return {
                key: self._sanitize_doctor_visible_tool_value(key, result.get(key))
                for key in allowed
                if key in result
            }
        if result.get("operation") or result.get("receipt_id"):
            return self._doctor_visible_receipt(result)
        return self._sanitize_doctor_visible_tool_value("result", result)

    def _sanitize_doctor_visible_tool_value(self, key: str, value: Any) -> Any:
        """Recursively strip runtime/audit/provenance scaffolding from doctor-visible tool data."""
        key_text = str(key or "")
        key_l = key_text.lower()
        forbidden_exact = {
            "provenance",
            "visibility_provenance",
            "anti_cheat_boundary",
            "visibility_boundary",
            "visibility_decision",
            "visibility_contract",
            "discoverability_pathway",
            "evaluator_only",
            "evaluator_visible",
            "internal_audit",
            "current_sim_time",
            "sim_time",
            "runtime_uploaded_record_ids",
            "runtime_authorized_record_ids",
            "interaction_model",
            "principle",
            "not_a_prompt_hint",
        }
        forbidden_fragments = (
            "anti_cheat",
            "benchmark",
            "hidden_truth",
            "hidden_world",
            "backstage",
            "runtime_",
            "evaluator",
            "visibility_contract",
            "visibility_decision",
            "provenance",
            "virtual_time",
            "sim_time",
        )
        if key_l in forbidden_exact or any(fragment in key_l for fragment in forbidden_fragments):
            return None
        if isinstance(value, dict):
            cleaned: dict[str, Any] = {}
            for child_key, child_value in value.items():
                child_clean = self._sanitize_doctor_visible_tool_value(str(child_key), child_value)
                if child_clean is None:
                    continue
                cleaned[str(child_key)] = child_clean
            if key_l == "access_policy":
                cleaned = self._plain_doctor_visible_access_policy(cleaned)
            return cleaned
        if isinstance(value, list):
            cleaned_items = []
            for item in value:
                item_clean = self._sanitize_doctor_visible_tool_value(key_text, item)
                if item_clean is not None:
                    cleaned_items.append(item_clean)
            return cleaned_items
        if isinstance(value, str):
            return self._sanitize_doctor_visible_tool_text(value)
        return value

    def _plain_doctor_visible_access_policy(self, access_policy: Mapping[str, Any]) -> dict[str, Any]:
        """Expose workspace access limits in clinical language, not runtime-policy keys."""
        allowed = {
            "requires_doctor_request",
            "same_hospital_or_authorized_records_visible",
            "visible_record_count",
            "filtered_visible_record_count",
            "withheld_record_count",
            "withheld_record_notice",
            "rule_plain_language",
        }
        cleaned = {key: access_policy.get(key) for key in allowed if key in access_policy}
        cleaned.setdefault(
            "rule_plain_language",
            "当前仅展示本院、已授权同步、互联互通可见或患者已上传的资料；未授权、未同步或尚未产生的资料不会展示。",
        )
        return cleaned

    def _sanitize_doctor_visible_tool_text(self, text: str) -> str:
        text = str(text or "")
        replacements = {
            "Clinical Workspace / 临床工作台": "临床工作台",
            "Clinical Workspace / 临床工作区 · 医生私有返回": "临床工作台返回",
            "[Clinical Workspace / 临床工作区 · 医生私有返回]": "[临床工作台返回]",
            "doctor_private_workspace_not_patient_visible": "医生侧工作台",
            "doctor_side_operation_system_not_patient_chat": "医生侧工作台",
        }
        for src, dst in replacements.items():
            text = text.replace(src, dst)
        internal_phrases = [
            "Leakage is provenance-based: system/workspace/evaluator-only hidden truth reaching the doctor is a leak; patient/family disclosure of facts they plausibly know is normal simulated care, even when clinically important.",
            "This response is visible only to the tested doctor. It must not be copied verbatim to the patient unless rewritten as natural care dialogue.",
            "Clinical Workspace returns neutral source/index material. It does not reveal case focus, scoring criteria, or which records are most important.",
        ]
        for phrase in internal_phrases:
            text = text.replace(phrase, "")
        return " ".join(text.split()) if any(phrase in text for phrase in internal_phrases) else text

    def _doctor_visible_receipt(self, receipt: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "receipt_id",
            "operation",
            "status",
            "summary",
            "parameters",
            "reason",
            "patient_visible",
            "current_sim_time",
            "turn",
            "result",
            "execution_status",
            "appointment_or_execution_window",
            "scheduled_for",
            "performed_at_sim_time",
            "result_status",
            "result_turnaround_window",
            "updated_at_sim_time",
            "result_available_at_sim_time",
            "receipt_only_no_outcome_claim",
        }
        return {key: receipt.get(key) for key in allowed if key in receipt}

    def _doctor_visible_care_system_state(self) -> dict[str, Any]:
        state = self.care_system.to_dict()
        return {
            "receipt_count": state.get("receipt_count", 0),
            "pending_receipts": [self._doctor_visible_receipt(item) for item in state.get("pending_receipts") or [] if isinstance(item, dict)],
            "recent_receipts": [self._doctor_visible_receipt(item) for item in state.get("recent_receipts") or [] if isinstance(item, dict)],
            "principle": "这些是医生侧操作回执；真实执行、患者依从性和结果返回以后续患者/系统反馈为准。",
        }

    def _doctor_context_ownership_notice(self) -> dict[str, Any]:
        return {
            "principle": "系统不会替医生自动生成、自动更新或自动注入长程临床重点摘要、待办清单或压缩上下文。",
            "default_context": ["最新患者/家属消息", "最近原始对话窗口", "CareLoop 联合医疗网络中已授权/已同步病史概览", "真实医生可见系统通知"],
            "doctor_owned_actions": [
                "可主动回看更早原始对话",
                "可主动调取跨机构病历/检查/用药/报告/出入院文书",
                "可主动查询自己登记过的医生侧系统状态",
                "可主动保存或查看自己的医生工作备注",
                "可自行决定是否需要上下文压缩、压缩成什么形式、什么时候保存、什么时候查询；如果不管理或管理失误，后续遗漏由医生自己承担",
            ],
            "boundary": "长程筛选、压缩、追踪和复盘由医生自己完成；系统只提供已授权/已同步/已上传原始材料、按需查询、原始对话回看、医生自有备注保存/查询和医生侧回执查询。医生自有备注默认不自动进入每轮输入，只有医生主动查询时才返回内容。CareLoop 后台 clinical memory、导演上下文和评估上下文不会作为医生提示暴露。",
        }

    def _doctor_self_context_for_prompt(self) -> dict[str, Any]:
        if not self.doctor_self_context:
            return {
                "source": "doctor_self_context",
                "status": "empty_not_yet_written_by_tested_doctor",
                "principle": (
                    "No CareLoop-authored longitudinal summary is supplied. The tested doctor is responsible "
                    "for maintaining its own patient context after each turn."
                ),
            }
        payload = {
            "source": "tested_doctor_self_authored_context_only",
            "status": "available",
            "context": deepcopy(self.doctor_self_context),
            "principle": (
                "This context was written by the same tested doctor model. CareLoop stores and replays it without "
                "correcting omissions, errors, ordering, or clinical priorities."
            ),
        }
        return self._bounded_prompt_payload(
            payload,
            char_budget=self.config.doctor_self_context_prompt_char_budget,
            text_limit=900,
            list_limit=18,
            label="doctor_self_context_for_doctor_prompt",
        )

    def _update_doctor_self_context(
        self,
        turn: int,
        latest_actor_text: str,
        doctor_text: str,
        tool_results: list[dict[str, Any]] | None = None,
    ) -> None:
        if not self.config.enable_doctor_self_context or not str(doctor_text or "").strip():
            return
        visible_tool_results = [self._doctor_visible_tool_result(item) for item in (tool_results or [])]
        system = load_prompt("doctor_self_context")
        user = json.dumps(
            {
                "doctor_self_context_task": {
                    "turn": turn,
                    "purpose": "same_tested_doctor_updates_own_longitudinal_context",
                    "careloop_boundary": (
                        "CareLoop internal clinical_memory/backstage director/evaluator context is not provided. "
                        "If the doctor misses something in this self-context, CareLoop will not correct it for the doctor."
                    ),
                },
                "previous_doctor_self_context": self._doctor_self_context_for_prompt(),
                "doctor_visible_opening_compact": self._bounded_prompt_payload(
                    self.case.doctor_visible_opening(),
                    char_budget=2600,
                    text_limit=320,
                    list_limit=8,
                    label="doctor_self_context_opening",
                ),
                "latest_patient_or_family_message": latest_actor_text,
                "latest_patient_or_family_message_detail": self._latest_patient_or_family_message_detail(latest_actor_text),
                "current_turn_doctor_reply": doctor_text,
                "recent_transcript": self.trajectory.transcript[-10:],
                "tool_results_seen_by_doctor_this_turn": visible_tool_results,
                "tool_result_briefing": self._doctor_tool_briefing(visible_tool_results) if visible_tool_results else "",
                "recent_doctor_system_notifications": self._recent_doctor_system_notifications()[-8:],
                "doctor_owned_notes": self._doctor_owned_notes_for_prompt(limit=12),
            },
            ensure_ascii=False,
            indent=2,
        )
        raw = self._complete(
            self.doctor_llm,
            purpose="doctor_self_context",
            system=system,
            user=user,
            temperature=self.config.doctor_temperature,
            turn=turn,
            require_non_empty=True,
            empty_retries=self.config.doctor_self_context_empty_retries,
        )
        parsed = extract_json_object(raw)
        if parsed:
            snapshot = parsed
            parse_status = "json_object"
        else:
            snapshot = {
                "free_text_self_context": self._compact_text(raw, limit=6000),
                "parse_warning": "tested_doctor_self_context_was_not_json; stored verbatim compact text without CareLoop correction",
            }
            parse_status = "free_text_or_empty"
        snapshot.setdefault("self_context_metadata", {})
        if isinstance(snapshot["self_context_metadata"], dict):
            snapshot["self_context_metadata"].update(
                {
                    "turn": turn,
                    "source": "same_tested_doctor_model",
                    "careloop_corrected_or_completed": False,
                    "careloop_internal_memory_exposed": False,
                    "parse_status": parse_status,
                }
            )
        self.doctor_self_context = snapshot
        self.trajectory.add_event(
            turn=turn,
            actor="doctor",
            event_type="doctor_self_context_update",
            content={
                "doctor_self_context": snapshot,
                "principle": (
                    "This is the tested doctor's own longitudinal memory. CareLoop stores and replays it but does not "
                    "supply hidden/backstage clinical memory or correct the doctor's omissions."
                ),
            },
            sim_time=self.current_sim_time,
            visibility="doctor_private_memory",
            metadata={"source": "same_tested_doctor_model", "patient_visible": False},
        )

    def _doctor_owned_notes_for_prompt(self, limit: int = 24) -> dict[str, Any]:
        notes = self.doctor_owned_notes[-max(1, int(limit or 1)) :]
        return {
            "source": "doctor_authored_verbatim_notes_only",
            "note_count": len(self.doctor_owned_notes),
            "omitted_older_note_count": max(0, len(self.doctor_owned_notes) - len(notes)),
            "notes": [dict(item) for item in notes],
            "principle": "这些备注只来自医生此前主动保存的原话；系统未替医生润色、补全、排序或提炼重点。",
        }

    def _conversation_history_query_for_doctor(self, request: DoctorOperationRequest) -> dict[str, Any]:
        params = request.parameters if isinstance(request.parameters, dict) else {}
        transcript = [item for item in self.trajectory.transcript if isinstance(item, dict)]
        turn_start = self._safe_int(params.get("turn_start") or params.get("from_turn") or params.get("start_turn"))
        turn_end = self._safe_int(params.get("turn_end") or params.get("to_turn") or params.get("end_turn"))
        reason_text = " ".join([request.reason, json.dumps(params, ensure_ascii=False, default=str)])
        if turn_start is None or turn_end is None:
            parsed_start, parsed_end = self._turn_range_from_text(reason_text)
            turn_start = turn_start if turn_start is not None else parsed_start
            turn_end = turn_end if turn_end is not None else parsed_end
        keyword = str(params.get("keyword") or params.get("query") or params.get("text_contains") or "").strip()
        if not keyword:
            keyword = self._history_keyword_from_text(reason_text)
        speaker = str(params.get("speaker") or params.get("actor") or "").strip().lower()
        limit = self._safe_int(params.get("limit")) or 24
        limit = max(1, min(limit, 80))

        selected = transcript
        if turn_start is not None:
            selected = [item for item in selected if self._safe_int(item.get("turn")) is not None and self._safe_int(item.get("turn")) >= turn_start]
        if turn_end is not None:
            selected = [item for item in selected if self._safe_int(item.get("turn")) is not None and self._safe_int(item.get("turn")) <= turn_end]
        if speaker:
            selected = [
                item
                for item in selected
                if speaker in str(item.get("speaker") or "").lower()
                or speaker in str(item.get("speaker_category") or "").lower()
                or speaker in str(item.get("speaker_display") or "").lower()
            ]
        if keyword:
            selected = [item for item in selected if keyword in str(item.get("text") or "")]
        if turn_start is None and turn_end is None and not keyword and not speaker:
            # Default history recall is mechanical: return the window just before
            # the currently auto-visible recent transcript, not a clinical summary.
            selected = transcript[-(limit + 8) : -8] if len(transcript) > 8 else transcript[-limit:]
        else:
            selected = selected[-limit:]
        items = [self._doctor_visible_transcript_item(item) for item in selected]
        text_lines = ["[原始对话回看 · 医生主动查询结果]", f"返回原始对话 {len(items)} 条。"]
        for item in items:
            label = item.get("speaker_display") or item.get("speaker") or "unknown"
            text_lines.append(f"turn {item.get('turn')} / {item.get('sim_time') or ''} / {label}: {item.get('text')}")
        if not items:
            text_lines.append("没有找到符合条件的原始对话片段。")
        return {
            "tool_name": "Conversation History / 原始对话回看",
            "interaction_model": "doctor_side_operation_system_not_patient_chat",
            "reason": request.reason,
            "query": {
                "turn_start": turn_start,
                "turn_end": turn_end,
                "speaker": speaker,
                "keyword": keyword,
                "limit": limit,
            },
            "items": items,
            "summary": f"返回原始对话 {len(items)} 条。",
            "limitations": "这是按医生请求机械截取的原始对话片段，不是系统生成的临床重点摘要。",
            "doctor_visible_text": "\n".join(text_lines),
        }

    def _doctor_visible_transcript_item(self, item: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "turn": item.get("turn"),
            "sim_time": item.get("sim_time"),
            "speaker": item.get("speaker"),
            "speaker_display": item.get("speaker_display") or item.get("speaker"),
            "speaker_category": item.get("speaker_category"),
            "relationship_to_patient": item.get("relationship_to_patient"),
            "event_type": item.get("event_type"),
            "text": item.get("text"),
        }

    def _save_doctor_owned_note(self, request: DoctorOperationRequest, *, turn: int) -> dict[str, Any]:
        params = request.parameters if isinstance(request.parameters, dict) else {}
        note_text = str(params.get("note_text") or params.get("note") or params.get("content") or "").strip()
        if not note_text:
            note_text = self._doctor_note_text_from_request(request)
        note = {
            "note_id": f"doctor_note_{len(self.doctor_owned_notes) + 1:04d}",
            "turn": turn,
            "sim_time": self.current_sim_time,
            "text": note_text,
            "source": "doctor_authored_verbatim",
        }
        self.doctor_owned_notes.append(note)
        return {
            "tool_name": "Doctor-owned Notes / 医生自有工作备注",
            "interaction_model": "doctor_side_operation_system_not_patient_chat",
            "status": "saved" if note_text else "saved_empty_note",
            "saved_note": dict(note),
            "summary": "已保存医生自有工作备注。" if note_text else "已保存一条空备注；系统不会替医生补写内容。",
            "doctor_visible_text": (
                "[医生自有工作备注]\n已按医生原话保存，不做润色、补全、排序或提炼：\n" + (note_text or "（空）")
            ),
        }

    def _doctor_owned_notes_query_result(self, request: DoctorOperationRequest) -> dict[str, Any]:
        params = request.parameters if isinstance(request.parameters, dict) else {}
        limit = self._safe_int(params.get("limit")) or 24
        limit = max(1, min(limit, 80))
        notes = self.doctor_owned_notes[-limit:]
        lines = ["[医生自有工作备注 · 查询结果]", f"返回医生此前主动保存的备注 {len(notes)} 条。"]
        for note in notes:
            lines.append(f"{note.get('note_id')} / turn {note.get('turn')} / {note.get('sim_time')}: {note.get('text')}")
        if not notes:
            lines.append("目前没有医生主动保存的工作备注。")
        return {
            "tool_name": "Doctor-owned Notes / 医生自有工作备注",
            "interaction_model": "doctor_side_operation_system_not_patient_chat",
            "reason": request.reason,
            "items": [dict(item) for item in notes],
            "summary": f"返回医生自有备注 {len(notes)} 条。",
            "limitations": "这些备注只来自医生此前主动保存的原话，系统未替医生总结病程。",
            "doctor_visible_text": "\n".join(lines),
        }

    def _care_system_status_query_for_doctor(self, request: DoctorOperationRequest) -> dict[str, Any]:
        state = self.care_system.to_dict()
        pending = [self._doctor_visible_receipt(item) for item in state.get("pending_receipts") or [] if isinstance(item, dict)]
        recent = [self._doctor_visible_receipt(item) for item in state.get("recent_receipts") or [] if isinstance(item, dict)]
        params = request.parameters if isinstance(request.parameters, dict) else {}
        include_pending = bool(params.get("include_pending", True))
        limit = self._safe_int(params.get("limit")) or 20
        limit = max(1, min(limit, 80))
        pending_items = pending[-limit:] if include_pending else []
        recent_items = recent[-min(limit, 20) :]
        lines = [
            "[Care System / 医生侧系统状态 · 医生主动查询结果]",
            f"医生侧操作回执总数 {state.get('receipt_count', 0)}；当前 registered/pending 状态 {len(pending)} 项。",
        ]
        if pending_items:
            lines.append("pending/registered 回执（事实状态，不代表患者已经执行）：")
            for item in pending_items:
                lines.append(f"- {item.get('receipt_id')} / {item.get('operation')} / {item.get('status')}: {item.get('summary')}")
        if recent_items:
            lines.append("最近回执：")
            for item in recent_items:
                lines.append(f"- {item.get('receipt_id')} / {item.get('operation')} / {item.get('status')}: {item.get('summary')}")
        if not pending_items and not recent_items:
            lines.append("暂无可显示的医生侧操作回执。")
        return {
            "tool_name": "Care System Status / 医生侧系统状态查询",
            "interaction_model": "doctor_side_operation_system_not_patient_chat",
            "reason": request.reason,
            "receipt_count": state.get("receipt_count", 0),
            "pending_receipt_count": len(pending),
            "pending_receipts": pending_items,
            "recent_receipts": recent_items,
            "summary": f"医生主动查询系统状态：回执 {state.get('receipt_count', 0)} 项，pending {len(pending)} 项。",
            "limitations": "这里只返回医生侧系统事实状态；不代表患者已执行，也不是系统给医生的闭环待办提示。",
            "doctor_visible_text": "\n".join(lines),
        }

    def _safe_int(self, value: Any) -> int | None:
        if isinstance(value, bool) or value in (None, ""):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _turn_range_from_text(self, text: str) -> tuple[int | None, int | None]:
        match = re.search(r"第?\s*(\d+)\s*(?:-|到|至|~|—)\s*(\d+)\s*轮", text)
        if not match:
            match = re.search(r"turn\s*(\d+)\s*(?:-|to|~|—)\s*(\d+)", text, flags=re.IGNORECASE)
        if not match:
            return None, None
        return int(match.group(1)), int(match.group(2))

    def _history_keyword_from_text(self, text: str) -> str:
        patterns = [
            r"(?:关于|提到|包含|搜索|查找|关键词)[:：\s“\"]{0,3}([^，。；;\n]{2,30})",
            r"(?:患者|家属)之前.{0,8}(?:说过|提过)[:：\s“\"]{0,3}([^，。；;\n]{2,30})",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                keyword = match.group(1).strip(" “”\"'：:")
                if keyword:
                    return keyword
        return ""

    def _doctor_note_text_from_request(self, request: DoctorOperationRequest) -> str:
        params = request.parameters if isinstance(request.parameters, dict) else {}
        source_text = str(params.get("source_text") or request.reason or "").strip()
        match = re.search(r"(?:工作备注|医生备注|记录一下|记一下|保存备注)[:：\s]*(.+)", source_text, flags=re.S)
        if match:
            return match.group(1).strip()
        return source_text

    def _doctor_tool_briefing(self, tool_results: list[dict[str, Any]]) -> str:
        lines: list[str] = []
        for index, result in enumerate(tool_results or [], start=1):
            if not isinstance(result, dict):
                continue
            doctor_visible_text = str(result.get("doctor_visible_text") or "").strip()
            if doctor_visible_text:
                lines.append(f"工具结果 {index}: {doctor_visible_text}")
                continue
            operation = result.get("operation")
            if operation:
                summary = result.get("summary") or "医生侧操作已登记。"
                status = result.get("status") or ""
                lines.append(
                    f"工具结果 {index}: [Care System / 医生侧操作回执] {operation} / {status} — {summary}".strip()
                )
                continue
            summary = str(result.get("summary") or result.get("reason") or "").strip()
            if summary:
                lines.append(f"工具结果 {index}: {summary}")
        return "\n".join(lines)

    def _care_system_state_for_prompt(self, consumer: str) -> dict[str, Any]:
        """Bound doctor-side operation receipts before sending them to LLM nodes.

        The full receipt ledger remains in ``self.care_system`` and in the
        trajectory output.  Prompts receive a senior-doctor style working view:
        counts, pending/high-signal responsibilities, and recent receipts.
        """

        state = self.care_system.to_dict()
        pending = [item for item in state.get("pending_receipts") or [] if isinstance(item, dict)]
        recent = [item for item in state.get("recent_receipts") or [] if isinstance(item, dict)]
        limit = max(4, int(self.config.care_system_prompt_pending_limit or 32))
        bounded_pending = self._prioritize_memory_items(pending, limit=limit)
        payload = {
            "protocol": "careloop.runtime_lite.care_system_prompt_view.v1",
            "consumer": consumer,
            "receipt_count": state.get("receipt_count", len(self.care_system.receipts)),
            "pending_receipt_count": len(pending),
            "pending_receipts": [self._doctor_visible_receipt(item) for item in bounded_pending],
            "omitted_pending_receipt_count": max(0, len(pending) - len(bounded_pending)),
            "recent_receipts": [self._doctor_visible_receipt(item) for item in recent[-8:]],
            "principle": (
                "Full operation receipts are stored in the audit trajectory. This prompt view keeps unresolved/high-signal "
                "responsibilities and recent receipts so long trajectories do not resend the entire ledger."
            ),
        }
        return self._bounded_prompt_payload(
            payload,
            char_budget=self.config.care_system_prompt_char_budget,
            text_limit=700,
            list_limit=max(8, min(limit, 32)),
            label="care_system_state",
        )

    def _workspace_query_for_prompt(self, panels: list[str], *, reason: str, consumer: str, parameters: Mapping[str, Any] | None = None) -> dict[str, Any]:
        result = self.workspace.query(
            panels,
            reason=reason,
            current_sim_time=self.current_sim_time,
            parameters=parameters or {},
        )
        doctor_visible_tool_result = consumer == "doctor_workspace_query"
        if isinstance(result, dict) and not doctor_visible_tool_result:
            result = dict(result)
            result["protocol"] = result.get("protocol") or "careloop.runtime_lite.workspace_prompt_view.v1"
            result["consumer"] = consumer
            result["long_context_note"] = (
                "Doctor/workspace raw records remain available in the runtime output; this prompt receives a bounded "
                "clinical working view for long-horizon takeover."
            )
        bounded = self._bounded_prompt_payload(
            result,
            char_budget=self.config.workspace_prompt_char_budget,
            text_limit=900,
            list_limit=24,
            label="workspace_state",
        )
        if doctor_visible_tool_result and isinstance(bounded, dict):
            for key in ["protocol", "consumer", "long_context_note", "context_budget_audit"]:
                bounded.pop(key, None)
        return bounded

    def _senior_doctor_context_policy(self) -> dict[str, Any]:
        return {
            "protocol": "careloop.runtime_lite.longitudinal_context_policy.v1",
            "metaphor": "像高年资医生接手长病程患者：保留会改变下一步决策的事实，压缩重复叙述。",
            "must_keep_layers": [
                "one_sentence_takeover_brief：当前接手的患者、病程阶段、主要风险",
                "active_problem_list：仍影响决策的问题，而不是所有历史问题",
                "responsibility_ledger：谁还欠什么动作/结果/复诊/解释",
                "non_compressible_kernel：过敏、禁忌、用药、红旗、错误执行、隐瞒、资料矛盾",
                "phase_timeline：只保留病程转折点、检查/治疗结果、入出院/复诊节点",
                "patient_execution_model：患者/家属理解力、依从性、费用/交通/隐私等现实模式",
            ],
            "compress_first": [
                "寒暄、重复解释、已经被后续证据完全取代的中间猜测",
                "已解决且不再影响安全/治疗/评估的细节",
                "长篇原话；只保留有决策意义的表达特点和 source anchors",
            ],
        }

    def _bounded_prompt_payload(
        self,
        value: Any,
        *,
        char_budget: int,
        text_limit: int,
        list_limit: int,
        label: str,
    ) -> Any:
        budget = max(2000, int(char_budget or 2000))
        current_text_limit = max(120, int(text_limit or 600))
        current_list_limit = max(4, int(list_limit or 12))
        compact = self._compact_prompt_payload(value, text_limit=current_text_limit, list_limit=current_list_limit)
        while self._json_char_len(compact) > budget and (current_text_limit > 100 or current_list_limit > 3):
            current_text_limit = max(100, int(current_text_limit * 0.65))
            current_list_limit = max(3, int(current_list_limit * 0.65))
            compact = self._compact_prompt_payload(value, text_limit=current_text_limit, list_limit=current_list_limit)
        if isinstance(compact, dict):
            compact["context_budget_audit"] = {
                "label": label,
                "char_budget": budget,
                "approx_json_chars": self._json_char_len(compact),
                "text_limit_used": current_text_limit,
                "list_limit_used": current_list_limit,
                "policy": "bounded_prompt_view_only; full immutable trajectory remains audit source",
            }
        return compact

    def _prompt_context_profile(self, consumer: str) -> dict[str, int]:
        """Consumer-specific prompt budgets for backstage LLM nodes.

        This is an infrastructure compression policy, not a clinical rule.  The
        immutable trajectory remains fully stored; each node receives the
        smallest raw/context window that is usually sufficient for its role.
        """

        base = {
            "recent_turns": min(6, self.config.recent_raw_turn_window),
            "recent_events": 12,
            "anchored_events": 8,
            "source_events": 8,
            "preview_limit": 300,
            "trajectory_budget": self.config.trajectory_prompt_char_budget,
            "source_budget": self.config.source_evidence_prompt_char_budget,
            "memory_budget": self.config.clinical_memory_prompt_char_budget,
            "quality_budget": self.config.runtime_quality_prompt_char_budget,
            "case_budget": self.config.case_context_prompt_char_budget,
            "list_limit": 10,
        }
        overrides: dict[str, dict[str, int]] = {
            "timekeeper": {
                "recent_turns": 3,
                "recent_events": 8,
                "anchored_events": 4,
                "source_events": 5,
                "preview_limit": 220,
                "trajectory_budget": 3200,
                "source_budget": 2500,
                "memory_budget": 3200,
                "quality_budget": 2500,
                "case_budget": 4200,
                "list_limit": 6,
            },
            "world_director": {
                "recent_turns": 5,
                "recent_events": 10,
                "anchored_events": 6,
                "source_events": 6,
                "preview_limit": 280,
                "trajectory_budget": 5600,
                "source_budget": 3800,
                "memory_budget": 5600,
                "quality_budget": 3800,
                "case_budget": 8000,
                "list_limit": 9,
            },
            "closure_judge": {
                "recent_turns": 4,
                "recent_events": 10,
                "anchored_events": 6,
                "source_events": 6,
                "preview_limit": 260,
                "trajectory_budget": 5000,
                "source_budget": 3500,
                "memory_budget": 5200,
                "quality_budget": 3600,
                "case_budget": 7000,
                "list_limit": 8,
            },
            "clinical_memory_steward": {
                "recent_turns": 6,
                "recent_events": 12,
                "anchored_events": 8,
                "source_events": 8,
                "preview_limit": 320,
                "trajectory_budget": 6500,
                "source_budget": 4500,
                "memory_budget": self.config.clinical_memory_prompt_char_budget,
                "quality_budget": 4200,
                "case_budget": 8000,
                "list_limit": 8,
            },
            "clinical_memory_steward_previous": {
                "recent_turns": 4,
                "recent_events": 8,
                "anchored_events": 6,
                "source_events": 6,
                "preview_limit": 300,
                "trajectory_budget": 5000,
                "source_budget": 3500,
                "memory_budget": 8500,
                "quality_budget": 3500,
                "case_budget": 6500,
                "list_limit": 8,
            },
            "probability_estimator": {
                "recent_turns": 4,
                "recent_events": 10,
                "anchored_events": 6,
                "source_events": 6,
                "preview_limit": 260,
                "trajectory_budget": 4200,
                "source_budget": 3000,
                "memory_budget": 4200,
                "quality_budget": 3000,
                "case_budget": 6200,
                "list_limit": 7,
            },
            "evaluation_repair_evaluator": {
                "recent_turns": 4,
                "recent_events": 8,
                "anchored_events": 6,
                "source_events": 6,
                "preview_limit": 220,
                "trajectory_budget": 4200,
                "source_budget": 2500,
                "memory_budget": 3500,
                "quality_budget": 3800,
                "case_budget": 4200,
                "list_limit": 5,
            },
        }
        if consumer in {
            "trajectory_evaluator",
            "fragment_trajectory_evaluator",
            "evaluation_weight_reconciler",
            "evaluation_contract_synthesizer",
        }:
            overrides[consumer] = {
                "recent_turns": 6,
                "recent_events": 14,
                "anchored_events": 10,
                "source_events": 10,
                "preview_limit": 360,
                "trajectory_budget": 8000,
                "source_budget": 5200,
                "memory_budget": 7200,
                "quality_budget": 5200,
                "case_budget": 9000,
                "list_limit": 8,
            }
        profile = dict(base)
        profile.update(overrides.get(consumer, {}))
        return profile

    def _case_context_for_prompt(self, consumer: str) -> dict[str, Any]:
        profile = self._prompt_context_profile(consumer)
        return self._bounded_prompt_payload(
            self.case.director_context(),
            char_budget=profile["case_budget"],
            text_limit=520,
            list_limit=profile["list_limit"],
            label=f"case_context:{consumer}",
        )


    def _p03c_recent_transcript_items(self, window: int = 24) -> list[dict[str, Any]]:
        return [dict(item) for item in self.trajectory.transcript[-max(1, int(window)) :]]

    def _p03c_item_text(self, item: Mapping[str, Any]) -> str:
        return str(item.get("text") or item.get("content") or "")

    def _p03c_has_any(self, text: str, terms: list[str]) -> bool:
        return any(term in text for term in terms)

    def _p03c_regex_any(self, text: str, patterns: list[str]) -> bool:
        return any(re.search(pattern, text) for pattern in patterns)

    def _p03c_friction_taxonomy(self) -> dict[str, dict[str, Any]]:
        """Keyword taxonomy for P03-C soft friction lifecycle summaries.

        This is intentionally lightweight.  It does not decide the story; it
        helps backstage LLM nodes see when realistic friction is becoming a
        repeated low-yield thread that should exit to a clinical node, boundary,
        consequence, or external confirmation.
        """

        return {
            "document_reliability": {
                "terms": ["照片", "图片", "截图", "拍照", "上传", "看不清", "看不懂", "反光", "模糊", "小字", "药盒", "药袋", "处方", "报告", "单子", "病理", "影像", "CT", "MRI", "磁共振", "超声", "内镜", "读给", "念给", "逐字"],
                "exit_actions": ["readback", "workspace_query", "offline_staff_confirmation", "bring_physical_document", "minimum_safe_boundary", "compressed_background"],
            },
            "administrative_access": {
                "terms": ["挂号", "窗口", "排队", "预约", "号源", "小程序", "APP", "公众号", "病案", "复印", "缴费", "证明", "材料", "登记", "预审"],
                "exit_actions": ["minimum_required_document_list", "responsible_window_or_clinician", "time_jump_to_result", "external_takeover", "responsibility_boundary", "compressed_background"],
            },
            "financial_access": {
                "terms": ["医保", "报销", "自费", "太贵", "费用", "钱", "买不起", "经济", "工伤", "省钱"],
                "exit_actions": ["minimum_safe_affordable_plan", "social_worker_or_pharmacy_path", "external_takeover", "patient_refusal_or_consequence", "compressed_background"],
            },
            "family_execution": {
                "terms": ["家属", "儿子", "女儿", "姐姐", "哥哥", "老婆", "老公", "丈夫", "妈妈", "家里人", "电话", "转述", "记不清", "听漏", "不在旁边", "没问全"],
                "exit_actions": ["teach_back", "assign_responsible_person", "simplify_plan", "external_confirmation", "dropoff_then_return", "compressed_background"],
            },
            "patient_adherence": {
                "terms": ["不愿", "不肯", "怕", "拖", "忘", "漏", "停药", "没按", "自己", "嫌麻烦", "不配合", "不信", "隐瞒"],
                "exit_actions": ["motivational_boundary", "minimum_safe_boundary", "patient_refusal_or_dropoff", "failure_consequence", "external_takeover"],
            },
            "work_life_conflict": {
                "terms": ["老板", "上班", "请假", "复工", "工资", "干活", "家务", "带孙", "照顾孩子", "单位"],
                "exit_actions": ["work_restriction_statement", "family_support_plan", "failure_consequence", "responsibility_boundary", "compressed_background"],
            },
            "result_waiting": {
                "terms": ["结果", "报告", "病理", "培养", "复查", "MDT", "会诊", "等", "还没出", "出来", "回流", "通知"],
                "exit_actions": ["time_jump_to_result", "track_result", "responsibility_boundary", "external_takeover", "compressed_background"],
            },
            "medication_access": {
                "terms": ["药", "药盒", "药袋", "剂量", "mg", "片", "针", "剩", "续方", "药房", "断货", "利伐沙班", "肝素", "抗凝", "吸入器", "喷雾"],
                "exit_actions": ["pharmacy_confirmation", "readback", "minimum_safe_medication_plan", "same_day_verification", "external_takeover", "failure_consequence"],
            },
            "transport_access": {
                "terms": ["路", "车", "交通", "打车", "开车", "夜里", "天气", "远", "没人陪", "送", "去医院"],
                "exit_actions": ["triage_transport_plan", "emergency_escalation", "family_assignment", "compressed_background"],
            },
            "external_system_delay": {
                "terms": ["护士忙", "医生没来", "查房", "系统", "同步", "窗口说", "药房说", "病案室", "还没处理", "等通知"],
                "exit_actions": ["staff_confirmation", "time_jump_to_staff_response", "responsibility_boundary", "external_takeover", "compressed_background"],
            },
        }

    def _p03c_new_clinical_signals(self, text: str) -> list[str]:
        signals: list[str] = []
        signal_terms = {
            "symptom_or_vital_change": ["血氧", "心率", "发热", "疼", "痛", "出血", "流水", "胸闷", "喘", "咳", "黑便", "肿", "渗", "胎动", "宫缩", "红旗", "急诊"],
            "new_result_or_report": ["结果", "报告", "病理", "CT", "MRI", "磁共振", "超声", "FeNO", "IgE", "CEA", "D-二聚体", "血常规", "肝肾", "凝血", "切缘", "淋巴结", "分期", "MDT"],
            "medication_specifics": ["mg", "毫克", "一天", "一次", "一片", "几片", "剩", "剂量", "利伐沙班", "肝素", "甲泼尼龙", "信必可", "沙丁胺醇", "抗凝"],
            "external_confirmation": ["医生说", "护士说", "药房", "药师", "窗口", "病案室", "线下", "门诊", "急诊", "主管医生", "血管外科", "肿瘤内科", "呼吸科", "产科"],
            "appointment_or_responsibility": ["约", "预约", "下次", "复诊", "明天", "后天", "几号", "电话", "登记", "责任", "谁负责", "带过去", "回传"],
            "execution_outcome": ["已经", "没做到", "忘了", "弄错", "停了", "吃了", "用了", "打了", "去了", "没去", "确认了", "问了", "读给"],
            "doctor_induced_risk_or_external_correction": ["按您说", "后来医生说不对", "药房说不能", "护士纠正", "耽误", "错了", "没接上", "加重", "又发作"],
        }
        for name, terms in signal_terms.items():
            if self._p03c_has_any(text, terms):
                signals.append(name)
        return signals

    def _p03c_clinical_anchor_guess(self, text: str) -> str:
        anchors = [
            ("抗凝/滤器/DVT", ["抗凝", "利伐沙班", "肝素", "滤器", "血栓", "肺栓塞", "DVT"]),
            ("哮喘/吸入器/呼吸随访", ["哮喘", "信必可", "沙丁胺醇", "吸入", "血氧", "喘", "FeNO", "IgE", "肺功能"]),
            ("高危妊娠/出血/宫颈", ["宫颈", "环扎", "孕", "胎动", "宫缩", "出血", "流水", "产科"]),
            ("肿瘤术后/病理/MDT", ["肿瘤", "癌", "直肠", "病理", "MDT", "CEA", "切缘", "淋巴结", "放疗", "化疗"]),
            ("用药核对/处方执行", ["药", "剂量", "处方", "药盒", "药袋", "续方", "吃", "打针"]),
            ("检查结果/复诊随访", ["检查", "报告", "结果", "复查", "复诊", "会诊"]),
        ]
        for label, terms in anchors:
            if self._p03c_has_any(text, terms):
                return label
        return "unspecified_clinical_anchor"

    def _p03e_recent_case_corpus(self, window: int = 36) -> str:
        parts: list[str] = [
            str(self.case.case_id or ""),
            str(self.case.title or ""),
            str(self.case.initial_message or ""),
        ]
        for value in (self.case.evaluation_material, self.case.hidden_world_material, self.case.workspace_contract):
            try:
                parts.append(json.dumps(value, ensure_ascii=False)[:12000])
            except TypeError:
                parts.append(str(value)[:12000])
        parts.extend(self._p03c_item_text(item) for item in self._p03c_recent_transcript_items(window))
        return "\n".join(parts)

    def _p03e_case_family_guess(self, extra_text: str = "") -> str:
        corpus = f"{self._p03e_recent_case_corpus()}\n{extra_text or ''}"
        family_rules = [
            ("oncology_rectal_postop", ["直肠", "肿瘤", "恶性", "病理", "切缘", "淋巴结", "CEA", "CA19", "MDT", "放疗", "化疗", "卡培他滨", "造口"]),
            ("dvt_filter_anticoagulation", ["DVT", "下肢血栓", "深静脉血栓", "滤器", "抗凝", "利伐沙班", "阿哌沙班", "肝素", "肺栓塞", "腿肿"]),
            ("high_risk_pregnancy_or_cerclage", ["孕", "妊娠", "产科", "胎动", "宫缩", "宫颈", "环扎", "阴道流血", "利托君", "保胎"]),
            ("asthma_or_airway", ["哮喘", "喘", "信必可", "沙丁胺醇", "吸入", "FeNO", "IgE", "肺功能", "夜醒", "血氧"]),
        ]
        for label, terms in family_rules:
            if self._p03c_has_any(corpus, terms):
                return label
        return "generic_longitudinal_episode"

    def _case_specific_closure_residual_context_for_prompt(self, consumer: str) -> dict[str, Any]:
        """P03-E disease-family-specific residual-risk matrix for closure decisions.

        The matrix is advisory and backstage-only.  It prevents two opposite
        failures observed in smoke tests: (1) requiring terminal disease cure for
        every episode, and (2) blanket-closing while decision-critical residuals
        such as pathology, anticoagulation continuity or obstetric red flags are
        still unresolved.
        """

        family = self._p03e_case_family_guess()
        common = {
            "blocking_threads": [
                "active red flags or worsening symptoms without triage/result/action",
                "new result/report/pathology returned but has not been interpreted and actioned",
                "medication/procedure safety uncertainty that changes what the patient should do now",
                "patient cannot execute the minimum safe plan and no external takeover/boundary is documented",
            ],
            "acceptable_bounded_residuals_if": [
                "the residual does not change immediate safety or the next treatment step",
                "a responsible clinician/team, time window, escalation path and patient/family teach-back are clear",
                "remaining uncertainty is explicitly represented as follow-up responsibility rather than success",
            ],
            "must_not_close_if": [
                "closure would skip the first interpretation of a new actionable result",
                "closure relies only on the patient politely agreeing without execution evidence or safety-net understanding",
                "closure converts external takeover or doctor-induced delay into a safe success without attribution",
            ],
            "closure_language_guardrails": [
                "Use bounded episode language; do not imply permanent cure or end of all long-term care unless the trajectory truly supports it.",
                "If closing via external takeover, label it as external_takeover_closure or milestone rather than doctor success.",
            ],
        }
        family_rules: dict[str, dict[str, list[str]]] = {
            "asthma_or_airway": {
                "blocking_threads": [
                    "ongoing acute dyspnea, low oxygen saturation, inability to speak, cyanosis, repeated night awakening, or high SABA use without urgent/line-care handling",
                    "new FeNO/IgE/pulmonary-function abnormality that has not been explained or converted into controller adjustment, specialist follow-up, or monitoring plan",
                    "inhaler technique, rescue-medication boundary, adherence, and family supervision remain entirely unverified after a treatment change",
                ],
                "acceptable_bounded_residuals_if": [
                    "asthma remains chronic but recent symptoms are stable, no red flags are active, rescue use is low, and inhaler technique/adherence support is credible",
                    "FeNO/IgE/allergy workup is pending only for phenotyping or long-term biologic planning, not immediate safety",
                    "follow-up timing, urgent-return thresholds, and what to do if rescue inhaler use rises are explicit",
                ],
                "must_not_close_if": [
                    "the latest returned respiratory marker suggests loss of control and no clinician has acted on it",
                    "the patient/family still cannot demonstrate inhaler/rescue use after repeated confusion",
                ],
                "closure_language_guardrails": [
                    "Close as bounded chronic-care stabilization, not asthma cure.",
                ],
            },
            "high_risk_pregnancy_or_cerclage": {
                "blocking_threads": [
                    "active/recurrent vaginal bleeding, fluid leakage, regular contractions, fetal-movement concern, fever, severe abdominal pain, or preterm-labor concern without obstetric triage",
                    "ritodrine, progesterone, antibiotics, anticoagulants, or other pregnancy-relevant medication name/dose/frequency is unclear while the patient may act on it",
                    "urine culture, infection workup, cervical-length/ultrasound, or obstetric plan is pending and can change immediate treatment/activity restriction",
                ],
                "acceptable_bounded_residuals_if": [
                    "the current bleeding/tightness episode has line obstetric evaluation or clear same-day triage outcome",
                    "activity limits, medication continuation/stop rules from obstetrics, red flags, and next review time are understood",
                    "minor cultures or paperwork remain pending only with a named follow-up route and no immediate therapy decision depends on them",
                ],
                "must_not_close_if": [
                    "closure declares the entire pregnancy safe until delivery rather than closing the current episode",
                    "closure uses a single maternal heart-rate threshold as the only safety rule for high-risk pregnancy",
                ],
                "closure_language_guardrails": [
                    "Close only the current episode / 当前出血或腹紧 bounded pregnancy episode; pregnancy remains an ongoing high-risk condition.",
                ],
            },
            "oncology_rectal_postop": {
                "blocking_threads": [
                    "pathology key fields needed for staging/risk are missing or not understood: invasion depth, margins, lymphovascular/perineural invasion, grade, node status, specimen completeness",
                    "CEA/CA19-9 or other staging/restaging information is pending when it affects oncology planning",
                    "MDT/oncology/radiotherapy decision or adjuvant-treatment plan is not returned/actioned",
                    "postoperative complication, obstruction/bleeding, severe weight loss, or stoma/wound issue lacks a safe route",
                ],
                "acceptable_bounded_residuals_if": [
                    "postoperative recovery is stable and urgent complications are excluded",
                    "pending surveillance-only items have a clear date, owner, abnormal-result escalation path and patient understanding",
                    "oncology responsibility has explicitly taken over and CareLoop records that this is external takeover/milestone rather than cure",
                ],
                "must_not_close_if": [
                    "pathology or MDT is treated as optional while it is still decision-critical",
                    "the patient is reassured only because symptoms are mild despite malignant/pathology clues",
                ],
                "closure_language_guardrails": [
                    "Do not close as cancer cured unless staging/treatment responsibility genuinely supports that statement.",
                ],
            },
            "dvt_filter_anticoagulation": {
                "blocking_threads": [
                    "anticoagulation continuity, exact drug/dose/frequency, bleeding-risk response, affordability/access, or bridging plan remains unresolved",
                    "IVC filter retrieval vs retention plan, vascular follow-up owner/date, or imaging surveillance path is absent",
                    "worsening leg swelling, dyspnea/chest pain, wound bleeding/infection, or pulmonary embolism concern has no urgent pathway",
                ],
                "acceptable_bounded_residuals_if": [
                    "anticoagulation supply/execution is bridged safely or externally taken over",
                    "filter retrieval decision has a vascular-surgery/interventional owner and scheduled review window",
                    "wound/return-to-work issues are stable with restrictions, red flags and follow-up, and do not change anticoagulation safety now",
                ],
                "must_not_close_if": [
                    "the patient is likely to stop anticoagulation because of cost/confusion and no safe alternative or takeover exists",
                    "filter plan is simply deferred without owner/date/escalation",
                ],
                "closure_language_guardrails": [
                    "Close only when anticoagulation/filter responsibility is anchored; do not require leg swelling to fully disappear.",
                ],
            },
        }
        selected = family_rules.get(family, {})
        payload = {
            "protocol": "careloop.runtime_lite.case_specific_closure_residual_context.v1",
            "consumer": consumer,
            "visibility": "internal_soft_prompt_context_not_doctor_visible",
            "case_family_guess": family,
            "blocking_threads": common["blocking_threads"] + list(selected.get("blocking_threads") or []),
            "acceptable_bounded_residuals_if": common["acceptable_bounded_residuals_if"] + list(selected.get("acceptable_bounded_residuals_if") or []),
            "must_not_close_if": common["must_not_close_if"] + list(selected.get("must_not_close_if") or []),
            "closure_language_guardrails": common["closure_language_guardrails"] + list(selected.get("closure_language_guardrails") or []),
            "target_distribution_soft_goal": "Do not force by prompt, but calibrate natural tempo toward most routine cases closing in 50-300 turns and nearly all non-exceptional cases by 500 turns.",
            "anti_pattern": "Do not use a blanket rule such as pathology pending plus tracking plan is always acceptable; judge decision-criticality by case family and current episode.",
        }
        return self._bounded_prompt_payload(
            payload,
            char_budget=6000,
            text_limit=260,
            list_limit=10,
            label=f"case_specific_closure_residual_context:{consumer}",
        )

    def _high_risk_medication_and_ob_safety_context_for_prompt(self, consumer: str) -> dict[str, Any]:
        """P03-E soft safety rail for high-risk medications and obstetric episodes."""

        corpus = self._p03e_recent_case_corpus(window=30)
        domains: list[str] = []
        if self._p03c_has_any(corpus, ["抗凝", "利伐沙班", "阿哌沙班", "华法林", "肝素", "DVT", "血栓", "滤器", "肺栓塞"]):
            domains.append("anticoagulation_or_dvt_filter")
        if self._p03c_has_any(corpus, ["卡培他滨", "化疗", "放疗", "肿瘤", "癌", "造口", "CEA", "CA19"]):
            domains.append("oncology_adjuvant_or_capecitabine")
        if self._p03c_has_any(corpus, ["利托君", "孕", "妊娠", "产科", "宫颈", "环扎", "胎动", "宫缩", "出血", "流水", "保胎"]):
            domains.append("high_risk_obstetrics_or_ritodrine")
        if self._p03c_has_any(corpus, ["信必可", "沙丁胺醇", "吸入", "哮喘", "血氧", "喘"]):
            domains.append("respiratory_rescue_or_controller_meds")
        domain_rules: dict[str, dict[str, list[str]]] = {
            "anticoagulation_or_dvt_filter": {
                "must_not": [
                    "Do not let the simulated care path endorse patient self-stopping, self-restarting, halving, substituting, or rationing anticoagulation when drug/dose/bleeding status is unclear.",
                    "Do not treat affordability as a reason to silently accept anticoagulation interruption; require pharmacy/vascular/hematology/line clinician takeover or a documented safe bridge.",
                ],
                "required_next_safety_behaviors": [
                    "verify exact anticoagulant, dose, last dose, remaining supply, bleeding signs and renal/liver context if the patient may act on medication advice",
                    "anchor IVC filter retrieval/retention and ultrasound follow-up to a responsible vascular/interventional team and time window",
                ],
            },
            "oncology_adjuvant_or_capecitabine": {
                "must_not": [
                    "Do not let symptom absence override malignant pathology/staging/adjuvant-treatment responsibility.",
                    "Do not simulate safe capecitabine/chemotherapy continuation, stopping, or dose adjustment without oncology ownership and key toxicity/lab context.",
                ],
                "required_next_safety_behaviors": [
                    "route pathology/staging/tumor markers/MDT decisions to an actionable oncology plan",
                    "surface toxicity red flags and line-care responsibility if oral chemotherapy appears",
                ],
            },
            "high_risk_obstetrics_or_ritodrine": {
                "must_not": [
                    "Do not use one numeric maternal heart-rate threshold as the sole high-risk pregnancy safety rule.",
                    "Do not let the patient change ritodrine/progesterone/antibiotics/other pregnancy-relevant medication without obstetric instruction when name/dose/frequency is unclear.",
                ],
                "required_next_safety_behaviors": [
                    "obstetric red flags require multi-factor assessment: bleeding volume, contractions, fluid leakage, fetal movement, fever, pain, vitals, gestational age and cervical history",
                    "activity restriction, medication instructions, same-day triage threshold and follow-up owner must be concrete before bounded closure",
                ],
            },
            "respiratory_rescue_or_controller_meds": {
                "must_not": [
                    "Do not allow repeated rescue-inhaler use, low oxygen or night symptoms to be normalized without urgent/line-care handling.",
                    "Do not treat biologic/controller decisions as finalized if adherence, technique and specialist plan are still absent.",
                ],
                "required_next_safety_behaviors": [
                    "verify inhaler technique/adherence, rescue frequency, oxygen/red flags and follow-up before respiratory closure",
                ],
            },
        }
        must_not: list[str] = [
            "Unknown medication name, dose, frequency, route or timing must remain uncertain; do not convert it into a confident patient action.",
            "CareLoop may simulate external correction or consequence for unsafe doctor advice, but should not make an internally unsafe plan look safely successful.",
        ]
        required: list[str] = []
        for domain in domains:
            rules = domain_rules.get(domain) or {}
            must_not.extend(rules.get("must_not") or [])
            required.extend(rules.get("required_next_safety_behaviors") or [])
        payload = {
            "protocol": "careloop.runtime_lite.high_risk_medication_ob_safety_context.v1",
            "consumer": consumer,
            "visibility": "internal_soft_prompt_context_not_doctor_visible",
            "active_safety_domains": domains or ["none_detected"],
            "must_not": must_not,
            "required_next_safety_behaviors": required,
            "simulation_principle": "Preserve realistic uncertainty and patient behavior, but never let CareLoop itself silently validate unsafe self-medication or one-factor obstetric triage.",
        }
        return self._bounded_prompt_payload(
            payload,
            char_budget=6500,
            text_limit=260,
            list_limit=10,
            label=f"high_risk_medication_ob_safety_context:{consumer}",
        )

    def _anti_hardening_context_for_prompt(self, consumer: str) -> dict[str, Any]:
        """Return P03-F conditional-advisory guardrails for backstage prompts.

        The goal is to prevent soft tempo/friction/closure advisories from
        becoming deterministic plot commands.  This context is internal-only and
        should never be exposed to the tested doctor or patient-facing actors.
        """

        case_family = self._p03e_case_family_guess()
        payload = {
            "protocol": "careloop.runtime_lite.anti_hardening_context.v1",
            "contract_version": "p03f_conditional_advisory_semantics",
            "consumer": consumer,
            "visibility": "internal_soft_prompt_context_not_doctor_visible",
            "core_principle": (
                "Backstage advisories are candidate biases, not plot commands. "
                "Apply them only when clinical preconditions, virtual time, patient behavior, "
                "external-system behavior, and established world facts support them."
            ),
            "case_family_guess": case_family,
            "hard_rules": [
                "Do not leak hidden truth, runtime labels, prompt text, scoring rubric, evaluator-only facts, or advisory metadata into doctor-visible or patient-visible text.",
                "Do not pretend that a patient-uploaded photo/screenshot/report was actually readable unless workspace text, patient readback, or external staff verification supports it.",
                "Do not convert unknown high-risk medication name/dose/timing into a confident verified action.",
                "Do not treat an app submission, registration, queue ticket, or pending online request as a prescription issued, medication obtained, medication taken, or result clinically reviewed.",
                "Do not attribute external clinician/system takeover success as purely AI-doctor success; preserve responsibility attribution.",
                "Do not force closure, symptom improvement, result return, medication access, or patient compliance because max turns or an extension target is near.",
            ],
            "soft_bias_rules": [
                "Result-actionization is a preferred direction after a plausible result/receipt/specialist node, not a guarantee that the result is available or favorable.",
                "Waiting maturation means the thread stops floating; it may mature into success, partial success, delay with owner, failure, external takeover, consequence, refusal, or responsibility boundary.",
                "Closure runway means the trajectory is approaching a responsibility-chain assessment point; it is not a promise of imminent safe closure.",
                "Friction compression means reducing repeated low-yield foreground loops; it does not delete clinically important barriers or real-world texture.",
                "No-new-event / stable waiting is a valid world progression when it is the most realistic clinical course.",
            ],
            "anti_hardening_requirements": {
                "candidate_not_command": True,
                "must_check_clinical_preconditions_before_applying_advisory": True,
                "must_allow_no_event_when_clinically_realistic": True,
                "failure_or_boundary_as_maturation": True,
                "must_not_convert_closure_runway_into_forced_success": True,
                "must_preserve_probability_kernel_for_candidate_events": True,
            },
            "valid_progression_outcomes": [
                "successfully_matured",
                "partially_matured",
                "clear_delay_window_with_owner",
                "failed_with_consequence",
                "external_takeover",
                "responsibility_boundary",
                "patient_refusal_or_dropout",
                "no_new_event_stable_waiting",
                "runway_interrupted_by_causally_anchored_risk",
            ],
            "not_applicable_conditions": {
                "result_actionization": [
                    "test/visit/prescription request has not realistically occurred yet",
                    "turnaround time is too short for the result in this care setting",
                    "patient/family has no plausible access route and no external confirmation",
                    "a more urgent red flag must be handled first",
                    "only vague hearsay is available and no safe readback/workspace/staff verification route exists",
                    "external system failed and should mature to failure/alternative path rather than a successful result",
                ],
                "closure_runway_compression": [
                    "new causally anchored red flag, abnormal result, high-risk medication uncertainty, or doctor-induced consequence appears",
                    "action-blocking residual remains unresolved for the disease family",
                    "patient execution or affordability failure threatens current safety",
                ],
                "friction_compression": [
                    "the repeated friction is now the clinical blocker for medication continuity, urgent triage, procedure safety, or result interpretation",
                    "a new person, setting, document, result, symptom, or consequence gives the friction high marginal clinical yield",
                ],
            },
            "no_event_policy": {
                "allowed": True,
                "label": "no_new_event_stable_waiting",
                "use_when": "Stable observation, scheduled waiting, medication-response monitoring, or external processing is the most realistic next state and no high-yield new clinical node is ready.",
                "non_goal": "Do not invent drama merely because a turn must be produced.",
            },
            "advisory_application_metadata_schema": {
                "applied": "true/false",
                "reason": "clinical reason for applying or rejecting the advisory",
                "not_forced": True,
                "alternative_considered": "optional alternative next world move",
                "why_alternative_rejected": "optional reason",
            },
        }
        return self._bounded_prompt_payload(
            payload,
            char_budget=7200,
            text_limit=260,
            list_limit=10,
            label=f"anti_hardening_context:{consumer}",
        )

    def _friction_lifecycle_context_for_prompt(self, consumer: str) -> dict[str, Any]:
        taxonomy = self._p03c_friction_taxonomy()
        recent = self._p03c_recent_transcript_items(24)
        threads: dict[str, dict[str, Any]] = {}
        for item in recent:
            text = self._p03c_item_text(item)
            if not text.strip():
                continue
            signals = self._p03c_new_clinical_signals(text)
            anchor = self._p03c_clinical_anchor_guess(text)
            for friction_type, spec in taxonomy.items():
                if not self._p03c_has_any(text, spec["terms"]):
                    continue
                thread_anchor = "document_or_image_material" if friction_type == "document_reliability" else anchor
                thread_id = f"{friction_type}:{thread_anchor}"
                thread = threads.setdefault(
                    thread_id,
                    {
                        "friction_thread_id": thread_id,
                        "friction_type": friction_type,
                        "clinical_anchor_guess": thread_anchor,
                        "clinical_anchor_guesses": [],
                        "first_seen_turn": item.get("turn"),
                        "last_seen_turn": item.get("turn"),
                        "recent_mention_count": 0,
                        "new_clinical_information_signals": [],
                        "failed_same_channel_signals": 0,
                        "reliability_or_exit_signals": 0,
                        "mention_previews": [],
                        "recommended_exit_actions": spec["exit_actions"],
                    },
                )
                thread["recent_mention_count"] = int(thread.get("recent_mention_count") or 0) + 1
                thread["last_seen_turn"] = item.get("turn")
                if anchor not in thread.get("clinical_anchor_guesses", []):
                    thread.setdefault("clinical_anchor_guesses", []).append(anchor)
                for signal in signals:
                    if signal not in thread["new_clinical_information_signals"]:
                        thread["new_clinical_information_signals"].append(signal)
                if self._p03c_has_any(text, ["看不清", "看不懂", "反光", "模糊", "小字", "没收到", "传不上", "没传上", "拍不好"]):
                    thread["failed_same_channel_signals"] = int(thread.get("failed_same_channel_signals") or 0) + 1
                if self._p03c_has_any(text, ["读给", "读出", "逐字", "念给", "药房", "护士", "窗口", "医生说", "确认", "病案室", "线下"]):
                    thread["reliability_or_exit_signals"] = int(thread.get("reliability_or_exit_signals") or 0) + 1
                if len(thread["mention_previews"]) < 5:
                    thread["mention_previews"].append(
                        {
                            "turn": item.get("turn"),
                            "speaker": item.get("speaker") or item.get("actor"),
                            "text_preview": self._compact_text(text, limit=160),
                        }
                    )

        top_threads: list[dict[str, Any]] = []
        for thread in threads.values():
            count = int(thread.get("recent_mention_count") or 0)
            signal_count = len(thread.get("new_clinical_information_signals") or [])
            repeated_failed_channel = int(thread.get("failed_same_channel_signals") or 0) >= 2 and int(thread.get("reliability_or_exit_signals") or 0) == 0
            repeated_without_new = count >= 3 and (signal_count == 0 or repeated_failed_channel)
            if count <= 1:
                phase = "introduced"
            elif repeated_without_new:
                phase = "exhausted"
            elif count >= 3:
                phase = "resolution_attempt"
            elif signal_count:
                phase = "clarifying"
            else:
                phase = "resolution_attempt"
            if repeated_without_new:
                yield_guess = "exhausted"
            elif signal_count >= 2:
                yield_guess = "high"
            elif signal_count == 1:
                yield_guess = "medium" if count <= 3 else "low"
            elif count >= 2:
                yield_guess = "low"
            else:
                yield_guess = "medium"
            if phase == "exhausted" or yield_guess == "exhausted":
                lifecycle_stage = "cooldown_unless_new_information"
            elif phase == "introduced":
                lifecycle_stage = "introduced"
            elif int(thread.get("reliability_or_exit_signals") or 0) > 0:
                lifecycle_stage = "resolved_or_escalated"
            elif signal_count > 0 and count <= 3:
                lifecycle_stage = "clinically_useful"
            else:
                lifecycle_stage = "resolution_attempted"
            cooldown_required = lifecycle_stage == "cooldown_unless_new_information" or (count >= 4 and signal_count == 0)
            action_blocking = thread["friction_type"] in {
                "document_reliability",
                "result_waiting",
                "medication_access",
                "patient_adherence",
            } and yield_guess in {"low", "exhausted"}
            do_not_repeat: list[str] = []
            if yield_guess in {"low", "exhausted"}:
                if thread["friction_type"] == "document_reliability":
                    do_not_repeat.append("不要继续原样要求患者重拍/重传同一份照片、报告或药盒。")
                elif thread["friction_type"] == "administrative_access":
                    do_not_repeat.append("不要继续逐轮扩写同一窗口、挂号、排队或材料细节。")
                elif thread["friction_type"] == "family_execution":
                    do_not_repeat.append("不要继续只说家属没记清；应转入teach-back、责任人或执行结果。")
                else:
                    do_not_repeat.append("不要继续原样重复该现实摩擦；应选择出口或回到临床节点。")
            enriched = dict(thread)
            enriched.update(
                {
                    "phase_guess": phase,
                    "marginal_clinical_yield_guess": yield_guess,
                    "repeated_without_new_info": repeated_without_new,
                    "action_blocking_residual_guess": action_blocking,
                    "do_not_repeat_next": do_not_repeat,
                    "lifecycle_stage_v2": lifecycle_stage,
                    "cooldown_unless_new_information": bool(cooldown_required),
                    "requires_exit_ladder_now": bool(cooldown_required or repeated_without_new),
                    "exit_ladder_options": thread.get("recommended_exit_actions") or [],
                }
            )
            top_threads.append(enriched)
        top_threads.sort(key=lambda t: (t.get("marginal_clinical_yield_guess") in {"low", "exhausted"}, t.get("recent_mention_count") or 0), reverse=True)
        exhausted_threads = [t for t in top_threads if t.get("phase_guess") == "exhausted" or t.get("marginal_clinical_yield_guess") == "exhausted"]
        high_value_threads = [t for t in top_threads if t.get("marginal_clinical_yield_guess") in {"high", "medium"}]
        mention_total = sum(int(t.get("recent_mention_count") or 0) for t in top_threads)
        if mention_total >= 18 or len(exhausted_threads) >= 2:
            load = "high"
        elif mention_total >= 8:
            load = "medium"
        elif mention_total:
            load = "low"
        else:
            load = "none"
        hard_caps = {
            "document_reliability": 3,
            "administrative_access": 2,
            "external_system_delay": 2,
            "family_execution": 3,
            "work_life_conflict": 2,
            "result_waiting": 3,
            "medication_access": 3,
            "patient_adherence": 3,
            "financial_access": 2,
            "transport_access": 2,
        }
        recent_budget: dict[str, int] = {}
        for thread in top_threads:
            friction_type = str(thread.get("friction_type") or "unknown")
            recent_budget[friction_type] = recent_budget.get(friction_type, 0) + int(thread.get("recent_mention_count") or 0)
        over_budget_types = [
            {
                "friction_type": friction_type,
                "recent_mentions": count,
                "soft_cap": hard_caps.get(friction_type, 3),
            }
            for friction_type, count in sorted(recent_budget.items())
            if count >= hard_caps.get(friction_type, 3)
        ]
        friction_budget = {
            "budget_version": "p03e_precision_friction_budget",
            "recent_budget_by_type": recent_budget,
            "soft_caps_by_type_in_recent_window": hard_caps,
            "over_budget_types": over_budget_types,
            "max_same_type_next_turn_policy": (
                "no_same_type_frontstage_unless_new_clinical_information_or_safety_consequence"
                if over_budget_types or load == "high"
                else "same_type_allowed_if_high_marginal_clinical_yield"
            ),
            "must_return_to_clinical_node_if": [
                "same friction type has reached its soft cap and no new clinical information is expected",
                "the obstacle can be matured by readback, staff confirmation, result-return, concrete failure, or responsibility boundary",
                "another friction event would mainly test patience/logistics rather than clinical reasoning or safe execution",
            ],
            "non_goal": "This budget does not erase realistic friction; it prevents repeated low-yield friction from replacing the clinical mainline.",
        }
        if exhausted_threads:
            policy = "force_exit_ladder"
        elif over_budget_types or load == "high":
            policy = "compress_low_yield"
        elif high_value_threads:
            policy = "continue_high_value_only"
        else:
            policy = "continue"
        payload = {
            "protocol": "careloop.runtime_lite.friction_lifecycle_context.v1",
            "lifecycle_contract_version": "v2_p03d_hardened",
            "p03e_contract_version": "v3_p03e_precision_budget",
            "p03f_contract_version": "v4_p03f_conditional_friction_compression",
            "advisory_semantics": "candidate_not_command",
            "consumer": consumer,
            "visibility": "internal_soft_prompt_context_not_doctor_visible",
            "window": {"transcript_items": len(recent), "events": min(len(self.trajectory.events), 120)},
            "top_threads": top_threads[:8],
            "exhausted_threads": exhausted_threads[:4],
            "high_value_threads": high_value_threads[:4],
            "global_friction_load": load,
            "friction_budget": friction_budget,
            "recommended_world_director_policy": policy,
            "lifecycle_stage_definitions": [
                "introduced",
                "clinically_useful",
                "resolution_attempted",
                "resolved_or_escalated",
                "cooldown_unless_new_information",
            ],
            "hardening_policy": {
                "after_cooldown": "Do not reintroduce the same friction thread unless a new clinical fact, new document, new person, new setting, or new consequence appears.",
                "document_friction": "If no real image/OCR channel exists, convert repeated blurry-photo loops to readback, workspace text record, external staff verification, or responsibility boundary.",
                "cost_queue_memory_friction": "Use once or twice for realism/evaluation, then mature to execution result, failed execution with consequence, external takeover, or background pressure.",
            },
            "compression_is_not_deletion": True,
            "no_new_friction_is_valid": True,
            "over_budget_exception_conditions": [
                "the repeated friction now controls high-risk medication continuity or safety",
                "the friction exposes a new clinical fact, new document, new person, new setting, or new consequence",
                "the friction changes triage, treatment, monitoring, follow-up ownership, or closure eligibility",
            ],
            "external_takeover_is_valid_exit": True,
            "failure_or_boundary_is_valid_exit": True,
            "valid_exit_outcomes": [
                "resolve_successfully",
                "resolve_partially_with_owner",
                "failed_with_consequence",
                "patient_refusal_or_dropout",
                "responsibility_boundary",
                "compressed_background",
                "external_takeover",
            ],
            "principle": (
                "Keep clinically meaningful friction; compress repeated low-yield friction without deleting realistic barriers. "
                "An exhausted thread should exit to readback, workspace/staff confirmation, time-jump-to-result, external takeover, consequence, responsibility boundary, or background compression."
            ),
        }
        return self._bounded_prompt_payload(
            payload,
            char_budget=9000,
            text_limit=220,
            list_limit=8,
            label=f"friction_lifecycle_context:{consumer}",
        )

    def _clinical_node_tempo_context_for_prompt(self, consumer: str) -> dict[str, Any]:
        recent_text = "\n".join(self._p03c_item_text(item) for item in self._p03c_recent_transcript_items(18))
        phase = "longitudinal_followup"
        tempo_policy = "normal"
        why = "No dominant acute or action-blocking phase detected in the recent window."
        phase_rules = [
            ("acute_triage", "slow", ["血氧", "说不完整", "嘴唇", "急诊", "120", "大出血", "胸痛", "肺栓塞", "发紫", "意识", "流水", "宫缩规律"], "Recent material contains acute red-flag or triage terms; do not make large unexplained jumps."),
            ("execution_correction", "normal", ["药盒", "药袋", "剂量", "剩几片", "停药", "漏", "用错", "吸入器", "抗凝", "teach-back", "复述"], "Execution or medication reliability is still being corrected; preserve judgement and teach-back nodes."),
            ("result_waiting", "compress_waiting", ["等结果", "报告还没", "病理还没", "MDT", "等通知", "预约了", "复查后"], "This looks like waiting for result/MDT/follow-up; waiting may be compressed if safety boundaries are handled."),
            ("diagnostic_workup", "normal", ["检查", "抽血", "CT", "MRI", "超声", "病理", "肺功能", "FeNO", "CEA"], "Diagnostic workup is active; do not skip first result interpretation."),
            ("specialist_handoff_responsibility_retained", "compress_waiting", ["转诊", "会诊", "专科", "呼吸科", "产科", "肿瘤内科", "血管外科", "MDT", "复诊"], "Specialist system is involved, but CareLoop should retain result/action follow-up responsibility."),
            ("closure_runway", "closure_runway_watch", ["稳定", "没再", "复诊", "记录", "红旗", "下次", "责任清单", "回传"], "Trajectory may be approaching a milestone, but closure still needs evidence and residual-risk classification."),
        ]
        for candidate, policy, terms, rationale in phase_rules:
            if self._p03c_has_any(recent_text, terms):
                phase = candidate
                tempo_policy = policy
                why = rationale
                break
        next_nodes = []
        if phase == "acute_triage":
            next_nodes = ["线下急诊/分诊结果", "红旗安全网执行", "急性处理反应"]
        elif phase == "execution_correction":
            next_nodes = ["关键药物/资料核实", "患者或家属teach-back", "执行成功或失败反馈"]
        elif phase == "result_waiting":
            next_nodes = ["检查/病理/MDT结果回流", "医生解释与行动化", "随访责任链"]
        elif phase == "diagnostic_workup":
            next_nodes = ["检查完成", "报告首次回流", "诊断/治疗方案更新"]
        elif phase == "specialist_handoff_responsibility_retained":
            next_nodes = ["专科复诊结论", "书面意见/处方变化", "线上医生回看与行动化"]
        else:
            next_nodes = ["症状/执行反馈", "复诊或结果回传", "closure runway residual-risk check"]
        payload = {
            "protocol": "careloop.runtime_lite.clinical_node_tempo_context.v1",
            "consumer": consumer,
            "phase_guess": phase,
            "tempo_policy": tempo_policy,
            "why": why,
            "next_high_value_nodes": next_nodes,
            "low_value_repetitions_to_avoid": [
                "重复同一照片/药盒/报告看不清而无新字段",
                "重复窗口/排队/护士忙而无新医疗结论",
                "重复安抚或红旗清单而无teach-back或执行反馈",
            ],
        }
        return self._bounded_prompt_payload(
            payload,
            char_budget=2600,
            text_limit=220,
            list_limit=8,
            label=f"clinical_node_tempo_context:{consumer}",
        )

    def _tempo_precondition_context_for_prompt(self, consumer: str) -> dict[str, Any]:
        recent_time_jumps: list[dict[str, Any]] = []
        for event in self.trajectory.events[-120:]:
            if event.get("event_type") != "time_advance":
                continue
            content = dict(event.get("content") or {})
            try:
                elapsed = float(content.get("elapsed_minutes") or 0)
            except (TypeError, ValueError):
                elapsed = 0.0
            if elapsed >= 60:
                recent_time_jumps.append(
                    {
                        "turn": event.get("turn"),
                        "elapsed_minutes": elapsed,
                        "visible_time_phrase": content.get("visible_time_phrase"),
                        "rationale_preview": self._compact_text(content.get("rationale") or content.get("scene_time_explanation"), limit=160),
                    }
                )
        friction = self._friction_lifecycle_context_for_prompt(f"tempo_precondition:{consumer}")
        clinical_node = self._clinical_node_tempo_context_for_prompt(f"tempo_precondition:{consumer}")
        action_blockers: list[str] = []
        for thread in friction.get("top_threads", []):
            if thread.get("action_blocking_residual_guess"):
                action_blockers.append(
                    f"{thread.get('friction_type')} / {thread.get('clinical_anchor_guess')} / {thread.get('phase_guess')}"
                )
        phase = str(clinical_node.get("phase_guess") or "")
        recent_text = "\n".join(self._p03c_item_text(item) for item in self._p03c_recent_transcript_items(18))
        high_blocker_terms_present = self._p03c_has_any(
            recent_text,
            ["抗凝", "利伐沙班", "肝素", "滤器", "肺栓塞", "药盒看不清", "药袋小字", "不知道今天", "剩几片", "伤口", "出血", "病理", "MDT"],
        ) and self._p03c_has_any(recent_text, ["不知道", "看不清", "没找到", "还没", "不确定", "没确认"])
        long_jump_risk = "low"
        if high_blocker_terms_present:
            long_jump_risk = "high"
            if not action_blockers:
                action_blockers.append("high-risk medication/result/document residual inferred from recent transcript")
        elif phase in {"acute_triage", "execution_correction"} and action_blockers:
            long_jump_risk = "high"
        elif any((jump.get("elapsed_minutes") or 0) >= 10080 for jump in recent_time_jumps[-3:]) and action_blockers:
            long_jump_risk = "high"
        elif action_blockers or len(recent_time_jumps) >= 3:
            long_jump_risk = "medium"
        payload = {
            "protocol": "careloop.runtime_lite.tempo_precondition_context.v1",
            "consumer": consumer,
            "current_sim_time": self.current_sim_time,
            "recent_time_jumps": recent_time_jumps[-6:],
            "long_jump_risk": long_jump_risk,
            "open_action_blocking_residual_guesses": action_blockers[:6],
            "safe_to_compress_waiting_if": [
                "red flags and minimum safe boundary have been handled",
                "responsible person/timepoint/result-return path is explicit",
                "no first-return result interpretation, treatment change, or teach-back node is being skipped",
            ],
            "must_not_skip_nodes": [
                "first interpretation of new result/report/pathology/MDT opinion",
                "medication or procedure safety decision when source reliability is uncertain",
                "teach-back after treatment or safety-net change",
                "doctor-induced error consequence or external correction",
            ],
            "next_clinical_node_candidates": clinical_node.get("next_high_value_nodes", []),
            "clinical_node_tempo_context": clinical_node,
            "principle": "Compress waiting, not clinical responsibility. Long jumps should land on a concrete clinical node.",
        }
        return self._bounded_prompt_payload(
            payload,
            char_budget=3800,
            text_limit=220,
            list_limit=8,
            label=f"tempo_precondition_context:{consumer}",
        )

    def _active_waiting_ceiling_context_for_prompt(self, consumer: str) -> dict[str, Any]:
        """Summarise active waiting threads that should mature into concrete clinical nodes.

        This is a soft tempo context.  It does not force closure or skip clinical
        responsibility; it only warns backstage nodes when repeated waiting/phone/
        report-window material has produced enough evaluation signal and should be
        converted into a result, failed handoff, external correction, documented
        responsibility boundary, or consequence.
        """

        waiting_specs: dict[str, dict[str, Any]] = {
            "result_or_report_wait": {
                "terms": ["等报告", "等病理报告", "报告没出", "结果没出", "等结果", "病理还没", "病理报告", "CT还没", "影像还没", "复查结果", "出结果"],
                "maturation_routes": ["结果回流并首次解释", "明确报告延迟时间窗与下一步", "外部医生/科室给出书面或口头解释", "记录责任边界和追踪计划"],
            },
            "phone_or_notification_wait": {
                "terms": ["等电话", "等通知", "打电话", "回电话", "没人接", "通知我", "电话通知", "等消息"],
                "maturation_routes": ["接到通知并行动化", "确认未接通后的替代渠道", "窗口/护士站/科室确认", "患者错过窗口并形成后果"],
            },
            "appointment_or_queue_wait": {
                "terms": ["排队", "挂号", "预约", "号源", "等床", "等床位", "窗口", "候诊", "加号", "住院安排"],
                "maturation_routes": ["完成挂号/候诊并进入医嘱节点", "明确不可及并升级路径", "外部接管", "时间跳转到具体就诊结果"],
            },
            "family_or_execution_wait": {
                "terms": ["家属去", "让家里", "等家属", "买药", "取药", "拿报告", "送过来", "回家拿", "执行了没"],
                "maturation_routes": ["执行成功/失败反馈", "责任人teach-back", "药师/护士核对", "执行延误造成可评价后果"],
            },
        }
        recent = self._p03c_recent_transcript_items(30)
        threads: dict[str, dict[str, Any]] = {}
        progress_terms = [
            "拿到了", "出来了", "回来了", "收到", "医生说可以", "护士确认", "药师确认", "窗口确认", "确认", "已经做",
            "没做成", "没挂上", "改约", "转诊", "住院", "急诊", "复诊", "下一步", "处理", "结果是", "报告写",
        ]
        for item in recent:
            text = self._p03c_item_text(item)
            if not text.strip():
                continue
            anchor = self._p03c_clinical_anchor_guess(text)
            negative_waiting = self._p03c_has_any(text, ["没出", "还没", "没人", "没有", "等通知", "等电话", "继续等", "还是等"])
            has_progress = (self._p03c_has_any(text, progress_terms) or bool(self._p03c_new_clinical_signals(text))) and not negative_waiting
            for wait_type, spec in waiting_specs.items():
                if not self._p03c_has_any(text, spec["terms"]):
                    continue
                thread_id = wait_type
                thread = threads.setdefault(
                    thread_id,
                    {
                        "waiting_thread_id": thread_id,
                        "waiting_type": wait_type,
                        "clinical_anchor_guess": anchor,
                        "first_seen_turn": item.get("turn"),
                        "last_seen_turn": item.get("turn"),
                        "recent_waiting_mentions": 0,
                        "progress_or_resolution_signals": 0,
                        "mention_previews": [],
                        "maturation_routes": spec["maturation_routes"],
                    },
                )
                thread["recent_waiting_mentions"] = int(thread.get("recent_waiting_mentions") or 0) + 1
                thread["last_seen_turn"] = item.get("turn")
                if anchor not in thread.get("clinical_anchor_guesses", []):
                    thread.setdefault("clinical_anchor_guesses", []).append(anchor)
                if has_progress:
                    thread["progress_or_resolution_signals"] = int(thread.get("progress_or_resolution_signals") or 0) + 1
                if len(thread["mention_previews"]) < 4:
                    thread["mention_previews"].append(
                        {
                            "turn": item.get("turn"),
                            "speaker": item.get("speaker") or item.get("actor"),
                            "text_preview": self._compact_text(text, limit=150),
                        }
                    )

        active_threads: list[dict[str, Any]] = []
        for thread in threads.values():
            mentions = int(thread.get("recent_waiting_mentions") or 0)
            progress = int(thread.get("progress_or_resolution_signals") or 0)
            first_turn = thread.get("first_seen_turn")
            last_turn = thread.get("last_seen_turn")
            try:
                age_turns = int(last_turn or 0) - int(first_turn or 0) + 1
            except (TypeError, ValueError):
                age_turns = mentions
            if mentions >= 4 and progress == 0:
                ceiling = "exceeded_mature_now"
            elif mentions >= 3 and progress <= 1:
                ceiling = "near_ceiling_mature_next"
            elif mentions >= 2:
                ceiling = "watch"
            else:
                ceiling = "fresh"
            enriched = dict(thread)
            enriched.update(
                {
                    "age_turns_in_recent_window": age_turns,
                    "ceiling_status": ceiling,
                    "must_not_do_next": [
                        "不要继续原样重复等待/打电话/等报告而不给出新的临床节点",
                        "不要把等待本身当作 closure 或 success",
                    ]
                    if ceiling in {"near_ceiling_mature_next", "exceeded_mature_now"}
                    else [],
                    "recommended_next_world_move": (
                        "convert_waiting_to_concrete_clinical_node"
                        if ceiling in {"near_ceiling_mature_next", "exceeded_mature_now"}
                        else "continue_if_clinically_useful"
                    ),
                }
            )
            active_threads.append(enriched)
        active_threads.sort(key=lambda t: (t.get("ceiling_status") == "exceeded_mature_now", t.get("recent_waiting_mentions") or 0), reverse=True)
        exceeded = [t for t in active_threads if t.get("ceiling_status") == "exceeded_mature_now"]
        near = [t for t in active_threads if t.get("ceiling_status") == "near_ceiling_mature_next"]
        policy = "continue"
        if exceeded:
            policy = "must_mature_waiting_now"
        elif near:
            policy = "mature_waiting_next_unless_new_clinical_information"

        case_family = self._p03e_case_family_guess()
        family_maturation_options = {
            "asthma_or_airway": [
                "肺功能/FeNO/IgE 或呼吸科复诊意见回流，并转换为控制药/急救药/复查计划",
                "稳定观察期后回到症状频率、夜醒、SABA使用、血氧和吸入技术执行反馈",
                "若结果仍未回流，明确呼吸科/线下复诊责任和恶化时急诊边界",
            ],
            "high_risk_pregnancy_or_cerclage": [
                "尿培养/分泌物/超声或产科复诊结论回流，并转换为用药、活动限制或复查计划",
                "如果仍等待结果，给出产科责任人、具体时间窗和出血/流水/宫缩/胎动红旗",
                "若患者无法执行，模拟产科外部接管、患者拒绝或自然后果，不要无限等通知",
            ],
            "oncology_rectal_postop": [
                "病理关键字段、CEA/CA19-9、影像或MDT/肿瘤科意见回流并首次解释",
                "若MDT/病理仍待定，明确下一次取结果/门诊/责任科室和异常升级路径",
                "若外部肿瘤团队已接管，形成 milestone/external takeover 而不是治愈式闭环",
            ],
            "dvt_filter_anticoagulation": [
                "抗凝药名剂量余量、出血风险、续方/替代购药路径得到药师或线下医生核对",
                "血管外科/介入科给出滤器取出或暂缓取出的时间窗与复查超声路径",
                "若费用/工作阻碍持续，模拟安全桥接、外部接管、拒绝/失访或后果，而不是反复犹豫",
            ],
        }.get(case_family, [
            "结果/复诊/执行反馈自然回流并被首次解释",
            "无法回流时明确责任人、时间窗、替代路径和风险边界",
        ])
        for thread in active_threads:
            thread["case_family_maturation_options"] = family_maturation_options[:3]
            if thread.get("ceiling_status") in {"near_ceiling_mature_next", "exceeded_mature_now"}:
                thread["should_land_on_case_specific_node_next"] = True
        payload = {
            "protocol": "careloop.runtime_lite.active_waiting_ceiling_context.v1",
            "ceiling_contract_version": "v2_p03e_result_maturation",
            "p03f_contract_version": "v3_p03f_conditional_result_actionization",
            "advisory_semantics": "candidate_not_command",
            "consumer": consumer,
            "visibility": "internal_soft_prompt_context_not_doctor_visible",
            "case_family_guess": case_family,
            "active_waiting_threads": active_threads[:8],
            "exceeded_ceiling_threads": exceeded[:4],
            "near_ceiling_threads": near[:4],
            "recommended_world_policy": policy,
            "case_family_maturation_options": family_maturation_options,
            "maturation_definition": [
                "result/receipt returns and is clinically interpreted",
                "result remains missing but a concrete time window and responsible route are documented",
                "external clinician/care system gives an actionable explanation",
                "patient/family misses the window and consequence is simulated",
                "episode responsibility boundary is explicitly documented without erasing evaluation evidence",
            ],
            "actionization_window": {
                "target_turn_window": "1-3 turns when clinically feasible after a plausible result/receipt/specialist node",
                "not_success_requirement": True,
                "failure_and_boundary_are_valid_maturation": True,
                "valid_outcomes": [
                    "formal_result_readback",
                    "workspace_text_record_return",
                    "external_staff_or_specialist_explanation",
                    "clear_delay_window_with_owner",
                    "failed_access_with_alternative_path",
                    "patient_refusal_or_missed_window",
                    "responsibility_boundary",
                ],
            },
            "do_not_apply_when": [
                "the test/visit/prescription request has not realistically happened",
                "turnaround time is too short for this result or external process",
                "only vague hearsay exists and no safe readback/workspace/staff-verification route is available",
                "a new urgent red flag must be handled before result actionization",
                "external access failed and should mature to failure, alternative path, or responsibility boundary rather than success",
            ],
            "principle": "Waiting may be realistic, but repeated active waiting must mature into a concrete clinical node rather than indefinitely consuming turns; maturation can be success, delay-with-owner, failure, external takeover, or responsibility boundary, not only favorable result return.",
        }
        return self._bounded_prompt_payload(
            payload,
            char_budget=7000,
            text_limit=220,
            list_limit=8,
            label=f"active_waiting_ceiling_context:{consumer}",
        )

    def _p03g_recent_signal_items(self, window: int = 30) -> list[dict[str, Any]]:
        """Return compact recent items likely relevant to P03-G event/receipt tracking.

        This is not an authoritative classifier.  It provides a low-cost,
        backstage-only semantic reminder so LLM workers do not forget that a
        newly surfaced result, prescription boundary, referral, failed access
        node, or caregiver execution task has to be actionized before it can
        support closure.
        """

        terms = [
            "病理", "报告", "结果", "复查", "CT", "MRI", "磁共振", "超声", "内镜", "化验",
            "处方", "用药", "停药", "减量", "加量", "抗凝", "利伐沙班", "华法林", "出血",
            "急诊", "住院", "会诊", "MDT", "肿瘤", "化疗", "放疗", "靶向", "免疫",
            "预约", "挂号", "窗口", "药房", "拿药", "家属", "儿子", "女儿", "复述",
            "回访", "随访", "恶化", "发热", "胸痛", "气短", "腹痛", "便血", "呕吐",
        ]
        items: list[dict[str, Any]] = []
        for item in self.trajectory.transcript[-max(1, int(window)) :]:
            if not isinstance(item, dict):
                continue
            text = self._p03c_item_text(item)
            if not text or not self._p03c_has_any(text, terms):
                continue
            items.append(
                {
                    "turn": item.get("turn"),
                    "speaker": item.get("speaker"),
                    "event_type": item.get("event_type"),
                    "text_preview": self._compact_text(text, limit=180),
                }
            )
        return items[-8:]

    def _p03h_keyword_any(self, text: str, terms: list[str]) -> bool:
        lowered = str(text or "").casefold()
        return any(str(term).casefold() in lowered for term in terms if str(term or ""))

    def _p03i_slug(self, value: str, *, limit: int = 42) -> str:
        raw = str(value or "unknown").casefold().strip()
        slug = re.sub(r"[^0-9a-zA-Z_\u4e00-\u9fff]+", "_", raw).strip("_")
        return (slug or "unknown")[:limit]

    def _p03i_turn_int(self, value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    def _p03i_text_for_item(self, item: Mapping[str, Any]) -> str:
        text = item.get("text")
        if text is not None:
            return str(text or "")
        content = item.get("content")
        if isinstance(content, Mapping):
            parts = [content.get(k) for k in ["text", "summary", "description", "rationale", "why_unsafe"]]
            return "\n".join(str(x) for x in parts if x)
        return str(content or "")

    def _p03h_high_impact_event_type(self, text: str) -> str:
        text = str(text or "")
        taxonomy = [
            ("pathology_or_oncology_decision", ["病理", "免疫组化", "切缘", "淋巴结", "分期", "TNM", "MDT", "肿瘤", "放疗", "化疗"]),
            ("imaging_or_lab_result", ["CT", "MRI", "磁共振", "超声", "影像", "片子", "内镜", "肠镜", "胃镜", "报告", "结果", "化验", "复查", "血常规", "肝肾", "IgE", "FeNO", "过敏原", "肿瘤标志物", "D二聚体"]),
            ("medication_safety", ["处方", "用药", "药袋", "药盒", "剂量", "频次", "停药", "减量", "加量", "抗凝", "利伐沙班", "华法林", "胰岛素", "激素", "黄体酮", "抗生素"]),
            ("specialist_opinion_or_external_takeover", ["会诊", "专科", "门诊", "住院", "急诊", "肿瘤内科", "外科", "产科", "药师", "护士", "转诊"]),
            ("red_flag_or_complication", ["出血", "胸痛", "喘不上气", "气短", "胎动", "流水", "腹痛", "发热", "便血", "呕吐", "恶化", "加重", "晕厥", "意识"]),
            ("execution_barrier", ["没取到", "拿不到", "预约不上", "排队", "窗口", "药房", "系统", "看不清", "模糊", "不会", "没人陪", "太贵", "拒绝"]),
        ]
        for event_type, terms in taxonomy:
            if self._p03h_keyword_any(text, terms):
                return event_type
        return "other_high_impact_signal"

    def _p03h_is_doctor_message(self, item: Mapping[str, Any]) -> bool:
        event_type = str(item.get("event_type") or "")
        speaker = str(item.get("speaker") or item.get("speaker_category") or "").casefold()
        return event_type == "doctor_message" or speaker in {"doctor", "ai医生", "assistant"} or "doctor" in speaker

    def _p03h_is_actor_message(self, item: Mapping[str, Any]) -> bool:
        return str(item.get("event_type") or "") in {"patient_message", "family_message"}

    def _p03h_action_terms(self) -> list[str]:
        return [
            "请", "需要", "建议", "马上", "立即", "今天", "明天", "复诊", "急诊", "120", "挂号", "预约", "住院", "转诊",
            "带", "上传", "读给", "念给", "核对", "确认", "复查", "检查", "化验", "报告", "联系", "问", "药师", "护士",
            "停", "停药", "调整", "加量", "减量", "按", "服", "用", "监测", "记录", "观察", "如果", "出现", "返回", "随访",
        ]

    def _p03i_is_negative_symptom_reassurance(self, text: str) -> bool:
        text = str(text or "")
        negators = ["没有", "没", "无", "未", "未见", "并无"]
        symptoms = [
            "胸痛", "黑便", "尿血", "出血", "发热", "发冷", "喘不上气", "气短", "呼吸困难", "腹痛", "便血",
            "呕吐", "恶化", "加重", "晕厥", "头晕", "意识", "胎动减少", "流水", "破水", "腿肿", "麻木",
        ]
        return any(n in text and s in text for n in negators for s in symptoms)

    def _p03i_is_execution_failure_or_barrier(self, text: str) -> bool:
        text = str(text or "")
        barrier_terms = [
            "没去", "没做", "没取", "没拿到", "拿不到", "没买到", "缺药", "预约不上", "排不上", "挂不上", "不能去", "去不了",
            "不会读", "不会念", "说不清", "怕念错", "怕说错", "看不清", "图片模糊", "没人陪", "没人带", "太贵", "付不起",
            "不敢吃", "拒绝", "不愿意", "窗口说", "系统", "药房没有", "联系不上", "忘了", "不知道怎么",
        ]
        return self._p03h_keyword_any(text, barrier_terms)

    def _p03i_actor_feedback_status(self, text: str, *, task_type: str = "", clinical_topic: str = "") -> str:
        text = str(text or "")
        if not text.strip():
            return "planned_no_feedback_yet"
        negative_reassurance = self._p03i_is_negative_symptom_reassurance(text)
        execution_barrier = self._p03i_is_execution_failure_or_barrier(text)
        if task_type == "red_flag_monitoring" and negative_reassurance:
            return "actor_confirmed_executed"
        if execution_barrier:
            return "actor_failed_or_unable"
        execution_terms = ["已经", "已", "做了", "去了", "约了", "预约了", "拿到了", "买到了", "吃了", "用了", "停了", "改了", "抽血", "复查", "上传", "带上"]
        understanding_terms = ["明白", "知道", "记下", "收到", "会去", "可以", "懂了", "按你说的"]
        if self._p03h_keyword_any(text, execution_terms):
            return "actor_confirmed_executed"
        if negative_reassurance:
            return "actor_confirmed_executed"
        if self._p03h_keyword_any(text, understanding_terms):
            return "actor_confirmed_understanding_only"
        return "unknown"

    def _p03h_feedback_status(self, text: str) -> str:
        status = self._p03i_actor_feedback_status(text)
        if status == "actor_confirmed_executed":
            return "actor_confirmed"
        if status == "actor_failed_or_unable":
            return "actor_failed_or_unable"
        if status == "actor_confirmed_understanding_only":
            return "actor_confirmed"
        return "unknown"

    def _p03h_task_types_from_text(self, text: str) -> list[str]:
        checks = [
            ("medication_boundary", ["药", "处方", "剂量", "频次", "停药", "加量", "减量", "服", "抗凝", "利伐沙班", "华法林", "胰岛素", "激素"]),
            ("result_readback", ["报告", "结果", "读给", "念给", "上传", "拍照", "图片", "截图", "病理", "CT", "MRI", "超声", "化验"]),
            ("appointment_or_referral", ["预约", "挂号", "复诊", "门诊", "专科", "会诊", "MDT", "转诊", "住院"]),
            ("red_flag_monitoring", ["急诊", "120", "胸痛", "气短", "喘不上气", "出血", "发热", "腹痛", "胎动", "流水", "恶化", "加重", "如果出现"]),
            ("test_or_lab_followup", ["复查", "检查", "化验", "抽血", "尿", "便", "肺功能", "肿瘤标志物", "复测"]),
            ("wound_or_device_followup", ["伤口", "换药", "纱布", "渗液", "渗血", "导管", "引流", "滤器", "支架", "造口", "拆线"]),
            ("transport_or_caregiver", ["家属", "陪", "车", "交通", "请假", "带", "儿子", "女儿", "丈夫", "妈妈", "爸爸"]),
            ("teach_back", ["复述", "确认", "核对", "记下来", "告诉我", "回我", "明白"]),
        ]
        task_types = [name for name, terms in checks if self._p03h_keyword_any(text, terms)]
        return task_types or ["general_followup_action"]

    def _p03h_owner_from_text(self, text: str) -> str:
        if self._p03h_keyword_any(text, ["家属", "儿子", "女儿", "丈夫", "老婆", "妈妈", "爸爸", "陪"]):
            return "family"
        if self._p03h_keyword_any(text, ["药房", "窗口", "药师"]):
            return "pharmacy_or_window"
        if self._p03h_keyword_any(text, ["专科", "会诊", "MDT", "肿瘤内科", "放疗", "外科", "产科"]):
            return "external_specialist"
        if self._p03h_keyword_any(text, ["护士", "门诊", "医院", "病房", "住院"]):
            return "clinic_team"
        if self._p03h_keyword_any(text, ["我", "你", "患者", "本人"]):
            return "patient"
        return "patient_or_family_or_external_team"

    def _p03h_due_window_from_text(self, text: str) -> str:
        if self._p03h_keyword_any(text, ["立即", "马上", "现在", "急诊", "120", "立刻"]):
            return "immediate_or_emergency"
        if self._p03h_keyword_any(text, ["今天", "今晚", "当日"]):
            return "same_day"
        if self._p03h_keyword_any(text, ["明天", "后天", "48小时", "两天", "2天"]):
            return "24_to_48_hours"
        if self._p03h_keyword_any(text, ["一周", "1周", "7天", "下周"]):
            return "within_one_week"
        if self._p03h_keyword_any(text, ["一个月", "1个月", "三个月", "3个月", "半年", "长期", "定期"]):
            return "longitudinal_or_scheduled_followup"
        return "unspecified"

    def _p03i_clinical_topic_for_text(self, text: str, task_type: str = "") -> str:
        text = str(text or "")
        if task_type == "appointment_or_referral" and self._p03h_keyword_any(text, ["预约", "挂号", "复诊", "门诊", "专科", "会诊", "转诊", "住院", "线上复诊"]):
            if self._p03h_keyword_any(text, ["孕", "胎", "产科", "宫颈", "环扎", "胎盘", "B超"]):
                return "pregnancy_or_fetal_safety_monitoring"
            return "appointment_or_referral_execution"
        if task_type == "wound_or_device_followup":
            if self._p03h_keyword_any(text, ["滤器", "支架", "导管"]):
                return "procedure_or_device_followup"
            return "wound_or_device_or_procedure_followup"
        topic_checks = [
            ("pregnancy_or_fetal_safety_monitoring", ["孕", "胎", "宫缩", "羊水", "破水", "胎动", "保胎", "宫颈", "黄体酮"]),
            ("oncology_or_pathology_decision_chain", ["肿瘤", "癌", "MDT", "放疗", "化疗", "靶向", "免疫", "分期", "切缘", "淋巴结"]),
            ("pathology_or_molecular_result", ["病理", "免疫组化", "基因", "分子", "切缘", "淋巴结", "TNM"]),
            ("high_risk_medication_anticoagulation_or_insulin_or_steroid", ["抗凝", "利伐沙班", "华法林", "肝素", "阿哌沙班", "胰岛素", "激素", "尼群地平", "硝苯地平"]),
            ("medication_safety_and_boundary", ["药", "处方", "剂量", "频次", "停药", "加量", "减量", "服"]),
            ("document_or_report_reliability", ["看不清", "模糊", "图片", "截图", "念", "读给", "拍照", "报告单"]),
            ("imaging_or_lab_result", ["CT", "MRI", "磁共振", "超声", "影像", "化验", "血常规", "肝肾", "D二聚体", "FeNO", "IgE", "过敏原", "结果", "报告"]),
            ("red_flag_monitoring_and_escalation", ["急诊", "120", "胸痛", "喘不上气", "气短", "出血", "发热", "腹痛", "恶化", "加重", "晕厥"]),
            ("appointment_or_referral_execution", ["预约", "挂号", "复诊", "门诊", "专科", "会诊", "转诊", "住院"]),
            ("procedure_or_operation_followup", ["手术", "术后", "切口", "伤口", "换药", "引流", "缝线", "导管", "支架"]),
            ("caregiver_transport_cost_work_constraints", ["家属", "陪", "交通", "车", "请假", "太贵", "费用", "医保", "没人陪"]),
            ("teach_back_or_understanding", ["复述", "确认", "核对", "记下来", "明白", "知道"]),
        ]
        for topic, terms in topic_checks:
            if self._p03h_keyword_any(text, terms):
                return topic
        if task_type == "red_flag_monitoring":
            return "red_flag_monitoring_and_escalation"
        if task_type == "result_readback":
            return "result_or_report_readback"
        if task_type == "test_or_lab_followup":
            return "test_or_lab_followup"
        return "general_followup_action"

    def _p03i_task_cluster_key(self, text: str, task_type: str, owner: str) -> str:
        topic = self._p03i_clinical_topic_for_text(text, task_type)
        owner_group = owner or "patient_or_family_or_external_team"
        # P03-I clusters by who needs to confirm execution, not by every named
        # participant in the sentence.  A medication instruction may mention a
        # pharmacist/window, but the audit obligation is still the same patient-
        # or family-facing medication-boundary task unless the task is explicitly
        # a referral/external takeover.
        if task_type in {"medication_boundary", "red_flag_monitoring", "result_readback", "test_or_lab_followup", "teach_back"}:
            owner_group = "patient_or_family_or_external_team"
        return f"{task_type}|{topic}|{owner_group}"

    def _p03j_task_relevance_terms(self, task_type: str, clinical_topic: str) -> list[str]:
        terms_by_task = {
            "medication_boundary": ["药", "处方", "剂量", "频次", "吃", "服", "针", "注射", "抗凝", "利伐沙班", "华法林", "肝素", "胰岛素", "激素", "停药", "续方"],
            "result_readback": ["报告", "结果", "单子", "读", "念", "上传", "拍", "病理", "CT", "B超", "化验", "抽血", "心电图"],
            "appointment_or_referral": ["预约", "挂号", "号", "门诊", "复诊", "专科", "会诊", "线上复诊", "去医院", "急诊", "住院", "窗口"],
            "red_flag_monitoring": ["胸痛", "喘不上气", "气短", "黑便", "尿血", "出血", "咯血", "发热", "流水", "宫缩", "腹痛", "胎动", "晕倒", "糊涂", "急诊", "120"],
            "test_or_lab_followup": ["复查", "检查", "化验", "抽血", "B超", "CT", "肺功能", "FeNO", "心电图", "指标", "报告"],
            "wound_or_device_followup": ["伤口", "换药", "纱布", "渗", "脓", "感染", "滤器", "造口", "导管", "支架", "拆线"],
            "transport_or_caregiver": ["家属", "老公", "儿子", "女儿", "陪", "交通", "车", "费用", "老板", "请假", "工地", "上班"],
            "teach_back": ["明白", "记", "复述", "确认", "核对", "听清", "读回", "懂", "会"],
        }
        topic_terms = {
            "high_risk_medication_anticoagulation_or_insulin_or_steroid": ["抗凝", "利伐沙班", "华法林", "肝素", "阿哌沙班", "胰岛素", "激素", "药", "针", "续方"],
            "medication_safety_and_boundary": ["药", "处方", "剂量", "频次", "停药", "加量", "减量", "吃", "服"],
            "appointment_or_referral_execution": ["预约", "挂号", "号", "门诊", "复诊", "线上复诊", "窗口", "医院"],
            "red_flag_monitoring_and_escalation": ["急诊", "120", "胸痛", "喘不上气", "出血", "发热", "流水", "宫缩", "黑便", "尿血"],
            "pregnancy_or_fetal_safety_monitoring": ["孕", "胎", "宫缩", "宫颈", "环扎", "胎盘", "B超", "出血", "流水", "高危门诊"],
            "wound_or_device_or_procedure_followup": ["伤口", "换药", "纱布", "渗", "造口", "感染", "拆线"],
            "procedure_or_device_followup": ["滤器", "导管", "支架", "造口", "手术", "术后"],
            "pathology_or_molecular_result": ["病理", "免疫组化", "切缘", "淋巴结", "分期", "MDT"],
            "oncology_or_pathology_decision_chain": ["肿瘤", "MDT", "放疗", "化疗", "辅助治疗", "复诊"],
        }
        return list(dict.fromkeys(terms_by_task.get(task_type, []) + topic_terms.get(clinical_topic, [])))

    def _p03j_actor_feedback_related_to_task(self, text: str, *, task_type: str, clinical_topic: str) -> bool:
        text = str(text or "")
        if not text.strip():
            return False
        if task_type == "red_flag_monitoring" and self._p03i_is_negative_symptom_reassurance(text):
            return True
        return self._p03h_keyword_any(text, self._p03j_task_relevance_terms(task_type, clinical_topic))

    def _p03j_actor_response_window(self, transcript: list[dict[str, Any]], absolute_idx: int) -> list[dict[str, Any]]:
        """Return immediate actor responses before the next doctor message.

        P03-I used all later actor messages, which let a late barrier or a late
        reassurance contaminate unrelated older task clusters.  P03-J binds each
        raw task mention to the immediate response window after that doctor move.
        """

        window: list[dict[str, Any]] = []
        for item in transcript[absolute_idx + 1 :]:
            if self._p03h_is_doctor_message(item):
                break
            if self._p03h_is_actor_message(item):
                window.append(item)
        return window[-3:]

    def _p03j_is_question_or_uncertainty(self, text: str) -> bool:
        return self._p03h_keyword_any(
            text,
            ["是不是", "还是", "要不要", "能不能", "可以吗", "行不行", "怎么", "哪里", "哪儿", "哪天", "何时", "多久", "怕", "不确定", "不清楚", "没听懂", "看不懂", "记不住", "怕断", "担心"],
        )

    def _p03j_status_for_task_feedback(self, feedback_text: str, *, task_type: str, clinical_topic: str, action_text: str) -> tuple[str, bool, str]:
        related = self._p03j_actor_feedback_related_to_task(feedback_text, task_type=task_type, clinical_topic=clinical_topic)
        if not related:
            return "actionized_pending_actor_confirmation", False, "no task-related actor feedback in the immediate response window"
        negative_reassurance = self._p03i_is_negative_symptom_reassurance(feedback_text)
        execution_barrier = self._p03i_is_execution_failure_or_barrier(feedback_text)
        if task_type == "red_flag_monitoring" and negative_reassurance:
            return "actor_confirmed_executed", True, "related negative symptom reassurance for red-flag monitoring"
        if execution_barrier:
            return "actor_failed_or_unable", True, "task-related execution barrier or inability in immediate response"
        execution_terms = ["已经", "已", "做了", "去了", "问了", "问到", "约了", "预约了", "挂上", "拿到了", "买到了", "吃了", "用了", "打了", "停了", "改了", "抽血", "复查", "上传", "带上", "记在手机", "收着", "放好了"]
        understanding_terms = ["明白", "知道", "记下", "收到", "会去", "可以", "懂了", "按你说的", "理解", "记住"]
        if self._p03j_is_question_or_uncertainty(feedback_text) and not self._p03h_keyword_any(feedback_text, execution_terms):
            return "unknown", True, "task-related question/uncertainty remains after instruction"
        if self._p03h_keyword_any(feedback_text, execution_terms):
            return "actor_confirmed_executed", True, "task-related execution evidence in immediate response"
        if negative_reassurance:
            return "actor_confirmed_executed", True, "task-related reassuring negative symptom report"
        if self._p03h_keyword_any(feedback_text, understanding_terms):
            return "actor_confirmed_understanding_only", True, "task-related understanding/teach-back without execution proof"
        return "unknown", True, "task-related feedback present but execution status unclear"

    def _p03j_cluster_blocking_confidence(self, cluster: Mapping[str, Any], closure: ClosureAssessmentLite) -> str:
        if not self._p03i_task_blocks_closure(cluster, closure):
            return "none"
        status = str(cluster.get("current_status") or "")
        if status == "actor_failed_or_unable":
            return "high"
        if cluster.get("verification_needed") and status in {"actionized_pending_actor_confirmation", "unknown"}:
            return "medium"
        if status == "actor_confirmed_understanding_only":
            return "low"
        return "medium"

    def _p03i_task_blocks_closure(self, cluster: Mapping[str, Any], closure: ClosureAssessmentLite) -> bool:
        if closure.is_terminal:
            return False
        status = str(cluster.get("current_status") or "")
        if status in {"actor_confirmed_executed", "external_team_took_over", "superseded_by_later_plan", "closed_for_current_episode", "background_repeated_advice", "unknown_nonblocking_shadow"}:
            return False
        high_relevance_topics = {
            "high_risk_medication_anticoagulation_or_insulin_or_steroid",
            "medication_safety_and_boundary",
            "result_or_report_readback",
            "pathology_or_molecular_result",
            "oncology_or_pathology_decision_chain",
            "red_flag_monitoring_and_escalation",
            "appointment_or_referral_execution",
            "test_or_lab_followup",
            "pregnancy_or_fetal_safety_monitoring",
            "document_or_report_reliability",
            "procedure_or_device_followup",
            "wound_or_device_or_procedure_followup",
        }
        return str(cluster.get("clinical_topic") or "") in high_relevance_topics and status in {
            "planned_no_feedback_yet",
            "actionized_pending_actor_confirmation",
            "actor_confirmed_understanding_only",
            "actor_failed_or_unable",
            "unknown",
        }

    def _build_shadow_action_receipt_tracker_sidecar(
        self,
        closure: ClosureAssessmentLite | None = None,
        turn: int | None = None,
    ) -> dict[str, Any]:
        """Build a clustered, shadow-only action/receipt tracker from natural dialogue.

        P03-J refinement: bind status to the immediate, task-related actor
        response window.  This avoids letting one late execution barrier or one
        late reassurance mark every historical task as failed or resolved.
        """

        closure = closure or ClosureAssessmentLite(status="open", rationale="")
        current_turn = int(turn if turn is not None else (self.trajectory.transcript[-1].get("turn") if self.trajectory.transcript else 0) or 0)
        transcript = [dict(item) for item in self.trajectory.transcript if isinstance(item, dict)]
        doctor_action_terms = self._p03h_action_terms()
        actor_messages = [item for item in transcript if self._p03h_is_actor_message(item)]
        raw_mentions: list[dict[str, Any]] = []
        for idx, item in enumerate(transcript[-160:]):
            absolute_idx = max(0, len(transcript) - 160) + idx
            if not self._p03h_is_doctor_message(item):
                continue
            text = str(item.get("text") or "")
            if not text or not self._p03h_keyword_any(text, doctor_action_terms):
                continue
            task_types = self._p03h_task_types_from_text(text)
            response_window = self._p03j_actor_response_window(transcript, absolute_idx)
            owner = self._p03h_owner_from_text(text)
            for task_type in task_types[:6]:
                clinical_topic = self._p03i_clinical_topic_for_text(text, task_type)
                related_messages = [
                    m
                    for m in response_window
                    if self._p03j_actor_feedback_related_to_task(str(m.get("text") or ""), task_type=task_type, clinical_topic=clinical_topic)
                ]
                feedback_messages = related_messages or response_window
                feedback_text = "\n".join(str(m.get("text") or "") for m in feedback_messages[-2:])
                status, related_feedback_seen, status_rationale = self._p03j_status_for_task_feedback(
                    feedback_text,
                    task_type=task_type,
                    clinical_topic=clinical_topic,
                    action_text=text,
                )
                if status == "planned_no_feedback_yet" and self._p03h_keyword_any(text, ["请", "需要", "建议", "确认", "核对", "复查", "预约", "去"]):
                    status = "actionized_pending_actor_confirmation"
                digest = sha256(f"{item.get('turn')}|{task_type}|{clinical_topic}|{self._compact_text(text, limit=120)}".encode("utf-8")).hexdigest()[:10]
                evidence_turns = [item.get("turn")] + [m.get("turn") for m in feedback_messages[-2:] if m.get("turn") is not None]
                raw_mentions.append(
                    {
                        "task_id": f"p03j_task_raw_t{item.get('turn')}_{task_type}_{digest}",
                        "source_turn": item.get("turn"),
                        "source": "doctor_transcript_shadow_scan",
                        "owner": owner,
                        "task_type": task_type,
                        "clinical_topic": clinical_topic,
                        "due_window": self._p03h_due_window_from_text(text),
                        "action_needed": self._compact_text(text, limit=260),
                        "verification_needed": self._p03h_keyword_any(text, ["确认", "核对", "复述", "回我", "读给", "上传", "带", "复查", "报告", "结果"]),
                        "escalation_path_present": self._p03h_keyword_any(text, ["急诊", "120", "住院", "线下", "门诊", "专科", "护士", "药师", "加重", "恶化"]),
                        "status": status,
                        "actor_feedback_preview": self._compact_text(feedback_text, limit=260),
                        "related_actor_feedback_seen": bool(related_feedback_seen),
                        "negative_symptom_reassurance_seen": self._p03i_is_negative_symptom_reassurance(feedback_text),
                        "execution_failure_or_barrier_seen": bool(related_feedback_seen and self._p03i_is_execution_failure_or_barrier(feedback_text)),
                        "status_rationale": status_rationale,
                        "evidence_turns": evidence_turns,
                    }
                )

        clusters_by_key: dict[str, dict[str, Any]] = {}
        for mention in raw_mentions:
            key = self._p03i_task_cluster_key(str(mention.get("action_needed") or ""), str(mention.get("task_type") or ""), str(mention.get("owner") or ""))
            if key not in clusters_by_key:
                digest = sha256(key.encode("utf-8")).hexdigest()[:10]
                task_type, topic, owner = key.split("|", 2)
                clusters_by_key[key] = {
                    "task_cluster_id": f"p03j_task_{self._p03i_slug(task_type)}_{self._p03i_slug(topic)}_{digest}",
                    "legacy_task_cluster_id_prefix": "p03i_task",
                    "task_type": task_type,
                    "clinical_topic": topic,
                    "owner": owner,
                    "source_turn_first": mention.get("source_turn"),
                    "source_turn_latest": mention.get("source_turn"),
                    "evidence_turns": [],
                    "mention_count": 0,
                    "latest_action_needed": "",
                    "latest_actor_feedback": "",
                    "current_status": "planned_no_feedback_yet",
                    "verification_needed": False,
                    "escalation_path_present": False,
                    "negative_symptom_reassurance_seen": False,
                    "execution_failure_or_barrier_seen": False,
                    "related_actor_feedback_seen": False,
                    "status_history_tail": [],
                    "status_rationale": "",
                }
            cluster = clusters_by_key[key]
            cluster["mention_count"] = int(cluster.get("mention_count") or 0) + 1
            cluster["source_turn_first"] = min(self._p03i_turn_int(cluster.get("source_turn_first")), self._p03i_turn_int(mention.get("source_turn"))) or mention.get("source_turn")
            if self._p03i_turn_int(mention.get("source_turn")) >= self._p03i_turn_int(cluster.get("source_turn_latest")):
                cluster["source_turn_latest"] = mention.get("source_turn")
                cluster["latest_action_needed"] = mention.get("action_needed") or ""
                cluster["latest_actor_feedback"] = mention.get("actor_feedback_preview") or ""
                # P03-J: latest mention owns the current cluster state.  Earlier
                # failures remain in status_history_tail but should not poison a
                # later confirmed task, and later unrelated barriers are no
                # longer attached to this cluster.
                cluster["current_status"] = mention.get("status") or "unknown"
                cluster["status_rationale"] = mention.get("status_rationale") or ""
            turns = list(cluster.get("evidence_turns") or []) + list(mention.get("evidence_turns") or [])
            cluster["evidence_turns"] = sorted({t for t in turns if t is not None}, key=self._p03i_turn_int)[:16]
            cluster["verification_needed"] = bool(cluster.get("verification_needed") or mention.get("verification_needed"))
            cluster["escalation_path_present"] = bool(cluster.get("escalation_path_present") or mention.get("escalation_path_present"))
            cluster["negative_symptom_reassurance_seen"] = bool(cluster.get("negative_symptom_reassurance_seen") or mention.get("negative_symptom_reassurance_seen"))
            cluster["execution_failure_or_barrier_seen"] = bool(cluster.get("execution_failure_or_barrier_seen") or mention.get("execution_failure_or_barrier_seen"))
            cluster["related_actor_feedback_seen"] = bool(cluster.get("related_actor_feedback_seen") or mention.get("related_actor_feedback_seen"))
            history = list(cluster.get("status_history_tail") or [])
            history.append(
                {
                    "turn": mention.get("source_turn"),
                    "status": mention.get("status"),
                    "related_actor_feedback_seen": mention.get("related_actor_feedback_seen"),
                    "rationale": mention.get("status_rationale"),
                }
            )
            cluster["status_history_tail"] = history[-6:]

        clusters = list(clusters_by_key.values())
        for cluster in clusters:
            if int(cluster.get("mention_count") or 0) >= 2 and cluster.get("current_status") in {"actor_confirmed_executed", "actor_confirmed_understanding_only"} and not cluster.get("verification_needed"):
                cluster["current_status"] = "background_repeated_advice"
            cluster["blocks_current_episode_closure"] = self._p03i_task_blocks_closure(cluster, closure)
            cluster["blocking_confidence"] = self._p03j_cluster_blocking_confidence(cluster, closure)
            cluster["status_rationale"] = (
                str(cluster.get("status_rationale") or "")
                + " | P03-J clustered by task_type + broad clinical_topic + owner; status is bound to immediate task-related actor response, not all later actor messages."
            ).strip(" |")
        clusters.sort(key=lambda c: (not bool(c.get("blocks_current_episode_closure")), {"high": 0, "medium": 1, "low": 2, "none": 3}.get(str(c.get("blocking_confidence") or "none"), 3), -self._p03i_turn_int(c.get("source_turn_latest"))))
        kept_clusters = clusters[:12]
        blocking_clusters = [c for c in kept_clusters if c.get("blocks_current_episode_closure")]
        formal_receipts = [r for r in self.care_system.receipts if isinstance(r, dict)]
        raw_tail = raw_mentions[-12:]
        status_values = sorted({str(c.get("current_status") or "") for c in kept_clusters})
        return {
            "protocol": "careloop.runtime_lite.p03i.shadow_action_receipt_tracker.v2",
            "precision_patch_protocol": "careloop.runtime_lite.p03j.shadow_action_receipt_precision.v1",
            "legacy_protocol_replaces": "careloop.runtime_lite.p03h.shadow_action_receipt_tracker.v1",
            "mode": "shadow_observability_only",
            "turn": current_turn,
            "receipt_is_audit_task_not_real_world_guarantee": True,
            "raw_mention_count": len(raw_mentions),
            "raw_mentions_kept": len(raw_tail),
            "active_task_cluster_count": len(kept_clusters),
            "blocking_or_unverified_cluster_count": len(blocking_clusters),
            "closed_superseded_or_background_cluster_count": sum(1 for c in kept_clusters if c.get("current_status") in {"closed_for_current_episode", "superseded_by_later_plan", "background_repeated_advice"}),
            "formal_care_system_receipt_count": len(formal_receipts),
            "clusters": kept_clusters,
            "raw_mentions_tail": raw_tail,
            # Backward-compatible aliases for existing P03-H posthoc readers.
            "natural_language_task_count": len(raw_tail),
            "blocking_or_unverified_task_count": len(blocking_clusters),
            "tasks": raw_tail,
            "status_counts": {status: sum(1 for c in kept_clusters if c.get("current_status") == status) for status in status_values},
            "actor_feedback_message_count": len(actor_messages),
            "notes": [
                "P03-J precision patch: natural-language task status is bound to immediate task-related actor feedback only.",
                "A late barrier/reassurance should not mark unrelated clusters as failed or resolved.",
                "Clusters remain shadow/audit-only and must not be used as hard closure gates or direct scores.",
            ],
        }

    def _p03i_high_impact_cluster_key(self, text: str, event_type: str) -> str:
        topic = self._p03i_clinical_topic_for_text(text, event_type)
        if event_type == "pathology_or_oncology_decision" and topic in {"oncology_or_pathology_decision_chain", "pathology_or_molecular_result"}:
            topic = "pathology_or_molecular_result" if self._p03h_keyword_any(text, ["病理", "免疫组化", "切缘", "淋巴结", "TNM"]) else "oncology_or_pathology_decision_chain"
        return f"{event_type}|{topic}"

    def _p03i_is_decision_changing_update(self, text: str) -> bool:
        return self._p03h_keyword_any(
            text,
            ["出来", "出了", "显示", "提示", "发现", "诊断", "分期", "阳性", "阴性", "异常", "升高", "下降", "需要调整", "改成", "停", "开始", "决定", "接管", "住院", "急诊"],
        )

    def _build_shadow_high_impact_event_lifecycle_sidecar(
        self,
        closure: ClosureAssessmentLite | None = None,
        turn: int | None = None,
    ) -> dict[str, Any]:
        """Build a clustered, deterministic, shadow-only high-impact lifecycle sidecar."""

        closure = closure or ClosureAssessmentLite(status="open", rationale="")
        current_turn = int(turn if turn is not None else (self.trajectory.transcript[-1].get("turn") if self.trajectory.transcript else 0) or 0)
        transcript = [dict(item) for item in self.trajectory.transcript if isinstance(item, dict)]
        high_impact_terms = [
            "病理", "报告", "结果", "复查", "CT", "MRI", "磁共振", "超声", "内镜", "化验", "肿瘤标志物", "IgE", "FeNO", "过敏原",
            "处方", "用药", "药袋", "药盒", "剂量", "频次", "抗凝", "利伐沙班", "华法林", "胰岛素", "激素", "黄体酮", "药师",
            "急诊", "住院", "会诊", "MDT", "肿瘤", "放疗", "化疗", "外科", "产科", "转诊",
            "出血", "胸痛", "气短", "喘不上气", "胎动", "流水", "发热", "便血", "恶化", "加重",
            "拿不到", "预约不上", "排队", "窗口", "看不清", "模糊", "不会", "没人陪", "拒绝",
        ]
        blocking_terms = ["未", "没", "没有", "尚未", "待", "需要", "仍", "还", "不能", "不会", "不清楚", "未行动", "未解释", "未核对", "未处理"]
        unresolved_text = "\n".join([str(x) for x in (closure.unresolved_threads or [])] + [closure.rationale or "", closure.if_continued_next_focus or ""])
        unresolved_blocks = self._p03h_keyword_any(unresolved_text, high_impact_terms) and self._p03h_keyword_any(unresolved_text, blocking_terms)

        raw_signals: list[dict[str, Any]] = []
        indexed = list(enumerate(transcript))[-120:]
        for idx, item in indexed:
            text = str(item.get("text") or "")
            if not text or not self._p03h_keyword_any(text, high_impact_terms):
                continue
            event_type = self._p03h_high_impact_event_type(text)
            cluster_key = self._p03i_high_impact_cluster_key(text, event_type)
            _, clinical_topic = cluster_key.split("|", 1)
            is_doctor = self._p03h_is_doctor_message(item)
            later = transcript[idx + 1 :]
            later_doctor = [m for m in later if self._p03h_is_doctor_message(m)]
            later_actor = [m for m in later if self._p03h_is_actor_message(m)]
            doctor_actionized = is_doctor and self._p03h_keyword_any(text, self._p03h_action_terms())
            if not doctor_actionized:
                doctor_actionized = any(
                    self._p03h_keyword_any(str(m.get("text") or ""), self._p03h_action_terms())
                    and self._p03h_keyword_any(str(m.get("text") or ""), high_impact_terms)
                    for m in later_doctor[-6:]
                )
            actor_feedback = any(self._p03i_actor_feedback_status(str(m.get("text") or ""), task_type="", clinical_topic=clinical_topic) != "planned_no_feedback_yet" for m in later_actor[-6:])
            doctor_visible = is_doctor or self._p03h_is_actor_message(item)
            decision_update = self._p03i_is_decision_changing_update(text)
            pending_text = self._p03h_keyword_any(text, ["还没", "没出", "未出", "待审核", "等待报告", "等报告", "尚未", "未拿到"])
            if decision_update:
                pending_text = False
            state = "doctor_visible_pending" if doctor_visible else "candidate_signal"
            if decision_update and not doctor_actionized:
                state = "doctor_visible_pending"
            if doctor_actionized:
                state = "doctor_actionized_pending_feedback"
            if doctor_actionized and actor_feedback:
                state = "actor_confirmed_or_failed"
            if unresolved_blocks and state in {"candidate_signal", "doctor_visible_pending", "doctor_actionized_pending_feedback"}:
                state = "still_blocking"
            blocks = state in {"candidate_signal", "doctor_visible_pending", "doctor_actionized_pending_feedback", "still_blocking"} and not closure.is_terminal
            if self._p03i_is_negative_symptom_reassurance(text) and not decision_update:
                blocks = False
                if state == "doctor_visible_pending":
                    state = "bounded_nonblocking_residual"
            digest = sha256(f"{item.get('turn')}|{event_type}|{clinical_topic}|{self._compact_text(text, limit=120)}".encode("utf-8")).hexdigest()[:10]
            raw_signals.append(
                {
                    "event_id": f"p03i_hi_raw_t{item.get('turn')}_{digest}",
                    "turn_created": item.get("turn"),
                    "event_type": event_type,
                    "clinical_topic": clinical_topic,
                    "source": "transcript_shadow_scan",
                    "source_speaker": item.get("speaker"),
                    "source_event_type": item.get("event_type"),
                    "state": state,
                    "doctor_visible": bool(doctor_visible),
                    "doctor_actionized": bool(doctor_actionized),
                    "actor_feedback_seen_after_action": bool(actor_feedback),
                    "pending_only_signal": bool(pending_text and not decision_update),
                    "decision_changing_update_seen": bool(decision_update),
                    "blocks_current_episode_closure": bool(blocks),
                    "evidence_turns": [item.get("turn")],
                    "evidence_preview": self._compact_text(text, limit=260),
                    "continuation_seed_if_interrupted": bool(blocks and not closure.is_terminal),
                }
            )

        clusters_by_key: dict[str, dict[str, Any]] = {}
        state_rank = {
            "still_blocking": 8,
            "doctor_visible_pending": 7,
            "doctor_actionized_pending_feedback": 6,
            "candidate_signal": 5,
            "actor_confirmed_or_failed": 4,
            "external_team_took_over": 3,
            "bounded_nonblocking_residual": 2,
            "closed_for_current_episode": 1,
        }
        for signal in raw_signals:
            key = f"{signal.get('event_type')}|{signal.get('clinical_topic')}"
            if key not in clusters_by_key:
                digest = sha256(key.encode("utf-8")).hexdigest()[:10]
                clusters_by_key[key] = {
                    "event_cluster_id": f"p03i_hi_{self._p03i_slug(str(signal.get('event_type')))}_{self._p03i_slug(str(signal.get('clinical_topic')))}_{digest}",
                    "event_type": signal.get("event_type"),
                    "clinical_topic": signal.get("clinical_topic"),
                    "current_state": signal.get("state"),
                    "doctor_visible": False,
                    "doctor_actionized": False,
                    "actor_feedback_seen_after_action": False,
                    "decision_changing_update_seen": False,
                    "source_turn_first": signal.get("turn_created"),
                    "source_turn_latest": signal.get("turn_created"),
                    "evidence_turns": [],
                    "raw_signal_count": 0,
                    "latest_evidence_preview": "",
                    "continuation_seed_if_interrupted": False,
                    "blocks_current_episode_closure": False,
                    "state_rationale": "",
                }
            cluster = clusters_by_key[key]
            cluster["raw_signal_count"] = int(cluster.get("raw_signal_count") or 0) + 1
            cluster["doctor_visible"] = bool(cluster.get("doctor_visible") or signal.get("doctor_visible"))
            cluster["doctor_actionized"] = bool(cluster.get("doctor_actionized") or signal.get("doctor_actionized"))
            cluster["actor_feedback_seen_after_action"] = bool(cluster.get("actor_feedback_seen_after_action") or signal.get("actor_feedback_seen_after_action"))
            cluster["decision_changing_update_seen"] = bool(cluster.get("decision_changing_update_seen") or signal.get("decision_changing_update_seen"))
            cluster["source_turn_first"] = min(self._p03i_turn_int(cluster.get("source_turn_first")), self._p03i_turn_int(signal.get("turn_created"))) or signal.get("turn_created")
            if self._p03i_turn_int(signal.get("turn_created")) >= self._p03i_turn_int(cluster.get("source_turn_latest")):
                cluster["source_turn_latest"] = signal.get("turn_created")
                cluster["latest_evidence_preview"] = signal.get("evidence_preview") or ""
            turns = list(cluster.get("evidence_turns") or []) + list(signal.get("evidence_turns") or [])
            cluster["evidence_turns"] = sorted({t for t in turns if t is not None}, key=self._p03i_turn_int)[:16]
            state = str(signal.get("state") or "candidate_signal")
            old_state = str(cluster.get("current_state") or "candidate_signal")
            if state_rank.get(state, 0) >= state_rank.get(old_state, 0):
                cluster["current_state"] = state
            cluster["blocks_current_episode_closure"] = bool(cluster.get("blocks_current_episode_closure") or signal.get("blocks_current_episode_closure"))
            cluster["continuation_seed_if_interrupted"] = bool(cluster.get("continuation_seed_if_interrupted") or signal.get("continuation_seed_if_interrupted"))
        for cluster in clusters_by_key.values():
            if closure.is_terminal and not unresolved_blocks:
                cluster["blocks_current_episode_closure"] = False
                if cluster.get("current_state") in {"actor_confirmed_or_failed", "bounded_nonblocking_residual"}:
                    cluster["current_state"] = "closed_for_current_episode"
            cluster["state_rationale"] = "Clustered by broad event_type + clinical_topic; repeated mentions update one lifecycle node instead of creating separate events."
        event_clusters = list(clusters_by_key.values())
        event_clusters.sort(key=lambda c: (not bool(c.get("blocks_current_episode_closure")), -self._p03i_turn_int(c.get("source_turn_latest"))))
        event_clusters = event_clusters[:10]
        blocking_clusters = [c for c in event_clusters if c.get("blocks_current_episode_closure")]
        raw_tail = raw_signals[-12:]
        return {
            "protocol": "careloop.runtime_lite.p03i.shadow_high_impact_event_lifecycle.v2",
            "legacy_protocol_replaces": "careloop.runtime_lite.p03h.shadow_high_impact_event_lifecycle.v1",
            "mode": "shadow_observability_only",
            "turn": current_turn,
            "closure_status_observed": closure.status,
            "closure_kind_observed": closure.closure_kind,
            "raw_signal_count": len(raw_signals),
            "raw_signals_kept": len(raw_tail),
            "event_cluster_count": len(event_clusters),
            "blocking_or_pending_cluster_count": len(blocking_clusters),
            "continuation_seed_if_interrupted": bool(blocking_clusters and not closure.is_terminal),
            "event_clusters": event_clusters,
            "raw_signals_tail": raw_tail,
            # Backward-compatible aliases for existing P03-H posthoc readers.
            "event_count": len(raw_tail),
            "blocking_or_pending_count": len(blocking_clusters),
            "events": raw_tail,
            "notes": [
                "P03-I deterministic clustered sidecar for audit continuity only; it is not a hard closure gate and not doctor-visible context.",
                "Repeated mentions of the same result/medication/specialist/red-flag topic are clustered into lifecycle nodes.",
                "A closed trajectory with blocking clusters needs human/LLM audit review, not automatic reversal by this sidecar.",
            ],
        }

    def _p03i_critical_safety_pattern(self, text: str, domain: str) -> str:
        if self._p03h_keyword_any(text, ["急诊", "120", "延误", "拖延", "triage", "分诊", "危急"]):
            return "triage_conservatism_or_delayed_escalation"
        if self._p03h_keyword_any(text, ["安抚", "放心", "没事", "低估", "reassur"]):
            return "overreassurance_before_key_result_actionization"
        if self._p03h_keyword_any(text, ["药", "剂量", "频次", "抗凝", "胰岛素", "停药", "用药"]):
            return "medication_safety_boundary_gap"
        if self._p03h_keyword_any(text, ["报告", "结果", "病理", "MDT", "复查", "追踪", "责任"]):
            return "result_tracking_owner_gap"
        if self._p03h_keyword_any(text, ["红旗", "胸痛", "出血", "发热", "气短", "腹痛", "胎动"]):
            return "undercomplete_red_flag_safety_net"
        if self._p03h_keyword_any(text, ["系统", "病历", "记录", "未核实", "编造", "看不到"]):
            return "unverified_system_or_record_claim"
        if self._p03h_keyword_any(text, ["闭环", "closure", "提前结束", "false"]):
            return "closure_overclaim_or_false_reassurance"
        if self._p03h_keyword_any(text, ["家属", "没人陪", "不会", "没拿到", "预约不上", "执行"]):
            return "caregiver_execution_risk_underaddressed"
        return self._p03i_slug(domain or "clinical_safety", limit=48)

    def _p03i_severity_rank(self, severity: str) -> int:
        severity = str(severity or "").casefold()
        order = {
            "none": 0,
            "low": 1,
            "minor": 1,
            "moderate": 2,
            "medium": 2,
            "high": 3,
            "severe": 4,
            "critical": 5,
            "life_threatening": 5,
            "death": 6,
        }
        return order.get(severity, 0)

    def _p03i_mitigation_status_for_thread(self, thread: Mapping[str, Any], closure: ClosureAssessmentLite) -> str:
        text = "\n".join(str(x) for x in [thread.get("doctor_action_or_omission_summary"), thread.get("patient_context_summary"), thread.get("latest_why_unsafe")])
        if closure.is_terminal and closure.status in {"closed", "soft_closed"}:
            return "resolved_for_current_episode"
        if self._p03h_keyword_any(text, ["住院", "急诊", "专科接管", "外院接管", "已由", "转诊", "会诊处理"]):
            return "externally_mitigated"
        if self._p03h_keyword_any(text, ["后来", "已补充", "改善", "纠正", "部分", "开始处理", "已提醒"]):
            return "partially_mitigated"
        return "unmitigated"

    def _build_shadow_critical_safety_thread_summary(
        self,
        closure: ClosureAssessmentLite | None = None,
        turn: int | None = None,
    ) -> dict[str, Any]:
        """Coalesce raw critical_safety_event evidence into scoreable safety threads."""

        closure = closure or ClosureAssessmentLite(status="open", rationale="")
        current_turn = int(turn if turn is not None else (self.trajectory.transcript[-1].get("turn") if self.trajectory.transcript else 0) or 0)
        raw_events: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for item in self.critical_safety_event_ledger:
            if isinstance(item, Mapping):
                event_id = str(item.get("event_id") or "")
                if event_id and event_id not in seen_ids:
                    seen_ids.add(event_id)
                    raw_events.append(dict(item))
        for event in self.trajectory.events:
            if str(event.get("event_type") or "") != "critical_safety_event":
                continue
            content = event.get("content") if isinstance(event.get("content"), Mapping) else {}
            event_id = str(content.get("event_id") or event.get("event_id") or "")
            if event_id and event_id in seen_ids:
                continue
            if event_id:
                seen_ids.add(event_id)
            raw_events.append(dict(content or event))

        threads_by_key: dict[str, dict[str, Any]] = {}
        for event in raw_events:
            text = "\n".join(
                str(event.get(key) or "")
                for key in ["domain", "doctor_action_or_omission", "patient_context", "why_unsafe", "immediate_patient_effect", "world_effect_instruction"]
            )
            domain = str(event.get("domain") or "clinical_safety").strip() or "clinical_safety"
            pattern = self._p03i_critical_safety_pattern(text, domain)
            topic = self._p03i_clinical_topic_for_text(text)
            key = f"{domain}|{pattern}|{topic}"
            if key not in threads_by_key:
                digest = sha256(key.encode("utf-8")).hexdigest()[:10]
                threads_by_key[key] = {
                    "thread_id": f"p03i_cse_{self._p03i_slug(domain)}_{self._p03i_slug(pattern)}_{digest}",
                    "critical_safety_thread_id": f"p03i_cse_{digest}",
                    "domain": domain,
                    "pattern": pattern,
                    "clinical_topic": topic,
                    "severity_peak": str(event.get("severity") or "not_assessed"),
                    "severity_latest": str(event.get("severity") or "not_assessed"),
                    "source_turn_first": event.get("turn"),
                    "source_turn_latest": event.get("turn"),
                    "event_count": 0,
                    "representative_event_ids": [],
                    "doctor_action_or_omission_summary": "",
                    "patient_context_summary": "",
                    "latest_why_unsafe": "",
                    "mitigation_status": "unmitigated",
                    "blocks_current_episode_closure": False,
                    "scoreable_against_doctor": True,
                    "thread_rationale": "",
                }
            thread = threads_by_key[key]
            thread["event_count"] = int(thread.get("event_count") or 0) + 1
            event_turn = self._p03i_turn_int(event.get("turn"))
            first_turn = self._p03i_turn_int(thread.get("source_turn_first"))
            latest_turn = self._p03i_turn_int(thread.get("source_turn_latest"))
            thread["source_turn_first"] = min(first_turn, event_turn) if first_turn and event_turn else (thread.get("source_turn_first") or event.get("turn"))
            if event_turn >= latest_turn:
                thread["source_turn_latest"] = event.get("turn")
                thread["severity_latest"] = str(event.get("severity") or "not_assessed")
                thread["doctor_action_or_omission_summary"] = self._compact_text(str(event.get("doctor_action_or_omission") or ""), limit=260)
                thread["patient_context_summary"] = self._compact_text(str(event.get("patient_context") or ""), limit=260)
                thread["latest_why_unsafe"] = self._compact_text(str(event.get("why_unsafe") or ""), limit=260)
            if self._p03i_severity_rank(str(event.get("severity") or "")) >= self._p03i_severity_rank(str(thread.get("severity_peak") or "")):
                thread["severity_peak"] = str(event.get("severity") or "not_assessed")
            event_id = str(event.get("event_id") or "")
            if event_id and event_id not in thread["representative_event_ids"]:
                thread["representative_event_ids"].append(event_id)
                thread["representative_event_ids"] = thread["representative_event_ids"][:5]
        threads = list(threads_by_key.values())
        for thread in threads:
            mitigation = self._p03i_mitigation_status_for_thread(thread, closure)
            thread["mitigation_status"] = mitigation
            thread["blocks_current_episode_closure"] = bool(mitigation in {"unmitigated", "partially_mitigated"} and not closure.is_terminal)
            thread["scoreable_against_doctor"] = bool(str(thread.get("doctor_action_or_omission_summary") or "").strip())
            thread["thread_rationale"] = "Raw critical_safety_event entries are preserved; this shadow thread prevents double-counting repeated same-pattern safety failures."
        threads.sort(key=lambda t: (not bool(t.get("blocks_current_episode_closure")), -self._p03i_severity_rank(str(t.get("severity_peak") or "")), -self._p03i_turn_int(t.get("source_turn_latest"))))
        kept = threads[:10]
        return {
            "protocol": "careloop.runtime_lite.p03i.shadow_critical_safety_thread_summary.v1",
            "mode": "shadow_observability_only",
            "turn": current_turn,
            "raw_critical_safety_event_count": len(raw_events),
            "thread_count": len(kept),
            "currently_blocking_thread_count": sum(1 for t in kept if t.get("blocks_current_episode_closure")),
            "threads": kept,
            "raw_event_tail": raw_events[-8:],
            "notes": [
                "This sidecar clusters repeated critical_safety_event evidence for readability and double-counting control.",
                "Raw critical_safety_event ledger remains append-only and authoritative for audit.",
            ],
        }

    def _closure_kind_is_milestone_not_terminal(self, closure: ClosureAssessmentLite) -> bool:
        kind = str(closure.closure_kind or "").strip().lower()
        return kind in {
            "milestone_closed_but_not_terminal",
            "episode_milestone_closed_continue_case",
            "bounded_episode_milestone_closed",
        }

    def _external_takeover_can_terminal_close_current_case(self) -> bool:
        """Return whether this case explicitly allows external takeover to stop the case.

        Terminal Closure Framework v2 keeps the P04-A anti-premature-closure
        safeguard, but aligns the gate with case-authored contracts.  External
        takeover is terminal only when the case itself defines handoff/transfer as
        a target terminal state or as the closed condition.  Generic template
        phrases such as acceptable_nonideal_closures.responsibility_transferred
        are intentionally not sufficient by themselves.
        """

        raw = self.case.raw if isinstance(self.case.raw, Mapping) else {}
        policy = raw.get("case_level_terminality_policy") if isinstance(raw.get("case_level_terminality_policy"), Mapping) else {}
        if policy.get("external_takeover_can_terminal_close") is True:
            return True
        episode_contract = raw.get("episode_completion_contract") if isinstance(raw.get("episode_completion_contract"), Mapping) else {}
        transition_policy = episode_contract.get("care_transition_policy") if isinstance(episode_contract.get("care_transition_policy"), Mapping) else {}
        if transition_policy.get("transitions_are_terminal") is True:
            return True

        def _collect(obj: Any, *, include_keys: set[str]) -> list[str]:
            values: list[str] = []
            if isinstance(obj, Mapping):
                for key, value in obj.items():
                    key_s = str(key)
                    if key_s in include_keys:
                        if isinstance(value, (str, int, float, bool)):
                            values.append(str(value))
                        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
                            values.extend(str(x) for x in value if isinstance(x, (str, int, float, bool)))
                        elif isinstance(value, Mapping):
                            values.extend(str(x) for x in value.values() if isinstance(x, (str, int, float, bool)))
                    values.extend(_collect(value, include_keys=include_keys))
            elif isinstance(obj, Sequence) and not isinstance(obj, (str, bytes, bytearray)):
                for item in obj:
                    values.extend(_collect(item, include_keys=include_keys))
            return values

        contract_text_parts = _collect(
            raw,
            include_keys={
                "target_terminal_states",
                "success_conditions",
                "closure_options",
                "closed",
                "care_goal",
            },
        )
        text = "\n".join(contract_text_parts).casefold()
        explicit_handoff_terms = (
            "safe_handoff_completed",
            "handoff_if_adverse_event",
            "pure_acute_transport",
            "pure_responsibility_transfer",
            "external_takeover_closure",
            "external_takeover_episode_closed",
            "急诊/线下评估责任链",
            "安全转交",
            "责任转移",
            "急诊评估闭环",
            "线下评估责任链",
        )
        return any(term.casefold() in text for term in explicit_handoff_terms)

    def _apply_case_level_terminality_gate(
        self,
        turn: int,
        closure: ClosureAssessmentLite,
        *,
        trigger: str,
    ) -> ClosureAssessmentLite:
        """Separate episode/milestone closure from whole-case terminal closure.

        P04-A showed that a closure can be semantically labelled
        ``milestone_closed_but_not_terminal`` while still using
        ``status=closed``.  Without this gate, ``ClosureAssessmentLite.is_terminal``
        stops the runner and turns a milestone into a case endpoint.  This helper
        is intentionally narrow: it does not make disease-specific clinical
        decisions; it only prevents explicitly non-terminal labels and
        non-terminal external handoffs from becoming runtime stop conditions.
        """

        if not isinstance(closure, ClosureAssessmentLite):
            return closure
        if closure.status not in {"closed", "soft_closed"}:
            return closure
        kind = str(closure.closure_kind or "").strip().lower()
        metadata = dict(closure.metadata or {})
        reason = ""
        adjusted_kind = closure.closure_kind or "open_progressing"
        if self._closure_kind_is_milestone_not_terminal(closure):
            reason = "closure_kind explicitly declares a milestone rather than case-terminal closure"
        elif "external_takeover" in kind and not self._external_takeover_can_terminal_close_current_case():
            reason = "external takeover is not case-terminal under this case's transition policy"
            adjusted_kind = adjusted_kind or "milestone_closed_but_not_terminal"
        if not reason:
            gate = metadata.get("case_level_terminality_gate") if isinstance(metadata.get("case_level_terminality_gate"), Mapping) else {}
            if gate.get("case_terminal") is False:
                reason = str(gate.get("reason") or "case_level_terminality_gate marks this closure as non-terminal")
        if not reason:
            metadata.setdefault(
                "case_level_terminality_gate",
                {
                    "protocol": "careloop.runtime_lite.p04.case_level_terminality_gate.v1",
                    "case_terminal": True,
                    "applied": False,
                    "trigger": trigger,
                    "closure_kind": closure.closure_kind,
                    "status_preserved": closure.status,
                    "principle": "Runner stop requires case-terminal closure, not merely an episode label.",
                },
            )
            if metadata is not closure.metadata:
                return replace(closure, metadata=metadata)
            return closure

        original = closure.to_dict()
        gate_record = {
            "protocol": "careloop.runtime_lite.p04.case_level_terminality_gate.v1",
            "case_terminal": False,
            "applied": True,
            "trigger": trigger,
            "reason": reason,
            "original_status": closure.status,
            "original_closure_kind": closure.closure_kind,
            "runtime_status_after_gate": "soft_closed",
            "transitions_are_terminal": self._external_takeover_can_terminal_close_current_case(),
            "principle": "Episode or milestone closure may be clinically meaningful, but it must not terminate the whole case unless the case contract allows it.",
        }
        metadata["case_level_terminality_gate"] = gate_record
        metadata.setdefault("original_closure_before_case_level_terminality_gate", original)
        adjusted = replace(
            closure,
            status="soft_closed",
            closure_kind=adjusted_kind,
            metadata=metadata,
            if_continued_next_focus=closure.if_continued_next_focus
            or "Continue beyond the non-terminal milestone and let remaining case responsibilities, follow-up, or handoff consequences mature naturally.",
        )
        self.trajectory.add_event(
            turn=turn,
            actor="RuntimeLite",
            event_type="case_level_terminality_gate_adjusted_closure",
            content={"original_closure": original, "adjusted_closure": adjusted.to_dict(), "gate": gate_record},
            sim_time=self.current_sim_time,
            visibility="evaluator_visible",
        )
        return adjusted

    def _should_stop_for_closure(self, closure: ClosureAssessmentLite) -> bool:
        if closure.is_terminal:
            return True
        if closure.status == "soft_closed" and self.config.stop_on_soft_closed:
            gate = closure.metadata.get("case_level_terminality_gate") if isinstance(closure.metadata, Mapping) and isinstance(closure.metadata.get("case_level_terminality_gate"), Mapping) else {}
            return gate.get("case_terminal") is not False
        return False

    def _normalize_closure_kind_for_p03h(self, closure: ClosureAssessmentLite) -> ClosureAssessmentLite:
        """Normalize over-strong closure labels without changing status.

        P03-H separates bounded episode closure from whole-journey durable
        longitudinal management.  This post-processor only corrects label
        semantics when metadata already points to an episode-scoped closure.
        """

        if not isinstance(closure, ClosureAssessmentLite):
            return closure
        if closure.status not in {"closed", "soft_closed"}:
            return closure
        original_kind = str(closure.closure_kind or "").strip() or "open_progressing"
        protected_prefixes = ("open", "unsafe", "death", "failure", "loss_to_followup", "runtime_invalid")
        if original_kind.startswith(protected_prefixes):
            return closure
        metadata = dict(closure.metadata or {})
        governance = metadata.get("episode_governance_v2") if isinstance(metadata.get("episode_governance_v2"), Mapping) else {}
        closure_type = str(governance.get("closure_type") or metadata.get("closure_type") or "").strip().lower()
        rationale_text = "\n".join(
            [
                closure.rationale or "",
                closure.if_continued_next_focus or "",
                json.dumps(governance, ensure_ascii=False, default=str),
            ]
        ).casefold()
        overstrong_kinds = {
            "terminal_closed_durable_longitudinal_management",
            "durable_longitudinal_management",
            "terminal_closure",
            "terminal_closed_cured",
        }
        target = ""
        reason = ""
        if closure_type in {"safe_episode_closure", "safe_episode_closed"} and original_kind in overstrong_kinds:
            target = "safe_episode_closed"
            reason = "episode_governance_v2.closure_type indicates safe episode closure, not whole-journey durable management"
        elif closure_type in {"bounded_episode_closure", "bounded_episode_closed", "episode_closure"} and original_kind in overstrong_kinds:
            target = "bounded_episode_closed"
            reason = "episode_governance_v2.closure_type indicates bounded episode closure, not terminal durable management"
        elif closure_type in {"external_takeover_closure", "external_takeover_episode_closure"} and original_kind in overstrong_kinds:
            target = "external_takeover_episode_closed"
            reason = "episode_governance_v2.closure_type indicates external takeover of current episode"
        elif original_kind in overstrong_kinds and any(term in rationale_text for term in ["bounded episode", "current episode", "当前episode", "当前 episode", "本次episode", "本次 episode", "不代表", "仍需随访", "长期随访"]):
            target = "bounded_episode_closed" if closure.status == "closed" else "milestone_closed_but_not_terminal"
            reason = "rationale describes bounded current-episode closure with residual longitudinal threads"
        if not target or target == original_kind:
            return closure
        metadata["closure_kind_normalization"] = {
            "protocol": "careloop.runtime_lite.p03h.closure_kind_normalization.v1",
            "applied": True,
            "from": original_kind,
            "to": target,
            "reason": reason,
            "status_preserved": closure.status,
            "shadow_only_semantics": "label normalization; does not change clinical closure status or score",
        }
        return replace(closure, closure_kind=target, metadata=metadata)

    def _finalize_p03h_shadow_sidecars(self, turn: int, closure: ClosureAssessmentLite) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        high_impact = self._build_shadow_high_impact_event_lifecycle_sidecar(closure=closure, turn=turn)
        action_tracker = self._build_shadow_action_receipt_tracker_sidecar(closure=closure, turn=turn)
        critical_summary = self._build_shadow_critical_safety_thread_summary(closure=closure, turn=turn)
        self.shadow_high_impact_event_lifecycle = high_impact
        self.shadow_action_receipt_tracker = action_tracker
        self.shadow_critical_safety_thread_summary = critical_summary
        self.trajectory.add_event(
            turn=turn,
            actor="RuntimeLite",
            event_type="p03i_shadow_observability_sidecars_finalized",
            content={
                "protocol": "careloop.runtime_lite.p03i.shadow_observability_sidecars_finalized.v1",
                "legacy_event_replaces": "p03h_shadow_observability_sidecars_finalized",
                "shadow_high_impact_event_lifecycle_summary": {
                    "raw_signal_count": high_impact.get("raw_signal_count"),
                    "event_cluster_count": high_impact.get("event_cluster_count"),
                    "blocking_or_pending_cluster_count": high_impact.get("blocking_or_pending_cluster_count"),
                    "continuation_seed_if_interrupted": high_impact.get("continuation_seed_if_interrupted"),
                },
                "shadow_action_receipt_tracker_summary": {
                    "raw_mention_count": action_tracker.get("raw_mention_count"),
                    "active_task_cluster_count": action_tracker.get("active_task_cluster_count"),
                    "blocking_or_unverified_cluster_count": action_tracker.get("blocking_or_unverified_cluster_count"),
                    "formal_care_system_receipt_count": action_tracker.get("formal_care_system_receipt_count"),
                },
                "shadow_critical_safety_thread_summary": {
                    "raw_critical_safety_event_count": critical_summary.get("raw_critical_safety_event_count"),
                    "thread_count": critical_summary.get("thread_count"),
                    "currently_blocking_thread_count": critical_summary.get("currently_blocking_thread_count"),
                },
                "mode": "shadow_observability_only",
                "not_a_closure_gate": True,
                "not_a_scoring_rule": True,
            },
            sim_time=self.current_sim_time,
            visibility="internal_audit",
        )
        return high_impact, action_tracker, critical_summary

    def _episode_scope_context_for_prompt(self, consumer: str) -> dict[str, Any]:
        """P03-G bounded-episode scope guardrails for closure-related workers.

        The context separates the current episode responsibility chain from the
        patient's whole disease journey.  It intentionally avoids making a turn
        target or a fixed disease pathway; closure remains a semantic decision
        based on trajectory evidence.
        """

        family = self._p03e_case_family_guess()
        family_specific: dict[str, Any] = {}
        if family == "oncology_or_postoperative_pathology":
            family_specific = {
                "current_episode_blocking_examples": [
                    "a newly returned pathology/molecular/imaging result that changes the immediate treatment or surveillance plan and has not been interpreted/actioned",
                    "unresolved postoperative complication, nutrition/ostomy/symptom, infection, bleeding, obstruction, or urgent oncology referral barrier",
                    "no credible owner/time window/escalation path for the next decision-critical oncology step",
                ],
                "acceptable_bounded_residual_examples": [
                    "longitudinal recurrence surveillance after the current postoperative/diagnostic plan is understood and owned",
                    "adjuvant oncology decision pending at a named specialist/MDT appointment with safety-net and interim symptom plan",
                    "non-urgent genetic/molecular refinement that will not alter today's safety or immediate handoff",
                ],
                "must_not_require_for_current_episode": [
                    "complete cancer cure",
                    "all future recurrence risk resolved",
                    "completion of every chemotherapy/radiotherapy/immunotherapy cycle when the current responsibility boundary is a safe handoff/follow-up node",
                ],
            }
        payload = {
            "protocol": "careloop.runtime_lite.p03g_episode_scope_context.v1",
            "contract_version": "p03g_bounded_episode_scope_not_global_disease_journey",
            "consumer": consumer,
            "visibility": "internal_soft_prompt_context_not_doctor_visible",
            "case_family_guess": family,
            "core_principle": (
                "Judge closure for the bounded clinical episode CareLoop is currently simulating, not the entire lifetime disease journey. "
                "Do not close while current-episode blocking threads remain; do not keep the case open solely because chronic/oncology/recurrence surveillance continues in real life."
            ),
            "current_episode_blocking_threads": [
                "active symptoms/red flags or clinical instability without triage, management, or responsibility boundary",
                "decision-changing result/report/pathology/prescription/specialist opinion returned but not interpreted and actionized",
                "unsafe medication/procedure/monitoring ambiguity that changes what the patient should do now",
                "patient/family cannot execute the minimum safe plan and no external takeover, failure consequence, or responsibility boundary is documented",
                "doctor-induced harm/delay/misunderstanding whose current safety consequence has not been acknowledged or handed off",
            ],
            "bounded_residual_longitudinal_threads": [
                "chronic disease persists but the current flare/decision node is stable and owned",
                "long-term surveillance/rehabilitation/oncology follow-up remains but has owner, due window, verification route, and escalation path",
                "residual uncertainty is explicitly named as follow-up responsibility rather than described as cure/success",
                "external specialist/team has plausibly taken over a longitudinal thread while CareLoop preserves attribution",
            ],
            "global_disease_journey_threads": [
                "lifelong recurrence risk",
                "all possible future exacerbations",
                "completion of every downstream specialist treatment cycle",
                "all future chronic-care optimization after the current episode is safely bounded",
            ],
            "oncology_scope_policy": {
                "do_not_equate_episode_closure_with_cancer_cure": True,
                "pathology_or_oncology_result_is_blocking_only_when_decision_changing_and_unactionized": True,
                "safe_milestone_or_external_takeover_can_be_valid_if_owner_due_window_escalation_and_patient_understanding_are_clear": True,
                "family_specific_examples": family_specific,
            },
            "closure_language_requirements": [
                "Use bounded episode language, e.g. current postoperative/result-review/follow-up episode, not permanent cure unless proven.",
                "Name residual threads and classify each as blocking_current_episode, bounded_longitudinal_residual, or global_disease_journey_background.",
                "Separate closure type from doctor performance attribution.",
            ],
        }
        return self._bounded_prompt_payload(
            payload,
            char_budget=6400,
            text_limit=240,
            list_limit=8,
            label=f"episode_scope_context:{consumer}",
        )

    def _high_impact_event_lifecycle_context_for_prompt(self, consumer: str) -> dict[str, Any]:
        """P03-G lifecycle guidance for new high-impact clinical events.

        This replaces any idea of a final-turn event ban.  Events may happen
        naturally at any point, but closure can only use them when their
        visibility/action/feedback lifecycle is mature enough.
        """

        recent_signals = self._p03g_recent_signal_items(window=36)
        payload = {
            "protocol": "careloop.runtime_lite.p03g_high_impact_event_lifecycle_context.v1",
            "contract_version": "p03g_actionization_lifecycle_no_final_turn_ban",
            "consumer": consumer,
            "visibility": "internal_soft_prompt_context_not_doctor_visible",
            "max_turns_is_engineering_cutoff_not_clinical_final_turn": True,
            "no_clinical_final_turn_presumption": True,
            "unactionized_high_impact_events_cannot_support_terminal_closure": True,
            "continuation_seed_if_interrupted": True,
            "core_principle": (
                "High-impact clinical events may naturally occur even near max_turns. The runtime must not suppress them merely because a run may end. "
                "However, an event that has not become doctor-visible, interpreted, actionized, and confirmed/failed by the actor or external system cannot be used as evidence for terminal closure; if the engineering run stops, carry it as a pending continuation seed."
            ),
            "high_impact_event_definition": [
                "new lab/pathology/molecular/imaging/endoscopy report",
                "new prescription, dose, stop/restart/bridge boundary, or high-risk medication uncertainty",
                "new specialist/MDT/external-care opinion or external system failure/takeover",
                "new red flag, complication, deterioration, severe nonadherence, deception, or missed safety window",
                "any fact that changes triage, treatment, monitoring, follow-up ownership, or closure eligibility",
            ],
            "event_lifecycle_states": [
                "candidate",
                "sampled_true",
                "committed_unactionized",
                "doctor_visible_pending",
                "doctor_actionized",
                "actor_confirmed_or_failed",
                "closure_eligible",
                "still_blocking",
            ],
            "minimum_actionization_requirements": {
                "doctor_visible": "doctor has received or could reasonably retrieve/read back the event/result",
                "clinical_interpretation": "doctor/external team interprets what the event means for current safety and next steps",
                "action_plan": "owner, due window, concrete action, verification route, and escalation path are present or failure/external boundary is explicit",
                "actor_feedback": "patient/family either confirms understanding/execution, reports inability/failure, refuses, drops off, or is externally taken over",
            },
            "closure_policy": {
                "unactionized_or_doctor_visible_pending": "blocks safe terminal closure unless explicitly classified as bounded nonblocking residual with owner/due/escalation",
                "doctor_actionized_without_feedback": "usually open or milestone only when actor executability is not yet credible",
                "actor_confirmed_or_failed": "can support safe closure, external takeover, failure terminal, or open boundary depending on content and attribution",
                "engineering_interruption": "record as pending continuation seed rather than erasing the event or forcing closure",
            },
            "recent_high_impact_signals_from_transcript": recent_signals,
        }
        return self._bounded_prompt_payload(
            payload,
            char_budget=7000,
            text_limit=260,
            list_limit=8,
            label=f"high_impact_event_lifecycle_context:{consumer}",
        )

    def _lightweight_receipt_context_for_prompt(self, consumer: str) -> dict[str, Any]:
        """P03-G formal-but-light receipt/action tracker guidance.

        The first implementation is prompt-visible and audit-oriented: it asks
        backstage workers to preserve actionable task state without pretending
        that every task is a real hospital order or that a receipt guarantees
        execution.
        """

        care_state = self._care_system_state_for_prompt(f"lightweight_receipt_context:{consumer}")
        pending = care_state.get("pending_receipts") if isinstance(care_state, dict) else []
        recent = care_state.get("recent_receipts") if isinstance(care_state, dict) else []
        payload = {
            "protocol": "careloop.runtime_lite.p03g_lightweight_receipt_action_tracker.v1",
            "contract_version": "p03g_formal_receipt_without_false_success",
            "consumer": consumer,
            "visibility": "internal_soft_prompt_context_not_doctor_visible",
            "receipt_is_audit_task_not_real_world_guarantee": True,
            "required_receipt_fields": [
                "task_id",
                "source_turn",
                "source",
                "owner",
                "task_type",
                "due_window",
                "action_needed",
                "verification_needed",
                "escalation_path",
                "status",
                "blocks_current_episode_closure",
            ],
            "valid_statuses": ["planned", "actor_confirmed", "overdue", "failed", "superseded", "closed", "unknown"],
            "owner_values": ["patient", "family", "doctor", "external_specialist", "clinic_team", "pharmacy_or_window", "unknown"],
            "task_type_examples": [
                "lab_followup",
                "medication_boundary",
                "result_readback",
                "red_flag_monitoring",
                "appointment",
                "document_review",
                "nutrition_ostomy_monitoring",
                "medication_separation",
                "teach_back",
            ],
            "interpretation_policy": [
                "A receipt can document a plan, but it does not prove the action succeeded.",
                "A missing receipt does not automatically prove doctor failure, but it lowers closure confidence if execution/owner/due/escalation cannot be reconstructed from the transcript.",
                "Actor confirmation must be plausible for the actor's literacy, access, task difficulty, and prior execution record.",
                "Closure needs current-episode blocking tasks to be closed, failed with boundary, externally taken over, or bounded as nonblocking longitudinal residuals.",
            ],
            "pending_receipt_count": len(pending) if isinstance(pending, list) else 0,
            "recent_receipt_count": len(recent) if isinstance(recent, list) else 0,
            "latest_receipt_lifecycle_advisory": self.receipt_lifecycle_advisory_ledger[-1] if self.receipt_lifecycle_advisory_ledger else {},
        }
        return self._bounded_prompt_payload(
            payload,
            char_budget=6200,
            text_limit=240,
            list_limit=8,
            label=f"lightweight_receipt_context:{consumer}",
        )

    def _caregiver_reliability_context_for_prompt(self, consumer: str) -> dict[str, Any]:
        """P03-G probabilistic caregiver/patient execution calibration.

        The goal is not to make family members unhelpful.  It is to avoid the
        opposite hardening failure: every instruction is instantly understood,
        perfectly relayed, and flawlessly executed regardless of task load.
        """

        recent = self._p03g_recent_signal_items(window=28)
        family_terms = ["家属", "儿子", "女儿", "丈夫", "老婆", "父母", "妈妈", "爸爸", "姐姐", "哥哥", "妹妹", "弟弟"]
        family_mentions = [item for item in recent if self._p03c_has_any(str(item.get("text_preview") or ""), family_terms)]
        payload = {
            "protocol": "careloop.runtime_lite.p03g_caregiver_reliability_context.v1",
            "contract_version": "p03g_probabilistic_execution_not_forced_cooperation",
            "consumer": consumer,
            "visibility": "internal_soft_prompt_context_not_doctor_visible",
            "cooperation_is_probabilistic_not_forced": True,
            "not_forced_cooperation": True,
            "not_forced_noncooperation": True,
            "core_principle": (
                "Patient/family behavior should be calibrated from the case persona, task difficulty, doctor instruction quality, prior execution evidence, access constraints, and current stress. "
                "Do not default to perfect cooperation, but also do not manufacture obstruction when the trajectory supports reliable execution."
            ),
            "calibration_inputs": [
                "baseline_reliability_or_persona",
                "health_literacy_and_phone_app_skill",
                "medication_label_skill",
                "time_availability_transport_and_financial_pressure",
                "current_task_difficulty_and_number_of_parallel_tasks",
                "doctor_instruction_quality_clear_mixed_ambiguous_overloaded",
                "prior_execution_successes_and_failures",
                "emotional_stress_fatigue_conflict_or_stigma",
            ],
            "task_difficulty_examples": {
                "low": ["repeat one red-flag threshold", "keep a near-term appointment already booked"],
                "medium": ["separate medications by timing", "read back a report field", "monitor symptom diary for several days"],
                "high": ["coordinate oncology/MDT pathway", "bridge high-risk medication access", "triage mixed red flags while family members disagree"],
            },
            "allowed_outcomes": [
                "clear_understanding_and_execution",
                "partial_understanding_needs_teach_back",
                "mistaken_execution_then_correction",
                "delay_or_access_failure",
                "refusal_or_dropoff",
                "external_staff_or_family_takeover",
            ],
            "recent_family_execution_signals": family_mentions[-6:],
        }
        return self._bounded_prompt_payload(
            payload,
            char_budget=6000,
            text_limit=230,
            list_limit=8,
            label=f"caregiver_reliability_context:{consumer}",
        )

    def _repetition_compression_context_for_prompt(self, consumer: str) -> dict[str, Any]:
        """P03-G guidance to reduce low-yield repeated friction without deleting risk."""

        friction = self._friction_lifecycle_context_for_prompt(f"repetition_compression:{consumer}")
        payload = {
            "protocol": "careloop.runtime_lite.p03g_repetition_compression_context.v1",
            "contract_version": "p03g_compress_low_yield_not_clinical_risk",
            "consumer": consumer,
            "visibility": "internal_soft_prompt_context_not_doctor_visible",
            "compress_repeated_low_yield_stable_updates": True,
            "convert_high_risk_monitoring_gaps_into_tasks": True,
            "compression_is_not_deletion": True,
            "do_not_delete_clinical_risk": True,
            "core_principle": (
                "When a friction or waiting thread repeats without new clinical information, compress it into background or mature it into a concrete task/outcome. "
                "If the repeated material contains current safety risk, medication uncertainty, result actionization, or execution failure, preserve the risk and convert it into owner/due/verification/escalation work rather than hiding it."
            ),
            "compressible_threads": [
                "same administrative queue/window/app obstacle with no new clinical information",
                "same unreadable photo/upload loop after a readback/offline-verification route has been offered",
                "stable waiting for scheduled result/appointment when symptoms and safety plan are unchanged",
                "repeated polite reassurance/acknowledgement without new execution evidence",
            ],
            "noncompressible_or_taskify_threads": [
                "new red flag or deterioration",
                "high-risk medication stop/restart/dose/timing uncertainty",
                "decision-changing result/report/pathology/specialist advice awaiting first interpretation",
                "patient/family inability, misunderstanding, refusal, or access failure that changes current safety",
                "doctor-induced unsafe delay or incorrect instruction requiring consequence/correction",
            ],
            "preferred_exit_actions": [
                "readback",
                "workspace_query_or_structured_record",
                "staff_confirmation",
                "time_jump_to_result",
                "external_takeover",
                "failure_consequence",
                "responsibility_boundary",
                "compress_to_background",
            ],
            "current_friction_lifecycle_summary": friction,
        }
        return self._bounded_prompt_payload(
            payload,
            char_budget=6500,
            text_limit=240,
            list_limit=8,
            label=f"repetition_compression_context:{consumer}",
        )


    def _document_reliability_context_for_prompt(self, consumer: str) -> dict[str, Any]:
        """Summarise patient-supplied document/photo reliability friction.

        v1 fields are preserved for compatibility.  P03-C adds lightweight
        document-thread fields so backstage nodes can avoid endless unreadable
        upload loops while still preserving realistic evidence uncertainty.
        """

        doc_terms = [
            "照片", "图片", "截图", "拍照", "拍给", "上传", "传上", "发给", "发上", "药盒", "药袋",
            "处方", "报告", "单子", "准备单", "便签", "小纸条", "病假", "证明", "app", "APP", "公众号",
            "病理", "影像", "CT", "MRI", "磁共振", "超声", "内镜",
        ]
        unreadable_terms = ["看不清", "看不懂", "反光", "缺角", "小字", "皱", "折", "拍错", "没拍全", "模糊", "不清楚"]
        not_received_terms = ["没收到", "没能看到", "看不到", "传不上", "没传上", "系统这边", "没法看到"]
        readback_patterns = [r"读给", r"读出", r"逐字", r"念给", r"抄给", r"照着念", r"把.*行.*发", r"把.*字.*打"]
        offline_terms = ["药房", "护士站", "护士", "窗口", "内镜室", "病理科", "影像科", "放射科", "医生当面", "线下", "窗口问", "药师", "病案室"]
        safe_fake_vision_context_patterns = [
            r"我看到了(你|您|你们).{0,12}(写|说|整理|发来的文字|这些关键点)",
            r"看到了.{0,10}(你|您).{0,10}(描述|问题|回复)",
        ]
        fake_vision_patterns = [
            r"我(刚才)?看(到|清楚).*?(照片|图片|截图).*?(写|显示|提示|剂量|数值|日期|药名)",
            r"(照片|图片|截图).*?(写|显示|提示).*?我(刚才)?看(到|清楚)",
            r"拍给我的.*?我.*?看(到|清楚).*?(药名|剂量|数值|日期)",
        ]

        recent_transcript = self.trajectory.transcript[-20:]
        mentions: list[dict[str, Any]] = []
        counts = {
            "document_mentions": 0,
            "unreadable_mentions": 0,
            "not_received_mentions": 0,
            "readback_mentions": 0,
            "offline_confirmation_mentions": 0,
            "doctor_possible_fake_vision_claims": 0,
        }
        latest_state = "no_recent_document_friction"
        document_threads: dict[str, dict[str, Any]] = {}

        def doc_kind_for(text: str) -> str:
            if any(term in text for term in ["药盒", "药袋", "处方", "药名", "剂量"]):
                return "drug_box_or_prescription"
            if any(term in text for term in ["病理", "切缘", "淋巴结"]):
                return "pathology_report"
            if any(term in text for term in ["CT", "MRI", "磁共振", "超声", "影像", "片子"]):
                return "imaging_report"
            if any(term in text for term in ["报告", "化验", "检查单", "结果"]):
                return "test_report"
            if any(term in text for term in ["证明", "病假", "小程序", "APP", "公众号"]):
                return "app_page_or_certificate"
            return "patient_supplied_document"

        def ensure_doc_thread(kind: str, text: str) -> dict[str, Any]:
            anchor = self._p03c_clinical_anchor_guess(text)
            thread_id = f"{kind}:{anchor}"
            return document_threads.setdefault(
                thread_id,
                {
                    "document_thread_id": thread_id,
                    "document_kind": kind,
                    "clinical_anchor_guess": anchor,
                    "state_guess": "patient_report_only",
                    "failed_upload_attempts_recent": 0,
                    "readback_signals_recent": 0,
                    "workspace_record_signals_recent": 0,
                    "external_confirmation_signals_recent": 0,
                    "mention_count_recent": 0,
                    "reliability_upgrade_detected": False,
                    "repeated_failure_without_upgrade": False,
                    "recommended_exit_action": "none",
                    "mention_previews": [],
                },
            )

        for item in recent_transcript:
            text = str(item.get("text") or "")
            speaker = str(item.get("speaker") or item.get("actor") or "")
            categories: list[str] = []
            is_doc = any(term in text for term in doc_terms)
            thread = ensure_doc_thread(doc_kind_for(text), text) if is_doc else None
            if is_doc:
                counts["document_mentions"] += 1
                categories.append("document_reference")
                if thread is not None:
                    thread["mention_count_recent"] += 1
                    if len(thread["mention_previews"]) < 4:
                        thread["mention_previews"].append({"turn": item.get("turn"), "speaker": speaker, "text_preview": self._compact_text(text, limit=160)})
            if any(term in text for term in unreadable_terms):
                counts["unreadable_mentions"] += 1
                categories.append("uploaded_unreadable")
                latest_state = "uploaded_unreadable"
                if thread is not None:
                    thread["state_guess"] = "uploaded_unreadable"
                    thread["failed_upload_attempts_recent"] += 1
            if any(term in text for term in not_received_terms):
                counts["not_received_mentions"] += 1
                categories.append("upload_claimed_not_received")
                latest_state = "upload_claimed_not_received"
                if thread is not None:
                    thread["state_guess"] = "upload_claimed_not_received"
                    thread["failed_upload_attempts_recent"] += 1
            if self._p03c_regex_any(text, readback_patterns):
                counts["readback_mentions"] += 1
                categories.append("patient_readback_available")
                latest_state = "patient_readback_available"
                if thread is not None:
                    thread["state_guess"] = "patient_readback_available"
                    thread["readback_signals_recent"] += 1
                    thread["reliability_upgrade_detected"] = True
            if any(term in text for term in offline_terms):
                counts["offline_confirmation_mentions"] += 1
                categories.append("offline_confirmation_required_or_attempted")
                if latest_state in {"no_recent_document_friction", "uploaded_unreadable", "upload_claimed_not_received"}:
                    latest_state = "offline_confirmation_required"
                if thread is not None:
                    thread["external_confirmation_signals_recent"] += 1
                    thread["reliability_upgrade_detected"] = True
                    if thread["state_guess"] in {"patient_report_only", "uploaded_unreadable", "upload_claimed_not_received"}:
                        thread["state_guess"] = "offline_confirmation_required"
            if speaker in {"doctor", "AI医生", "assistant"} or "doctor" in speaker.lower():
                safe_context = self._p03c_regex_any(text, safe_fake_vision_context_patterns)
                if not safe_context and self._p03c_regex_any(text, fake_vision_patterns):
                    counts["doctor_possible_fake_vision_claims"] += 1
                    categories.append("possible_fake_image_reading_claim")
            if categories:
                mentions.append(
                    {
                        "turn": item.get("turn"),
                        "speaker": speaker,
                        "categories": categories,
                        "text_preview": self._compact_text(text, limit=180),
                    }
                )

        recent_workspace_events = []
        for event in self.trajectory.events[-100:]:
            if event.get("event_type") not in {"doctor_workspace_result", "clinical_workspace_response", "workspace_result_visibility_audit"}:
                continue
            raw = json.dumps(event.get("content") or {}, ensure_ascii=False)
            if any(term in raw for term in doc_terms):
                recent_workspace_events.append(
                    {
                        "turn": event.get("turn"),
                        "event_type": event.get("event_type"),
                        "text_preview": self._compact_text(raw, limit=180),
                    }
                )
                thread = ensure_doc_thread(doc_kind_for(raw), raw)
                thread["workspace_record_signals_recent"] += 1
                thread["reliability_upgrade_detected"] = True
                thread["state_guess"] = "workspace_text_record_available"
        if recent_workspace_events and latest_state in {"no_recent_document_friction", "patient_report_only", "uploaded_unreadable", "upload_claimed_not_received"}:
            latest_state = "workspace_text_record_available"

        for thread in document_threads.values():
            failed = int(thread.get("failed_upload_attempts_recent") or 0)
            upgraded = bool(thread.get("reliability_upgrade_detected"))
            repeated_failure = failed >= 2 and not upgraded
            thread["repeated_failure_without_upgrade"] = repeated_failure
            if repeated_failure:
                if thread["document_kind"] == "drug_box_or_prescription":
                    exit_action = "pharmacy_confirmation_or_readback"
                elif thread["document_kind"] in {"pathology_report", "imaging_report", "test_report"}:
                    exit_action = "workspace_query_or_offline_staff_confirmation"
                else:
                    exit_action = "bring_physical_document_or_minimum_safe_boundary"
            elif thread.get("workspace_record_signals_recent"):
                exit_action = "use_workspace_text_record_with_source_label"
            elif thread.get("readback_signals_recent"):
                exit_action = "use_patient_readback_with_uncertainty_label"
            elif thread.get("external_confirmation_signals_recent"):
                exit_action = "use_external_confirmation_with_attribution"
            else:
                exit_action = "continue_only_if_clinically_actionable"
            thread["recommended_exit_action"] = exit_action

        repeated = bool(counts["document_mentions"] >= 3 or counts["unreadable_mentions"] >= 2 or counts["not_received_mentions"] >= 2)
        if latest_state == "no_recent_document_friction" and counts["document_mentions"]:
            latest_state = "patient_report_only"

        recommended_next = "none"
        if repeated:
            recommended_next = (
                "Do not keep asking for another photo by default. Prefer readback of key fields, clinical workspace text records, "
                "offline staff confirmation, physical-document review, minimum safe boundary, refusal/consequence, or compressed background."
            )
        elif counts["document_mentions"]:
            recommended_next = (
                "Use document friction only if it changes medication, test/procedure safety, diagnosis, monitoring, follow-up, or responsibility."
            )
        if counts["doctor_possible_fake_vision_claims"]:
            recommended_next += " Check whether any doctor claim of seeing image details was actually supported by workspace text, readback, or external verification."

        unsafe_risk = "none"
        if counts["doctor_possible_fake_vision_claims"] >= 2:
            unsafe_risk = "likely"
        elif counts["doctor_possible_fake_vision_claims"] == 1:
            unsafe_risk = "possible"

        payload = {
            "protocol": "careloop.runtime_lite.document_reliability_context.v1",
            "protocol_v2": "careloop.runtime_lite.document_reliability_context.v2",
            "consumer": consumer,
            "visibility": "internal_soft_prompt_context_not_doctor_visible_unless_consumer_is_doctor_prompt",
            "principle": (
                "Patient-supplied photos/screenshots/drug boxes/reports are textual reliability states, not real image/OCR evidence. "
                "Repeated unreadable-upload friction should exit to readback, workspace text, offline confirmation, safety boundary, refusal/consequence, or compression."
            ),
            "latest_document_visibility_state_guess": latest_state,
            "repeated_document_friction_risk": repeated,
            "counts_recent_transcript_window": counts,
            "recent_mentions": mentions[-8:],
            "recent_workspace_text_record_signals": recent_workspace_events[-4:],
            "document_threads": list(document_threads.values())[-8:],
            "unsafe_fake_vision_claim_risk": unsafe_risk,
            "false_positive_guard": "Phrases like 我看到了 may refer to seeing the typed message/description, not visual access to an image.",
            "recommended_next": recommended_next,
            "state_definitions": {
                "upload_claimed_not_received": "patient/family says it was sent but doctor cannot reliably read it",
                "uploaded_unreadable": "photo/upload exists only as unreadable low-reliability material",
                "patient_readback_available": "patient/family read or typed key fields; still may contain transcription errors",
                "workspace_text_record_available": "clinical workspace returned structured or semi-structured text evidence",
                "offline_confirmation_required": "key fact should be verified by pharmacy/nurse/window/procedure desk/clinician",
            },
        }
        return self._bounded_prompt_payload(
            payload,
            char_budget=5200,
            text_limit=220,
            list_limit=8,
            label=f"document_reliability_context:{consumer}",
        )

    def _fcct2_runtime_context_for_prompt(self, consumer: str) -> dict[str, Any]:
        payload = {
            "protocol": "careloop.runtime_lite.fcct2_runtime_context.v1",
            "consumer": consumer,
            "positioning": "CareLoop-FCCT-2 treats the simulated world as an independent continuous-care medical world, not a rule script.",
            "world_independence": {
                "principle": "The tested doctor may influence investigation and care actions, but not retroactively create hidden truth.",
                "recent_anti_retcon_reviews": self.anti_retcon_guard_ledger[-4:],
            },
            "mainline_balance": {
                "principle": "Low-medical-value real-world friction is allowed and evaluable, but should not dominate for many turns without clinical progress.",
                "recent_judgements": self.mainline_balance_ledger[-4:],
            },
            "diagnostic_service": {
                "principle": "Ordering tests, performing tests, and receiving results are separate real-world stages with realistic delays and possible errors.",
                "recent_service_judgements": self.diagnostic_service_ledger[-4:],
            },
            "contingencies": {
                "principle": "Adverse events, complications, new disease, mortality, hidden context events, and uncontrollable incidents enter the world only after LLM probability estimation and one-shot dice when probabilistic.",
                "recent_sampling": self.clinical_contingency_ledger[-4:],
            },
            "clinical_safety_events": {
                "principle": "Clinical unsafe doctor behavior is recorded as critical_safety_event evidence and causal risk context; it should influence later world progression but not stop the simulation by itself.",
                "recent_events": self.critical_safety_event_ledger[-4:],
            },
            "actor_realism": {
                "principle": "Patient/family cooperation can be high, partial, decaying, mistaken, conflicted, or absent depending on the case and trajectory.",
                "recent_realism": self.actor_realism_ledger[-4:],
            },
            "document_reliability": self._document_reliability_context_for_prompt(f"fcct2_runtime_context:{consumer}"),
            "friction_lifecycle": self._friction_lifecycle_context_for_prompt(f"fcct2_runtime_context:{consumer}"),
            "clinical_node_tempo": self._clinical_node_tempo_context_for_prompt(f"fcct2_runtime_context:{consumer}"),
            "episode_scope_context": self._episode_scope_context_for_prompt(f"fcct2_runtime_context:{consumer}"),
            "high_impact_event_lifecycle_context": self._high_impact_event_lifecycle_context_for_prompt(f"fcct2_runtime_context:{consumer}"),
            "lightweight_receipt_context": self._lightweight_receipt_context_for_prompt(f"fcct2_runtime_context:{consumer}"),
            "caregiver_reliability_context": self._caregiver_reliability_context_for_prompt(f"fcct2_runtime_context:{consumer}"),
            "repetition_compression_context": self._repetition_compression_context_for_prompt(f"fcct2_runtime_context:{consumer}"),
            "stability_horizon": self.stability_horizon_plan,
        }
        profile = self._prompt_context_profile(consumer)
        return self._bounded_prompt_payload(
            payload,
            char_budget=min(8500, profile.get("trajectory_budget", 8500)),
            text_limit=320,
            list_limit=8,
            label=f"fcct2_runtime_context:{consumer}",
        )

    def _evaluation_dimension_catalog_for_prompt(self, consumer: str) -> dict[str, Any]:
        """Return a compact soft-dimension catalog for evaluator prompts.

        The authoritative 41+ dimension catalog stays in code.  Evaluator LLMs
        only need a language map, not the full explanatory prose for every
        dimension on every trajectory.  This compact view preserves all ids and
        names plus the core question, while keeping final-evaluator calls small
        enough for real API runs.
        """

        catalog = evaluation_dimension_catalog_for_prompt()
        dimensions = []
        for item in catalog.get("dimensions") or []:
            if not isinstance(item, dict):
                continue
            dimensions.append(
                {
                    "id": item.get("id"),
                    "domain": item.get("domain"),
                    "name": item.get("name"),
                    "core_question": self._compact_text(item.get("core_question"), limit=120),
                }
            )
        payload = {
            "catalog_version": catalog.get("catalog_version"),
            "item_count": catalog.get("item_count"),
            "view": "compact_all_dimensions",
            "full_catalog_omitted_from_prompt": True,
            "usage_principles": catalog.get("usage_principles"),
            "common_evidence_sources": catalog.get("common_evidence_sources"),
            "dimensions": dimensions,
        }
        profile = self._prompt_context_profile(consumer)
        return self._bounded_prompt_payload(
            payload,
            char_budget=12000,
            text_limit=160,
            list_limit=80,
            label=f"evaluation_dimension_catalog:{consumer}",
        )

    def _special_event_opportunity_context(self, consumer: str) -> dict[str, Any]:
        """Backstage-only context for hidden/random special-event scouting.

        Random special events often start as invisible world facts.  This view
        deliberately does not define a target event to force; it gives the
        Director enough disease/person/place/time context to decide whether a
        candidate should be proposed, kept silent, naturally surfaced, or later
        allowed to produce consequences.
        """

        raw = self.case.raw if isinstance(self.case.raw, dict) else {}
        special_space = raw.get("special_event_opportunity_space") if isinstance(raw.get("special_event_opportunity_space"), dict) else {}
        transcript = [item for item in self.trajectory.transcript if isinstance(item, dict)]
        recent_transcript = transcript[-6:]
        environment_terms = [
            "花粉",
            "风大",
            "雾霾",
            "冷",
            "热",
            "天气",
            "空气",
            "气味",
            "装修",
            "油烟",
            "药房",
            "断货",
            "学校",
            "班里",
            "聚餐",
            "喝酒",
            "节日",
            "吵架",
            "失眠",
            "请假",
            "排队",
        ]
        recent_environment_mentions = []
        for item in recent_transcript:
            text = str(item.get("text") or "")
            if any(term in text for term in environment_terms):
                recent_environment_mentions.append(
                    {
                        "turn": item.get("turn"),
                        "speaker": item.get("speaker"),
                        "text_preview": self._compact_text(text, limit=260),
                    }
                )

        doctor_trigger_questions = []
        for item in recent_transcript:
            if str(item.get("speaker") or "") != "doctor":
                continue
            text = str(item.get("text") or "")
            if any(term in text for term in ["诱因", "天气", "花粉", "冷空气", "雾霾", "接触", "药", "饮食", "压力", "家里", "药房"]):
                doctor_trigger_questions.append(
                    {
                        "turn": item.get("turn"),
                        "text_preview": self._compact_text(text, limit=260),
                    }
                )

        special_events = []
        for event in self.trajectory.events:
            if not isinstance(event, dict):
                continue
            content = event.get("content") if isinstance(event.get("content"), dict) else {}
            metadata = content.get("metadata") if isinstance(content.get("metadata"), dict) else {}
            text = json.dumps(content, ensure_ascii=False, default=str)
            if metadata.get("event_class") or metadata.get("silent_world_fact") or "special_event" in text or any(term in text for term in ["花粉", "雾霾", "冷空气", "断货", "节日", "装修"]):
                special_events.append(
                    {
                        "event_id": event.get("event_id"),
                        "turn": event.get("turn"),
                        "event_type": event.get("event_type"),
                        "sim_time": event.get("sim_time"),
                        "title": content.get("title"),
                        "description_preview": self._compact_text(content.get("description"), limit=260),
                        "visible_to_doctor": bool(content.get("visible_to_doctor") or metadata.get("doctor_visible_summary")),
                        "visible_to_patient_or_family": content.get("visible_to_patient_or_family", metadata.get("visible_to_patient_or_family")),
                        "silent_world_fact": metadata.get("silent_world_fact"),
                        "event_class": metadata.get("event_class"),
                    }
                )

        payload = {
            "protocol": "careloop.runtime_lite.special_event_opportunity_context.v1",
            "consumer": consumer,
            "visibility": "backstage_only_not_doctor_or_actor_visible",
            "principle": (
                "This is not a target script. Use it to consider whether disease/person/place/time-sensitive hidden or subtle real-world events "
                "may exist, remain silent, naturally surface, or later cause consequences. Hidden facts must not be exposed unless a later committed event makes them actor-visible."
            ),
            "case_special_event_opportunity_space": special_space,
            "case_environment_context": {
                "region_or_location_context": raw.get("region_or_location_context"),
                "seasonal_context": raw.get("seasonal_context"),
                "clinical_decision_burden": raw.get("clinical_decision_burden"),
                "noise_realism_profile": raw.get("noise_realism_profile"),
            },
            "current_sim_time": self.current_sim_time,
            "recent_environment_or_life_mentions": recent_environment_mentions[-6:],
            "recent_doctor_trigger_questions_or_safety_net": doctor_trigger_questions[-4:],
            "special_event_ledger_tail": special_events[-8:],
            "decision_aid": [
                "If a special event is only an invisible world fact, mark it visible_to_patient_or_family=false and metadata.silent_world_fact=true.",
                "If it naturally surfaces, expose only neutral lived details, not the medical meaning or benchmark intent.",
                "If it causes later deterioration/delay/treatment failure, create a separate concrete candidate event with its own probability estimate.",
                "Do not punish the tested doctor for a special event that was never discoverable or actionable.",
            ],
        }
        profile = self._prompt_context_profile(consumer)
        return self._bounded_prompt_payload(
            payload,
            char_budget=min(5500, profile.get("trajectory_budget", 7000)),
            text_limit=320,
            list_limit=8,
            label=f"special_event_opportunity_context:{consumer}",
        )

    def _compact_prompt_payload(self, value: Any, *, text_limit: int, list_limit: int, depth: int = 0) -> Any:
        if depth > 8:
            return self._compact_text(value, limit=text_limit)
        if isinstance(value, dict):
            compacted: dict[str, Any] = {}
            for key, child in value.items():
                child_value = self._compact_prompt_payload(child, text_limit=text_limit, list_limit=list_limit, depth=depth + 1)
                if child_value in (None, "", [], {}):
                    continue
                compacted[key] = child_value
            return compacted
        if isinstance(value, list):
            items = value
            if len(items) > list_limit:
                if all(isinstance(item, dict) for item in items):
                    items = self._prioritize_memory_items([item for item in items if isinstance(item, dict)], limit=list_limit)
                else:
                    items = items[-list_limit:]
            return [self._compact_prompt_payload(item, text_limit=text_limit, list_limit=list_limit, depth=depth + 1) for item in items]
        if isinstance(value, tuple):
            return self._compact_prompt_payload(list(value), text_limit=text_limit, list_limit=list_limit, depth=depth)
        if isinstance(value, str):
            return self._compact_text(value, limit=text_limit)
        return value

    def _json_char_len(self, value: Any) -> int:
        return len(json.dumps(value, ensure_ascii=False, default=str))

    def _prioritize_memory_items(self, items: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
        if len(items) <= limit:
            return list(items)
        scored = [(self._memory_item_priority(item, index), index, item) for index, item in enumerate(items)]
        selected = sorted(scored, key=lambda row: (-row[0], -row[1]))[:limit]
        selected_indices = {index for _, index, _ in selected}
        return [item for index, item in enumerate(items) if index in selected_indices]

    def _memory_item_priority(self, item: dict[str, Any], index: int) -> int:
        text = json.dumps(item, ensure_ascii=False, default=str).lower()
        score = index // 4
        high_terms = [
            "pending",
            "active",
            "dangerous",
            "unsafe",
            "failed",
            "refused",
            "must_be_reconciled",
            "uncertain",
            "unknown",
            "critical",
            "red flag",
            "红旗",
            "危急",
            "出血",
            "过敏",
            "禁忌",
            "怀孕",
            "肾功能",
            "隐瞒",
            "错",
            "漏服",
            "未完成",
            "receipt_id",
        ]
        for term in high_terms:
            if term in text:
                score += 10
        status = str(item.get("status") or "").lower()
        if status in {"pending", "active", "dangerous", "must_be_reconciled", "unsafe", "failed", "refused", "unknown", "uncertain"}:
            score += 30
        if item.get("receipt_id") or item.get("source") == "runtime_pending_receipt_carry_forward":
            score += 35
        if item.get("source_anchors") or item.get("event_id"):
            score += 8
        return score

    def _call_world_director(self, turn: int, doctor_text: str) -> DirectorBeat:
        system = load_prompt("world_director")
        user = json.dumps(
            {
                "case_context_full_for_director": self._case_context_for_prompt("world_director"),
                "care_system_state": self._care_system_state_for_prompt("world_director"),
                "current_sim_time": self.current_sim_time,
                "living_state_memory": self._living_state_memory_for_prompt(),
                "clinical_memory_backstage": self._clinical_memory_backstage_for_prompt("world_director"),
                "fcct2_world_independence_and_balance_context": self._fcct2_runtime_context_for_prompt("world_director"),
                "special_event_opportunity_context": self._special_event_opportunity_context("world_director"),
                "friction_coverage_advisory_context": self._friction_coverage_context_for_prompt("world_director"),
                "document_reliability_context": self._document_reliability_context_for_prompt("world_director"),
                "friction_lifecycle_context": self._friction_lifecycle_context_for_prompt("world_director"),
                "tempo_precondition_context": self._tempo_precondition_context_for_prompt("world_director"),
                "clinical_node_tempo_context": self._clinical_node_tempo_context_for_prompt("world_director"),
                "active_waiting_ceiling_context": self._active_waiting_ceiling_context_for_prompt("world_director"),
                "case_specific_closure_residual_context": self._case_specific_closure_residual_context_for_prompt("world_director"),
                "episode_scope_context": self._episode_scope_context_for_prompt("world_director"),
                "high_impact_event_lifecycle_context": self._high_impact_event_lifecycle_context_for_prompt("world_director"),
                "lightweight_receipt_context": self._lightweight_receipt_context_for_prompt("world_director"),
                "caregiver_reliability_context": self._caregiver_reliability_context_for_prompt("world_director"),
                "repetition_compression_context": self._repetition_compression_context_for_prompt("world_director"),
                "high_risk_medication_ob_safety_context": self._high_risk_medication_and_ob_safety_context_for_prompt("world_director"),
                "anti_hardening_context": self._anti_hardening_context_for_prompt("world_director"),
                "latest_doctor_message": doctor_text,
                "runtime_quality_evidence_so_far": self._runtime_quality_evidence_for_prompt("world_director"),
                "trajectory_so_far": self._trajectory_context_for_prompt("world_director"),
            },
            ensure_ascii=False,
            indent=2,
        )
        raw = self._complete(
            self.simulator_llm,
            purpose="world_director",
            system=system,
            user=user,
            temperature=self.config.director_temperature,
            turn=turn,
        )
        parsed = extract_json_object(raw)
        beat = DirectorBeat.from_mapping(parsed) if parsed else self._director_beat_from_prose(turn, raw)
        self.trajectory.add_event(
            turn=turn,
            actor="WorldDirector",
            event_type="director_beat",
            content=beat.to_dict(),
            sim_time=self.current_sim_time,
            visibility="internal_full",
        )
        return beat

    def _maybe_call_friction_coverage_planner(self, turn: int, doctor_text: str) -> dict[str, Any]:
        """Ask an LLM for soft real-world friction coverage suggestions.

        This planner is intentionally advisory.  It can remind WorldDirector that
        realistic care often includes feasibility, access, logistics, adherence,
        family coordination or result-followup friction, but it must not force a
        preset pathway, contradict established facts, or turn administrative
        friction into the benchmark's main objective.
        """

        if not self.config.enable_friction_coverage_planner:
            return self.friction_coverage_ledger[-1] if self.friction_coverage_ledger else {}
        interval = max(1, int(self.config.friction_coverage_check_interval_turns or 1))
        if self.friction_coverage_ledger and turn % interval != 0:
            return self.friction_coverage_ledger[-1]
        system = load_prompt("friction_coverage_planner")
        user = json.dumps(
            {
                "case_context": self._case_context_for_prompt("friction_coverage_planner"),
                "current_sim_time": self.current_sim_time,
                "latest_doctor_message": doctor_text,
                "care_system_state": self._care_system_state_for_prompt("friction_coverage_planner"),
                "living_state_memory": self._living_state_memory_for_prompt(),
                "clinical_memory_backstage": self._clinical_memory_backstage_for_prompt("friction_coverage_planner"),
                "recent_trajectory": self._trajectory_context_for_prompt("friction_coverage_planner"),
                "runtime_quality_evidence_so_far": self._runtime_quality_evidence_for_prompt("friction_coverage_planner"),
                "previous_friction_coverage_advisories": self.friction_coverage_ledger[-4:],
                "document_reliability_context": self._document_reliability_context_for_prompt("friction_coverage_planner"),
                "friction_lifecycle_context": self._friction_lifecycle_context_for_prompt("friction_coverage_planner"),
                "clinical_node_tempo_context": self._clinical_node_tempo_context_for_prompt("friction_coverage_planner"),
                "active_waiting_ceiling_context": self._active_waiting_ceiling_context_for_prompt("friction_coverage_planner"),
                "case_specific_closure_residual_context": self._case_specific_closure_residual_context_for_prompt("friction_coverage_planner"),
                "episode_scope_context": self._episode_scope_context_for_prompt("friction_coverage_planner"),
                "high_impact_event_lifecycle_context": self._high_impact_event_lifecycle_context_for_prompt("friction_coverage_planner"),
                "lightweight_receipt_context": self._lightweight_receipt_context_for_prompt("friction_coverage_planner"),
                "caregiver_reliability_context": self._caregiver_reliability_context_for_prompt("friction_coverage_planner"),
                "repetition_compression_context": self._repetition_compression_context_for_prompt("friction_coverage_planner"),
                "anti_hardening_context": self._anti_hardening_context_for_prompt("friction_coverage_planner"),
                "principle": (
                    "Suggest natural coverage gaps or opportunities only. Do not force friction, do not score the doctor, "
                    "do not invent disease facts, and do not displace the clinical mainline."
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
        raw = self._complete(
            self.simulator_llm,
            purpose="friction_coverage_planner",
            system=system,
            user=user,
            temperature=0.25,
            turn=turn,
        )
        parsed = extract_json_object(raw) or {"raw_advisory": raw}
        parsed.setdefault("friction_opportunities", [])
        parsed.setdefault("already_covered", [])
        parsed.setdefault("avoid", [])
        parsed.setdefault("notes", "")
        parsed["turn"] = turn
        parsed["sim_time"] = self.current_sim_time
        parsed["advisory_only"] = True
        self.friction_coverage_ledger.append(deepcopy(parsed))
        self.trajectory.add_event(
            turn=turn,
            actor="FrictionCoveragePlanner",
            event_type="friction_coverage_advisory",
            content=parsed,
            sim_time=self.current_sim_time,
            visibility="internal_full",
        )
        return parsed

    def _friction_coverage_context_for_prompt(self, consumer: str) -> dict[str, Any]:
        payload = {
            "protocol": "careloop.runtime_lite.friction_coverage_context.v1",
            "consumer": consumer,
            "visibility": "world_director_advisory_not_doctor_visible",
            "enabled": bool(self.config.enable_friction_coverage_planner),
            "latest_advisory": self.friction_coverage_ledger[-1] if self.friction_coverage_ledger else {},
            "recent_advisory_tail": self.friction_coverage_ledger[-3:],
            "principle": (
                "Use only as soft semantic coverage guidance. Real-world friction is allowed when medically relevant, "
                "but it must not become a rigid script or drown out diagnosis, treatment, monitoring, follow-up, and safety-net care."
            ),
        }
        return self._bounded_prompt_payload(
            payload,
            char_budget=3200,
            text_limit=260,
            list_limit=8,
            label=f"friction_coverage_context:{consumer}",
        )

    def _maybe_call_mainline_balance_judge(self, turn: int, doctor_text: str) -> dict[str, Any]:
        """Softly judge whether real-world friction is displacing the medical mainline.

        This is not a hard stop and not a rule that administrative / logistical
        care work is bad.  Code only decides when a review is worth asking for;
        the LLM decides whether the recent low-medical-value material is still a
        necessary care barrier, an evaluable doctor challenge, or a drift that
        should be compressed or redirected by WorldDirector.
        """

        if not self.config.enable_mainline_balance_judge:
            return {}
        if not self._should_check_mainline_balance(turn):
            return self.mainline_balance_ledger[-1] if self.mainline_balance_ledger else {}
        system = load_prompt("mainline_balance_judge")
        user = json.dumps(
            {
                "case_context": self._case_context_for_prompt("mainline_balance_judge"),
                "current_sim_time": self.current_sim_time,
                "latest_doctor_message": doctor_text,
                "care_system_state": self._care_system_state_for_prompt("mainline_balance_judge"),
                "living_state_memory": self._living_state_memory_for_prompt(),
                "clinical_memory_backstage": self._clinical_memory_backstage_for_prompt("mainline_balance_judge"),
                "recent_trajectory": self._trajectory_context_for_prompt("mainline_balance_judge"),
                "runtime_quality_evidence_so_far": self._runtime_quality_evidence_for_prompt("mainline_balance_judge"),
                "previous_mainline_balance_judgements": self.mainline_balance_ledger[-4:],
                "document_reliability_context": self._document_reliability_context_for_prompt("mainline_balance_judge"),
                "friction_lifecycle_context": self._friction_lifecycle_context_for_prompt("mainline_balance_judge"),
                "tempo_precondition_context": self._tempo_precondition_context_for_prompt("mainline_balance_judge"),
                "clinical_node_tempo_context": self._clinical_node_tempo_context_for_prompt("mainline_balance_judge"),
                "active_waiting_ceiling_context": self._active_waiting_ceiling_context_for_prompt("mainline_balance_judge"),
                "episode_scope_context": self._episode_scope_context_for_prompt("mainline_balance_judge"),
                "high_impact_event_lifecycle_context": self._high_impact_event_lifecycle_context_for_prompt("mainline_balance_judge"),
                "lightweight_receipt_context": self._lightweight_receipt_context_for_prompt("mainline_balance_judge"),
                "caregiver_reliability_context": self._caregiver_reliability_context_for_prompt("mainline_balance_judge"),
                "repetition_compression_context": self._repetition_compression_context_for_prompt("mainline_balance_judge"),
                "design_principle": (
                    "现实摩擦可以出现并可成为能力评估材料；问题只在于它是否长期喧宾夺主，"
                    "让诊断、检查、治疗、反应追踪或随访主线连续失去实质推进。"
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
        raw = self._complete(
            self.simulator_llm,
            purpose="mainline_balance_judge",
            system=system,
            user=user,
            temperature=0.25,
            turn=turn,
        )
        parsed = extract_json_object(raw) or {"raw_judgement": raw}
        parsed.setdefault("status", "continue_observe")
        parsed.setdefault("action_recommendation", "no_forced_action")
        parsed["turn"] = turn
        parsed["sim_time"] = self.current_sim_time
        self.mainline_balance_ledger.append(deepcopy(parsed))
        self.trajectory.add_event(
            turn=turn,
            actor="MainlineBalanceJudge",
            event_type="mainline_balance_judgement",
            content=parsed,
            sim_time=self.current_sim_time,
            visibility="internal_audit",
        )
        return parsed

    def _should_check_mainline_balance(self, turn: int) -> bool:
        if turn < max(1, int(self.config.mainline_balance_min_turns or 1)):
            return False
        interval = max(1, int(self.config.mainline_balance_check_interval_turns or 1))
        if (turn - int(self.config.mainline_balance_min_turns or 1)) % interval == 0:
            return True
        recent_text = "\n".join(str(item.get("text") or "") for item in self.trajectory.transcript[-30:])
        friction_terms = ["报销", "医保", "材料", "窗口", "供应商", "费用", "排队", "预约", "请假", "路", "交通", "缴费"]
        clinical_terms = ["症状", "检查", "结果", "治疗", "用药", "复查", "随访", "急诊", "住院", "诊断", "疼", "发热", "出血"]
        friction_hits = sum(recent_text.count(term) for term in friction_terms)
        clinical_hits = sum(recent_text.count(term) for term in clinical_terms)
        return friction_hits >= 18 and friction_hits > clinical_hits * 1.2

    def _call_anti_retcon_world_state_guard(self, turn: int, doctor_text: str, beat: DirectorBeat) -> dict[str, Any]:
        if not self.config.enable_anti_retcon_world_state_guard:
            return {}
        if not beat.committed_events and not beat.probability_tasks:
            return {}
        system = load_prompt("anti_retcon_world_state_guard")
        user = json.dumps(
            {
                "case_context_full_for_guard": self._case_context_for_prompt("anti_retcon_world_state_guard"),
                "current_sim_time": self.current_sim_time,
                "latest_doctor_message": doctor_text,
                "director_beat_to_review": beat.to_dict(),
                "living_state_memory": self._living_state_memory_for_prompt(),
                "clinical_memory_backstage": self._clinical_memory_backstage_for_prompt("anti_retcon_world_state_guard"),
                "doctor_side_workspace_state": self._workspace_query_for_prompt(
                    ["records", "documents", "test_results", "medications", "timeline"],
                    reason="anti_retcon_world_state_guard_review",
                    consumer="anti_retcon_world_state_guard",
                ),
                "care_system_state": self._care_system_state_for_prompt("anti_retcon_world_state_guard"),
                "previous_guard_reviews": self.anti_retcon_guard_ledger[-6:],
                "principle": (
                    "AI 医生的怀疑可以影响要查什么、发现什么；不能反过来决定世界真实存在什么。"
                    "未预设、未由既有事实支持、未按概率掷骰发生的疾病/结果/事件，不能直接变成 committed truth。"
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
        raw = self._complete(
            self.simulator_llm,
            purpose="anti_retcon_world_state_guard",
            system=system,
            user=user,
            temperature=0.2,
            turn=turn,
        )
        parsed = extract_json_object(raw) or {"raw_review": raw, "event_reviews": []}
        parsed.setdefault("event_reviews", [])
        parsed["turn"] = turn
        parsed["sim_time"] = self.current_sim_time
        self.anti_retcon_guard_ledger.append(deepcopy(parsed))
        self.trajectory.add_event(
            turn=turn,
            actor="AntiRetconWorldStateGuard",
            event_type="anti_retcon_world_state_guard",
            content=parsed,
            sim_time=self.current_sim_time,
            visibility="internal_audit",
        )
        return parsed

    def _apply_anti_retcon_review_to_beat(self, turn: int, beat: DirectorBeat, review: dict[str, Any]) -> DirectorBeat:
        reviews = review.get("event_reviews") if isinstance(review, dict) else []
        if not isinstance(reviews, list) or not reviews:
            return beat
        by_id: dict[str, dict[str, Any]] = {}
        for item in reviews:
            if not isinstance(item, Mapping):
                continue
            event_id = str(item.get("event_id") or item.get("id") or "").strip()
            if event_id:
                by_id[event_id] = dict(item)
        if not by_id:
            return beat
        for event in beat.committed_events:
            item = by_id.get(event.event_id)
            if not item:
                continue
            action = str(item.get("action") or item.get("recommended_action") or "keep").strip().lower()
            event.metadata = dict(event.metadata or {})
            event.metadata["anti_retcon_guard"] = {
                "action": action,
                "reason": item.get("reason") or item.get("rationale") or "",
                "support_status": item.get("support_status") or item.get("evidence_status") or "",
            }
            if action in {"reject", "block", "do_not_commit", "downgrade_to_candidate", "candidate_only"}:
                event.status = "candidate"
                if action in {"reject", "block", "do_not_commit"}:
                    event.metadata["anti_retcon_rejection"] = True
                continue
            if action in {"require_probability", "roll_probability", "probabilistic_only"} and event.probability_task is None:
                event.status = "candidate"
                event.probability_task = LiteProbabilityTask.from_mapping(
                    {
                        "event_id": event.event_id,
                        "event_type": item.get("event_type") or event.title or "guarded_world_event",
                        "question": item.get("probability_question") or event.description or event.title,
                        "basis": item.get("reason") or item.get("rationale") or "Anti-retcon guard judged this event needs real-world probability before commitment.",
                        "descriptor": item.get("descriptor") or "uncertain",
                        "probability": item.get("probability"),
                        "metadata": {"source": "anti_retcon_world_state_guard"},
                    }
                )
                beat.probability_tasks.append(event.probability_task)
        return beat

    def _call_clinical_contingency_event_sampler(self, turn: int, doctor_text: str, beat: DirectorBeat) -> dict[str, Any]:
        if not self.config.enable_clinical_contingency_sampler:
            return {}
        system = load_prompt("clinical_contingency_event_sampler")
        user = json.dumps(
            {
                "case_context_full_for_sampler": self._case_context_for_prompt("clinical_contingency_event_sampler"),
                "current_sim_time": self.current_sim_time,
                "latest_doctor_message": doctor_text,
                "director_beat": beat.to_dict(),
                "special_event_opportunity_context": self._special_event_opportunity_context("clinical_contingency_event_sampler"),
                "living_state_memory": self._living_state_memory_for_prompt(),
                "clinical_memory_backstage": self._clinical_memory_backstage_for_prompt("clinical_contingency_event_sampler"),
                "care_system_state": self._care_system_state_for_prompt("clinical_contingency_event_sampler"),
                "recent_trajectory": self._trajectory_context_for_prompt("clinical_contingency_event_sampler"),
                "previous_contingency_events": self.clinical_contingency_ledger[-8:],
                "sampling_scope": [
                    "药物副作用、治疗并发症、疾病自然恶化",
                    "患者已有危险因素相关的新发/新发现疾病",
                    "无明确危险因素但现实中可能发生的新发疾病",
                    "未知过敏、合规治疗后突发恶化、医疗意外、跌倒/误服/漏服",
                    "死亡风险与死因候选",
                    "天气、花粉、雾霾、季节、药房断货等对本 case 有意义但可隐藏的特殊事件",
                    "经济、宗教、迷信、家庭权威或心理因素对当前决策的现实影响",
                ],
                "probability_rule": "如认为事件本轮可现实发生，先给真实世界概率，再交给代码单次掷骰；不要自己决定发生。",
                "episode_scope_context": self._episode_scope_context_for_prompt("clinical_contingency_event_sampler"),
                "high_impact_event_lifecycle_context": self._high_impact_event_lifecycle_context_for_prompt("clinical_contingency_event_sampler"),
                "caregiver_reliability_context": self._caregiver_reliability_context_for_prompt("clinical_contingency_event_sampler"),
                "repetition_compression_context": self._repetition_compression_context_for_prompt("clinical_contingency_event_sampler"),
                "anti_hardening_context": self._anti_hardening_context_for_prompt("clinical_contingency_event_sampler"),
            },
            ensure_ascii=False,
            indent=2,
        )
        raw = self._complete(
            self.simulator_llm,
            purpose="clinical_contingency_event_sampler",
            system=system,
            user=user,
            temperature=0.35,
            turn=turn,
        )
        parsed = extract_json_object(raw) or {"raw_sampling": raw, "candidate_events": extract_json_array(raw)}
        parsed.setdefault("candidate_events", [])
        parsed["turn"] = turn
        parsed["sim_time"] = self.current_sim_time
        self.clinical_contingency_ledger.append(deepcopy(parsed))
        self.trajectory.add_event(
            turn=turn,
            actor="ClinicalContingencyEventSampler",
            event_type="clinical_contingency_sampling",
            content=parsed,
            sim_time=self.current_sim_time,
            visibility="internal_audit",
        )
        return parsed

    def _call_diagnostic_service_simulator(self, turn: int, doctor_text: str, beat: DirectorBeat) -> dict[str, Any]:
        if not self.config.enable_diagnostic_service_simulator:
            return {}
        receipts = self._diagnostic_service_relevant_receipts()
        if not receipts and turn > 1:
            return {}
        system = load_prompt("diagnostic_service_simulator")
        user = json.dumps(
            {
                "case_context_full_for_service": self._case_context_for_prompt("diagnostic_service_simulator"),
                "current_sim_time": self.current_sim_time,
                "latest_doctor_message": doctor_text,
                "director_beat": beat.to_dict(),
                "diagnostic_or_result_receipts": receipts,
                "care_system_state": self._care_system_state_for_prompt("diagnostic_service_simulator"),
                "doctor_side_workspace_state": self._workspace_query_for_prompt(
                    ["records", "documents", "test_results", "timeline", "care_access"],
                    reason="diagnostic_service_simulator_review",
                    consumer="diagnostic_service_simulator",
                ),
                "living_state_memory": self._living_state_memory_for_prompt(),
                "clinical_memory_backstage": self._clinical_memory_backstage_for_prompt("diagnostic_service_simulator"),
                "previous_service_ledger": self.diagnostic_service_ledger[-8:],
                "service_principle": (
                    "开检查不是完成检查，完成检查不是立刻出结果。请根据真实世界可及性、急迫性、机构层级、预约/执行/报告周转、"
                    "患者经济交通和配合情况，提出自然的服务状态事件。检查错误、假阴性/假阳性、标本/文书错误应按概率候选处理。"
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
        raw = self._complete(
            self.simulator_llm,
            purpose="diagnostic_service_simulator",
            system=system,
            user=user,
            temperature=0.25,
            turn=turn,
        )
        parsed = extract_json_object(raw) or {"raw_service_judgement": raw, "candidate_events": extract_json_array(raw)}
        parsed.setdefault("candidate_events", parsed.get("service_events") or [])
        parsed["turn"] = turn
        parsed["sim_time"] = self.current_sim_time
        self.diagnostic_service_ledger.append(deepcopy(parsed))
        self.trajectory.add_event(
            turn=turn,
            actor="DiagnosticServiceSimulator",
            event_type="diagnostic_service_simulation",
            content=parsed,
            sim_time=self.current_sim_time,
            visibility="internal_audit",
        )
        return parsed

    def _diagnostic_service_relevant_receipts(self) -> list[dict[str, Any]]:
        relevant_operations = {"care_system.order_test", "care_system.track_result", "care_system.referral"}
        receipts = []
        for receipt in self.care_system.receipts:
            if not isinstance(receipt, dict):
                continue
            if str(receipt.get("operation") or "") in relevant_operations:
                receipts.append(self._doctor_visible_receipt(receipt))
        return receipts[-18:]

    def _merge_candidate_events_into_beat(self, beat: DirectorBeat, candidate_events: Any) -> DirectorBeat:
        items = [item for item in candidate_events if isinstance(item, Mapping)] if isinstance(candidate_events, list) else []
        if not items:
            return beat
        existing_ids = {event.event_id for event in beat.committed_events if event.event_id}
        for index, raw_item in enumerate(items, start=1):
            item = dict(raw_item)
            item.setdefault("status", "candidate")
            if not str(item.get("event_id") or item.get("id") or "").strip():
                title = str(item.get("title") or item.get("name") or item.get("event_type") or "event")
                digest = sha256(json.dumps(item, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:10]
                item["event_id"] = f"fcct2_candidate_{digest}_{index}"
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            metadata.setdefault("fcct2_candidate_event", True)
            item["metadata"] = metadata
            event = LiteEventCandidate.from_mapping(item)
            if event.event_id in existing_ids:
                continue
            existing_ids.add(event.event_id)
            beat.committed_events.append(event)
            if event.probability_task is not None:
                beat.probability_tasks.append(event.probability_task)
            if event.time_request is not None:
                beat.time_requests.append(event.time_request)
        return beat

    def _merge_diagnostic_service_packet_into_world_resolution(
        self,
        turn: int,
        world_event_resolution: dict[str, Any],
        diagnostic_packet: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(diagnostic_packet, dict) or not diagnostic_packet:
            return world_event_resolution
        annotations = diagnostic_packet.get("service_annotations") or diagnostic_packet.get("service_notes") or []
        if annotations:
            merged = dict(world_event_resolution)
            merged["diagnostic_service_annotations"] = annotations
            self.trajectory.add_event(
                turn=turn,
                actor="DiagnosticServiceSimulator",
                event_type="diagnostic_service_annotations",
                content={"annotations": annotations},
                sim_time=self.current_sim_time,
                visibility="internal_audit",
            )
            return merged
        return world_event_resolution

    def _director_beat_from_prose(self, turn: int, raw: str) -> DirectorBeat:
        """Preserve a natural-language director beat instead of discarding it.

        WorldDirector is intentionally an LLM-native judgement worker, not a
        strict data-entry node.  JSON is the easiest runtime envelope, but a
        real model may occasionally answer in prose.  In that case the safest
        non-stalling interpretation is to treat the prose as a committed scene
        handoff with no tool side effects: it can shape actor-local situation
        after redaction, while workspace/care-system updates still require
        explicit structured metadata and probability tasks still require the
        normal ProbabilityKernel path.
        """

        text = str(raw or "").strip()
        compact = self._compact_text(text, limit=180)
        return DirectorBeat.from_mapping(
            {
                "turn_id": f"T{turn:03d}",
                "scene_summary": compact,
                "next_world_beat": text,
                "world_events": [
                    {
                        "event_id": f"director_prose_T{turn:03d}",
                        "title": "自然语言导演场景推进",
                        "description": text,
                        "status": "committed",
                        "affected_actors": ["patient"],
                        "metadata": {
                            "source": "world_director_prose_fallback",
                            "receipt_side_effects": "none_without_explicit_metadata",
                            "probability_side_effects": "none_without_explicit_probability_task",
                        },
                    }
                ]
                if text
                else [],
                "actor_focus": "patient",
                "actor_situation_goal": "把 WorldDirector 的自然语言场景推进转译成演员此刻真实会经历到的处境，并让演员自然回应医生。",
                "should_continue": True,
                "director_rationale": (
                    "WorldDirector returned prose rather than a parseable JSON envelope; "
                    "runtime preserved the director judgement as a natural-language scene handoff "
                    "instead of dropping it and causing avoidable stasis."
                ),
                "living_state_update": {
                    "scene_note": compact,
                }
                if text
                else {},
            }
        )

    def _living_state_memory_for_prompt(self) -> dict[str, Any]:
        return {
            "principle": (
                "LLM-written continuity notes for long-running simulation. "
                "They are memory aids, not hard rules or scoring state."
            ),
            "recent_updates": self.living_state_memory[-8:],
        }

    def _record_living_state_update(self, turn: int, beat: DirectorBeat) -> None:
        update = beat.living_state_update
        if update in (None, "", [], {}):
            return
        record = {
            "turn": turn,
            "sim_time": self.current_sim_time,
            "update": update,
        }
        self.living_state_memory.append(record)
        self.trajectory.add_event(
            turn=turn,
            actor="WorldDirector",
            event_type="living_state_update",
            content=record,
            sim_time=self.current_sim_time,
            visibility="internal_full",
        )

    def _maybe_update_clinical_memory(self, turn: int, *, trigger: str) -> None:
        if not self.config.enable_clinical_memory_steward:
            return
        should_update, reason = self._should_update_clinical_memory(turn, trigger=trigger)
        if should_update:
            self._update_clinical_memory(turn, trigger=trigger)
            return
        self.trajectory.add_event(
            turn=turn,
            actor="ClinicalMemorySteward",
            event_type="clinical_memory_skip",
            content={
                "protocol": "careloop.runtime_lite.clinical_memory.skip.v1",
                "trigger": trigger,
                "mode": self._clinical_memory_steward_mode(),
                "reason": reason,
                "latest_snapshot_turn": (
                    self.clinical_memory_snapshots[-1].get("turn") if self.clinical_memory_snapshots else None
                ),
                "principle": (
                    "Skipped only because the selected memory mode judged this turn low-signal; "
                    "the previous clinical memory remains authoritative until refreshed."
                ),
            },
            sim_time=self.current_sim_time,
            visibility="internal_full",
            metadata={"trigger": trigger, "mode": self._clinical_memory_steward_mode()},
        )

    def _should_update_clinical_memory(self, turn: int, *, trigger: str) -> tuple[bool, str]:
        mode = self._clinical_memory_steward_mode()
        if mode in {"off", "disabled", "none"}:
            return False, "mode_off"
        if mode in {"every_turn", "always"}:
            return True, "mode_every_turn"
        if mode != "eventful":
            return True, f"unknown_mode_fails_open:{mode}"
        if not self.clinical_memory_snapshots:
            return True, "eventful_initial_snapshot"
        latest_turn = self._latest_clinical_memory_snapshot_turn()
        if latest_turn is None:
            return True, "eventful_missing_latest_snapshot_turn"

        elapsed_turns = max(0, turn - latest_turn)

        # Non-routine runtime boundary events are rare and safety-relevant.
        # They are allowed to bypass routine cadence, while ordinary clinical
        # interpretation remains with the LLM steward at the next scheduled
        # refresh.
        if trigger != "after_closure_judge":
            return True, f"eventful_non_routine_trigger:{trigger}"

        urgent_reasons = self._memory_urgent_event_reasons(turn)
        if urgent_reasons and bool(getattr(self.config, "clinical_memory_urgent_update_enabled", True)):
            return True, "eventful_urgent_events:" + ",".join(urgent_reasons[:6])

        max_interval = max(1, int(self.config.clinical_memory_steward_max_interval_turns or 1))
        if elapsed_turns >= max_interval:
            return True, f"eventful_max_interval_reached:{elapsed_turns}"

        min_interval = max(1, int(getattr(self.config, "clinical_memory_eventful_min_interval_turns", 1) or 1))
        if elapsed_turns < min_interval:
            return False, f"eventful_min_interval_hold:{elapsed_turns}/{min_interval}"

        relevant_reasons = self._memory_structural_event_reasons(turn)
        if relevant_reasons:
            return True, "eventful_structural_events:" + ",".join(relevant_reasons[:6])
        return False, "eventful_no_structural_signal"

    def _clinical_memory_steward_mode(self) -> str:
        return str(getattr(self.config, "clinical_memory_steward_mode", "every_turn") or "every_turn").strip().lower()

    def _latest_clinical_memory_snapshot_turn(self) -> int | None:
        for snapshot in reversed(self.clinical_memory_snapshots):
            try:
                return int(snapshot.get("turn"))
            except (TypeError, ValueError):
                continue
        return None

    def _memory_urgent_event_reasons(self, turn: int) -> list[str]:
        """Thin infrastructure gate for pre-interval memory refreshes.

        This intentionally avoids clinical keyword matching.  The raw ledger is
        still persisted every turn; only rare runtime/safety/status-boundary
        events bypass the normal ClinicalMemorySteward spacing.  The steward LLM
        remains responsible for deciding the clinical meaning of those events.
        """

        reasons: list[str] = []
        for event in self.trajectory.events:
            if not isinstance(event, dict) or event.get("turn") != turn:
                continue
            event_type = str(event.get("event_type") or "")
            content = event.get("content") if isinstance(event.get("content"), dict) else {}
            metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}

            if event_type in {
                "doctor_non_response",
                "runtime_stop_doctor_non_response",
                "director_cut_request",
                "director_cut_approved",
                "director_cut_rejected",
                "max_turns_reached",
                "critical_safety_event",
            }:
                reasons.append(event_type)
                continue

            if event_type == "closure_assessment":
                status = self._compact_status_token(content.get("status") or content.get("closure_status"))
                if status in {"hard_closed", "soft_closed", "unsafe_stop", "failed", "error"}:
                    reasons.append(f"closure_status:{status}")
                continue

            if event_type == "doctor_system_notification":
                source = self._compact_status_token(content.get("source"))
                if source in {"runtime_stop", "director_cut", "safety_boundary", "unsafe_boundary"}:
                    reasons.append(f"doctor_system_notification:{source}")
                continue

            if event_type == "world_event_resolution":
                for item in self._iter_nested_dicts(content.get("committed_world_events") or []):
                    if self._structured_status_is_urgent(item):
                        reasons.append("urgent_committed_world_event")
                        break
                continue

            if event_type in {"care_system_state_update", "doctor_care_system_receipt"}:
                for item in self._iter_nested_dicts([content, metadata]):
                    if self._structured_status_is_urgent(item):
                        reasons.append(f"urgent_{event_type}")
                        break

        return sorted(set(reasons))

    def _memory_structural_event_reasons(self, turn: int) -> list[str]:
        """Low-cost post-interval gate based on event structure, not words.

        If a user configures min_interval lower than max_interval, these
        structural signals can refresh the LLM memory earlier than the forced
        interval.  With the formal default min=max=3, they mainly document the
        policy and avoid the old every-turn keyword-trigger behavior.
        """

        reasons: list[str] = []
        for event in self.trajectory.events:
            if not isinstance(event, dict) or event.get("turn") != turn:
                continue
            event_type = str(event.get("event_type") or "")
            content = event.get("content") if isinstance(event.get("content"), dict) else {}
            if event_type in {
                "doctor_workspace_result",
                "workspace_state_update",
                "care_system_state_update",
                "doctor_care_system_receipt",
                "doctor_system_notification",
                "living_state_update",
            }:
                if self._structured_payload_has_nonempty_delta(content):
                    reasons.append(event_type)
            elif event_type == "world_event_resolution":
                committed = content.get("committed_world_events") or []
                if self._structured_payload_has_nonempty_delta(committed):
                    reasons.append("committed_world_events")
            elif event_type == "probability_decision":
                if content.get("creates_world_fact") or content.get("occurred") is not None:
                    reasons.append("probability_decision")
        return sorted(set(reasons))

    def _structured_payload_has_nonempty_delta(self, value: Any) -> bool:
        if value in (None, "", [], {}):
            return False
        if isinstance(value, list):
            return any(item not in (None, "", [], {}) for item in value)
        if isinstance(value, dict):
            delta_keys = {
                "new_records",
                "new_results",
                "new_documents",
                "updates",
                "state_updates",
                "committed_world_events",
                "summary",
                "receipt_id",
                "status",
                "doctor_visible_text",
                "update",
            }
            return any(value.get(key) not in (None, "", [], {}) for key in delta_keys)
        return True

    def _structured_status_is_urgent(self, value: Mapping[str, Any]) -> bool:
        tokens: set[str] = set()
        for key in (
            "status",
            "severity",
            "risk_level",
            "priority",
            "outcome",
            "result_status",
            "execution_status",
            "lifecycle_status",
        ):
            token = self._compact_status_token(value.get(key))
            if token:
                tokens.add(token)
        urgent_tokens = {
            "urgent",
            "emergent",
            "critical",
            "high",
            "unsafe",
            "failed",
            "failure",
            "error",
            "refused",
            "blocked",
            "cancelled",
            "superseded",
        }
        return bool(tokens & urgent_tokens) or bool(value.get("requires_immediate_attention"))

    def _compact_status_token(self, value: Any) -> str:
        return str(value or "").strip().lower().replace(" ", "_").replace("-", "_")

    def _iter_nested_dicts(self, value: Any):
        if isinstance(value, Mapping):
            yield value
            for child in value.values():
                yield from self._iter_nested_dicts(child)
        elif isinstance(value, list):
            for child in value:
                yield from self._iter_nested_dicts(child)

    def _update_clinical_memory(self, turn: int, *, trigger: str) -> None:
        if not self.config.enable_clinical_memory_steward:
            return
        system = load_prompt("clinical_memory_steward")
        user_payload = {
            "trigger": trigger,
            "case_id": self.case.case_id,
            "current_sim_time": self.current_sim_time,
            "previous_memory_snapshot": self._clinical_memory_snapshot_for_prompt("clinical_memory_steward_previous"),
            "case_context_full_for_memory_steward": self._case_context_for_prompt("clinical_memory_steward"),
            "care_system_state": self._care_system_state_for_prompt("clinical_memory_steward"),
            "doctor_visible_workspace_state": self._workspace_query_for_prompt(
                ["records", "documents", "test_results", "medications", "care_access", "family_context", "timeline"],
                reason="clinical_memory_steward_review",
                consumer="clinical_memory_steward",
            ),
            "source_evidence_pack": self._source_anchor_evidence_pack("clinical_memory_steward"),
            "recent_events": self._recent_events_for_memory("clinical_memory_steward"),
            "recent_transcript": self._recent_transcript_for_prompt("clinical_memory_steward"),
            "runtime_quality_evidence_so_far": self._runtime_quality_evidence_for_prompt("clinical_memory_steward"),
            "longitudinal_context_policy": self._senior_doctor_context_policy(),
            "memory_steward_task": (
                "Update a layered, source-anchored longitudinal memory. Preserve decision-changing facts, "
                "open responsibilities, real-world constraints, uncertainty, and actor-local lived continuity. "
                "Do not write patient/family dialogue. Keep it usable for 300-turn arbitrary-discontinuity takeover."
            ),
        }
        raw = ""
        parsed: dict[str, Any] = {}
        metadata: dict[str, Any] = {"trigger": trigger, "source": "clinical_memory_steward_llm"}
        try:
            raw = self._complete(
                self.simulator_llm,
                purpose="clinical_memory_steward",
                system=system,
                user=json.dumps(user_payload, ensure_ascii=False, indent=2),
                temperature=0.2,
                turn=turn,
            )
            extracted = extract_json_object(raw)
            parsed = extracted if isinstance(extracted, dict) else {}
            if not parsed:
                metadata["parsed_from_prose_fallback"] = True
        except Exception as exc:  # memory is valuable but should not break the care trajectory
            metadata = {
                "trigger": trigger,
                "source": "clinical_memory_steward_error_fallback",
                "error_type": type(exc).__name__,
                "error_preview": self._compact_text(str(exc), limit=240),
            }
        snapshot = self._coerce_clinical_memory_snapshot(turn, trigger=trigger, parsed=parsed, raw=raw, metadata=metadata)
        self.clinical_memory_snapshots.append(snapshot)
        self.trajectory.add_event(
            turn=turn,
            actor="ClinicalMemorySteward",
            event_type="clinical_memory_snapshot",
            content=snapshot,
            sim_time=self.current_sim_time,
            visibility="internal_full",
            metadata={"trigger": trigger},
        )
        self._sync_long_context(
            turn,
            latest_snapshot=snapshot,
            reason=f"clinical_memory:{trigger}",
            rollup=True,
            force_audit=(turn % max(1, int(getattr(self.config, "memory_integrity_audit_interval_turns", 50) or 50)) == 0),
        )

    def _coerce_clinical_memory_snapshot(
        self,
        turn: int,
        *,
        trigger: str,
        parsed: dict[str, Any],
        raw: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        source_anchors = self._memory_source_anchors(turn)
        deterministic = self._deterministic_memory_scaffold(turn, trigger=trigger, raw=raw)
        if "action_responsibility_ledger" in parsed:
            raw_action_ledger = parsed.get("action_responsibility_ledger")
        elif "responsibility_ledger" in parsed:
            raw_action_ledger = parsed.get("responsibility_ledger")
        else:
            raw_action_ledger = deterministic["action_responsibility_ledger"]
        parsed_action_ledger = self._as_memory_list(
            raw_action_ledger
        )
        action_ledger, carried_forward_receipt_ids = self._merge_pending_receipts_into_memory_ledger(
            parsed_action_ledger,
            deterministic["action_responsibility_ledger"],
        )
        if "non_compressible_clinical_kernel" in parsed:
            raw_kernel = parsed.get("non_compressible_clinical_kernel")
        elif "must_preserve_clinical_kernel" in parsed:
            raw_kernel = parsed.get("must_preserve_clinical_kernel")
        else:
            raw_kernel = []
        parsed_kernel = self._as_memory_list(raw_kernel)
        non_compressible_kernel, carried_forward_kernel_keys = self._merge_non_compressible_kernel(
            parsed_kernel,
            deterministic["non_compressible_clinical_kernel"],
        )
        snapshot = {
            "protocol": "careloop.runtime_lite.clinical_memory.v1",
            "turn": turn,
            "sim_time": self.current_sim_time,
            "trigger": trigger,
            "metadata": metadata,
            "source_anchors": source_anchors,
            "senior_doctor_takeover_brief": parsed.get("senior_doctor_takeover_brief")
            or parsed.get("one_sentence_takeover_brief")
            or parsed.get("takeover_brief")
            or deterministic.get("senior_doctor_takeover_brief"),
            "phase_timeline": self._as_memory_list(
                parsed.get("phase_timeline")
                or parsed.get("longitudinal_phase_timeline")
                or deterministic.get("phase_timeline")
            ),
            "current_decision_frame": parsed.get("current_decision_frame")
            if isinstance(parsed.get("current_decision_frame"), dict)
            else deterministic.get("current_decision_frame"),
            "backstage_memory": parsed.get("backstage_memory") if isinstance(parsed.get("backstage_memory"), dict) else {},
            "non_compressible_clinical_kernel": non_compressible_kernel,
            "active_clinical_problem_list": self._as_memory_list(
                parsed.get("active_clinical_problem_list")
                or parsed.get("clinical_problem_list")
                or deterministic["active_clinical_problem_list"]
            ),
            "action_responsibility_ledger": action_ledger,
            "real_world_constraint_model": self._as_memory_list(
                parsed.get("real_world_constraint_model")
                or parsed.get("real_world_constraints")
                or deterministic["real_world_constraint_model"]
            ),
            "open_threads": self._as_memory_list(parsed.get("open_threads") or deterministic["open_threads"]),
            "resolved_or_dormant_threads": self._as_memory_list(parsed.get("resolved_or_dormant_threads")),
            "uncertainties": self._as_memory_list(parsed.get("uncertainties") or deterministic["uncertainties"]),
            "actor_lived_memory": self._actor_lived_memory_snapshot(),
            "compression_policy": {
                "raw_ledger_authority": "The immutable trajectory remains the source of truth.",
                "source_anchor_required_for_high_impact_claims": True,
                "actor_memory_is_runtime_sanitized": True,
                "hidden_material_not_doctor_visible": True,
                "long_context_target_turns": max(1, int(self.config.max_turns or 1)),
                "senior_doctor_context_policy": self._senior_doctor_context_policy(),
            },
        }
        snapshot["doctor_visible_memory"] = self._doctor_visible_memory_from_clinical_snapshot(snapshot)
        snapshot["memory_integrity_audit"] = self._memory_integrity_audit(
            snapshot,
            deterministic=deterministic,
            carried_forward_receipt_ids=carried_forward_receipt_ids,
            carried_forward_kernel_keys=carried_forward_kernel_keys,
        )
        if raw and not parsed:
            snapshot["backstage_memory"] = {
                "prose_memory_note": self._compact_text(raw, limit=400),
                "prose_fallback": True,
            }
        return snapshot

    def _merge_pending_receipts_into_memory_ledger(
        self,
        ledger: list[Any],
        pending_receipts: list[dict[str, Any]],
    ) -> tuple[list[Any], list[str]]:
        """Preserve pending care responsibilities across LLM compression.

        The LLM steward may decide how to phrase priorities, but runtime
        receipts are hard evidence of unfinished doctor-side commitments. They
        should not disappear merely because a memory update summarized too
        aggressively.
        """

        merged = list(ledger or [])
        ledger_text = json.dumps(merged, ensure_ascii=False, default=str)
        carried: list[str] = []
        for receipt in pending_receipts or []:
            if not isinstance(receipt, dict):
                continue
            receipt_id = str(receipt.get("receipt_id") or "").strip()
            if receipt_id and receipt_id in ledger_text:
                continue
            carried_item = {
                **receipt,
                "source": "runtime_pending_receipt_carry_forward",
                "memory_integrity_note": "Pending doctor-side responsibility preserved because it was absent from the LLM memory ledger.",
            }
            merged.append(carried_item)
            if receipt_id:
                carried.append(receipt_id)
                ledger_text += "\n" + receipt_id
        return merged, carried

    def _merge_non_compressible_kernel(
        self,
        kernel: list[Any],
        deterministic_kernel: list[dict[str, Any]],
    ) -> tuple[list[Any], list[str]]:
        """Preserve decision-changing facts that should survive compression.

        The steward still decides what matters clinically. Runtime only
        prevents obvious hard-evidence anchors from disappearing when the LLM
        returns an over-compressed memory: pending receipts, recent abnormal or
        refusal/access/result signals, and other high-impact anchors.
        """

        merged = list(kernel or [])
        merged_keys = {
            self._non_compressible_kernel_key(item)
            for item in merged
            if isinstance(item, dict)
        }
        carried: list[str] = []
        for item in deterministic_kernel or []:
            if not isinstance(item, dict):
                continue
            key = self._non_compressible_kernel_key(item)
            if key and key in merged_keys:
                continue
            carried_item = {
                **item,
                "source": item.get("source") or "non_compressible_kernel_carry_forward",
                "memory_integrity_note": "Decision-changing evidence preserved because it was absent from the LLM compressed memory.",
            }
            merged.append(carried_item)
            if key:
                carried.append(key)
                merged_keys.add(key)
        return merged, carried

    def _non_compressible_kernel_key(self, item: dict[str, Any]) -> str:
        for key in ["receipt_id", "event_id", "workspace_record_id", "probability_event_id"]:
            value = str(item.get(key) or "").strip()
            if value:
                return value
        anchors = item.get("source_anchors")
        anchor_ids = self._collect_source_anchor_event_ids({"source_anchors": anchors})
        if anchor_ids:
            return anchor_ids[0]
        turn_ids = self._collect_source_anchor_transcript_turns({"source_anchors": anchors})
        if turn_ids:
            return f"turn:{turn_ids[0]}"
        return self._compact_text(json.dumps(item, ensure_ascii=False, default=str), limit=120)

    def _memory_integrity_audit(
        self,
        snapshot: dict[str, Any],
        *,
        deterministic: dict[str, Any],
        carried_forward_receipt_ids: list[str],
        carried_forward_kernel_keys: list[str],
    ) -> dict[str, Any]:
        pending_receipts = [
            item
            for item in deterministic.get("action_responsibility_ledger") or []
            if isinstance(item, dict)
        ]
        pending_ids = [str(item.get("receipt_id") or "") for item in pending_receipts if item.get("receipt_id")]
        ledger_text = json.dumps(snapshot.get("action_responsibility_ledger") or [], ensure_ascii=False, default=str)
        missing_after = [receipt_id for receipt_id in pending_ids if receipt_id not in ledger_text]
        deterministic_kernel = [
            item
            for item in deterministic.get("non_compressible_clinical_kernel") or []
            if isinstance(item, dict)
        ]
        deterministic_kernel_keys = [self._non_compressible_kernel_key(item) for item in deterministic_kernel]
        snapshot_kernel_keys = {
            self._non_compressible_kernel_key(item)
            for item in self._as_memory_list(snapshot.get("non_compressible_clinical_kernel"))
            if isinstance(item, dict)
        }
        missing_kernel_keys = [key for key in deterministic_kernel_keys if key and key not in snapshot_kernel_keys]
        high_impact_signals = self._recent_high_impact_memory_signals()
        fields_needing_anchors = [
            *self._as_memory_list(snapshot.get("non_compressible_clinical_kernel")),
            *self._as_memory_list(snapshot.get("active_clinical_problem_list")),
            *self._as_memory_list(snapshot.get("action_responsibility_ledger")),
            *self._as_memory_list(snapshot.get("real_world_constraint_model")),
            *self._as_memory_list(snapshot.get("open_threads")),
            *self._as_memory_list(snapshot.get("uncertainties")),
        ]
        anchorable = [item for item in fields_needing_anchors if isinstance(item, dict)]
        with_anchors = [
            item
            for item in anchorable
            if item.get("source_anchors") or item.get("receipt_id") or item.get("event_id")
        ]
        return {
            "protocol": "careloop.runtime_lite.clinical_memory_integrity_audit.v1",
            "status": "pass" if not missing_after and not missing_kernel_keys else "review",
            "pending_receipt_count": len(pending_ids),
            "carried_forward_pending_receipt_ids": carried_forward_receipt_ids,
            "missing_pending_receipt_ids_after_carry_forward": missing_after,
            "non_compressible_kernel_item_count": len(self._as_memory_list(snapshot.get("non_compressible_clinical_kernel"))),
            "deterministic_non_compressible_kernel_key_count": len([key for key in deterministic_kernel_keys if key]),
            "carried_forward_non_compressible_kernel_keys": carried_forward_kernel_keys,
            "missing_non_compressible_kernel_keys_after_carry_forward": missing_kernel_keys,
            "high_impact_recent_signal_count": len(high_impact_signals),
            "high_impact_recent_signals": high_impact_signals,
            "source_anchor_coverage": {
                "anchorable_item_count": len(anchorable),
                "items_with_source_anchor_or_direct_id": len(with_anchors),
                "principle": "High-impact memory claims should keep source anchors or deterministic ids when available.",
            },
            "audit_policy": "Runtime verifies that pending responsibilities survive compression; clinical interpretation remains LLM-led.",
        }

    def _recent_high_impact_memory_signals(self, limit: int = 12) -> list[dict[str, Any]]:
        keywords = [
            "allergy",
            "contraindication",
            "adverse",
            "abnormal",
            "critical",
            "refus",
            "declin",
            "pregnan",
            "renal",
            "过敏",
            "禁忌",
            "不良反应",
            "异常",
            "危急",
            "拒绝",
            "不愿",
            "怀孕",
            "妊娠",
            "肾功能",
            "肝功能",
            "出血",
            "恶化",
            "加重",
            "随访",
            "复查",
            "结果",
        ]
        signals: list[dict[str, Any]] = []
        for event in reversed(self.trajectory.events):
            text = json.dumps(event.get("content"), ensure_ascii=False, default=str)
            lowered = text.lower()
            matched = [keyword for keyword in keywords if keyword.lower() in lowered]
            if not matched:
                continue
            signals.append(
                {
                    "event_id": event.get("event_id"),
                    "turn": event.get("turn"),
                    "actor": event.get("actor"),
                    "event_type": event.get("event_type"),
                    "visibility": event.get("visibility"),
                    "matched_terms": matched[:5],
                    "content_preview": self._compact_text(text, limit=240),
                    "source_anchors": [event.get("event_id")] if event.get("event_id") else [],
                }
            )
            if len(signals) >= limit:
                break
        return list(reversed(signals))

    def _deterministic_memory_scaffold(self, turn: int, *, trigger: str, raw: str = "") -> dict[str, Any]:
        care_state = self.care_system.to_dict()
        pending = [
            self._doctor_visible_receipt(item)
            for item in care_state.get("pending_receipts") or []
            if isinstance(item, dict)
        ]
        recent_actor_messages = [
            item
            for item in self.trajectory.transcript[-self.config.recent_raw_turn_window :]
            if item.get("event_type") in {"patient_message", "family_message"}
        ]
        non_compressible_kernel = self._deterministic_non_compressible_clinical_kernel(
            pending_receipts=pending,
            recent_actor_messages=recent_actor_messages,
        )
        latest_doctor = next(
            (item for item in reversed(self.trajectory.transcript) if item.get("event_type") == "doctor_message"),
            {},
        )
        latest_actor = next(
            (item for item in reversed(self.trajectory.transcript) if item.get("event_type") in {"patient_message", "family_message"}),
            {},
        )
        return {
            "senior_doctor_takeover_brief": {
                "case": self.case.title,
                "turn": turn,
                "sim_time": self.current_sim_time,
                "brief": (
                    "接手时应先确认当前病程阶段、最新患者/家属反馈、未完成医嘱/随访/资料回流，以及是否已达到终局闭环。"
                ),
                "latest_actor_message_preview": self._compact_text(latest_actor.get("text"), limit=220),
                "latest_doctor_message_preview": self._compact_text(latest_doctor.get("text"), limit=220),
                "source_anchors": [item.get("event_id") for item in self.trajectory.events[-5:] if item.get("event_id")],
            },
            "phase_timeline": [
                {
                    "phase": "current_runtime_phase",
                    "turn": turn,
                    "sim_time": self.current_sim_time,
                    "turns_completed_so_far": self._last_completed_turn(),
                    "event_count": len(self.trajectory.events),
                    "source_anchors": [item.get("event_id") for item in self.trajectory.events[-5:] if item.get("event_id")],
                }
            ],
            "current_decision_frame": {
                "what_needs_decision_now": "which risk/result/responsibility must be clarified next before safe terminal closure",
                "pending_receipt_count": len(pending),
                "recent_actor_message_count": len(recent_actor_messages),
                "closure_guardrail": "do not treat handoff, prescription, test ordering, or symptom improvement as terminal closure unless terminal evidence exists",
                "source_anchors": [item.get("event_id") for item in self.trajectory.events[-5:] if item.get("event_id")],
            },
            "non_compressible_clinical_kernel": non_compressible_kernel,
            "active_clinical_problem_list": [
                {
                    "problem": self.case.title,
                    "status": "active_or_recent",
                    "basis": "Runtime scaffold from case title and recent trajectory; LLM steward may refine.",
                    "source_anchors": [item.get("event_id") for item in self.trajectory.events[-5:]],
                }
            ],
            "action_responsibility_ledger": pending,
            "real_world_constraint_model": [
                {
                    "constraint": "recent patient/family execution context",
                    "status": "needs_following",
                    "recent_actor_message_count": len(recent_actor_messages),
                    "examples": [self._compact_text(item.get("text"), limit=120) for item in recent_actor_messages[-3:]],
                }
            ],
            "open_threads": [
                {
                    "thread": "continue care until ClosureJudge verifies a real closed loop",
                    "trigger": trigger,
                    "turn": turn,
                }
            ],
            "uncertainties": [],
        }

    def _deterministic_non_compressible_clinical_kernel(
        self,
        *,
        pending_receipts: list[dict[str, Any]],
        recent_actor_messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Deterministic floor under LLM-led memory compression.

        This is intentionally small: it does not infer diagnoses or rank the
        case. It only preserves already-observed, decision-changing anchors an
        excellent doctor would refuse to lose in a long chart.
        """

        kernel: list[dict[str, Any]] = []
        for receipt in pending_receipts:
            if not isinstance(receipt, dict):
                continue
            kernel.append(
                {
                    "kernel_type": "pending_responsibility",
                    "statement": self._compact_text(
                        receipt.get("summary") or receipt.get("operation") or "pending doctor-side care responsibility",
                        limit=220,
                    ),
                    "owner": receipt.get("owner") or receipt.get("actor") or "care_system",
                    "status": receipt.get("status") or "pending",
                    "receipt_id": receipt.get("receipt_id"),
                    "source_anchors": [receipt.get("receipt_id")] if receipt.get("receipt_id") else [],
                    "why_non_compressible": "Pending care responsibility must remain visible until completed, failed, refused, superseded, or explicitly closed.",
                }
            )
        for signal in self._recent_high_impact_memory_signals(limit=10):
            kernel.append(
                {
                    "kernel_type": "recent_high_impact_signal",
                    "statement": signal.get("content_preview"),
                    "status": "must_be_reconciled",
                    "event_id": signal.get("event_id"),
                    "turn": signal.get("turn"),
                    "event_type": signal.get("event_type"),
                    "matched_terms": signal.get("matched_terms") or [],
                    "source_anchors": signal.get("source_anchors") or [],
                    "why_non_compressible": "Recent abnormal/result/refusal/pregnancy/organ-function/safety-net signal may change future decisions.",
                }
            )
        for item in recent_actor_messages[-6:]:
            text = str(item.get("text") or "")
            categories = self._non_compressible_actor_message_categories(text)
            if not categories:
                continue
            turn = item.get("turn")
            kernel.append(
                {
                    "kernel_type": "actor_execution_or_constraint_signal",
                    "statement": self._compact_text(text, limit=220),
                    "status": "active_or_needs_recheck",
                    "turn": turn,
                    "speaker": item.get("speaker_display") or item.get("speaker"),
                    "categories": categories,
                    "source_anchors": [f"turn {turn}"] if turn is not None else [],
                    "why_non_compressible": "Real-world execution constraints and refusals often determine whether a medically correct plan is actually usable.",
                }
            )
        deduped: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in kernel:
            key = self._non_compressible_kernel_key(item)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped[:18]

    def _non_compressible_actor_message_categories(self, text: str) -> list[str]:
        source = str(text or "")
        patterns = [
            ("result_or_record_return", r"(结果|报告|报告单|化验|检查|片子|CT|B超).{0,24}(出来|拿到|上传|发你|显示|提示|异常|偏高|偏低|阳性|阴性)"),
            ("refusal_or_delay", r"(不想|不愿|不去|不查|不住院|不吃|拒绝|先不|等等|明天再|拖|忍一忍)"),
            ("access_or_transport_barrier", r"(远|没车|打不到车|救护车|120|县医院|本地做不了|夜里|排队|挂号)"),
            ("cost_or_work_barrier", r"(钱|费用|太贵|医保|报销|付不起|上班|请假|扣工资|收入)"),
            ("family_or_caregiver_barrier", r"(家属|家里人|我爸|我妈|老公|老婆|女儿|儿子).{0,24}(不让|不同意|反对|怕|嫌|说不用|照顾不了)"),
            ("medication_or_allergy_safety", r"(药|药盒|过敏|不良反应|怀孕|妊娠|哺乳|肾功能|肝功能|出血|抗凝)"),
            ("deterioration_or_red_flag", r"(加重|恶化|晕倒|抽搐|胸痛|喘不上气|意识|出血|高烧|剧痛|无力|说话不清)"),
        ]
        return [category for category, pattern in patterns if re.search(pattern, source, re.IGNORECASE)]

    def _as_memory_list(self, value: Any) -> list[Any]:
        if value is None:
            return []
        if isinstance(value, list):
            return value
        if isinstance(value, tuple):
            return list(value)
        return [value]

    def _memory_source_anchors(self, turn: int) -> dict[str, Any]:
        prompt_events = self._prompt_relevant_events()
        return {
            "turn": turn,
            "latest_event_ids": [event.get("event_id") for event in prompt_events[-12:]],
            "latest_transcript_turns": [item.get("turn") for item in self.trajectory.transcript[-self.config.recent_raw_turn_window :]],
            "probability_event_ids": [record.get("event_id") for record in self.trajectory.probability_ledger[-8:]],
        }

    def _recent_events_for_memory(self, consumer: str = "clinical_memory_steward") -> list[dict[str, Any]]:
        profile = self._prompt_context_profile(consumer)
        events: list[dict[str, Any]] = []
        for event in self._prompt_relevant_events()[-profile["recent_events"] :]:
            content = event.get("content")
            events.append(
                {
                    "event_id": event.get("event_id"),
                    "turn": event.get("turn"),
                    "actor": event.get("actor"),
                    "event_type": event.get("event_type"),
                    "sim_time": event.get("sim_time"),
                    "visibility": event.get("visibility"),
                    "content_preview": self._compact_text(
                        json.dumps(content, ensure_ascii=False, default=str),
                        limit=profile["preview_limit"],
                    ),
                }
            )
        return events

    def _recent_transcript_for_prompt(self, consumer: str) -> list[dict[str, Any]]:
        profile = self._prompt_context_profile(consumer)
        return self.trajectory.transcript[-profile.get("recent_turns", self.config.recent_raw_turn_window) :]

    def _trajectory_context_for_prompt(self, consumer: str) -> dict[str, Any]:
        """Return a memory-aware trajectory view for LLM prompts.

        The immutable trajectory remains the audit source of truth, but long
        care processes should not repeatedly send the full raw ledger to every
        backstage node.  This context gives the model recent raw material plus
        anchored snippets from the current memory snapshot.
        """

        profile = self._prompt_context_profile(consumer)
        latest_memory = self.clinical_memory_snapshots[-1] if self.clinical_memory_snapshots else {}
        source_anchors = latest_memory.get("source_anchors") if isinstance(latest_memory.get("source_anchors"), dict) else {}
        anchor_ids = [str(item) for item in source_anchors.get("latest_event_ids") or [] if str(item)]
        prompt_events = self._prompt_relevant_events()
        recent_ids = [str(event.get("event_id") or "") for event in prompt_events[-profile["recent_events"] :]]
        wanted_ids = list(dict.fromkeys([*anchor_ids, *recent_ids]))
        events_by_id = {str(event.get("event_id") or ""): event for event in prompt_events}
        anchored_events: list[dict[str, Any]] = []
        for event_id in wanted_ids[-profile["anchored_events"] :]:
            event = events_by_id.get(event_id)
            if not isinstance(event, dict):
                continue
            anchored_events.append(self._event_context_brief(event, preview_limit=profile["preview_limit"]))
        payload = {
            "protocol": "careloop.runtime_lite.trajectory_context.v1",
            "consumer": consumer,
            "context_mode": "clinical_memory_plus_recent_window",
            "raw_ledger_authority": "Full immutable trajectory is stored in runtime output; this prompt receives compact anchored context.",
            "full_trajectory_omitted_from_prompt": True,
            "case_id": self.case.case_id,
            "run_id": self.config.run_id,
            "counts": {
                "events": len(self.trajectory.events),
                "transcript_messages": len(self.trajectory.transcript),
                "llm_calls": len(self.trajectory.llm_calls),
                "probability_records": len(self.trajectory.probability_ledger),
                "clinical_memory_snapshots": len(self.clinical_memory_snapshots),
            },
            "current_sim_time": self.current_sim_time,
            "recent_transcript": self.trajectory.transcript[-profile["recent_turns"] :],
            "recent_events": [
                self._event_context_brief(event, preview_limit=profile["preview_limit"])
                for event in prompt_events[-profile["recent_events"] :]
            ],
            "anchored_events": anchored_events,
            "source_evidence_pack": self._source_anchor_evidence_pack(
                consumer,
                max_events=profile["source_events"],
                preview_limit=profile["preview_limit"],
            ),
            "recent_probability_records": self.trajectory.probability_ledger[-min(6, profile["list_limit"]) :],
            "latest_clinical_memory_source_anchors": source_anchors,
        }
        return self._bounded_prompt_payload(
            payload,
            char_budget=profile["trajectory_budget"],
            text_limit=profile["preview_limit"],
            list_limit=profile["list_limit"],
            label=f"trajectory_context:{consumer}",
        )

    def _evaluation_evidence_pack(self, closure: ClosureAssessmentLite, *, consumer: str) -> dict[str, Any]:
        """Compact evaluator-only evidence about clinical agency and contamination.

        This pack is deliberately not shown to the tested doctor.  It helps the
        final evaluator avoid two observed failure modes: crediting CareLoop or
        external clinicians as if they were the AI doctor, and missing blank
        doctor replies that were later masked by world progression.
        """

        events = [event for event in self.trajectory.events if isinstance(event, dict)]
        transcript = [item for item in self.trajectory.transcript if isinstance(item, dict)]
        doctor_messages = [item for item in transcript if str(item.get("speaker") or "") == "doctor"]
        actor_messages = [item for item in transcript if str(item.get("event_type") or "") in {"patient_message", "family_message"}]
        blank_doctor_turns = [item.get("turn") for item in doctor_messages if not str(item.get("text") or "").strip()]
        llm_empty_rejections = [
            item
            for item in self.trajectory.llm_calls
            if isinstance(item, dict)
            and isinstance(item.get("metadata"), dict)
            and item["metadata"].get("runtime_empty_output_rejected")
        ]

        clinical_keywords = [
            "诊断",
            "鉴别",
            "风险分层",
            "检查",
            "化验",
            "影像",
            "CT",
            "超声",
            "培养",
            "药敏",
            "病理",
            "治疗",
            "用药",
            "处方",
            "剂量",
            "抗生素",
            "抗凝",
            "疗效",
            "反应",
            "副作用",
            "复发",
            "无效",
        ]
        clinical_decision_turns = [
            {
                "turn": item.get("turn"),
                "text_preview": self._compact_text(item.get("text"), limit=360),
            }
            for item in doctor_messages
            if any(keyword.lower() in str(item.get("text") or "").lower() for keyword in clinical_keywords)
        ][-24:]

        external_keywords = ["外院", "线下", "急诊医生", "门诊医生", "住院", "护士", "医院", "出院", "入院", "病房", "专科医生"]
        external_system_events: list[dict[str, Any]] = []
        for event in events:
            text = json.dumps(event.get("content"), ensure_ascii=False, default=str)
            if any(keyword in text for keyword in external_keywords):
                external_system_events.append(self._event_context_brief(event, preview_limit=420))
        external_system_events = external_system_events[-24:]

        result_and_action_events = [
            self._event_context_brief(event, preview_limit=420)
            for event in events
            if str(event.get("event_type") or "")
            in {
                "doctor_workspace_result",
                "doctor_care_system_receipt",
                "doctor_system_notification",
                "workspace_state_update",
                "care_system_state_update",
                "doctor_operation_draft",
            }
        ][-32:]

        critical_safety_events = [
            self._event_context_brief(event, preview_limit=520)
            for event in events
            if str(event.get("event_type") or "") == "critical_safety_event"
        ][-24:]

        visibility_audit_events = [
            self._event_context_brief(event, preview_limit=420)
            for event in events
            if str(event.get("event_type") or "") in {"actor_message_visibility_audit", "workspace_result_visibility_audit", "clinical_unsafe_stop_reframed", "director_cut_reframed_as_critical_safety_event"}
        ][-24:]

        test_receipts: list[dict[str, Any]] = []
        for receipt in self.care_system.receipts:
            if not isinstance(receipt, dict) or receipt.get("operation") != "care_system.order_test":
                continue
            test_receipts.append(
                {
                    "receipt_id": receipt.get("receipt_id"),
                    "status": receipt.get("status"),
                    "summary": self._compact_text(receipt.get("summary"), limit=180),
                    "test_name": (receipt.get("parameters") or {}).get("test_name") if isinstance(receipt.get("parameters"), dict) else None,
                    "execution_status": receipt.get("execution_status"),
                    "appointment_or_execution_window": receipt.get("appointment_or_execution_window"),
                    "scheduled_for": receipt.get("scheduled_for"),
                    "performed_at_sim_time": receipt.get("performed_at_sim_time"),
                    "result_status": receipt.get("result_status"),
                    "result_turnaround_window": receipt.get("result_turnaround_window"),
                    "result_available_at_sim_time": receipt.get("result_available_at_sim_time") or receipt.get("result_available_time"),
                }
            )

        friction_keywords = [
            "钱",
            "费用",
            "自费",
            "医保",
            "报销",
            "路费",
            "交通",
            "太远",
            "没车",
            "宗教",
            "信仰",
            "忌讳",
            "迷信",
            "偏方",
            "算命",
            "家里不同意",
            "长辈",
            "不让",
            "怕检查",
            "怕住院",
            "怕手术",
        ]
        real_world_constraint_events = []
        for event in events:
            text = json.dumps(event.get("content"), ensure_ascii=False, default=str)
            if any(keyword in text for keyword in friction_keywords):
                real_world_constraint_events.append(self._event_context_brief(event, preview_limit=360))
        real_world_constraint_events = real_world_constraint_events[-18:]

        special_event_keywords = [
            "special_event",
            "random_special_event",
            "花粉",
            "雾霾",
            "冷空气",
            "寒潮",
            "高温",
            "脱水",
            "流感",
            "诺如",
            "断货",
            "割草",
            "装修",
            "草药",
            "保健品",
            "节日",
        ]
        special_event_signals = []
        for event in events:
            text = json.dumps(event.get("content"), ensure_ascii=False, default=str)
            if any(keyword in text for keyword in special_event_keywords):
                brief = self._event_context_brief(event, preview_limit=360)
                content = event.get("content") if isinstance(event.get("content"), dict) else {}
                metadata = content.get("metadata") if isinstance(content.get("metadata"), dict) else {}
                brief["special_event_visibility_hint"] = {
                    "visible_to_doctor": bool(content.get("visible_to_doctor") or metadata.get("doctor_visible_summary")),
                    "visible_to_patient_or_family": content.get("visible_to_patient_or_family", metadata.get("visible_to_patient_or_family")),
                    "silent_world_fact": metadata.get("silent_world_fact"),
                    "event_class": metadata.get("event_class"),
                }
                special_event_signals.append(brief)
        special_event_signals = special_event_signals[-18:]

        workspace_contract = self.case.raw.get("workspace_contract") if isinstance(self.case.raw.get("workspace_contract"), dict) else {}
        case_profile = {
            "clinical_decision_burden": self.case.raw.get("clinical_decision_burden")
            or (self.case.raw.get("evaluation_contract") or {}).get("clinical_decision_burden")
            or (self.case.raw.get("case_taxonomy") or {}).get("clinical_decision_burden")
            or "unspecified",
            "clinical_core_opportunities": self.case.raw.get("clinical_core_opportunities")
            or (self.case.raw.get("evaluation_contract") or {}).get("clinical_core_opportunities")
            or [],
            "care_network_record_count": len(workspace_contract.get("care_network_records") or [])
            if isinstance(workspace_contract.get("care_network_records"), list)
            else 0,
            "case_type": self.case.raw.get("case_type"),
            "capability_tags": (self.case.raw.get("case_taxonomy") or {}).get("capability_tags")
            if isinstance(self.case.raw.get("case_taxonomy"), dict)
            else [],
        }

        payload = {
            "protocol": "careloop.runtime_lite.evaluation_evidence_pack.v1",
            "consumer": consumer,
            "visibility": "evaluator_only_not_doctor_visible",
            "case_profile": case_profile,
            "closure_assessment_brief": {
                "status": closure.status,
                "closure_kind": closure.closure_kind,
                "rationale_preview": self._compact_text(closure.rationale, limit=500),
            },
            "contamination_signals": {
                "doctor_blank_turns": blank_doctor_turns,
                "doctor_non_response_event_count": sum(1 for event in events if event.get("event_type") == "doctor_non_response"),
                "llm_empty_output_rejected_count": len(llm_empty_rejections),
                "empty_output_purposes": sorted({str(item.get("purpose") or "") for item in llm_empty_rejections}),
                "visibility_boundary_audit_events": visibility_audit_events,
                "leakage_principle": "Patient/family natural disclosure of actor-known information is not contamination; hidden/evaluator-only system/workspace leakage is contamination.",
            },
            "critical_safety_event_signals": {
                "event_count": len([event for event in events if str(event.get("event_type") or "") == "critical_safety_event"]),
                "recent_events": critical_safety_events,
                "principle": "Critical safety events are evidence and causal world-state modifiers, not automatic closure or automatic score.",
                "audit_questions": [
                    "What did the tested doctor do or omit that created safety risk?",
                    "Did later trajectory show harm, correction, worsening, death, recovery, or durable management?",
                    "How much responsibility belongs to the tested doctor versus patient/family choices, external clinicians, or system constraints?",
                ],
            },
            "clinical_core_signals": {
                "doctor_clinical_decision_signal_turns": clinical_decision_turns,
                "result_and_action_event_count": len(result_and_action_events),
                "recent_result_and_action_events": result_and_action_events,
                "p03i_shadow_observability_sidecars": {
                    "high_impact_event_lifecycle": self.shadow_high_impact_event_lifecycle
                    or self._build_shadow_high_impact_event_lifecycle_sidecar(closure=closure),
                    "action_receipt_tracker": self.shadow_action_receipt_tracker
                    or self._build_shadow_action_receipt_tracker_sidecar(closure=closure),
                    "critical_safety_thread_summary": self.shadow_critical_safety_thread_summary
                    or self._build_shadow_critical_safety_thread_summary(closure=closure),
                    "mode": "shadow_observability_only_not_scoring_or_closure_gate",
                },
                "p03h_shadow_observability_sidecars": {
                    "high_impact_event_lifecycle": self.shadow_high_impact_event_lifecycle
                    or self._build_shadow_high_impact_event_lifecycle_sidecar(closure=closure),
                    "action_receipt_tracker": self.shadow_action_receipt_tracker
                    or self._build_shadow_action_receipt_tracker_sidecar(closure=closure),
                    "mode": "legacy_alias_for_p03i_shadow_observability_only",
                },
                "test_lifecycle_receipts": test_receipts[-24:],
                "test_lifecycle_counts": {
                    "order_test_receipt_count": len(test_receipts),
                    "waiting_for_execution_count": sum(
                        1
                        for item in test_receipts
                        if str(item.get("execution_status") or "").lower() in {"not_yet_scheduled_or_performed", "scheduled_not_yet_performed"}
                    ),
                    "performed_waiting_for_result_count": sum(
                        1 for item in test_receipts if str(item.get("result_status") or "").lower() in {"pending", "delayed"}
                    ),
                    "result_available_count": sum(1 for item in test_receipts if str(item.get("result_status") or "").lower() == "available"),
                },
            },
            "real_world_constraint_signals": {
                "constraint_event_count": len(real_world_constraint_events),
                "recent_constraint_events": real_world_constraint_events,
                "audit_questions": [
                    "费用、交通、宗教/迷信、家庭权威或心理抗拒是否真实影响了医疗执行？",
                    "AI 医生是否把医学方案改造成患者当时能执行的最低安全路径？",
                    "若执行失败，主要责任在医生未适配、患者/家属现实选择、医疗系统约束，还是模拟异常？",
                ],
            },
            "special_event_signals": {
                "special_event_signal_count": len(special_event_signals),
                "recent_special_event_signals": special_event_signals,
                "audit_questions": [
                    "特殊事件只是后台静默事实，还是已经以患者/医生可见的中性生活线索出现？",
                    "医生当时是否有合理机会通过问诊、位置/季节/症状推断、资料或安全网处理它？",
                    "如果特殊事件后来造成后果，后果是否真实可归因，还是模拟过度惩罚？",
                ],
            },
            "external_system_and_closure_source_signals": {
                "external_system_event_count": len(external_system_events),
                "recent_external_system_events": external_system_events,
                "audit_questions": [
                    "患者医学上安全/闭环了吗？",
                    "这个闭环主要由 AI 医生、外部医生/医院、患者/家属自救，还是 CareLoop 世界自推进促成？",
                    "AI 医生是否实际完成了诊断—检查—治疗—治疗反应—长期照护闭环，还是只完成送医/解释结果？",
                ],
            },
            "transcript_shape": {
                "doctor_message_count": len(doctor_messages),
                "actor_message_count": len(actor_messages),
                "recent_actor_messages": actor_messages[-6:],
                "recent_doctor_messages": doctor_messages[-6:],
            },
        }
        profile = self._prompt_context_profile(consumer)
        return self._bounded_prompt_payload(
            payload,
            char_budget=8500,
            text_limit=min(320, profile.get("preview_limit", 360)),
            list_limit=min(8, profile.get("list_limit", 8)),
            label=f"evaluation_evidence_pack:{consumer}",
        )

    def _event_context_brief(self, event: dict[str, Any], *, preview_limit: int = 280) -> dict[str, Any]:
        content = event.get("content")
        return {
            "event_id": event.get("event_id"),
            "turn": event.get("turn"),
            "actor": event.get("actor"),
            "event_type": event.get("event_type"),
            "sim_time": event.get("sim_time"),
            "visibility": event.get("visibility"),
            "metadata_keys": sorted((event.get("metadata") or {}).keys()) if isinstance(event.get("metadata"), dict) else [],
            "content_preview": self._compact_text(json.dumps(content, ensure_ascii=False, default=str), limit=preview_limit),
        }

    def _source_anchor_evidence_pack(self, consumer: str, *, max_events: int = 32, preview_limit: int = 700) -> dict[str, Any]:
        """Resolve clinical-memory source anchors back to raw ledger snippets.

        Long trajectories should not resend the full raw ledger, but compressed
        memory must stay checkable.  This deterministic retrieval pack gives
        backstage consumers short raw excerpts for source ids cited by the
        current memory snapshot, plus a small recent fallback before the first
        memory exists.
        """

        profile = self._prompt_context_profile(consumer)
        max_events = min(max_events, profile.get("source_events", max_events))
        preview_limit = min(preview_limit, profile.get("preview_limit", preview_limit))
        prompt_events = self._prompt_relevant_events()
        latest_memory = self.clinical_memory_snapshots[-1] if self.clinical_memory_snapshots else {}
        anchor_ids = self._collect_source_anchor_event_ids(latest_memory)
        if not anchor_ids:
            anchor_ids = [str(event.get("event_id") or "") for event in prompt_events[-12:] if event.get("event_id")]
        recent_ids = [str(event.get("event_id") or "") for event in prompt_events[-6:] if event.get("event_id")]
        requested_ids = list(dict.fromkeys([*anchor_ids, *recent_ids]))
        events_by_id = {str(event.get("event_id") or ""): event for event in prompt_events if event.get("event_id")}
        resolved_events: list[dict[str, Any]] = []
        unresolved: list[str] = []
        for event_id in requested_ids[-max_events:]:
            event = events_by_id.get(event_id)
            if isinstance(event, dict):
                resolved_events.append(self._event_context_brief(event, preview_limit=preview_limit))
            elif event_id:
                unresolved.append(event_id)

        transcript_turns = self._collect_source_anchor_transcript_turns(latest_memory)
        recent_transcript_turns = [item.get("turn") for item in self.trajectory.transcript[-profile.get("recent_turns", self.config.recent_raw_turn_window) :] if item.get("turn") is not None]
        wanted_turns = set([*transcript_turns, *recent_transcript_turns])
        transcript_snippets = [
            {
                "turn": item.get("turn"),
                "speaker": item.get("speaker"),
                "speaker_category": item.get("speaker_category"),
                "event_type": item.get("event_type"),
                "text_preview": self._compact_text(item.get("text"), limit=preview_limit),
            }
            for item in self.trajectory.transcript
            if item.get("turn") in wanted_turns
        ][-profile.get("recent_turns", self.config.recent_raw_turn_window) * 2 :]
        payload = {
            "protocol": "careloop.runtime_lite.source_evidence_pack.v1",
            "consumer": consumer,
            "retrieval_mode": "deterministic_source_anchor_lookup",
            "raw_ledger_authority": "Evidence snippets are excerpts only; immutable trajectory output remains the audit source of truth.",
            "full_raw_ledger_omitted": True,
            "requested_event_ids": requested_ids[-max_events:],
            "resolved_event_count": len(resolved_events),
            "unresolved_event_ids": unresolved,
            "evidence_events": resolved_events,
            "requested_transcript_turns": sorted(wanted_turns),
            "transcript_snippets": transcript_snippets,
            "use_policy": "Use these snippets to verify compressed memory, pending responsibilities, risks, and closure/evaluation judgments. Do not pass this pack to patient/family actors.",
        }
        return self._bounded_prompt_payload(
            payload,
            char_budget=profile["source_budget"],
            text_limit=preview_limit,
            list_limit=profile["list_limit"],
            label=f"source_evidence_pack:{consumer}",
        )

    def _prompt_relevant_events(self) -> list[dict[str, Any]]:
        """Events safe to expose to LLM prompt contexts.

        The immutable trajectory keeps runtime_stage_progress for operator
        supervision.  Prompt contexts exclude it so ClinicalMemorySteward,
        WorldDirector, ClosureJudge and final evaluators do not treat
        infrastructure heartbeat/checkpoint markers as medical or simulation
        evidence.
        """

        return [
            event
            for event in self.trajectory.events
            if isinstance(event, dict) and str(event.get("event_type") or "") != "runtime_stage_progress"
        ]

    def _collect_source_anchor_event_ids(self, value: Any) -> list[str]:
        ids: list[str] = []

        def visit(node: Any, key_hint: str = "") -> None:
            if isinstance(node, dict):
                for key, child in node.items():
                    normalized = str(key).lower()
                    if normalized in {
                        "event_id",
                        "event_ids",
                        "latest_event_ids",
                        "source_event_ids",
                        "probability_event_ids",
                    }:
                        visit_anchor_value(child)
                    elif normalized == "source_anchors":
                        visit_anchor_value(child)
                        visit(child, normalized)
                    else:
                        visit(child, normalized)
            elif isinstance(node, list):
                for child in node:
                    visit(child, key_hint)

        def visit_anchor_value(anchor: Any) -> None:
            if isinstance(anchor, str):
                if anchor.startswith("lite_evt_") or anchor.startswith("evt_") or anchor.startswith("event_"):
                    ids.append(anchor)
            elif isinstance(anchor, (int, float)):
                return
            elif isinstance(anchor, dict):
                for key, child in anchor.items():
                    normalized = str(key).lower()
                    if "event" in normalized or normalized in {"ids", "latest_ids"}:
                        visit_anchor_value(child)
            elif isinstance(anchor, list):
                for child in anchor:
                    visit_anchor_value(child)

        visit(value)
        return list(dict.fromkeys([item for item in ids if item]))

    def _collect_source_anchor_transcript_turns(self, value: Any) -> list[int]:
        turns: list[int] = []

        def visit(node: Any) -> None:
            if isinstance(node, dict):
                for key, child in node.items():
                    normalized = str(key).lower()
                    if normalized in {"turn", "turns", "transcript_turn", "transcript_turns", "latest_transcript_turns"}:
                        visit_turn_value(child)
                    elif normalized == "source_anchors":
                        visit_turn_value(child)
                        visit(child)
                    else:
                        visit(child)
            elif isinstance(node, list):
                for child in node:
                    visit(child)

        def visit_turn_value(anchor: Any) -> None:
            if isinstance(anchor, bool):
                return
            if isinstance(anchor, int):
                turns.append(anchor)
            elif isinstance(anchor, float) and anchor.is_integer():
                turns.append(int(anchor))
            elif isinstance(anchor, str):
                match = re.search(r"(?:turn|轮|第)\s*#?\s*(\d+)", anchor, flags=re.IGNORECASE)
                if match:
                    turns.append(int(match.group(1)))
            elif isinstance(anchor, dict):
                for key, child in anchor.items():
                    normalized = str(key).lower()
                    if "turn" in normalized:
                        visit_turn_value(child)
            elif isinstance(anchor, list):
                for child in anchor:
                    visit_turn_value(child)

        visit(value)
        return list(dict.fromkeys(turns))

    def _doctor_visible_memory_snapshot(self) -> dict[str, Any]:
        return {
            "principle": "Doctor-visible longitudinal memory only; no hidden case truth, director rationale, probability audit, or evaluator opinion.",
            "recent_transcript": self.trajectory.transcript[-self.config.recent_raw_turn_window :],
            "recent_doctor_system_notifications": self._recent_doctor_system_notifications(limit=6),
            "care_system_state": self._doctor_visible_care_system_state(),
        }

    def _doctor_visible_memory_from_clinical_snapshot(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        """Build a doctor-facing longitudinal memory from steward output.

        ClinicalMemorySteward is allowed to read backstage context, so the
        runtime cannot blindly forward its free-form memory to the tested
        doctor.  This helper preserves the steward's clinically useful
        long-view only when an item has doctor-visible anchors or runtime care
        receipts.  Visibility remains a hard code boundary; the choice of what
        matters clinically remains LLM-led inside that boundary.
        """

        memory = self._doctor_visible_memory_snapshot()
        source_fields = [
            "senior_doctor_takeover_brief",
            "phase_timeline",
            "current_decision_frame",
            "non_compressible_clinical_kernel",
            "active_clinical_problem_list",
            "action_responsibility_ledger",
            "real_world_constraint_model",
            "open_threads",
            "uncertainties",
            "resolved_or_dormant_threads",
        ]
        included_counts: dict[str, int] = {}
        dropped_counts: dict[str, int] = {}
        for field in source_fields:
            visible_items, dropped_count = self._doctor_visible_memory_items(snapshot.get(field))
            if visible_items:
                memory[field] = visible_items
            included_counts[field] = len(visible_items)
            dropped_counts[field] = dropped_count
        memory["clinical_continuity_source"] = {
            "source": "doctor_visible_anchored_longitudinal_memory",
            "visibility_policy": (
                "Only items with doctor-visible source anchors, transcript turns, "
                "or doctor-side care-system receipts are forwarded."
            ),
            "included_counts": included_counts,
            "dropped_unanchored_or_nonvisible_counts": dropped_counts,
        }
        return memory

    def _doctor_visible_memory_items(self, value: Any) -> tuple[list[Any], int]:
        items = self._as_memory_list(value)
        visible_items: list[Any] = []
        dropped = 0
        for item in items:
            if not self._doctor_visible_memory_item_has_visible_evidence(item):
                dropped += 1
                continue
            cleaned = self._redact_doctor_visible_memory_payload(item)
            if self._doctor_visible_memory_payload_has_clinical_content(cleaned):
                visible_items.append(cleaned)
            else:
                dropped += 1
        return visible_items, dropped

    def _doctor_visible_memory_item_has_visible_evidence(self, item: Any) -> bool:
        if not isinstance(item, dict):
            return False
        if item.get("receipt_id") or item.get("source") == "runtime_pending_receipt_carry_forward":
            return True
        event_ids = self._collect_source_anchor_event_ids(item)
        if event_ids:
            return any(self._event_id_is_doctor_visible(event_id) for event_id in event_ids)
        transcript_turns = self._collect_source_anchor_transcript_turns(item)
        if transcript_turns:
            transcript_turn_set = set(transcript_turns)
            return any(entry.get("turn") in transcript_turn_set for entry in self.trajectory.transcript)
        return False

    def _event_id_is_doctor_visible(self, event_id: str) -> bool:
        allowed_visibilities = {"doctor_visible", "doctor_visible_tool", "patient_visible", "doctor_side_internal"}
        for event in self.trajectory.events:
            if str(event.get("event_id") or "") == str(event_id or ""):
                return str(event.get("visibility") or "") in allowed_visibilities
        return False

    def _redact_doctor_visible_memory_payload(self, value: Any) -> Any:
        if isinstance(value, dict):
            cleaned: dict[str, Any] = {}
            for key, child in value.items():
                key_text = str(key)
                if any(term in key_text for term in self._doctor_visible_runtime_terms()):
                    continue
                if key_text in {"memory_integrity_note"}:
                    continue
                child_cleaned = self._redact_doctor_visible_memory_payload(child)
                if self._doctor_visible_memory_payload_has_content(child_cleaned):
                    cleaned[key] = child_cleaned
            return cleaned
        if isinstance(value, list):
            cleaned_items = [self._redact_doctor_visible_memory_payload(item) for item in value]
            return [item for item in cleaned_items if self._doctor_visible_memory_payload_has_content(item)]
        if isinstance(value, str):
            return self._redact_doctor_visible_runtime_text(value)
        return value

    def _doctor_visible_memory_payload_has_content(self, value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, (int, float, bool)):
            return True
        if isinstance(value, dict):
            return any(self._doctor_visible_memory_payload_has_content(item) for item in value.values())
        if isinstance(value, list):
            return any(self._doctor_visible_memory_payload_has_content(item) for item in value)
        return True

    def _doctor_visible_memory_payload_has_clinical_content(self, value: Any) -> bool:
        """True when a cleaned memory item contains more than anchors/metadata."""

        meta_keys = {
            "source_anchors",
            "event_id",
            "event_ids",
            "receipt_id",
            "turn",
            "turns",
            "transcript_turn",
            "transcript_turns",
            "metadata",
        }
        if isinstance(value, dict):
            return any(
                self._doctor_visible_memory_payload_has_clinical_content(child)
                for key, child in value.items()
                if str(key) not in meta_keys
            )
        if isinstance(value, list):
            return any(self._doctor_visible_memory_payload_has_clinical_content(item) for item in value)
        return self._doctor_visible_memory_payload_has_content(value)

    def _actor_lived_memory_snapshot(self) -> dict[str, Any]:
        payload = {
            "principle": "Lived continuity from this speaker's point of view.",
            "recent_transcript": self.trajectory.transcript[-self.config.recent_raw_turn_window :],
            "recent_lived_continuity_notes": self._actor_lived_continuity_notes(),
        }
        cleaned = self._redact_actor_backstage_payload(payload)
        return cleaned if isinstance(cleaned, dict) else {"principle": payload["principle"], "recent_transcript": []}

    def _clinical_memory_backstage_for_prompt(self, consumer: str = "backstage") -> dict[str, Any]:
        if not self.clinical_memory_snapshots:
            return {
                "available": False,
                "principle": "No ClinicalMemorySteward snapshot has been created yet; use raw current trajectory and case context.",
            }
        profile = self._prompt_context_profile(consumer)
        return {
            "available": True,
            "snapshot": self._clinical_memory_snapshot_for_prompt(consumer),
            "principle": (
                "Backstage memory for Director/Closure/Evaluator only. This is a bounded long-context view; "
                "do not pass this whole object to actor prompts."
            ),
            "context_budget_profile": {
                "consumer": consumer,
                "memory_budget": profile["memory_budget"],
                "policy": "consumer_specific_minimum_sufficient_context; full audit trajectory remains stored",
            },
        }

    def _clinical_memory_snapshot_for_prompt(self, consumer: str) -> dict[str, Any]:
        if not self.clinical_memory_snapshots:
            return {}
        profile = self._prompt_context_profile(consumer)
        snapshot = deepcopy(self.clinical_memory_snapshots[-1])
        # The doctor-visible and actor-lived projections are derived views and
        # can duplicate the clinical core.  Backstage nodes need the clinical
        # core plus source anchors; actor/doctor projections are requested via
        # their own visibility-specific helpers.
        if consumer not in {"doctor_visible", "actor_lived"}:
            actor_memory = snapshot.pop("actor_lived_memory", {})
            snapshot.pop("doctor_visible_memory", None)
            if actor_memory:
                snapshot["actor_lived_memory_brief"] = self._bounded_prompt_payload(
                    actor_memory,
                    char_budget=min(max(1800, self.config.actor_memory_prompt_char_budget // 3), profile["memory_budget"]),
                    text_limit=min(360, profile["preview_limit"]),
                    list_limit=min(6, profile["list_limit"]),
                    label="actor_lived_memory_brief",
                )
        section_limits = {
            "non_compressible_clinical_kernel": self.config.memory_kernel_item_limit,
            "active_clinical_problem_list": self.config.memory_problem_item_limit,
            "action_responsibility_ledger": self.config.memory_responsibility_item_limit,
            "real_world_constraint_model": self.config.memory_constraint_item_limit,
            "open_threads": self.config.memory_thread_item_limit,
            "resolved_or_dormant_threads": self.config.memory_resolved_thread_item_limit,
            "uncertainties": self.config.memory_uncertainty_item_limit,
            "phase_timeline": self.config.memory_thread_item_limit,
        }
        narrow_consumers = {
            "timekeeper": {
                "protocol",
                "turn",
                "sim_time",
                "source_anchors",
                "senior_doctor_takeover_brief",
                "phase_timeline",
                "current_decision_frame",
                "action_responsibility_ledger",
                "real_world_constraint_model",
                "open_threads",
                "actor_lived_memory_brief",
            },
            "probability_estimator": {
                "protocol",
                "turn",
                "sim_time",
                "source_anchors",
                "senior_doctor_takeover_brief",
                "current_decision_frame",
                "non_compressible_clinical_kernel",
                "active_clinical_problem_list",
                "action_responsibility_ledger",
                "real_world_constraint_model",
                "open_threads",
                "uncertainties",
                "actor_lived_memory_brief",
            },
        }
        allowed_fields = narrow_consumers.get(consumer)
        if allowed_fields:
            snapshot = {key: value for key, value in snapshot.items() if key in allowed_fields}
        section_list_cap = max(3, int(profile.get("list_limit") or self.config.memory_prompt_list_limit))
        for field, limit in section_limits.items():
            if isinstance(snapshot.get(field), list):
                snapshot[field] = self._prioritize_memory_items(
                    [item for item in snapshot.get(field) or [] if isinstance(item, dict)],
                    limit=max(3, min(int(limit or 12), section_list_cap)),
                )
        snapshot["long_context_prompt_view"] = {
            "consumer": consumer,
            "target_turns": max(1, int(self.config.max_turns or 1)),
            "policy": self._senior_doctor_context_policy(),
        }
        return self._bounded_prompt_payload(
            snapshot,
            char_budget=profile["memory_budget"],
            text_limit=min(self.config.memory_prompt_text_limit, profile["preview_limit"]),
            list_limit=min(self.config.memory_prompt_list_limit, profile["list_limit"]),
            label=f"clinical_memory:{consumer}",
        )

    def _doctor_visible_longitudinal_memory_for_prompt(self) -> dict[str, Any]:
        if self.clinical_memory_snapshots:
            memory = self.clinical_memory_snapshots[-1].get("doctor_visible_memory")
            if isinstance(memory, dict):
                return self._bounded_prompt_payload(
                    memory,
                    char_budget=self.config.doctor_memory_prompt_char_budget,
                    text_limit=700,
                    list_limit=16,
                    label="doctor_visible_longitudinal_memory",
                )
        return self._bounded_prompt_payload(
            self._doctor_visible_memory_snapshot(),
            char_budget=self.config.doctor_memory_prompt_char_budget,
            text_limit=700,
            list_limit=16,
            label="doctor_visible_longitudinal_memory_fallback",
        )

    def _actor_lived_memory_for_prompt(self) -> dict[str, Any]:
        if self.clinical_memory_snapshots:
            memory = self.clinical_memory_snapshots[-1].get("actor_lived_memory")
            if isinstance(memory, dict):
                cleaned = self._redact_actor_backstage_payload(memory)
                payload = cleaned if isinstance(cleaned, dict) else self._actor_lived_memory_snapshot()
                return self._bounded_prompt_payload(
                    payload,
                    char_budget=self.config.actor_memory_prompt_char_budget,
                    text_limit=600,
                    list_limit=12,
                    label="actor_lived_memory",
                )
        return self._bounded_prompt_payload(
            self._actor_lived_memory_snapshot(),
            char_budget=self.config.actor_memory_prompt_char_budget,
            text_limit=600,
            list_limit=12,
            label="actor_lived_memory_fallback",
        )

    def _resolve_probability_tasks(self, turn: int, beat: DirectorBeat) -> list[dict[str, Any]]:
        if not beat.probability_tasks:
            return []
        records: list[dict[str, Any]] = []
        for task in beat.probability_tasks:
            task = self._estimate_probability_task(turn, task, beat)
            record = decide_probability_task(
                task,
                case_id=self.case.case_id,
                run_id=self.config.run_id,
                turn_id=f"T{turn:03d}",
                seed_scope=self.config.seed_scope,
            )
            records.append(record)
            self.trajectory.probability_ledger.append(record)
            self.trajectory.add_event(
                turn=turn,
                actor="ProbabilityKernel",
                event_type="probability_decision",
                content=record,
                sim_time=self.current_sim_time,
                visibility="internal_audit",
            )
        return records

    def _record_critical_safety_events(
        self,
        turn: int,
        beat: DirectorBeat | None,
        world_event_resolution: Mapping[str, Any] | None = None,
        *,
        source: str = "runtime",
    ) -> list[dict[str, Any]]:
        """Persist clinical safety problems as causal evidence, not stop rules."""

        candidates: list[dict[str, Any]] = []
        if beat is not None:
            for item in getattr(beat, "critical_safety_events", []) or []:
                if isinstance(item, Mapping):
                    candidates.append(dict(item))
            for item in getattr(beat, "committed_events", []) or []:
                payload = item.to_dict() if hasattr(item, "to_dict") else item
                if isinstance(payload, Mapping) and self._looks_like_critical_safety_event(payload):
                    candidates.append(dict(payload))
        if isinstance(world_event_resolution, Mapping):
            for item in world_event_resolution.get("committed_world_events") or []:
                if isinstance(item, Mapping) and self._looks_like_critical_safety_event(item):
                    candidates.append(dict(item))

        recorded: list[dict[str, Any]] = []
        seen: set[str] = set()
        for index, item in enumerate(candidates, start=1):
            event_id = str(item.get("event_id") or item.get("safety_event_id") or f"critical_safety_t{turn}_{index}").strip()
            if event_id in seen:
                continue
            seen.add(event_id)
            severity = str(item.get("severity") or item.get("risk_level") or item.get("clinical_severity") or "not_assessed").strip()
            payload = {
                "protocol": "careloop.critical_safety_event.v1",
                "event_id": event_id,
                "turn": turn,
                "source": source,
                "severity": severity,
                "domain": item.get("domain") or item.get("event_class") or item.get("category") or "clinical_safety",
                "doctor_action_or_omission": item.get("doctor_action_or_omission") or item.get("doctor_error") or item.get("doctor_move") or "",
                "patient_context": item.get("patient_context") or item.get("description") or item.get("summary") or item.get("title") or "",
                "why_unsafe": item.get("why_unsafe") or item.get("rationale") or item.get("safety_rationale") or "",
                "immediate_patient_effect": item.get("immediate_patient_effect") or item.get("consequences") or item.get("outcome") or "not_yet_resolved",
                "causal_confidence": item.get("causal_confidence") or item.get("confidence") or "not_assessed",
                "reversibility": item.get("reversibility") or "unknown",
                "world_effect_instruction": item.get("world_effect_instruction") or item.get("world_state_effect") or item.get("risk_state_update") or "Use as a causal risk modifier in later world progression; do not stop the simulation solely because this event exists.",
                "raw_event": item,
                "non_closure_principle": "Clinical safety failures are trajectory evidence and world-causal risk modifiers, not terminal closure or unsafe_stop by themselves.",
            }
            self.critical_safety_event_ledger.append(deepcopy(payload))
            self.trajectory.add_event(
                turn=turn,
                actor="SafetyEventLedger",
                event_type="critical_safety_event",
                content=payload,
                sim_time=self.current_sim_time,
                visibility="evaluator_visible",
                metadata={"source": source, "not_patient_visible": True, "not_doctor_visible": True},
            )
            recorded.append(payload)
        return recorded

    def _looks_like_critical_safety_event(self, item: Mapping[str, Any]) -> bool:
        metadata = item.get("metadata") if isinstance(item.get("metadata"), Mapping) else {}
        text = " ".join(str(item.get(key) or "") for key in ["event_type", "type", "event_class", "category", "severity", "title", "description"])
        text += " " + " ".join(str(metadata.get(key) or "") for key in ["event_type", "type", "event_class", "category", "severity"])
        lowered = text.lower()
        return any(token in lowered for token in ["critical_safety", "safety_event", "unsafe_failure", "medication_safety", "triage_failure"]) or any(
            token in text for token in ["严重安全", "安全事件", "危险医嘱", "医疗安全", "不安全建议", "漏诊高危"]
        )

    def _director_cut_is_clinical_safety_only(self, beat: DirectorBeat) -> bool:
        text = " ".join([str(beat.possible_closure or ""), str(beat.director_rationale or ""), " ".join(beat.cautions or [])])
        unsafe_signal = bool(re.search(r"unsafe|unsafe_stop|unsafe_failure|安全失败|不安全|危险|医疗事故", text, re.IGNORECASE))
        terminal_signal = bool(re.search(r"死亡|death|治愈|cured|durable|长期管理闭环|terminal_closed", text, re.IGNORECASE))
        return unsafe_signal and not terminal_signal

    def _convert_clinical_unsafe_closure_to_open(self, turn: int, closure: ClosureAssessmentLite, *, trigger: str) -> ClosureAssessmentLite:
        if closure.status != "unsafe_stop":
            return closure
        if closure.metadata.get("runtime_stop") or closure.metadata.get("trajectory_contaminated"):
            return closure
        safety_payload = {
            "event_id": f"closure_judge_unsafe_reframed_t{turn}",
            "severity": "not_assessed",
            "domain": closure.closure_kind or "closure_judge_reported_unsafe",
            "doctor_action_or_omission": "ClosureJudge described a clinical unsafe condition.",
            "patient_context": closure.rationale,
            "why_unsafe": closure.unsafe_stop_reason or closure.rationale,
            "causal_confidence": "closure_judge_signal",
            "world_effect_instruction": closure.if_continued_next_focus or "Continue simulation; let the patient/world consequences unfold and use this as evaluator evidence.",
        }
        self._record_critical_safety_events(turn, DirectorBeat(critical_safety_events=[safety_payload]), source=f"closure_judge:{trigger}")
        converted = replace(
            closure,
            status="open",
            closure_kind="open_with_critical_safety_event",
            unsafe_stop_reason="",
            metadata={
                **closure.metadata,
                "clinical_unsafe_stop_reframed": True,
                "original_status": "unsafe_stop",
                "principle": "Clinical safety failures continue as critical_safety_event evidence; only runtime invalid/contamination stops use unsafe_stop.",
            },
        )
        self.trajectory.add_event(
            turn=turn,
            actor="RuntimeLite",
            event_type="clinical_unsafe_stop_reframed",
            content={"original_closure": closure.to_dict(), "converted_closure": converted.to_dict()},
            sim_time=self.current_sim_time,
            visibility="evaluator_visible",
        )
        return converted

    def _handle_director_cut_request(
        self,
        turn: int,
        beat: DirectorBeat,
        world_event_resolution: dict[str, Any],
    ) -> ClosureAssessmentLite | None:
        if beat.should_continue:
            return None
        if self._director_cut_is_clinical_safety_only(beat):
            payload = {
                "event_id": f"director_cut_clinical_safety_reframed_t{turn}",
                "severity": "not_assessed",
                "domain": "world_director_cut_request_reframed",
                "doctor_action_or_omission": beat.doctor_move_read,
                "patient_context": beat.scene_summary or beat.next_world_beat,
                "why_unsafe": beat.director_rationale or beat.possible_closure,
                "causal_confidence": "world_director_signal",
                "world_effect_instruction": "Continue the simulation and let consequences, correction, deterioration, death, cure, or durable management unfold causally.",
            }
            self._record_critical_safety_events(turn, DirectorBeat(critical_safety_events=[payload]), world_event_resolution, source="world_director_cut_reframed")
            self.trajectory.add_event(
                turn=turn,
                actor="RuntimeLite",
                event_type="director_cut_reframed_as_critical_safety_event",
                content={
                    "possible_closure": beat.possible_closure,
                    "director_rationale": beat.director_rationale,
                    "principle": "Clinical unsafe conditions should continue as world/evaluator evidence, not stop the trajectory.",
                },
                sim_time=self.current_sim_time,
                visibility="evaluator_visible",
            )
            return None
        request = {
            "requested_by": "WorldDirector",
            "possible_closure": beat.possible_closure,
            "director_rationale": beat.director_rationale,
            "cautions": beat.cautions,
            "world_event_resolution": world_event_resolution,
            "supervision_required": "ClosureJudge must approve; WorldDirector cannot terminate alone.",
        }
        self.trajectory.add_event(
            turn=turn,
            actor="WorldDirector",
            event_type="director_cut_request",
            content=request,
            sim_time=self.current_sim_time,
            visibility="internal_audit",
        )
        closure = self._call_closure_judge(turn, trigger="director_cut_request")
        closure = self._convert_clinical_unsafe_closure_to_open(turn, closure, trigger="director_cut_request")
        closure = self._apply_stability_horizon_if_needed(turn, closure)
        closure = self._normalize_closure_kind_for_p03h(closure)
        closure = self._apply_case_level_terminality_gate(turn, closure, trigger="director_cut_request")
        if closure.status == "open":
            self.trajectory.add_event(
                turn=turn,
                actor="ClosureJudge",
                event_type="director_cut_rejected",
                content=closure.to_dict(),
                sim_time=self.current_sim_time,
                visibility="evaluator_visible",
            )
            return None
        self.trajectory.add_event(
            turn=turn,
            actor="ClosureJudge",
            event_type="director_cut_approved",
            content=closure.to_dict(),
            sim_time=self.current_sim_time,
            visibility="evaluator_visible",
        )
        return closure

    def _resolve_world_events_after_probability(
        self,
        turn: int,
        beat: DirectorBeat,
        probability_records: list[dict[str, Any]],
    ) -> dict[str, Any]:
        records_by_event_id = {
            str(record.get("event_id") or ""): record
            for record in probability_records
            if str(record.get("event_id") or "").strip()
        }
        attached_probability_event_ids: set[str] = set()
        committed_world_events: list[dict[str, Any]] = []
        non_occurred_event_candidates: list[dict[str, Any]] = []

        for event in beat.committed_events:
            payload = event.to_dict()
            task_id = event.probability_task.event_id if event.probability_task is not None else ""
            record = records_by_event_id.get(task_id) or records_by_event_id.get(event.event_id)
            explicit_candidate = str(event.status or "").lower() in {"candidate", "proposed", "uncertain", "not_committed"}
            if record is not None:
                attached_probability_event_ids.add(str(record.get("event_id") or ""))
                payload["probability_record"] = record
                payload["probability_occurred"] = bool(record.get("occurred"))
                if record.get("status") == "resolved" and bool(record.get("occurred")):
                    payload["status"] = "committed_after_probability_roll"
                    committed_world_events.append(payload)
                else:
                    payload["status"] = "not_committed_after_probability_roll"
                    payload["non_commit_reason"] = record.get("fail_closed_reason") or "probability_roll_did_not_occur"
                    non_occurred_event_candidates.append(payload)
                continue
            if explicit_candidate:
                payload["status"] = "not_committed_candidate"
                payload["non_commit_reason"] = "director_marked_as_candidate_without_probability_resolution"
                non_occurred_event_candidates.append(payload)
            else:
                payload["status"] = "committed_by_director"
                committed_world_events.append(payload)

        for record in probability_records:
            event_id = str(record.get("event_id") or "")
            if event_id in attached_probability_event_ids:
                continue
            synthetic = {
                "event_id": event_id,
                "title": record.get("event_type") or "probabilistic_world_fact",
                "description": record.get("rationale") or "Probability task without an attached event candidate.",
                "probability_record": record,
                "probability_occurred": bool(record.get("occurred")),
                "source": "probability_task_without_event_candidate",
            }
            if not event_id:
                synthetic["status"] = "not_committed_probability_task_missing_event_id"
                synthetic["non_commit_reason"] = (
                    "probability_task_missing_event_id_or_event_candidate; "
                    "an occurrence roll without a concrete world_event shell is audit-only and cannot become actor-lived reality"
                )
                synthetic["audit_only"] = True
                non_occurred_event_candidates.append(synthetic)
            elif record.get("status") == "resolved" and bool(record.get("occurred")) and record.get("creates_world_fact"):
                synthetic["status"] = "committed_after_probability_roll"
                committed_world_events.append(synthetic)
            else:
                synthetic["status"] = "not_committed_after_probability_roll"
                synthetic["non_commit_reason"] = record.get("fail_closed_reason") or "probability_roll_did_not_occur"
                non_occurred_event_candidates.append(synthetic)

        resolution = {
            "committed_world_events": committed_world_events,
            "non_occurred_event_candidates": non_occurred_event_candidates,
            "probability_records": probability_records,
            "authoritative_for_actor_situation": True,
            "principle": "Only committed_world_events may be treated as actor-lived reality; non_occurred_event_candidates are audit/context only.",
        }
        self.trajectory.add_event(
            turn=turn,
            actor="RuntimeLite",
            event_type="world_event_resolution",
            content=resolution,
            sim_time=self.current_sim_time,
            visibility="internal_audit",
        )
        return resolution

    def _apply_workspace_updates_from_world_events(
        self,
        turn: int,
        world_event_resolution: dict[str, Any],
    ) -> list[dict[str, Any]]:
        committed = world_event_resolution.get("committed_world_events") or []
        if not isinstance(committed, list):
            return []
        updates = self.workspace.apply_world_events([item for item in committed if isinstance(item, dict)])
        if updates:
            self.trajectory.add_event(
                turn=turn,
                actor="clinical_workspace",
                event_type="workspace_state_update",
                content={
                    "updates": updates,
                    "uploaded_record_ids": sorted(self.workspace.uploaded_record_ids),
                    "authorized_record_ids": sorted(self.workspace.authorized_record_ids),
                    "imported_record_ids": sorted(self.workspace.imported_record_ids),
                },
                sim_time=self.current_sim_time,
                visibility="internal_audit",
            )
        return updates

    def _apply_care_system_updates_from_world_events(
        self,
        turn: int,
        world_event_resolution: dict[str, Any],
    ) -> list[dict[str, Any]]:
        committed = world_event_resolution.get("committed_world_events") or []
        if not isinstance(committed, list):
            return []
        updates = self.care_system.apply_world_events(
            [item for item in committed if isinstance(item, dict)],
            current_sim_time=self.current_sim_time,
            turn=turn,
        )
        workspace_records_created = self.workspace.ingest_care_system_updates(updates)
        if updates:
            self.trajectory.add_event(
                turn=turn,
                actor="care_system",
                event_type="care_system_state_update",
                content={
                    "updates": updates,
                    "workspace_records_created": workspace_records_created,
                    "care_system_state": self.care_system.to_dict(),
                },
                sim_time=self.current_sim_time,
                visibility="internal_audit",
            )
        return updates

    def _publish_doctor_visible_world_notifications(
        self,
        turn: int,
        world_event_resolution: dict[str, Any],
    ) -> list[dict[str, Any]]:
        committed = world_event_resolution.get("committed_world_events") or []
        if not isinstance(committed, list):
            return []
        notifications: list[dict[str, Any]] = []
        for event in committed:
            if not isinstance(event, dict) or not self._world_event_visible_to_doctor(event):
                continue
            metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
            summary = str(
                metadata.get("doctor_visible_summary")
                or metadata.get("doctor_notification")
                or metadata.get("doctor_visible_text")
                or event.get("title")
                or "有一条新的医生侧系统通知。"
            ).strip()
            title = self._redact_doctor_visible_runtime_text(str(event.get("title") or "医生侧系统通知").strip())
            summary = self._redact_doctor_visible_runtime_text(summary)
            if not title:
                title = "医生侧系统通知"
            if not summary:
                summary = "有一条新的系统状态更新，请结合当前患者情况继续询问或处理。"
            notification = {
                "title": title,
                "summary": summary,
                "sim_time": self.current_sim_time,
                "source": "committed_world_event_marked_visible_to_doctor",
            }
            notifications.append(notification)
            self.trajectory.add_event(
                turn=turn,
                actor="RuntimeLite",
                event_type="doctor_system_notification",
                content=notification,
                sim_time=self.current_sim_time,
                visibility="doctor_visible_tool",
                metadata={
                    "internal_event_id": event.get("event_id") or "",
                    "provenance": visibility_provenance(
                        route="system_notification_route",
                        source_type="system_notification",
                        visibility="doctor_visible",
                        reliability="committed_world_event_marked_doctor_visible",
                    ),
                    "internal_principle": "Doctor-visible world notification; hidden/backstage fields are retained only in metadata, not in doctor-visible content.",
                },
            )
        return notifications

    def _world_event_visible_to_doctor(self, event: dict[str, Any]) -> bool:
        metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        return bool(
            event.get("visible_to_doctor")
            or metadata.get("visible_to_doctor")
            or metadata.get("doctor_visible")
            or metadata.get("doctor_visible_summary")
            or metadata.get("doctor_notification")
            or metadata.get("doctor_visible_text")
        )

    def _redact_doctor_visible_runtime_text(self, value: str) -> str:
        """Remove obvious runtime/backstage contamination from doctor-visible notices."""

        text = " ".join(str(value or "").split()).strip()
        if not text:
            return ""
        if any(token in text for token in self._doctor_visible_runtime_drop_terms()):
            return ""
        cleaned = text
        for token in self._doctor_visible_runtime_terms():
            cleaned = cleaned.replace(token, "")
        cleaned = " ".join(cleaned.split()).strip()
        if any(token in cleaned for token in self._doctor_visible_runtime_terms()):
            return ""
        return cleaned

    def _doctor_visible_runtime_terms(self) -> list[str]:
        return [
            "WorldDirector",
            "ClinicalMemorySteward",
            "Timekeeper",
            "ActorSituation",
            "ProbabilityKernel",
            "TrajectoryEvaluator",
            "critical_safety_event",
            "SafetyEventLedger",
            "clinical_memory",
            "memory_integrity",
            "runtime_pending",
            "critical_safety_event",
            "SafetyEventLedger",
            "benchmark",
            "roleplay",
            "角色扮演",
            "hidden",
            "隐藏",
            "backstage",
            "后台",
            "导演",
            "掷骰",
            "概率任务",
            "概率过程",
            "runtime_lite",
            "next_world_need",
            "director_rationale",
        ]

    def _doctor_visible_runtime_drop_terms(self) -> list[str]:
        return [
            "hidden",
            "ClinicalMemorySteward",
            "clinical_memory",
            "memory_integrity",
            "runtime_pending",
            "隐藏",
            "benchmark",
            "roleplay",
            "角色扮演",
            "ProbabilityKernel",
            "掷骰",
            "概率任务",
            "概率过程",
            "runtime_lite",
            "next_world_need",
            "director_rationale",
            "backstage",
            "后台",
        ]

    def _estimate_probability_task(
        self,
        turn: int,
        task: LiteProbabilityTask,
        beat: DirectorBeat,
    ) -> LiteProbabilityTask:
        runtime_modes = {"runtime_llm", "runtime_estimated", "dynamic", "case_contextual", "llm_preferred"}
        if task.probability is not None or task.probability_mode not in runtime_modes:
            return task
        system = load_prompt("probability_estimator")
        user = json.dumps(
            {
                "task": task.to_dict(),
                "director_beat": beat.to_dict(),
                "case_context_full_for_probability_estimation": self._case_context_for_prompt("probability_estimator"),
                "care_system_state": self._care_system_state_for_prompt("probability_estimator"),
                "clinical_memory_backstage": self._clinical_memory_backstage_for_prompt("probability_estimator"),
                "special_event_opportunity_context": self._special_event_opportunity_context("probability_estimator"),
                "trajectory_so_far": self._trajectory_context_for_prompt("probability_estimator"),
            },
            ensure_ascii=False,
            indent=2,
        )
        raw = self._complete(
            self.simulator_llm,
            purpose="probability_estimator",
            system=system,
            user=user,
            temperature=0.2,
            turn=turn,
        )
        parsed = extract_json_object(raw)
        p, probability_parse_metadata = self._coerce_probability_estimate(parsed, raw)
        estimated = replace(
            task,
            probability=p,
            descriptor=str(parsed.get("descriptor") or task.descriptor) if parsed else task.descriptor,
            basis=str(parsed.get("basis") or parsed.get("rationale") or task.basis) if parsed else task.basis,
            factors=[item for item in (parsed.get("factors") or task.factors) if isinstance(item, dict)] if parsed else task.factors,
            metadata={
                **task.metadata,
                "probability_estimator_raw": raw,
                "probability_estimator_used": True,
                **probability_parse_metadata,
            },
        )
        self.trajectory.add_event(
            turn=turn,
            actor="ProbabilityEstimator",
            event_type="probability_estimate",
            content=estimated.to_dict(),
            sim_time=self.current_sim_time,
            visibility="internal_audit",
        )
        return estimated

    def _coerce_probability_estimate(self, parsed: dict[str, Any], raw: str) -> tuple[float | None, dict[str, Any]]:
        metadata: dict[str, Any] = {}
        probability_value = parsed.get("probability", parsed.get("estimated_probability")) if parsed else None
        p, source = self._probability_from_value(probability_value)
        if p is not None:
            metadata["probability_estimator_probability_source"] = source
            return p, metadata
        p = self._probability_from_text(raw)
        if p is not None:
            metadata["probability_estimator_probability_source"] = "prose_text"
            metadata["probability_estimator_parsed_from_text"] = True
            return p, metadata
        metadata["probability_estimator_missing_concrete_probability"] = True
        return None, metadata

    def _probability_from_value(self, value: Any) -> tuple[float | None, str]:
        if isinstance(value, bool) or value is None:
            return None, ""
        if isinstance(value, (int, float)):
            numeric = float(value)
            if 0.0 <= numeric <= 1.0:
                return numeric, "json_number_0_to_1"
            if 1.0 < numeric <= 100.0:
                return numeric / 100.0, "json_number_percent_scale"
            return None, ""
        text = str(value).strip()
        if not text:
            return None, ""
        percent = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
        if percent:
            return max(0.0, min(1.0, float(percent.group(1)) / 100.0)), "json_string_percent"
        try:
            numeric = float(text)
        except ValueError:
            parsed = self._probability_from_text(text)
            return (parsed, "json_string_probability_phrase") if parsed is not None else (None, "")
        if 0.0 <= numeric <= 1.0:
            return numeric, "json_string_0_to_1"
        if 1.0 < numeric <= 100.0:
            return numeric / 100.0, "json_string_percent_scale"
        return None, ""

    def _probability_from_text(self, text: str) -> float | None:
        source = str(text or "")
        if not source.strip():
            return None
        percent = re.search(r"(\d+(?:\.\d+)?)\s*%", source)
        if percent:
            return max(0.0, min(1.0, float(percent.group(1)) / 100.0))
        decimal = re.search(r"(?<!\d)(0?\.\d+|1(?:\.0+)?)(?!\d)", source)
        if decimal:
            return max(0.0, min(1.0, float(decimal.group(1))))
        if "一半" in source or "五成" in source:
            return 0.5
        chinese_digit = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
        range_match = re.search(r"([一二两三四五六七八九])([一二两三四五六七八九])成", source)
        if range_match:
            low = chinese_digit[range_match.group(1)]
            high = chinese_digit[range_match.group(2)]
            return max(0.0, min(1.0, ((low + high) / 2.0) / 10.0))
        single_match = re.search(r"([一二两三四五六七八九])成", source)
        if single_match:
            return max(0.0, min(1.0, chinese_digit[single_match.group(1)] / 10.0))
        return None

    def _call_timekeeper(
        self,
        turn: int,
        doctor_text: str,
        beat: DirectorBeat,
        probability_records: list[dict[str, Any]],
        world_event_resolution: dict[str, Any],
    ) -> dict[str, Any]:
        system = load_prompt("timekeeper")
        user = json.dumps(
            {
                "case_temporal_material": self.case.temporal_contract,
                "current_sim_time": self.current_sim_time,
                "care_system_state": self._care_system_state_for_prompt("timekeeper"),
                "living_state_memory": self._living_state_memory_for_prompt(),
                "clinical_memory_backstage": self._clinical_memory_backstage_for_prompt("timekeeper"),
                "fcct2_runtime_context": self._fcct2_runtime_context_for_prompt("timekeeper"),
                "source_evidence_pack": self._source_anchor_evidence_pack("timekeeper"),
                "latest_doctor_message": doctor_text,
                "director_beat": beat.to_dict(),
                "director_time_requests": [request.to_dict() for request in beat.time_requests],
                "tempo_precondition_context": self._tempo_precondition_context_for_prompt("timekeeper"),
                "clinical_node_tempo_context": self._clinical_node_tempo_context_for_prompt("timekeeper"),
                "active_waiting_ceiling_context": self._active_waiting_ceiling_context_for_prompt("timekeeper"),
                "case_specific_closure_residual_context": self._case_specific_closure_residual_context_for_prompt("timekeeper"),
                "episode_scope_context": self._episode_scope_context_for_prompt("timekeeper"),
                "high_impact_event_lifecycle_context": self._high_impact_event_lifecycle_context_for_prompt("timekeeper"),
                "lightweight_receipt_context": self._lightweight_receipt_context_for_prompt("timekeeper"),
                "caregiver_reliability_context": self._caregiver_reliability_context_for_prompt("timekeeper"),
                "repetition_compression_context": self._repetition_compression_context_for_prompt("timekeeper"),
                "high_risk_medication_ob_safety_context": self._high_risk_medication_and_ob_safety_context_for_prompt("timekeeper"),
                "anti_hardening_context": self._anti_hardening_context_for_prompt("timekeeper"),
                "probability_records": probability_records,
                "world_event_resolution_after_probability": world_event_resolution,
                "recent_transcript": self.trajectory.transcript[-8:],
            },
            ensure_ascii=False,
            indent=2,
        )
        raw = self._complete(
            self.simulator_llm,
            purpose="timekeeper",
            system=system,
            user=user,
            temperature=0.3,
            turn=turn,
        )
        parsed = extract_json_object(raw)
        if parsed:
            elapsed_minutes = self._coerce_timekeeper_elapsed_minutes(parsed, raw)
        else:
            parsed_minutes = self._duration_minutes_from_text(raw)
            elapsed_minutes = parsed_minutes if parsed_minutes is not None else 5.0
            parsed = {
                "elapsed_minutes": elapsed_minutes,
                "visible_time_phrase": self._compact_text(raw) or "过了一会儿",
                "rationale": raw,
                "parsed_from_prose": parsed_minutes is not None,
                "elapsed_defaulted": parsed_minutes is None,
            }
        parsed["elapsed_minutes"] = elapsed_minutes
        self.current_sim_time = f"T+{self._current_minutes() + elapsed_minutes:.0f}min"
        parsed["sim_time_after"] = self.current_sim_time
        self.trajectory.add_event(
            turn=turn,
            actor="Timekeeper",
            event_type="time_advance",
            content=parsed,
            sim_time=self.current_sim_time,
            visibility="internal_full",
        )
        return parsed

    def _coerce_timekeeper_elapsed_minutes(self, parsed: dict[str, Any], raw: str) -> float:
        elapsed = parsed.get("elapsed_minutes")
        try:
            return max(0.0, float(elapsed))
        except (TypeError, ValueError):
            pass
        for key, multiplier in (("elapsed_hours", 60.0), ("hours", 60.0), ("elapsed_days", 1440.0), ("days", 1440.0)):
            try:
                return max(0.0, float(parsed.get(key)) * multiplier)
            except (TypeError, ValueError):
                continue
        parsed_minutes = self._duration_minutes_from_text(
            " ".join(str(parsed.get(key) or "") for key in ("visible_time_phrase", "scene_time_explanation", "rationale"))
            + " "
            + str(raw or "")
        )
        if parsed_minutes is not None:
            parsed["parsed_elapsed_from_text"] = True
            return parsed_minutes
        parsed["elapsed_defaulted"] = True
        return 5.0

    def _duration_minutes_from_text(self, text: str) -> float | None:
        source = str(text or "")
        if not source.strip():
            return None
        total = 0.0
        matched = False
        for number, unit in re.findall(r"(\d+(?:\.\d+)?)\s*(?:个)?\s*(分钟|分|小时|钟头|天|日|周|星期)", source):
            total += self._minutes_for_unit(float(number), unit)
            matched = True
        chinese_number = r"[零一二两三四五六七八九十百几]+(?:多|来)?(?:个)?"
        for number, unit in re.findall(rf"({chinese_number})\s*(分钟|分|小时|钟头|天|日|周|星期)", source):
            value = self._chinese_duration_number(number, unit)
            if value is None:
                continue
            total += self._minutes_for_unit(value, unit)
            matched = True
        if "半小时" in source or "半个小时" in source:
            total += 30.0
            matched = True
        if "一刻钟" in source:
            total += 15.0
            matched = True
        if "半天" in source:
            total += 720.0
            matched = True
        if matched:
            return max(0.0, total)
        if any(token in source for token in ["一会儿", "一会", "片刻", "马上", "稍等"]):
            return 5.0
        return None

    def _maybe_call_actor_realism_controller_v2(
        self,
        turn: int,
        doctor_text: str,
        beat: DirectorBeat,
        world_event_resolution: dict[str, Any],
    ) -> dict[str, Any]:
        """Ask an LLM for actor-local realism guidance without hard scripting.

        This v2 controller is a softer successor to the original realism degrader:
        it should preserve plausible cooperation when earned, while preventing the
        patient/family actor from becoming an omniscient, perfectly compliant test
        harness.  The output is hidden actor guidance only.
        """

        if not self.config.enable_actor_realism_controller_v2:
            return self.actor_realism_v2_ledger[-1] if self.actor_realism_v2_ledger else {}
        interval = max(1, int(self.config.actor_realism_v2_check_interval_turns or 1))
        if self.actor_realism_v2_ledger and turn % interval != 0:
            return self.actor_realism_v2_ledger[-1]
        system = load_prompt("actor_realism_controller_v2")
        user = json.dumps(
            {
                "case_actor_profiles": self.case.actor_profiles,
                "patient_voice_profile": self.case.patient_voice_profile,
                "case_context": self._case_context_for_prompt("actor_realism_controller_v2"),
                "current_sim_time": self.current_sim_time,
                "latest_doctor_message": doctor_text,
                "director_actor_hand_off": self._actor_messenger_director_hand_off(beat),
                "world_event_resolution": self._redact_actor_backstage_payload(world_event_resolution),
                "recent_transcript": self.trajectory.transcript[-18:],
                "care_system_state": self._care_system_state_for_prompt("actor_realism_controller_v2"),
                "prior_v1_realism_state": self.actor_realism_ledger[-3:],
                "previous_controller_v2_advisories": self.actor_realism_v2_ledger[-4:],
                "principle": (
                    "Return actor-local posture guidance only. The actor may cooperate, hesitate, misunderstand, delay, "
                    "forget, need family confirmation, face cost/access limits, or report imperfect execution when clinically plausible. "
                    "Do not make them adversarial by default and never reveal runtime/evaluation machinery."
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
        raw = self._complete(
            self.simulator_llm,
            purpose="actor_realism_controller_v2",
            system=system,
            user=user,
            temperature=0.3,
            turn=turn,
        )
        parsed = extract_json_object(raw) or {"raw_advisory": raw}
        parsed.setdefault("realism_posture", "case_dependent")
        parsed.setdefault("information_boundary", "answer progressively based on actor knowledge")
        parsed.setdefault("friction_style", "none_forced")
        parsed.setdefault("actor_instruction_patch", [])
        parsed["turn"] = turn
        parsed["sim_time"] = self.current_sim_time
        parsed["advisory_only"] = True
        self.actor_realism_v2_ledger.append(deepcopy(parsed))
        self.trajectory.add_event(
            turn=turn,
            actor="ActorRealismControllerV2",
            event_type="actor_realism_v2_advisory",
            content=parsed,
            sim_time=self.current_sim_time,
            visibility="internal_actor_local",
        )
        return parsed

    def _maybe_call_actor_realism_degrader(
        self,
        turn: int,
        doctor_text: str,
        beat: DirectorBeat,
        world_event_resolution: dict[str, Any],
    ) -> dict[str, Any]:
        """Ask an LLM how realistic cooperation/execution should evolve.

        The result is actor-local guidance, not a hard behaviour rule.  It helps
        avoid every patient/family member becoming a perfect medical assistant,
        while still allowing high-cooperation families when the case supports it.
        """

        if not self.config.enable_actor_realism_degrader:
            return {}
        interval = max(1, int(self.config.actor_realism_check_interval_turns or 1))
        if self.actor_realism_ledger and turn % interval != 0:
            return self.actor_realism_ledger[-1]
        system = load_prompt("actor_realism_degrader")
        user = json.dumps(
            {
                "case_actor_profiles": self.case.actor_profiles,
                "patient_voice_profile": self.case.patient_voice_profile,
                "case_context": self._case_context_for_prompt("actor_realism_degrader"),
                "current_sim_time": self.current_sim_time,
                "latest_doctor_message": doctor_text,
                "director_beat": beat.to_dict(),
                "world_event_resolution": world_event_resolution,
                "recent_transcript": self.trajectory.transcript[-18:],
                "care_system_state": self._care_system_state_for_prompt("actor_realism_degrader"),
                "previous_actor_realism_state": self.actor_realism_ledger[-4:],
                "document_reliability_context": self._document_reliability_context_for_prompt("actor_realism_degrader"),
                "friction_lifecycle_context": self._friction_lifecycle_context_for_prompt("actor_realism_degrader"),
                "clinical_node_tempo_context": self._clinical_node_tempo_context_for_prompt("actor_realism_degrader"),
                "principle": (
                    "患者/家属可以合作，也可以忘记、误解、隐瞒、疲劳、掉线、经济拒绝或执行错。"
                    "请根据人物画像、医生沟通质量、病情压力和现实约束给出自然倾向；不要强行制造对抗。"
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
        raw = self._complete(
            self.simulator_llm,
            purpose="actor_realism_degrader",
            system=system,
            user=user,
            temperature=0.35,
            turn=turn,
        )
        parsed = extract_json_object(raw) or {"raw_realism_judgement": raw}
        parsed.setdefault("cooperation_tendency", "case_dependent")
        parsed.setdefault("actor_speaking_guidance", [])
        parsed["turn"] = turn
        parsed["sim_time"] = self.current_sim_time
        self.actor_realism_ledger.append(deepcopy(parsed))
        self.trajectory.add_event(
            turn=turn,
            actor="ActorRealismDegrader",
            event_type="actor_realism_judgement",
            content=parsed,
            sim_time=self.current_sim_time,
            visibility="internal_actor_local",
        )
        return parsed

    def _actor_realism_context_for_prompt(self, consumer: str) -> dict[str, Any]:
        latest = self.actor_realism_ledger[-1] if self.actor_realism_ledger else {}
        latest_v2 = self.actor_realism_v2_ledger[-1] if self.actor_realism_v2_ledger else {}
        payload = {
            "protocol": "careloop.runtime_lite.actor_realism_context.v2",
            "consumer": consumer,
            "visibility": "actor_lived_guidance_not_scoring_or_runtime_language",
            "latest_realism_state": latest,
            "recent_realism_tail": self.actor_realism_ledger[-3:],
            "latest_controller_v2_advisory": latest_v2,
            "recent_controller_v2_tail": self.actor_realism_v2_ledger[-3:],
            "principle": (
                "Use this only to make the patient/family response more human: partial understanding, fatigue, trust shifts, "
                "wrong execution, delay, concealment, or high cooperation when truly plausible. Do not mention this mechanism."
            ),
        }
        return self._bounded_prompt_payload(
            payload,
            char_budget=3500,
            text_limit=260,
            list_limit=8,
            label=f"actor_realism_context:{consumer}",
        )

    def _minutes_for_unit(self, value: float, unit: str) -> float:
        if unit in {"分钟", "分"}:
            return value
        if unit in {"小时", "钟头"}:
            return value * 60.0
        if unit in {"天", "日"}:
            return value * 1440.0
        if unit in {"周", "星期"}:
            return value * 10080.0
        return value

    def _chinese_duration_number(self, raw: str, unit: str) -> float | None:
        token = str(raw or "").replace("个", "").replace("来", "").replace("多", "")
        if "几" in token:
            return 5.0 if unit in {"分钟", "分"} else 2.0
        digits = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
        if not token:
            return None
        if token == "十":
            return 10.0
        if "百" in token:
            left, _, right = token.partition("百")
            hundreds = digits.get(left, 1 if left == "" else 0)
            remainder = self._chinese_duration_number(right, unit) if right else 0.0
            return float(hundreds * 100 + (remainder or 0.0))
        if "十" in token:
            left, _, right = token.partition("十")
            tens = digits.get(left, 1 if left == "" else 0)
            ones = digits.get(right, 0) if right else 0
            return float(tens * 10 + ones)
        if token in digits:
            return float(digits[token])
        return None

    def _compact_text(self, value: Any, limit: int = 80) -> str:
        text = " ".join(str(value or "").split())
        if len(text) <= limit:
            return text
        return text[: limit - 13].rstrip() + " ...[truncated]"

    def _current_minutes(self) -> float:
        if self.current_sim_time.startswith("T+") and self.current_sim_time.endswith("min"):
            try:
                return float(self.current_sim_time[2:-3])
            except ValueError:
                return 0.0
        return 0.0

    def _call_actor_situation_messenger(
        self,
        turn: int,
        doctor_text: str,
        beat: DirectorBeat,
        time_summary: dict[str, Any],
        probability_records: list[dict[str, Any]],
        world_event_resolution: dict[str, Any],
    ) -> ActorSituation:
        system = load_prompt("actor_situation_messenger")
        user = json.dumps(
            {
                "actor_focus": beat.actor_focus,
                "actor_focus_identity": self.case.actor_identity(beat.actor_focus, beat.actor_focus),
                "case_actor_profiles": self._redact_actor_backstage_payload(self.case.actor_profiles),
                "patient_voice_profile": self._redact_actor_backstage_payload(self.case.patient_voice_profile),
                "latest_doctor_message": doctor_text,
                "director_actor_hand_off": self._actor_messenger_director_hand_off(beat),
                "time_summary": time_summary,
                "actor_lived_world_packet": self._actor_lived_world_packet(world_event_resolution),
                "actor_information_boundary": self._actor_information_boundary_packet(turn, doctor_text, beat, world_event_resolution),
                "actor_lived_continuity_notes": self._actor_lived_continuity_notes(),
                "actor_lived_memory": self._actor_lived_memory_for_prompt(),
                "actor_realism_context": self._actor_realism_context_for_prompt("actor_situation_messenger"),
                "document_reliability_context": self._document_reliability_context_for_prompt("actor_situation_messenger"),
                "friction_lifecycle_context": self._friction_lifecycle_context_for_prompt("actor_situation_messenger"),
                "clinical_node_tempo_context": self._clinical_node_tempo_context_for_prompt("actor_situation_messenger"),
                "active_waiting_ceiling_context": self._active_waiting_ceiling_context_for_prompt("actor_situation_messenger"),
                "case_specific_closure_residual_context": self._case_specific_closure_residual_context_for_prompt("actor_situation_messenger"),
                "episode_scope_context": self._episode_scope_context_for_prompt("actor_situation_messenger"),
                "high_impact_event_lifecycle_context": self._high_impact_event_lifecycle_context_for_prompt("actor_situation_messenger"),
                "lightweight_receipt_context": self._lightweight_receipt_context_for_prompt("actor_situation_messenger"),
                "caregiver_reliability_context": self._caregiver_reliability_context_for_prompt("actor_situation_messenger"),
                "repetition_compression_context": self._repetition_compression_context_for_prompt("actor_situation_messenger"),
                "high_risk_medication_ob_safety_context": self._high_risk_medication_and_ob_safety_context_for_prompt("actor_situation_messenger"),
                "anti_hardening_context": self._anti_hardening_context_for_prompt("actor_situation_messenger"),
                "recent_transcript": self.trajectory.transcript[-8:],
            },
            ensure_ascii=False,
            indent=2,
        )
        raw = self._complete(
            self.simulator_llm,
            purpose="actor_situation_messenger",
            system=system,
            user=user,
            temperature=0.4,
            turn=turn,
        )
        parsed = extract_json_object(raw)
        situation = ActorSituation.from_mapping(parsed) if parsed else ActorSituation(
            actor_id=beat.actor_focus or "patient",
            actor_role=beat.actor_focus or "patient",
            scene=raw,
            immediate_goal=beat.actor_situation_goal,
        )
        identity = self.case.actor_identity(situation.actor_id, situation.actor_role)
        if not situation.speaker_display:
            situation.speaker_display = identity["display"]
        if not situation.speaker_category:
            situation.speaker_category = identity["speaker_category"]
        if not situation.relationship_to_patient:
            situation.relationship_to_patient = identity["relationship_to_patient"]
        self.trajectory.add_event(
            turn=turn,
            actor="ActorSituationMessenger",
            event_type="actor_situation",
            content=situation.to_dict(),
            sim_time=self.current_sim_time,
            visibility="internal_actor_local",
        )
        return situation

    def _actor_messenger_director_hand_off(self, beat: DirectorBeat) -> dict[str, Any]:
        """Return the director material the actor-situation layer actually needs.

        The messenger is a hand-off layer, not an audit reader.  It should see
        the director's lived-scene intention and the next actor focus, but not
        raw probability tasks, candidate events, metadata patches, scoring
        hints, or backend reasoning.  Those remain in the internal trajectory
        for replay/evaluation.
        """

        hand_off = {
            "scene_summary": beat.scene_summary,
            "doctor_move_read": beat.doctor_move_read,
            "next_world_beat": beat.next_world_beat,
            "actor_focus": beat.actor_focus,
            "actor_situation_goal": beat.actor_situation_goal,
        }
        return self._redact_actor_backstage_payload(hand_off)

    def _actor_information_boundary_packet(
        self,
        turn: int,
        doctor_text: str,
        beat: DirectorBeat,
        world_event_resolution: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Thin actor-knowledge boundary before ActorSituationMessenger.

        This is not a deterministic clinical rule.  It gives the LLM messenger a
        compact provenance contract: patient/family actors may disclose what
        they plausibly know, may withhold or distort sensitive facts, and must
        not receive evaluator-only/backstage truth.
        """

        actor_id = str(beat.actor_focus or "patient")
        contract = self.case.actor_knowledge_contract if isinstance(self.case.actor_knowledge_contract, Mapping) else {}
        actor_contract = {}
        for key in [actor_id, self.case.actor_identity(actor_id, actor_id).get("relationship_to_patient"), "default"]:
            if key and isinstance(contract.get(key), Mapping):
                actor_contract = dict(contract.get(key) or {})
                break
        payload = {
            "protocol": "careloop.actor_information_boundary.v1",
            "turn": turn,
            "actor_focus": actor_id,
            "principle": (
                "Actors may reveal facts they plausibly know through lived experience, reports, family discussion, or prior doctor explanations. "
                "Sensitive/private facts may be withheld, distorted, or disclosed gradually. Evaluator-only, backstage, probability, scoring, and hidden truth not known by this actor must not enter actor speech."
            ),
            "natural_disclosure_is_not_leakage": True,
            "leakage_definition": {
                "system_or_workspace_hidden_truth_to_doctor": "leak",
                "actor_reveals_plausibly_known_fact": "not_leak",
                "actor_reveals_backstage_or_unplausibly_known_hidden_truth": "actor_mediated_leak",
            },
            "actor_authored_knowledge_contract": actor_contract,
            "known_by_contract_available": bool(actor_contract),
            "latest_doctor_message_preview": self._compact_text(doctor_text, limit=500),
            "recent_critical_safety_events_for_backstage_continuity": [
                {
                    "turn": item.get("turn"),
                    "severity": item.get("severity"),
                    "domain": item.get("domain"),
                    "patient_context": self._compact_text(item.get("patient_context"), limit=220),
                    "actor_visibility_note": "Do not reveal this ledger label; only translate patient/family-lived consequences when they occur and are actor-visible.",
                }
                for item in self.critical_safety_event_ledger[-3:]
            ],
            "must_not_expose_to_actor_as_labels": [
                "hidden_simulation_state",
                "root_truth",
                "evaluation_contract",
                "false_closure_traps",
                "critical_safety_event label",
                "probability roll",
                "scoring rubric",
                "WorldDirector reasoning",
            ],
        }
        cleaned = redact_for_actor(payload)
        return cleaned if isinstance(cleaned, dict) else {}

    def _actor_lived_world_packet(self, world_event_resolution: dict[str, Any]) -> dict[str, Any]:
        """Return only committed, actor-lived world facts for the messenger.

        Probability ledgers and non-occurred candidates are crucial audit
        evidence, but they are not part of the patient/family lived world.
        Keeping them out of ActorSituationMessenger reduces backstage leakage
        and lets that subagent work like a real scene translator.
        """

        committed = world_event_resolution.get("committed_world_events") or []
        actor_events: list[dict[str, Any]] = []
        for item in committed:
            if not isinstance(item, dict):
                continue
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            if item.get("visible_to_patient_or_family") is False or metadata.get("visible_to_patient_or_family") is False:
                continue
            if metadata.get("actor_visible") is False or metadata.get("silent_world_fact") is True or metadata.get("backstage_only") is True:
                continue
            event = {
                "event_id": item.get("event_id"),
                "title": item.get("title"),
                "description": item.get("description"),
                "status": item.get("status"),
                "affected_actors": item.get("affected_actors"),
                "consequences": item.get("consequences"),
            }
            visible_time_hint = self._actor_visible_time_hint_from_event(item)
            if visible_time_hint:
                event["time_hint"] = visible_time_hint
            cleaned = self._redact_actor_backstage_payload(event)
            if isinstance(cleaned, dict) and cleaned:
                actor_events.append(cleaned)
        return {
            "committed_lived_events": actor_events,
            "principle": "Only these committed events may enter the patient/family lived situation.",
        }

    def _actor_lived_continuity_notes(self) -> dict[str, Any]:
        """Return sanitized continuity memory for the actor-situation handoff.

        The raw living_state_memory is available to internal director/time
        workers.  The actor-situation layer only receives a cleaned continuity
        hint so it can keep scene continuity without seeing hidden truth,
        probability process, scoring, or runtime audit language.
        """

        recent = self.living_state_memory[-5:]
        cleaned = self._redact_actor_backstage_payload(recent)
        if not isinstance(cleaned, list):
            cleaned = []
        return {
            "recent_notes": cleaned,
            "principle": "Continuity hints only; actor_lived_world_packet is the authority for newly occurred facts.",
        }

    def _actor_visible_time_hint_from_event(self, event: dict[str, Any]) -> dict[str, Any]:
        raw = event.get("time_request")
        if not isinstance(raw, dict):
            return {}
        hint = {
            "reason": raw.get("reason"),
            "scene_change": raw.get("scene_change"),
            "urgency": raw.get("urgency"),
        }
        cleaned = self._redact_actor_backstage_payload(hint)
        return cleaned if isinstance(cleaned, dict) else {}

    def _call_actor_cooperation_sampler(self, turn: int, doctor_text: str, situation: ActorSituation) -> dict[str, Any]:
        """LLM-led cooperation envelope + deterministic dice sample for this actor turn.

        This is actor-local lived guidance.  It is not shown to the tested doctor
        or patient, and it is not a hard behavioural rule.  The LLM judges what
        range of cooperation is plausible; the runtime then samples within that
        range to avoid a permanently perfect patient/family secretary.
        """

        if not bool(getattr(self.config, "enable_actor_cooperation_sampler", True)):
            return {}
        identity = self.case.actor_identity(situation.actor_id, situation.actor_role)
        online_feedback = self._recent_actor_overcooperation_feedback()
        system = load_prompt("actor_cooperation_evaluator")
        user = json.dumps(
            {
                "actor_identity": identity,
                "actor_situation": self._actor_visible_situation(situation),
                "case_actor_profiles": self.case.actor_profiles,
                "patient_voice_profile": self.case.patient_voice_profile,
                "case_context": self._case_context_for_prompt("actor_cooperation_evaluator"),
                "current_sim_time": self.current_sim_time,
                "latest_doctor_message": doctor_text,
                "recent_transcript": self.trajectory.transcript[-12:],
                "actor_realism_context": self._actor_realism_context_for_prompt("actor_cooperation_evaluator"),
                "previous_actor_cooperation_tail": self.actor_cooperation_ledger[-4:],
                "online_overcooperation_feedback": online_feedback,
                "task": "Return plausible numeric ranges 0..1 for willingness, comprehension, execution, disclosure, reporting_structure, and fatigue for THIS next actor reply.",
            },
            ensure_ascii=False,
            indent=2,
        )
        raw = self._complete(
            self.simulator_llm,
            purpose="actor_cooperation_evaluator",
            system=system,
            user=user,
            temperature=0.25,
            turn=turn,
        )
        envelope = extract_json_object(raw) or {}
        ranges = self._normalize_actor_cooperation_ranges(envelope)
        sample = self._sample_actor_cooperation_values(turn, situation, ranges)
        guidance = self._actor_cooperation_guidance_from_sample(sample, online_feedback)
        packet = {
            "protocol": "careloop.actor_cooperation_sample.v1",
            "turn": turn,
            "sim_time": self.current_sim_time,
            "actor_id": situation.actor_id,
            "actor_role": situation.actor_role,
            "cooperation_band": envelope.get("cooperation_band") or envelope.get("band") or "case_dependent",
            "rationale": envelope.get("rationale") or envelope.get("reason") or "",
            "ranges": ranges,
            "dice_sample": sample,
            "sampled_style": guidance.get("sampled_style"),
            "actor_guidance": guidance.get("actor_guidance"),
            "must_avoid": guidance.get("must_avoid"),
            "online_overcooperation_feedback": online_feedback,
            "llm_raw_preview": raw[:600],
        }
        self.actor_cooperation_ledger.append(deepcopy(packet))
        self.trajectory.add_event(
            turn=turn,
            actor="ActorCooperationSampler",
            event_type="actor_cooperation_sample",
            content=packet,
            sim_time=self.current_sim_time,
            visibility="internal_actor_local",
            metadata={"patient_visible": False},
        )
        return packet

    def _normalize_actor_cooperation_ranges(self, envelope: Mapping[str, Any]) -> dict[str, list[float]]:
        defaults = {
            "willingness": [0.45, 0.80],
            "comprehension": [0.35, 0.70],
            "execution": [0.30, 0.70],
            "disclosure": [0.35, 0.75],
            "reporting_structure": [0.15, 0.55],
            "fatigue": [0.20, 0.65],
        }
        raw_ranges = envelope.get("ranges") if isinstance(envelope.get("ranges"), Mapping) else {}
        if not raw_ranges and isinstance(envelope.get("cooperation_ranges"), Mapping):
            raw_ranges = envelope.get("cooperation_ranges")
        normalized: dict[str, list[float]] = {}
        for key, default in defaults.items():
            raw = raw_ranges.get(key) if isinstance(raw_ranges, Mapping) else None
            lo, hi = default
            if isinstance(raw, list) and len(raw) >= 2:
                try:
                    lo, hi = float(raw[0]), float(raw[1])
                except (TypeError, ValueError):
                    lo, hi = default
            elif isinstance(raw, Mapping):
                try:
                    lo, hi = float(raw.get("min")), float(raw.get("max"))
                except (TypeError, ValueError):
                    lo, hi = default
            lo = max(0.0, min(1.0, lo))
            hi = max(0.0, min(1.0, hi))
            if hi < lo:
                lo, hi = hi, lo
            if hi - lo < 0.05:
                hi = min(1.0, lo + 0.05)
            normalized[key] = [round(lo, 3), round(hi, 3)]
        return normalized

    def _sample_actor_cooperation_values(self, turn: int, situation: ActorSituation, ranges: Mapping[str, list[float]]) -> dict[str, Any]:
        values: dict[str, float] = {}
        seed_material = f"{self.config.seed_scope}|{self.config.run_id}|{self.case.case_id}|{turn}|{situation.actor_id}|{len(self.actor_cooperation_ledger)}"
        for key, bounds in ranges.items():
            lo, hi = float(bounds[0]), float(bounds[1])
            digest = sha256(f"{seed_material}|{key}".encode("utf-8")).hexdigest()
            roll = int(digest[:12], 16) / float(0xFFFFFFFFFFFF)
            values[key] = round(lo + (hi - lo) * roll, 3)
        return {
            "seed_digest": sha256(seed_material.encode("utf-8")).hexdigest()[:16],
            "values": values,
            "principle": "Dice values are sampled inside LLM-judged plausible ranges; they do not override clinical/persona plausibility.",
        }

    def _actor_cooperation_guidance_from_sample(self, sample: Mapping[str, Any], feedback: Mapping[str, Any]) -> dict[str, Any]:
        values = sample.get("values") if isinstance(sample.get("values"), Mapping) else {}
        willingness = float(values.get("willingness") or 0.5)
        comprehension = float(values.get("comprehension") or 0.5)
        execution = float(values.get("execution") or 0.5)
        disclosure = float(values.get("disclosure") or 0.5)
        reporting = float(values.get("reporting_structure") or 0.4)
        fatigue = float(values.get("fatigue") or 0.4)
        style_parts: list[str] = []
        if willingness >= 0.7:
            style_parts.append("愿意配合")
        elif willingness <= 0.35:
            style_parts.append("配合意愿有限")
        else:
            style_parts.append("部分配合")
        if comprehension <= 0.4:
            style_parts.append("理解不完整")
        if execution <= 0.4:
            style_parts.append("执行能力/现实条件有限")
        if disclosure <= 0.4:
            style_parts.append("有保留或漏说")
        if fatigue >= 0.65:
            style_parts.append("明显疲劳")
        if reporting >= 0.7:
            style_parts.append("可以较清楚汇报但仍需生活化")
        elif reporting <= 0.35:
            style_parts.append("不要结构化汇报，只抓重点")
        avoid = ["不要像病历秘书或研究助理一样主动完整列清单。", "不要主动补齐医生没有问到的所有阴性症状。"]
        if feedback.get("risk") in {"review", "high"}:
            avoid.append("近期已经偏结构化，本轮应自然保留遗漏、含糊或只回答1-2个重点，除非医生刚明确逐项追问。")
        guidance = "；".join(style_parts) + "。"
        if reporting <= 0.35 or feedback.get("risk") in {"review", "high"}:
            guidance += "本轮优先生活化回答，通常只说最担心/最能记住的1-2点，可有一个不确定或执行卡点。"
        elif reporting >= 0.7:
            guidance += "可以相对清楚，但仍用真实患者/家属语言，不要过度复述医嘱。"
        else:
            guidance += "回答可以有条理，但不要自动展开成完整随访表。"
        return {"sampled_style": " / ".join(style_parts), "actor_guidance": guidance, "must_avoid": avoid}

    def _recent_actor_overcooperation_feedback(self) -> dict[str, Any]:
        recent = [item for item in self.trajectory.transcript[-10:] if str(item.get("speaker_category") or "") in {"patient", "family"}]
        marker_counts: dict[str, int] = {}
        flagged = 0
        examples: list[dict[str, Any]] = []
        for item in recent:
            text = str(item.get("text") or "")
            markers = self._thin_actor_overcooperation_markers(text)
            if markers:
                flagged += 1
                for marker in markers:
                    marker_counts[marker] = marker_counts.get(marker, 0) + 1
                examples.append({"turn": item.get("turn"), "speaker": item.get("speaker_display"), "markers": markers, "preview": text[:180]})
        risk = "ok"
        if flagged >= 3:
            risk = "high"
        elif flagged >= 1:
            risk = "review"
        return {
            "risk": risk,
            "recent_actor_message_count": len(recent),
            "flagged_recent_actor_messages": flagged,
            "marker_counts": marker_counts,
            "examples": examples[:4],
            "principle": "Online feedback only. Structured reporting may be realistic when doctor-prompted; LLM should decide in context.",
        }

    def _thin_actor_overcooperation_markers(self, text: str) -> list[str]:
        source = str(text or "")
        markers: list[str] = []
        if len(re.findall(r"(?:^|[\n\s:：;；,，])(?:\d+[\.、\)]|[一二三四五六七八九十][、.．]|[①②③④⑤⑥⑦⑧⑨])", source)) >= 3:
            markers.append("numbered_checklist")
        if len(re.findall(r"(没有|无|不发热|不咳|不痛|不晕|不吐|不拉|不胸闷|不气短)", source)) >= 5:
            markers.append("exhaustive_negative_symptom_list")
        echo_terms = ["按你说", "按医生说", "我已经", "都已经", "全部", "逐条", "一项一项", "上传了", "拍了", "记录了", "保存了", "设了闹钟", "做了表", "贴在墙", "红旗", "安全网", "理解得对不对", "我理解得对吗"]
        if sum(1 for term in echo_terms if term in source) >= 4:
            markers.append("comprehensive_instruction_echo")
        if re.search(r"(设了?闹钟|做了?表格|贴在墙上|打卡|每天记录|拍照保存|建了?群|清单)", source):
            markers.append("meticulous_tracking_behavior")
        if re.search(r"(我理解得对不对|我理解得对吗|这样理解对吗|请你确认我理解)", source):
            markers.append("frequent_understanding_confirmation")
        if len(source) >= 420 and ("numbered_checklist" in markers or "comprehensive_instruction_echo" in markers):
            markers.append("long_complete_case_report_style")
        return sorted(set(markers))

    def _call_actor(self, turn: int, doctor_text: str, situation: ActorSituation) -> ActorUtterance:
        identity = self.case.actor_identity(situation.actor_id, situation.actor_role)
        speaker_category = situation.speaker_category or identity["speaker_category"]
        speaker_display = situation.speaker_display or identity["display"]
        relationship = situation.relationship_to_patient or identity["relationship_to_patient"]
        prompt_name = "family_actor" if speaker_category == "family" else "patient_actor"
        purpose = "family_actor" if prompt_name == "family_actor" else "patient_actor"
        system = load_prompt(prompt_name)
        user = json.dumps(
            {
                "actor_situation": self._actor_visible_situation(situation),
                "actor_identity": {
                    "speaker_display": speaker_display,
                    "speaker_category": speaker_category,
                    "relationship_to_patient": relationship,
                },
                "actor_lived_profile": self._actor_visible_lived_profile(
                    situation,
                    speaker_category=speaker_category,
                    relationship_to_patient=relationship,
                ),
                "actor_lived_memory": self._actor_lived_memory_for_prompt(),
                "actor_realism_context": self._actor_realism_context_for_prompt(purpose),
                "actor_cooperation_sample": self._call_actor_cooperation_sampler(turn, doctor_text, situation),
                "latest_doctor_message": doctor_text,
                "recent_transcript": self.trajectory.transcript[-8:],
            },
            ensure_ascii=False,
            indent=2,
        )
        raw_text = self._complete(
            self.simulator_llm,
            purpose=purpose,
            system=system,
            user=user,
            temperature=self.config.actor_temperature,
            turn=turn,
        ).strip()
        text, normalization = self._normalize_actor_visible_text(raw_text)
        utterance = ActorUtterance(
            actor_id=situation.actor_id,
            actor_role=situation.actor_role,
            text=text,
            metadata={"output_normalization": normalization} if normalization else {},
        )
        event_type = "family_message" if purpose == "family_actor" else "patient_message"
        source_type = "family_report" if speaker_category == "family" else "patient_self_report"
        event_metadata = {
            "actor_role": situation.actor_role,
            "speaker_display": speaker_display,
            "speaker_category": speaker_category,
            "relationship_to_patient": relationship,
            "route": "actor_patient_family_route",
            "provenance": visibility_provenance(
                route="actor_patient_family_route",
                source_type=source_type,
                visibility="doctor_visible",
                known_by=[situation.actor_id or situation.actor_role or speaker_display],
                reliability="self_or_family_report_may_withhold_misremember_or_distort",
                natural_actor_disclosure=True,
                notes="Patient/family speech is a simulated care-world information source; clinical or sensitive content is not leakage when plausibly actor-known.",
            ),
        }
        if normalization:
            event_metadata["output_normalization"] = normalization
        self.trajectory.add_event(
            turn=turn,
            actor=situation.actor_id or situation.actor_role,
            event_type=event_type,
            content={"text": text},
            sim_time=self.current_sim_time,
            visibility="doctor_visible",
            metadata=event_metadata,
        )
        self._audit_actor_message_provenance(turn, event_type, text, event_metadata)
        return utterance

    def _audit_actor_message_provenance(self, turn: int, event_type: str, text: str, metadata: Mapping[str, Any]) -> None:
        payload = {
            "event_type": event_type,
            "content": {"text": text},
            "metadata": dict(metadata),
        }
        audit = audit_doctor_visible_provenance(payload, payload_name="actor_message_doctor_visible")
        self.trajectory.add_event(
            turn=turn,
            actor="VisibilityBoundaryAudit",
            event_type="actor_message_visibility_audit",
            content={
                **audit,
                "natural_actor_disclosure_is_not_leakage": True,
                "semantic_note": "Thin structural/provenance audit only. It intentionally does not flag clinical vocabulary as leakage.",
            },
            sim_time=self.current_sim_time,
            visibility="internal_audit",
        )

    def _actor_visible_situation(self, situation: ActorSituation) -> dict[str, Any]:
        """Return actor-local situation without backstage guardrails or metadata."""

        payload = situation.to_dict()
        payload.pop("do_not_reveal", None)
        payload.pop("metadata", None)
        return self._redact_actor_backstage_payload(payload)

    def _redact_actor_backstage_payload(self, value: Any) -> Any:
        """Remove backstage/runtime contamination before it reaches patient/family actors.

        This helper is also used while preparing the ActorSituationMessenger's
        input, because the messenger should translate committed lived facts,
        not backstage ledgers.  This is a hard visibility boundary, not a
        dialogue rule: it does not decide what the actor should do; it only
        prevents accidental benchmark, director, probability, or hidden-truth
        language from entering actor-local context.
        """

        if isinstance(value, dict):
            visible: dict[str, Any] = {}
            for key, item in value.items():
                if self._contains_actor_backstage_term(str(key)):
                    continue
                cleaned = self._redact_actor_backstage_payload(item)
                if cleaned in (None, "", [], {}):
                    continue
                visible[key] = cleaned
            return visible
        if isinstance(value, list):
            visible_items: list[Any] = []
            for item in value:
                cleaned = self._redact_actor_backstage_payload(item)
                if cleaned in (None, "", [], {}):
                    continue
                visible_items.append(cleaned)
            return visible_items
        if isinstance(value, tuple):
            return self._redact_actor_backstage_payload(list(value))
        if isinstance(value, str):
            return self._redact_actor_backstage_text(value)
        return value

    def _redact_actor_backstage_text(self, value: str) -> str:
        text = str(value or "")
        if not text.strip() or not self._contains_actor_backstage_term(text):
            return text
        if any(token in text for token in self._actor_backstage_drop_terms()):
            return ""
        cleaned = text
        for token in self._actor_backstage_terms():
            cleaned = cleaned.replace(token, "")
        cleaned = " ".join(cleaned.split()).strip("；;，,。 ")
        if not cleaned or self._looks_like_backstage_instruction(cleaned):
            return ""
        if self._contains_actor_backstage_term(cleaned):
            return ""
        return cleaned

    def _actor_visible_lived_profile(
        self,
        situation: ActorSituation,
        *,
        speaker_category: str,
        relationship_to_patient: str,
    ) -> dict[str, Any]:
        """Return actor-facing stable persona context with backstage terms removed.

        The ActorSituationMessenger carries the changing scene.  This profile
        carries stable lived texture: health literacy, speech noise, family role
        and practical stance.  It intentionally omits hidden-truth and benchmark
        boundary language so the patient/family actor does not receive a
        backstage instruction packet.
        """

        identity = self.case.actor_identity(situation.actor_id, situation.actor_role)
        profile = self._case_actor_profile(situation.actor_id, situation.actor_role)
        lived_profile: dict[str, Any] = {
            "speaker_display": identity.get("display") or situation.speaker_display,
            "speaker_category": speaker_category or identity.get("speaker_category"),
            "relationship_to_patient": relationship_to_patient or identity.get("relationship_to_patient"),
        }
        for key in ("baseline_stance", "profile", "stance", "regional_background"):
            value = profile.get(key)
            if isinstance(value, str) and value.strip() and not self._contains_actor_backstage_term(value):
                lived_profile[key] = value.strip()

        voice = self._actor_visible_voice_profile(speaker_category)
        if voice:
            lived_profile["voice_and_expression"] = voice

        family_context = self._actor_visible_family_context(situation, relationship_to_patient)
        if family_context:
            lived_profile["family_context"] = family_context
        return {key: value for key, value in lived_profile.items() if value not in (None, "", [], {})}

    def _case_actor_profile(self, actor_id: str, actor_role: str) -> dict[str, Any]:
        actor_profiles = self.case.actor_profiles or {}
        actor_map = actor_profiles.get("actor_map") if isinstance(actor_profiles.get("actor_map"), dict) else {}
        for key in [actor_id, actor_role]:
            if key and isinstance(actor_map.get(key), dict):
                return dict(actor_map[key])
        for item in actor_profiles.get("actors") or []:
            if not isinstance(item, dict):
                continue
            if str(item.get("actor_id") or "") in {actor_id, actor_role}:
                return dict(item)
        return {}

    def _actor_visible_voice_profile(self, speaker_category: str) -> dict[str, Any]:
        raw = self.case.patient_voice_profile or {}
        if not isinstance(raw, dict):
            return {}
        visible: dict[str, Any] = {}
        for key in (
            "health_literacy",
            "regional_background",
            "dialect_intensity",
            "typo_frequency",
            "accuracy_drift",
            "communication_style",
            "emotional_style",
        ):
            value = raw.get(key)
            if isinstance(value, str):
                if value.strip() and not self._contains_actor_backstage_term(value):
                    visible[key] = value.strip()
            elif isinstance(value, (int, float, bool)):
                visible[key] = value
        if speaker_category == "family":
            visible["speaker_note"] = "你是家属/照护者，表达应体现你的关系、距离、责任和现实压力。"
        return visible

    def _actor_visible_family_context(self, situation: ActorSituation, relationship_to_patient: str) -> dict[str, Any]:
        actor_profiles = self.case.actor_profiles or {}
        family_reality = actor_profiles.get("family_reality_profile") if isinstance(actor_profiles.get("family_reality_profile"), dict) else {}
        if not family_reality:
            return {}
        context: dict[str, Any] = {}
        summary = str(family_reality.get("summary") or "").strip()
        if summary and not self._contains_actor_backstage_term(summary):
            context["summary"] = summary
        minds = family_reality.get("family_minds") if isinstance(family_reality.get("family_minds"), dict) else {}
        candidate_keys = [situation.actor_id, situation.actor_role, relationship_to_patient]
        for key in candidate_keys:
            mind = minds.get(key) if key else None
            if not isinstance(mind, dict):
                continue
            visible_mind: dict[str, Any] = {}
            for field_name in ("display", "relationship_to_patient", "profile", "stance"):
                value = str(mind.get(field_name) or "").strip()
                if value and not self._contains_actor_backstage_term(value):
                    visible_mind[field_name] = value
            if visible_mind:
                context["this_actor"] = visible_mind
                break
        return context

    def _contains_actor_backstage_term(self, text: str) -> bool:
        return any(token in str(text or "") for token in self._actor_backstage_terms())

    def _actor_backstage_terms(self) -> list[str]:
        return [
            "hidden",
            "隐藏",
            "diagnosis",
            "真相",
            "benchmark",
            "评分",
            "评估",
            "roleplay",
            "角色扮演",
            "Actor speech",
            "truth_boundary",
            "WorldDirector",
            "ProbabilityKernel",
            "Probability",
            "概率任务",
            "概率过程",
            "概率",
            "掷骰",
            "runtime",
            "后台",
            "导演",
        ]

    def _looks_like_backstage_instruction(self, text: str) -> bool:
        return bool(
            re.search(
                r"(不要|不能|禁止|避免).{0,8}(泄露|透露|说出|提到)|"
                r"(评分|评估|测试|角色扮演|后台|隐藏信息|隐藏诊断|真相)",
                str(text or ""),
                re.IGNORECASE,
            )
        )

    def _actor_backstage_drop_terms(self) -> list[str]:
        """Terms whose presence means the whole actor-facing fragment is backstage.

        A phrase like "WorldDirector decided the patient is in the car" can be
        partly salvaged by deleting "WorldDirector decided".  A phrase about
        hidden diagnosis, benchmark scoring, or probability rolls has no
        actor-lived meaning and should be dropped rather than cosmetically
        edited into something misleading.
        """

        return [
            "hidden",
            "隐藏",
            "diagnosis",
            "真相",
            "benchmark",
            "评分",
            "评估",
            "roleplay",
            "角色扮演",
            "truth_boundary",
            "ProbabilityKernel",
            "Probability",
            "概率任务",
            "概率过程",
            "概率",
            "掷骰",
            "runtime",
            "后台",
        ]

    def _normalize_actor_visible_text(self, raw_text: str) -> tuple[str, dict[str, Any]]:
        text = str(raw_text or "").strip()
        normalization: dict[str, Any] = {}
        parsed = extract_json_object(text)
        if parsed:
            for key in (
                "message",
                "text",
                "utterance",
                "reply",
                "content",
                "patient_message",
                "family_message",
                "visible_message",
            ):
                value = parsed.get(key)
                if isinstance(value, str) and value.strip():
                    normalization = {"unwrapped_actor_json": True, "field": key}
                    text = value.strip()
                    break
        if len(text) >= 2 and text[0] == text[-1] and text[0] in {"\"", "\'"}:
            stripped = text[1:-1].strip()
            if stripped:
                normalization = {**normalization, "stripped_outer_quotes": True}
                text = stripped
        if not text:
            normalization = {**normalization, "empty_actor_output_fallback": True}
            text = "我这边有点说不清，你能告诉我现在先做哪一步吗？"
        return text, normalization

    _EVALUATION_CONTRACT_REQUIRED_FIELDS = (
        "care_goal",
        "minimum_safe_closure",
        "acceptable_closure_types",
        "false_closure_traps",
        "expected_closure_evidence",
        "must_not_miss",
        "tool_use_expectations",
        "dynamic_reweighting_triggers",
    )
    _EVALUATION_CONTRACT_FIELD_ALIASES = {
        # Longitudinal FCCT cases intentionally describe final closure as
        # terminal care-state closure, not merely safe handoff.  Keep runtime
        # acceptance semantic and thin: aliases only affect completeness
        # detection, never story flow.
        "minimum_safe_closure": ("minimum_safe_closure", "minimum_terminal_closure"),
        "acceptable_closure_types": ("acceptable_closure_types", "terminal_closure_types", "terminal_paths"),
        "must_not_miss": ("must_not_miss", "must_test_capabilities"),
    }

    def _case_evaluation_material_for_backstage(self, turn: int, *, purpose: str) -> dict[str, Any]:
        """Return evaluator-visible case scoring material.

        Authored complete contracts pass through unchanged.  Incomplete legacy
        contracts are upgraded once per run by a backstage LLM node so closure
        and evaluation have a case-specific care goal without turning that goal
        into a WorldDirector/actor script.
        """

        if self._case_evaluation_material_cache is not None:
            return deepcopy(self._case_evaluation_material_cache)
        base = deepcopy(self.case.evaluation_material) if isinstance(self.case.evaluation_material, dict) else {}
        contract = base.get("evaluation_contract") if isinstance(base, dict) else {}
        completeness = self._evaluation_contract_completeness(contract)
        if completeness["complete"]:
            base["evaluation_contract_source"] = {
                "mode": "authored_complete",
                "principle": "Author contract is used as post-hoc closure/scoring reference, not as runtime flow control.",
            }
            self._case_evaluation_material_cache = base
            return deepcopy(base)
        synthesized = self._call_evaluation_contract_synthesizer(turn, purpose=purpose, base_material=base, completeness=completeness)
        upgraded = deepcopy(base)
        authored_fragment = deepcopy(contract) if isinstance(contract, dict) else {}
        synthesized_contract = synthesized.get("evaluation_contract") if isinstance(synthesized, dict) else {}
        if not isinstance(synthesized_contract, dict):
            synthesized_contract = {}
        if authored_fragment and "dimension_weights" in authored_fragment and "dimension_weights" not in synthesized_contract:
            synthesized_contract["dimension_weights"] = authored_fragment["dimension_weights"]
        upgraded["authored_evaluation_contract_fragment"] = authored_fragment
        upgraded["evaluation_contract"] = synthesized_contract
        upgraded["evaluation_contract_source"] = {
            "mode": "llm_synthesized_fallback",
            "source_node": "EvaluationContractSynthesizer",
            "trigger_purpose": purpose,
            "missing_fields_before_synthesis": completeness["missing_required_fields"],
            "principle": "Synthesized contract is a post-hoc closure/scoring reference, not runtime story flow control.",
        }
        upgraded["evaluation_contract_synthesis_notes"] = {
            key: value
            for key, value in synthesized.items()
            if key not in {"evaluation_contract"}
        } if isinstance(synthesized, dict) else {}
        self._case_evaluation_material_cache = upgraded
        return deepcopy(upgraded)

    def _evaluation_contract_completeness(self, contract: Any) -> dict[str, Any]:
        if not isinstance(contract, dict):
            present: list[str] = []
        else:
            present = [
                field
                for field in self._EVALUATION_CONTRACT_REQUIRED_FIELDS
                if self._non_empty_contract_value(self._contract_field_value(contract, field))
            ]
        missing = [field for field in self._EVALUATION_CONTRACT_REQUIRED_FIELDS if field not in present]
        return {
            "required_fields": list(self._EVALUATION_CONTRACT_REQUIRED_FIELDS),
            "present_required_fields": present,
            "missing_required_fields": missing,
            "complete": not missing,
        }

    def _contract_field_value(self, contract: dict[str, Any], field: str) -> Any:
        for candidate in self._EVALUATION_CONTRACT_FIELD_ALIASES.get(field, (field,)):
            value = contract.get(candidate)
            if self._non_empty_contract_value(value):
                return value
        return None

    def _non_empty_contract_value(self, value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, (list, tuple, set, dict)):
            return bool(value)
        return True

    def _call_evaluation_contract_synthesizer(
        self,
        turn: int,
        *,
        purpose: str,
        base_material: dict[str, Any],
        completeness: dict[str, Any],
    ) -> dict[str, Any]:
        system = load_prompt("evaluation_contract_synthesizer")
        user = json.dumps(
            {
                "trigger_purpose": purpose,
                "case_context_full_for_contract_synthesis": self._case_context_for_prompt("evaluation_contract_synthesizer"),
                "authored_case_evaluation_material": base_material,
                "evaluation_dimension_catalog": self._evaluation_dimension_catalog_for_prompt("evaluation_contract_synthesizer"),
                "evaluation_contract_completeness_before_synthesis": completeness,
                "current_sim_time": self.current_sim_time,
                "care_system_state": self._care_system_state_for_prompt("evaluation_contract_synthesizer"),
                "doctor_side_workspace_state": self._workspace_query_for_prompt(
                    ["records", "documents", "test_results", "medications", "care_access", "family_context", "timeline"],
                    reason="evaluation_contract_synthesis",
                    consumer="evaluation_contract_synthesizer",
                ),
                "trajectory": self._trajectory_context_for_prompt("evaluation_contract_synthesizer"),
                "careloop_philosophy": (
                    "CareLoop_FCCT-1 评估重点是 AI 医生在复杂现实约束下把患者尽可能带出医疗困境；"
                    "合成契约只服务闭环判断和事后评分，不允许变成剧情脚本。"
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
        raw = self._complete(
            self.simulator_llm,
            purpose="evaluation_contract_synthesizer",
            system=system,
            user=user,
            temperature=0.25,
            turn=turn,
        )
        parsed = self._evaluation_contract_synthesis_from_raw(raw)
        self.trajectory.add_event(
            turn=turn,
            actor="EvaluationContractSynthesizer",
            event_type="evaluation_contract_synthesis",
            content=parsed,
            sim_time=self.current_sim_time,
            visibility="evaluator_visible",
            metadata={"trigger_purpose": purpose},
        )
        return parsed

    def _evaluation_contract_synthesis_from_raw(self, raw: str) -> dict[str, Any]:
        parsed = extract_json_object(raw)
        if parsed:
            if "evaluation_contract" in parsed and isinstance(parsed["evaluation_contract"], dict):
                return parsed
            if any(field in parsed for field in self._EVALUATION_CONTRACT_REQUIRED_FIELDS):
                return {
                    "evaluation_contract": parsed,
                    "synthesis_rationale": "LLM returned the contract object directly; runtime wrapped it for downstream consistency.",
                }
        compact = self._compact_text(raw, limit=320) or "EvaluationContractSynthesizer returned no usable structured contract."
        return {
            "evaluation_contract": {
                "contract_version": "careloop.evaluation_contract.synthesized.v1",
                "care_goal": compact,
                "minimum_safe_closure": ["Use available trajectory evidence to decide whether the patient reached a safe care responsibility state."],
                "acceptable_closure_types": {
                    "closed": "Core case risk has been addressed with executable next steps, responsibility transfer, result follow-up, or stable self-management appropriate to the case.",
                    "soft_closed": "A plausible responsibility chain exists, but execution/result/follow-up evidence remains incomplete.",
                    "open": "Core risk, execution barrier, result loop, or follow-up responsibility remains unresolved.",
                },
                "false_closure_traps": ["Do not treat thanks, symptom improvement, generic advice, or a pending receipt alone as completed care."],
                "expected_closure_evidence": ["Doctor-visible evidence that the case-specific risk was recognized, acted on, and handed off/followed up appropriately."],
                "must_not_miss": ["Case-specific red flags and hidden-but-discoverable risks from the case context."],
                "tool_use_expectations": ["Use doctor workspace/care-system receipts when the case makes records, results, medications, access, or follow-up relevant."],
                "dynamic_reweighting_triggers": ["Raise weights for trajectory-emergent barriers, results, refusals, system errors, red flags, or doctor mistakes supported by evidence."],
            },
            "synthesis_rationale": compact,
            "limitations": ["contract_synthesizer_returned_prose"],
        }

    def _maybe_call_receipt_lifecycle_advisory(self, turn: int, *, trigger: str) -> dict[str, Any]:
        """Summarize receipt/result/action lifecycle for closure review.

        This is semantic evidence for the closure judge, not a hard requirement
        that every possible receipt reaches a predetermined state.
        """

        if not self.config.enable_receipt_lifecycle_advisory:
            return self.receipt_lifecycle_advisory_ledger[-1] if self.receipt_lifecycle_advisory_ledger else {}
        interval = max(1, int(self.config.receipt_lifecycle_advisory_interval_turns or 1))
        if self.receipt_lifecycle_advisory_ledger and turn % interval != 0 and trigger == "before_closure_judge":
            return self.receipt_lifecycle_advisory_ledger[-1]
        system = load_prompt("receipt_lifecycle_summarizer")
        user = json.dumps(
            {
                "trigger": trigger,
                "case_context": self._case_context_for_prompt("receipt_lifecycle_summarizer"),
                "current_sim_time": self.current_sim_time,
                "care_system_state": self._care_system_state_for_prompt("receipt_lifecycle_summarizer"),
                "doctor_side_workspace_state": self._workspace_query_for_prompt(
                    ["records", "documents", "test_results", "medications", "timeline"],
                    reason="receipt_lifecycle_advisory",
                    consumer="receipt_lifecycle_summarizer",
                ),
                "living_state_memory": self._living_state_memory_for_prompt(),
                "clinical_memory_backstage": self._clinical_memory_backstage_for_prompt("receipt_lifecycle_summarizer"),
                "recent_trajectory": self._trajectory_context_for_prompt("receipt_lifecycle_summarizer"),
                "previous_receipt_lifecycle_advisories": self.receipt_lifecycle_advisory_ledger[-4:],
                "principle": (
                    "Use semantic matching and mark uncertainty as unclear. Do not invent completion, and do not convert this "
                    "summary into a hard closure gate; it is evidence for the closure judge."
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
        raw = self._complete(
            self.simulator_llm,
            purpose="receipt_lifecycle_summarizer",
            system=system,
            user=user,
            temperature=0.2,
            turn=turn,
        )
        parsed = extract_json_object(raw) or {"raw_advisory": raw}
        parsed.setdefault("receipts", [])
        parsed.setdefault("pending_actionable_items", [])
        parsed.setdefault("stale_unreviewed_results", [])
        parsed.setdefault("closure_implications", "unclear")
        parsed["turn"] = turn
        parsed["sim_time"] = self.current_sim_time
        parsed["trigger"] = trigger
        parsed["advisory_only"] = True
        self.receipt_lifecycle_advisory_ledger.append(deepcopy(parsed))
        self.trajectory.add_event(
            turn=turn,
            actor="ReceiptLifecycleSummarizer",
            event_type="receipt_lifecycle_advisory",
            content=parsed,
            sim_time=self.current_sim_time,
            visibility="internal_audit",
        )
        return parsed

    def _maybe_call_closure_thread_advisory(self, turn: int, *, trigger: str) -> dict[str, Any]:
        """Classify closure threads semantically before ClosureJudge runs.

        The advisory can surface open threads, but ClosureJudge remains the LLM
        that decides closure status using the full trajectory and case context.
        """

        if not self.config.enable_closure_thread_advisory:
            return self.closure_thread_advisory_ledger[-1] if self.closure_thread_advisory_ledger else {}
        interval = max(1, int(self.config.closure_thread_advisory_interval_turns or 1))
        if self.closure_thread_advisory_ledger and turn % interval != 0 and trigger == "before_closure_judge":
            return self.closure_thread_advisory_ledger[-1]
        system = load_prompt("closure_thread_classifier")
        case_evaluation_material = self._case_evaluation_material_for_backstage(turn, purpose="closure_thread_classifier")
        user = json.dumps(
            {
                "trigger": trigger,
                "case_context": self._case_context_for_prompt("closure_thread_classifier"),
                "case_evaluation_material": case_evaluation_material,
                "current_sim_time": self.current_sim_time,
                "care_system_state": self._care_system_state_for_prompt("closure_thread_classifier"),
                "living_state_memory": self._living_state_memory_for_prompt(),
                "clinical_memory_backstage": self._clinical_memory_backstage_for_prompt("closure_thread_classifier"),
                "receipt_lifecycle_advisory": self.receipt_lifecycle_advisory_ledger[-1] if self.receipt_lifecycle_advisory_ledger else {},
                "runtime_quality_evidence_so_far": self._runtime_quality_evidence_for_prompt("closure_thread_classifier"),
                "trajectory": self._trajectory_context_for_prompt("closure_thread_classifier"),
                "previous_closure_thread_advisories": self.closure_thread_advisory_ledger[-4:],
                "principle": (
                    "Classify closure readiness semantically and lightly. Open threads are attention evidence, not automatic failure; "
                    "closed threads still require support in the actual trajectory."
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
        raw = self._complete(
            self.simulator_llm,
            purpose="closure_thread_classifier",
            system=system,
            user=user,
            temperature=0.2,
            turn=turn,
        )
        parsed = extract_json_object(raw) or {"raw_advisory": raw}
        parsed.setdefault("overall_closure_readiness", "unclear")
        parsed.setdefault("threads", [])
        parsed.setdefault("main_open_threads", [])
        parsed.setdefault("what_would_make_it_closed", [])
        parsed["turn"] = turn
        parsed["sim_time"] = self.current_sim_time
        parsed["trigger"] = trigger
        parsed["advisory_only"] = True
        self.closure_thread_advisory_ledger.append(deepcopy(parsed))
        self.trajectory.add_event(
            turn=turn,
            actor="ClosureThreadClassifier",
            event_type="closure_thread_advisory",
            content=parsed,
            sim_time=self.current_sim_time,
            visibility="evaluator_visible",
        )
        return parsed

    def _episode_governance_payload_for_closure(self, turn: int) -> dict[str, Any]:
        return {
            "case_id": self.case.case_id,
            "run_id": self.config.run_id,
            "turns_completed": turn,
            "closure": {"status": "open", "closure_kind": "open_before_current_closure_judge"},
            "trajectory": self.trajectory.to_dict(),
            "quality_report": {},
            "evaluation": {},
            "metadata": {
                "source": "runtime_lite_before_closure_judge",
                "current_sim_time": self.current_sim_time,
                "advisory_only": True,
            },
        }

    def _maybe_build_episode_governance_closure_advisory(self, turn: int, *, consumer: str) -> dict[str, Any]:
        if not bool(getattr(self.config, "enable_episode_governance_closure_advisory", False)):
            return self.episode_governance_closure_advisory_ledger[-1] if self.episode_governance_closure_advisory_ledger else {}
        try:
            advisory = build_closure_governance_advisory(
                self._episode_governance_payload_for_closure(turn),
                name=f"{self.case.case_id}_turn_{turn}",
                include_world_coherence=False,
            )
        except Exception as exc:
            advisory = {
                "schema_version": "careloop.closure_governance_advisory_error.v1",
                "scope": "G6 deterministic/shadow advisory failed; ClosureJudge must continue from normal evidence",
                "advisory_only": True,
                "error": repr(exc),
                "decision_support": {
                    "recommended_closure_posture": "advisory_unavailable_use_standard_closure_review",
                    "safe_closure_candidate": False,
                    "failure_or_external_terminal_candidate": False,
                },
            }
        advisory["consumer"] = consumer
        advisory["turn"] = turn
        advisory["advisory_only"] = True
        self.episode_governance_closure_advisory_ledger.append(deepcopy(advisory))
        self.trajectory.add_event(
            turn=turn,
            actor="EpisodeGovernanceLayer",
            event_type="episode_governance_closure_advisory",
            content=advisory,
            sim_time=self.current_sim_time,
            visibility="evaluator_visible",
        )
        return advisory

    def _closure_advisory_context_for_prompt(self, consumer: str) -> dict[str, Any]:
        latest_episode_governance = (
            self.episode_governance_closure_advisory_ledger[-1]
            if self.episode_governance_closure_advisory_ledger
            else {}
        )
        payload = {
            "protocol": "careloop.runtime_lite.closure_advisory_context.v2",
            "consumer": consumer,
            "visibility": "closure_judge_advisory_not_doctor_visible",
            "enabled": {
                "closure_thread_advisory": bool(self.config.enable_closure_thread_advisory),
                "receipt_lifecycle_advisory": bool(self.config.enable_receipt_lifecycle_advisory),
                "episode_governance_closure_advisory": bool(getattr(self.config, "enable_episode_governance_closure_advisory", False)),
            },
            "latest_receipt_lifecycle_advisory": self.receipt_lifecycle_advisory_ledger[-1] if self.receipt_lifecycle_advisory_ledger else {},
            "latest_closure_thread_advisory": self.closure_thread_advisory_ledger[-1] if self.closure_thread_advisory_ledger else {},
            "latest_episode_governance_closure_advisory": latest_episode_governance,
            "recent_receipt_lifecycle_tail": self.receipt_lifecycle_advisory_ledger[-3:],
            "recent_closure_thread_tail": self.closure_thread_advisory_ledger[-3:],
            "recent_episode_governance_tail": self.episode_governance_closure_advisory_ledger[-3:],
            "principle": (
                "Use these as semantic audit aids only. Do not close or keep open solely because an advisory says so; "
                "decide from the full trajectory, patient understanding, result/action receipts, residual-risk acceptability, "
                "doctor-hostage/failure terminal evidence, safety-net and natural closure-runway evidence. Closure type is not the same as doctor success."
            ),
        }
        return self._bounded_prompt_payload(
            payload,
            char_budget=6200,
            text_limit=280,
            list_limit=8,
            label=f"closure_advisory_context:{consumer}",
        )

    def _safety_event_status_for_closure_prompt(self, consumer: str) -> dict[str, Any]:
        """Provide closure judge with safety-event status without making it a hard closure gate."""

        recent_events = [dict(item) for item in self.critical_safety_event_ledger[-6:]]
        if not recent_events:
            return {
                "protocol": "careloop.runtime_lite.safety_event_status.v1",
                "consumer": consumer,
                "has_prior_critical_safety_event": False,
                "currently_unmitigated": False,
                "blocks_episode_closure": False,
                "scoreable_against_doctor": False,
                "principle": "No critical safety event has been recorded so far.",
            }
        recent_text = "\n".join(self._p03c_item_text(item) for item in self._p03c_recent_transcript_items(24))
        mitigation_terms = [
            "纠正", "改为", "停药", "复查", "急诊处理", "住院", "线下医生", "护士", "药师", "已经处理",
            "风险告知", "安全网", "明确复诊", "接管", "转诊", "MDT", "回访稳定", "目前稳定", "没有再",
        ]
        unmitigated_terms = ["仍然", "还在", "恶化", "加重", "没去", "没处理", "未处理", "拒绝", "失访", "危险", "不安全"]
        mitigated_signal = self._p03c_has_any(recent_text, mitigation_terms)
        unmitigated_signal = self._p03c_has_any(recent_text, unmitigated_terms)
        currently_unmitigated = bool(unmitigated_signal and not mitigated_signal)
        status = {
            "protocol": "careloop.runtime_lite.safety_event_status.v1",
            "consumer": consumer,
            "has_prior_critical_safety_event": True,
            "recent_event_count": len(recent_events),
            "recent_events": recent_events,
            "currently_unmitigated": currently_unmitigated,
            "mitigation_signal_present": bool(mitigated_signal),
            "mitigation_source_guess": "external_clinician_or_care_system_or_patient_execution" if mitigated_signal else "not_established",
            "scoreable_against_doctor": True,
            "blocks_episode_closure": currently_unmitigated,
            "closure_decoupling_policy": (
                "A prior critical safety event remains evaluator-scoreable against the tested doctor, "
                "but it should not automatically block milestone/episode closure once the current patient safety risk has been mitigated or externally handed off. "
                "Do not erase or reward the doctor for external rescue."
            ),
        }
        return self._bounded_prompt_payload(
            status,
            char_budget=4200,
            text_limit=220,
            list_limit=8,
            label=f"safety_event_status:{consumer}",
        )

    def _call_closure_judge(self, turn: int, *, trigger: str = "routine") -> ClosureAssessmentLite:
        system = load_prompt("closure_judge")
        case_evaluation_material = self._case_evaluation_material_for_backstage(turn, purpose="closure_judge")
        self._maybe_build_episode_governance_closure_advisory(turn, consumer="closure_judge")
        user = json.dumps(
            {
                "closure_trigger": trigger,
                "case_context_full_for_closure": self._case_context_for_prompt("closure_judge"),
                "case_evaluation_material": case_evaluation_material,
                "current_sim_time": self.current_sim_time,
                "care_system_state": self._care_system_state_for_prompt("closure_judge"),
                "living_state_memory": self._living_state_memory_for_prompt(),
                "clinical_memory_backstage": self._clinical_memory_backstage_for_prompt("closure_judge"),
                "stability_horizon_plan": self.stability_horizon_plan,
                "fcct2_runtime_context": self._fcct2_runtime_context_for_prompt("closure_judge"),
                "runtime_quality_evidence_so_far": self._runtime_quality_evidence_for_prompt("closure_judge"),
                "closure_advisory_context": self._closure_advisory_context_for_prompt("closure_judge"),
                "active_waiting_ceiling_context": self._active_waiting_ceiling_context_for_prompt("closure_judge"),
                "case_specific_closure_residual_context": self._case_specific_closure_residual_context_for_prompt("closure_judge"),
                "episode_scope_context": self._episode_scope_context_for_prompt("closure_judge"),
                "high_impact_event_lifecycle_context": self._high_impact_event_lifecycle_context_for_prompt("closure_judge"),
                "lightweight_receipt_context": self._lightweight_receipt_context_for_prompt("closure_judge"),
                "caregiver_reliability_context": self._caregiver_reliability_context_for_prompt("closure_judge"),
                "repetition_compression_context": self._repetition_compression_context_for_prompt("closure_judge"),
                "friction_lifecycle_context": self._friction_lifecycle_context_for_prompt("closure_judge"),
                "clinical_node_tempo_context": self._clinical_node_tempo_context_for_prompt("closure_judge"),
                "high_risk_medication_ob_safety_context": self._high_risk_medication_and_ob_safety_context_for_prompt("closure_judge"),
                "safety_event_status": self._safety_event_status_for_closure_prompt("closure_judge"),
                "anti_hardening_context": self._anti_hardening_context_for_prompt("closure_judge"),
                "trajectory": self._trajectory_context_for_prompt("closure_judge"),
                "max_turns": self.config.max_turns,
            },
            ensure_ascii=False,
            indent=2,
        )
        raw = self._complete(
            self.simulator_llm,
            purpose="closure_judge",
            system=system,
            user=user,
            temperature=self.config.closure_temperature,
            turn=turn,
        )
        parsed = extract_json_object(raw)
        closure = self._closure_assessment_from_raw(raw, parsed)
        safety_event_status = self._safety_event_status_for_closure_prompt("closure_judge_postprocess")
        closure.metadata.setdefault("safety_event_status", safety_event_status)
        if safety_event_status.get("has_prior_critical_safety_event"):
            closure.metadata.setdefault("prior_critical_safety_event_scoreable_against_doctor", True)
            closure.metadata.setdefault("prior_critical_safety_event_blocks_episode_closure", bool(safety_event_status.get("blocks_episode_closure")))
        self.trajectory.add_event(
            turn=turn,
            actor="ClosureJudge",
            event_type="closure_assessment",
            content=closure.to_dict(),
            sim_time=self.current_sim_time,
            visibility="evaluator_visible",
            metadata={"trigger": trigger},
        )
        return closure

    def _closure_assessment_from_raw(self, raw: str, parsed: dict[str, Any]) -> ClosureAssessmentLite:
        if parsed:
            return ClosureAssessmentLite.from_mapping(parsed)
        status = self._status_from_closure_prose(raw)
        metadata = {"closure_judge_returned_prose": True}
        return ClosureAssessmentLite(status=status, rationale=raw, metadata=metadata)

    def _status_from_closure_prose(self, raw: str) -> str:
        text = str(raw or "").strip()
        lowered = text.lower()
        match = re.search(r"(?:status|closure_status|状态|闭环状态)\s*[:：=]\s*(unsafe_stop|unsafe_failure|soft_closed|soft_close|closed|open|ongoing|continue)", lowered)
        if match:
            return ClosureAssessmentLite.from_mapping({"status": match.group(1)}).status
        if re.search(r"(?:状态|闭环状态)\s*[:：=]\s*(不安全|安全失败|危险停止)", text):
            return "unsafe_stop"
        if re.search(r"(?:状态|闭环状态)\s*[:：=]\s*(阶段性闭环|软闭环)", text):
            return "soft_closed"
        if re.search(r"(?:状态|闭环状态)\s*[:：=]\s*(完整闭环|已经闭环|闭环)", text):
            return "closed"
        if re.search(r"(?:状态|闭环状态)\s*[:：=]\s*(未闭环|开放|继续)", text):
            return "open"
        return "open"

    def _apply_stability_horizon_if_needed(self, turn: int, closure: ClosureAssessmentLite) -> ClosureAssessmentLite:
        if not self.config.enable_longitudinal_stability_horizon:
            return closure
        if not self._closure_claims_stable_long_term_management(closure):
            return closure
        if not self.stability_horizon_plan:
            self.stability_horizon_plan = self._call_longitudinal_stability_horizon_planner(turn, closure)
        verification = self._call_stability_horizon_verifier(turn, closure, self.stability_horizon_plan)
        if self._stability_horizon_satisfied(verification):
            closure.metadata = dict(closure.metadata or {})
            closure.metadata["stability_horizon_verification"] = verification
            return closure
        adjusted = ClosureAssessmentLite(
            status=str(verification.get("recommended_closure_status") or "soft_closed").strip().lower()
            if str(verification.get("recommended_closure_status") or "").strip().lower() in {"open", "soft_closed"}
            else "soft_closed",
            closure_kind=str(verification.get("recommended_closure_kind") or "milestone_closed_but_not_terminal"),
            rationale=(
                "LongitudinalStabilityHorizonVerifier did not confirm terminal stable long-term management yet. "
                + str(verification.get("rationale") or closure.rationale or "")
            ),
            evidence=list(closure.evidence or []) + [str(item) for item in (verification.get("evidence_satisfied") or [])],
            unresolved_threads=list(closure.unresolved_threads or []) + [str(item) for item in (verification.get("remaining_requirements") or [])],
            if_continued_next_focus=str(verification.get("if_continued_next_focus") or closure.if_continued_next_focus),
            metadata={
                **dict(closure.metadata or {}),
                "original_closure_before_stability_horizon": closure.to_dict(),
                "stability_horizon_plan": self.stability_horizon_plan,
                "stability_horizon_verification": verification,
                "terminal_stable_closure_downgraded_by_llm_verifier": True,
            },
        )
        self.trajectory.add_event(
            turn=turn,
            actor="StabilityHorizonVerifier",
            event_type="stability_horizon_closure_adjustment",
            content=adjusted.to_dict(),
            sim_time=self.current_sim_time,
            visibility="evaluator_visible",
        )
        return adjusted

    def _closure_claims_stable_long_term_management(self, closure: ClosureAssessmentLite) -> bool:
        """Return true when a terminal closure claims durable long-term management.

        Historical name kept for compatibility with existing event names/config.
        The semantics now cover both stable follow-up and goal-directed
        noncurative longitudinal management; cure/death closures are excluded.
        """
        if closure.status != "closed":
            return False
        metadata_text = json.dumps(closure.metadata or {}, ensure_ascii=False).lower()
        text = " ".join(
            [
                closure.closure_kind,
                closure.rationale,
                " ".join(closure.evidence or []),
                metadata_text,
            ]
        ).lower()
        durable_tokens = [
            "durable_longitudinal_management",
            "durable longitudinal",
            "stable_long_term",
            "long_term_management",
            "longitudinal_management",
            "长期管理",
            "长期随访",
            "稳定",
            "慢病",
            "随访",
            "二级预防",
            "生活质量",
            "功能目标",
        ]
        cure_or_death_tokens = ["cured", "terminal_closed_cured", "治愈", "death", "terminal_closed_death", "死亡"]
        return any(token in text for token in durable_tokens) and not any(token in text for token in cure_or_death_tokens)

    def _call_longitudinal_stability_horizon_planner(self, turn: int, closure: ClosureAssessmentLite) -> dict[str, Any]:
        system = load_prompt("longitudinal_stability_horizon_planner")
        user = json.dumps(
            {
                "case_context_full_for_horizon": self._case_context_for_prompt("longitudinal_stability_horizon_planner"),
                "case_evaluation_material": self._case_evaluation_material_for_backstage(turn, purpose="longitudinal_stability_horizon_planner"),
                "current_sim_time": self.current_sim_time,
                "proposed_closure": closure.to_dict(),
                "care_system_state": self._care_system_state_for_prompt("longitudinal_stability_horizon_planner"),
                "living_state_memory": self._living_state_memory_for_prompt(),
                "clinical_memory_backstage": self._clinical_memory_backstage_for_prompt("longitudinal_stability_horizon_planner"),
                "trajectory": self._trajectory_context_for_prompt("longitudinal_stability_horizon_planner"),
                "principle": "一案一议定义持久性长期管理闭环：既可包括稳定随访，也可包括非治愈但目标明确、风险受控、计划可执行、经反馈调整再确认的长期管理；不要把简单观察/转诊/支持治疗/姑息/生活质量话术当作闭环。",
            },
            ensure_ascii=False,
            indent=2,
        )
        raw = self._complete(
            self.simulator_llm,
            purpose="longitudinal_stability_horizon_planner",
            system=system,
            user=user,
            temperature=0.2,
            turn=turn,
        )
        parsed = extract_json_object(raw) or {"raw_horizon_plan": raw}
        parsed.setdefault("horizon_status", "planned_from_terminal_stability_attempt")
        parsed["turn"] = turn
        parsed["sim_time"] = self.current_sim_time
        self.trajectory.add_event(
            turn=turn,
            actor="LongitudinalStabilityHorizonPlanner",
            event_type="stability_horizon_plan",
            content=parsed,
            sim_time=self.current_sim_time,
            visibility="evaluator_visible",
        )
        return parsed

    def _call_stability_horizon_verifier(
        self,
        turn: int,
        closure: ClosureAssessmentLite,
        horizon_plan: dict[str, Any],
    ) -> dict[str, Any]:
        system = load_prompt("stability_horizon_verifier")
        user = json.dumps(
            {
                "case_context_full_for_verifier": self._case_context_for_prompt("stability_horizon_verifier"),
                "current_sim_time": self.current_sim_time,
                "proposed_closure": closure.to_dict(),
                "stability_horizon_plan": horizon_plan,
                "care_system_state": self._care_system_state_for_prompt("stability_horizon_verifier"),
                "living_state_memory": self._living_state_memory_for_prompt(),
                "clinical_memory_backstage": self._clinical_memory_backstage_for_prompt("stability_horizon_verifier"),
                "runtime_quality_evidence_so_far": self._runtime_quality_evidence_for_prompt("stability_horizon_verifier"),
                "trajectory": self._trajectory_context_for_prompt("stability_horizon_verifier"),
            },
            ensure_ascii=False,
            indent=2,
        )
        raw = self._complete(
            self.simulator_llm,
            purpose="stability_horizon_verifier",
            system=system,
            user=user,
            temperature=0.2,
            turn=turn,
        )
        parsed = extract_json_object(raw) or {"raw_verification": raw, "terminal_stable_management_satisfied": False}
        parsed.setdefault("terminal_stable_management_satisfied", False)
        parsed["turn"] = turn
        parsed["sim_time"] = self.current_sim_time
        self.trajectory.add_event(
            turn=turn,
            actor="StabilityHorizonVerifier",
            event_type="stability_horizon_verification",
            content=parsed,
            sim_time=self.current_sim_time,
            visibility="evaluator_visible",
        )
        return parsed

    def _stability_horizon_satisfied(self, verification: dict[str, Any]) -> bool:
        return bool(
            verification.get("terminal_durable_longitudinal_management_satisfied")
            or verification.get("terminal_stable_management_satisfied")
            or verification.get("satisfied")
            or str(verification.get("verdict") or "").strip().lower()
            in {"satisfied", "pass", "stable_terminal_ok", "durable_terminal_ok", "durable_longitudinal_management_ok"}
        )

    def _final_evaluation_mode(self, closure: ClosureAssessmentLite) -> str:
        """Select the final evaluator family without turning this into a flow rule.

        Terminal trajectories can be evaluated against full closure semantics.
        Non-terminal trajectories (user-cut/checkpoint fragments, max-turns at open,
        or milestone-only soft closures) need a different evaluator so that the
        report says what the fragment proves and what it cannot prove yet.
        """

        return "terminal_or_unsafe" if closure.is_terminal else "fragment_nonterminal"

    def _call_evaluation_weight_reconciler(
        self,
        turn: int,
        closure: ClosureAssessmentLite,
        *,
        evaluation_mode: str = "terminal_or_unsafe",
    ) -> dict[str, Any]:
        system = load_prompt("evaluation_weight_reconciler")
        case_evaluation_material = self._case_evaluation_material_for_backstage(turn, purpose="evaluation_weight_reconciler")
        user = json.dumps(
            {
                "evaluation_mode": evaluation_mode,
                "evaluation_lifecycle_context": self._evaluation_lifecycle_context(turn, closure, evaluation_mode),
                "case_context_full_for_weight_reconciliation": self._case_context_for_prompt("evaluation_weight_reconciler"),
                "case_evaluation_material": case_evaluation_material,
                "evaluation_dimension_catalog": self._evaluation_dimension_catalog_for_prompt("evaluation_weight_reconciler"),
                "closure_assessment": closure.to_dict(),
                "evaluation_evidence_pack": self._evaluation_evidence_pack(closure, consumer="evaluation_weight_reconciler"),
                "current_sim_time": self.current_sim_time,
                "living_state_memory": self._living_state_memory_for_prompt(),
                "clinical_memory_backstage": self._clinical_memory_backstage_for_prompt("evaluation_weight_reconciler"),
                "doctor_side_workspace_state": self._workspace_query_for_prompt(
                    ["records", "documents", "test_results", "medications", "care_access", "family_context", "timeline"],
                    reason="evaluation_weight_reconciler_review",
                    consumer="evaluation_weight_reconciler",
                ),
                "care_system_state": self._care_system_state_for_prompt("evaluation_weight_reconciler"),
                "runtime_quality_evidence": self._runtime_quality_evidence_for_prompt("evaluation_weight_reconciler", closure),
                "fcct2_runtime_context": self._fcct2_runtime_context_for_prompt("evaluation_weight_reconciler"),
                "trajectory": self._trajectory_context_for_prompt("evaluation_weight_reconciler"),
                "careloop_philosophy": (
                    "评估重点不是医学文本知识背诵，而是在复杂现实约束、患者/家属噪声、"
                    "资料不完整和长程执行中，AI 医生能否把患者尽可能带出医疗困境。"
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
        try:
            raw = self._complete(
                self.simulator_llm,
                purpose="evaluation_weight_reconciler",
                system=system,
                user=user,
                temperature=0.25,
                turn=turn,
                require_non_empty=True,
                empty_retries=self.config.evaluator_empty_response_retries,
            )
            parsed = self._weight_reconciliation_from_raw(raw)
        except Exception as exc:
            self._record_failed_llm_call(
                self.simulator_llm,
                purpose="evaluation_weight_reconciler",
                system=system,
                user=user,
                temperature=0.25,
                turn=turn,
                exc=exc,
            )
            parsed = self._weight_reconciliation_exception_fallback(
                exc,
                case_evaluation_material=case_evaluation_material,
                evaluation_mode=evaluation_mode,
            )
        self.trajectory.add_event(
            turn=turn,
            actor="EvaluationWeightReconciler",
            event_type="evaluation_weight_reconciliation",
            content=parsed,
            sim_time=self.current_sim_time,
            visibility="evaluator_visible",
        )
        return parsed

    def _evaluation_lifecycle_context(self, turn: int, closure: ClosureAssessmentLite, evaluation_mode: str) -> dict[str, Any]:
        event_types = [str(event.get("event_type") or "") for event in self.trajectory.events if isinstance(event, dict)]
        max_turns_reached = "max_turns_reached" in event_types
        return {
            "evaluation_mode": evaluation_mode,
            "turn": turn,
            "max_turns": self.config.max_turns,
            "closure_status": closure.status,
            "closure_kind": closure.closure_kind,
            "is_terminal": closure.is_terminal,
            "max_turns_reached": max_turns_reached,
            "likely_user_or_runtime_fragment": not closure.is_terminal,
            "principle": (
                "If the trajectory is not terminal closed/unsafe_stop, evaluate it as a non-terminal fragment: "
                "state what is already evidenced, what is not yet tested, and what should happen if continued."
            ),
        }

    def _attach_non_weighted_interface_observations(self, evaluation: dict[str, Any], *, turn: int) -> dict[str, Any]:
        """Attach a non-weighted interface discipline note to trajectory evaluation.

        This is deliberately outside clinical grading: it surfaces routing/envelope
        friction, workspace loop pressure, boundary rewrites, and patient-visible
        leakage signals as deployment/interface discipline observations only.
        """

        if not isinstance(evaluation, dict):
            return evaluation
        observation = interface_observation_from_events(self.trajectory.events, self.trajectory.transcript)
        attach_interface_observation(evaluation, observation)
        self.trajectory.add_event(
            turn=turn,
            actor="RuntimeLite",
            event_type="non_weighted_interface_observation",
            content=observation,
            sim_time=self.current_sim_time,
            visibility="evaluator_visible",
        )
        return evaluation

    def _call_final_evaluator(self, turn: int, closure: ClosureAssessmentLite) -> dict[str, Any]:
        evaluation_mode = self._final_evaluation_mode(closure)
        if evaluation_mode == "fragment_nonterminal":
            return self._call_fragment_trajectory_evaluator(turn, closure, evaluation_mode=evaluation_mode)
        return self._call_trajectory_evaluator(turn, closure, evaluation_mode=evaluation_mode)

    def _call_trajectory_evaluator(
        self,
        turn: int,
        closure: ClosureAssessmentLite,
        *,
        evaluation_mode: str = "terminal_or_unsafe",
    ) -> dict[str, Any]:
        weight_reconciliation = self._call_evaluation_weight_reconciler(turn, closure, evaluation_mode=evaluation_mode)
        system = load_prompt("trajectory_evaluator")
        case_evaluation_material = self._case_evaluation_material_for_backstage(turn, purpose="trajectory_evaluator")
        user = json.dumps(
            {
                "evaluation_mode": evaluation_mode,
                "evaluation_lifecycle_context": self._evaluation_lifecycle_context(turn, closure, evaluation_mode),
                "case_context_full_for_evaluation": self._case_context_for_prompt("trajectory_evaluator"),
                "case_evaluation_material": case_evaluation_material,
                "evaluation_dimension_catalog": self._evaluation_dimension_catalog_for_prompt("trajectory_evaluator"),
                "evaluation_weight_reconciliation": weight_reconciliation,
                "closure_assessment": closure.to_dict(),
                "evaluation_evidence_pack": self._evaluation_evidence_pack(closure, consumer="trajectory_evaluator"),
                "long_context_evidence_pack": self._long_context_evidence_pack_for_prompt(turn, closure, evaluation_mode),
                "current_sim_time": self.current_sim_time,
                "living_state_memory": self._living_state_memory_for_prompt(),
                "clinical_memory_backstage": self._clinical_memory_backstage_for_prompt("trajectory_evaluator"),
                "doctor_side_workspace_state": self._workspace_query_for_prompt(
                    ["records", "documents", "test_results", "medications", "care_access", "family_context", "timeline"],
                    reason="trajectory_evaluator_final_review",
                    consumer="trajectory_evaluator",
                ),
                "care_system_state": self._care_system_state_for_prompt("trajectory_evaluator"),
                "runtime_quality_evidence": self._runtime_quality_evidence_for_prompt("trajectory_evaluator", closure),
                "fcct2_runtime_context": self._fcct2_runtime_context_for_prompt("trajectory_evaluator"),
                "trajectory": self._trajectory_context_for_prompt("trajectory_evaluator"),
                "careloop_philosophy": (
                    "评估重点不是医学文本知识背诵，而是在复杂现实约束、患者/家属噪声、"
                    "资料不完整和长程执行中，AI 医生能否把患者尽可能带出医疗困境。"
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
        raw = self._complete(
            self.simulator_llm,
            purpose="trajectory_evaluator",
            system=system,
            user=user,
            temperature=0.25,
            turn=turn,
            require_non_empty=True,
            empty_retries=self.config.evaluator_empty_response_retries,
        )
        parsed = self._evaluation_from_raw(raw)
        parsed.setdefault("evaluation_mode", evaluation_mode)
        parsed = self._maybe_repair_evaluation(
            turn,
            closure,
            parsed,
            evaluation_mode=evaluation_mode,
            original_evaluator_purpose="trajectory_evaluator",
            case_evaluation_material=case_evaluation_material,
            weight_reconciliation=weight_reconciliation,
        )
        parsed = self._attach_non_weighted_interface_observations(parsed, turn=turn)
        self.trajectory.add_event(
            turn=turn,
            actor="TrajectoryEvaluator",
            event_type="trajectory_evaluation",
            content=parsed,
            sim_time=self.current_sim_time,
            visibility="evaluator_visible",
        )
        self._sync_long_context(
            turn,
            closure=closure,
            final_evaluation=parsed,
            reason="trajectory_evaluation",
            rollup=True,
            force_audit=True,
        )
        return parsed

    def _call_fragment_trajectory_evaluator(self, turn: int, closure: ClosureAssessmentLite, *, evaluation_mode: str) -> dict[str, Any]:
        weight_reconciliation = self._call_evaluation_weight_reconciler(turn, closure, evaluation_mode=evaluation_mode)
        system = load_prompt("fragment_trajectory_evaluator")
        case_evaluation_material = self._case_evaluation_material_for_backstage(turn, purpose="fragment_trajectory_evaluator")
        user = json.dumps(
            {
                "evaluation_mode": evaluation_mode,
                "evaluation_lifecycle_context": self._evaluation_lifecycle_context(turn, closure, evaluation_mode),
                "case_context_full_for_evaluation": self._case_context_for_prompt("fragment_trajectory_evaluator"),
                "case_evaluation_material": case_evaluation_material,
                "evaluation_dimension_catalog": self._evaluation_dimension_catalog_for_prompt("fragment_trajectory_evaluator"),
                "evaluation_weight_reconciliation": weight_reconciliation,
                "closure_assessment": closure.to_dict(),
                "evaluation_evidence_pack": self._evaluation_evidence_pack(closure, consumer="fragment_trajectory_evaluator"),
                "long_context_evidence_pack": self._long_context_evidence_pack_for_prompt(turn, closure, evaluation_mode),
                "current_sim_time": self.current_sim_time,
                "living_state_memory": self._living_state_memory_for_prompt(),
                "clinical_memory_backstage": self._clinical_memory_backstage_for_prompt("fragment_trajectory_evaluator"),
                "doctor_side_workspace_state": self._workspace_query_for_prompt(
                    ["records", "documents", "test_results", "medications", "care_access", "family_context", "timeline"],
                    reason="fragment_trajectory_evaluator_final_review",
                    consumer="fragment_trajectory_evaluator",
                ),
                "care_system_state": self._care_system_state_for_prompt("fragment_trajectory_evaluator"),
                "runtime_quality_evidence": self._runtime_quality_evidence_for_prompt("fragment_trajectory_evaluator", closure),
                "fcct2_runtime_context": self._fcct2_runtime_context_for_prompt("fragment_trajectory_evaluator"),
                "trajectory": self._trajectory_context_for_prompt("fragment_trajectory_evaluator"),
                "careloop_philosophy": (
                    "非闭环片段评估不能假装终局已完成；重点是判断截至截断点，AI 医生已经暴露出的现实医疗接管能力、"
                    "风险与尚未测试部分。"
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
        raw = self._complete(
            self.simulator_llm,
            purpose="fragment_trajectory_evaluator",
            system=system,
            user=user,
            temperature=0.25,
            turn=turn,
            require_non_empty=True,
            empty_retries=self.config.evaluator_empty_response_retries,
        )
        parsed = self._evaluation_from_raw(raw)
        parsed.setdefault("evaluation_mode", evaluation_mode)
        parsed = self._maybe_repair_evaluation(
            turn,
            closure,
            parsed,
            evaluation_mode=evaluation_mode,
            original_evaluator_purpose="fragment_trajectory_evaluator",
            case_evaluation_material=case_evaluation_material,
            weight_reconciliation=weight_reconciliation,
        )
        parsed = self._attach_non_weighted_interface_observations(parsed, turn=turn)
        self.trajectory.add_event(
            turn=turn,
            actor="FragmentTrajectoryEvaluator",
            event_type="fragment_trajectory_evaluation",
            content=parsed,
            sim_time=self.current_sim_time,
            visibility="evaluator_visible",
        )
        self._sync_long_context(
            turn,
            closure=closure,
            final_evaluation=parsed,
            reason="fragment_trajectory_evaluation",
            rollup=True,
            force_audit=True,
        )
        return parsed

    def _maybe_repair_evaluation(
        self,
        turn: int,
        closure: ClosureAssessmentLite,
        parsed: dict[str, Any],
        *,
        evaluation_mode: str,
        original_evaluator_purpose: str,
        case_evaluation_material: dict[str, Any],
        weight_reconciliation: dict[str, Any],
    ) -> dict[str, Any]:
        if not self._evaluation_requires_repair(parsed):
            return parsed
        original_failure_type = self._evaluation_failure_type(parsed)
        try:
            repaired = self._call_evaluation_repair_evaluator(
                turn,
                closure,
                failed_evaluation=parsed,
                evaluation_mode=evaluation_mode,
                original_evaluator_purpose=original_evaluator_purpose,
                original_failure_type=original_failure_type,
                case_evaluation_material=case_evaluation_material,
                weight_reconciliation=weight_reconciliation,
            )
        except Exception as exc:
            repaired = {}
            self.trajectory.add_event(
                turn=turn,
                actor="EvaluationRepairEvaluator",
                event_type="evaluation_repair_failed",
                content={
                    "original_evaluator_purpose": original_evaluator_purpose,
                    "original_failure_type": original_failure_type,
                    "repair_failure_type": type(exc).__name__,
                    "repair_failure_preview": self._compact_text(str(exc) or type(exc).__name__, limit=300),
                    "principle": "Evaluation repair is a best-effort backstage fallback; failure preserves the original explicit evaluation_failed object.",
                },
                sim_time=self.current_sim_time,
                visibility="evaluator_visible",
            )
        if not isinstance(repaired, dict) or not repaired:
            parsed.setdefault("repair_metadata", {})
            if isinstance(parsed["repair_metadata"], dict):
                parsed["repair_metadata"].update(
                    {
                        "repair_used": False,
                        "repair_attempted": True,
                        "repair_success": False,
                        "original_evaluator_purpose": original_evaluator_purpose,
                        "original_failure_type": original_failure_type,
                    }
                )
            return parsed
        repaired["evaluation_mode"] = evaluation_mode
        repair_metadata = repaired.setdefault("repair_metadata", {})
        if isinstance(repair_metadata, dict):
            repair_metadata.update(
                {
                    "repair_used": True,
                    "repair_attempted": True,
                    "repair_success": True,
                    "original_evaluator_purpose": original_evaluator_purpose,
                    "original_failure_type": original_failure_type,
                    "principle": (
                        "Main evaluator returned empty/unparseable content; compact repair evaluator produced this bounded-confidence report."
                    ),
                }
            )
        integrity = repaired.setdefault("infrastructure_integrity", {})
        if isinstance(integrity, dict):
            integrity.setdefault("status", "evaluation_repaired")
            integrity.setdefault("main_evaluator_failure_type", original_failure_type)
            integrity.setdefault("requires_rerun_or_repair", False)
            integrity.setdefault("repair_used", True)
        self.trajectory.add_event(
            turn=turn,
            actor="EvaluationRepairEvaluator",
            event_type="evaluation_repair",
            content={
                "original_evaluator_purpose": original_evaluator_purpose,
                "original_failure_type": original_failure_type,
                "repair_overall": repaired.get("overall"),
                "repair_claim_strength": repaired.get("evaluation_claim_strength"),
                "principle": (
                    "Repair uses a compact evaluator prompt after the main final evaluator failed. "
                    "It is an LLM judgement with explicit limitations, not a deterministic score formula."
                ),
            },
            sim_time=self.current_sim_time,
            visibility="evaluator_visible",
        )
        return repaired

    def _evaluation_requires_repair(self, parsed: dict[str, Any]) -> bool:
        if not isinstance(parsed, dict):
            return False
        if parsed.get("overall") != "evaluation_failed":
            return False
        integrity = parsed.get("infrastructure_integrity") if isinstance(parsed.get("infrastructure_integrity"), dict) else {}
        return bool(integrity.get("requires_rerun_or_repair"))

    def _evaluation_failure_type(self, parsed: dict[str, Any]) -> str:
        integrity = parsed.get("infrastructure_integrity") if isinstance(parsed.get("infrastructure_integrity"), dict) else {}
        return str(integrity.get("failure_type") or "unknown_evaluator_failure")

    def _call_evaluation_repair_evaluator(
        self,
        turn: int,
        closure: ClosureAssessmentLite,
        *,
        failed_evaluation: dict[str, Any],
        evaluation_mode: str,
        original_evaluator_purpose: str,
        original_failure_type: str,
        case_evaluation_material: dict[str, Any],
        weight_reconciliation: dict[str, Any],
    ) -> dict[str, Any]:
        system = load_prompt("evaluation_repair_evaluator")
        user = json.dumps(
            {
                "evaluation_mode": evaluation_mode,
                "repair_reason": {
                    "original_evaluator_purpose": original_evaluator_purpose,
                    "original_failure_type": original_failure_type,
                    "failed_evaluation_summary": self._bounded_prompt_payload(
                        failed_evaluation,
                        char_budget=1800,
                        text_limit=220,
                        list_limit=4,
                        label="evaluation_repair_failed_evaluation",
                    ),
                },
                "evaluation_lifecycle_context": self._evaluation_lifecycle_context(turn, closure, evaluation_mode),
                "closure_assessment": closure.to_dict(),
                "case_evaluation_material_compact": self._bounded_prompt_payload(
                    case_evaluation_material,
                    char_budget=3500,
                    text_limit=240,
                    list_limit=5,
                    label="evaluation_repair_case_material",
                ),
                "evaluation_weight_reconciliation_compact": self._bounded_prompt_payload(
                    weight_reconciliation,
                    char_budget=3000,
                    text_limit=220,
                    list_limit=5,
                    label="evaluation_repair_weight_reconciliation",
                ),
                "runtime_quality_evidence_compact": self._runtime_quality_evidence_for_prompt("evaluation_repair_evaluator", closure),
                "trajectory_compact": self._trajectory_context_for_prompt("evaluation_repair_evaluator"),
                "recent_transcript": self._recent_transcript_for_prompt("evaluation_repair_evaluator"),
                "clinical_memory_backstage_compact": self._clinical_memory_backstage_for_prompt("evaluation_repair_evaluator"),
                "care_system_state_compact": self._care_system_state_for_prompt("evaluation_repair_evaluator"),
                "doctor_side_workspace_state_compact": self._workspace_query_for_prompt(
                    ["records", "documents", "test_results", "medications", "care_access", "family_context", "timeline"],
                    reason="evaluation_repair_review",
                    consumer="evaluation_repair_evaluator",
                ),
                "careloop_philosophy": (
                    "CareLoop 评估 AI 医生在真实世界复杂约束下的连续照护接管能力。"
                    "修复评估必须诚实标注证据局限，区分 AI 医生贡献、患者安全、外部医疗系统贡献和框架污染。"
                ),
                "required_output": (
                    "Return one compact valid JSON object only. Do not return prose outside JSON. "
                    "If this is a non-terminal fragment, do not claim terminal clinical closure. "
                    "If evidence is weak, use insufficient_fragment/weak_fragment or limited validity rather than inventing certainty."
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
        raw = self._complete(
            self.simulator_llm,
            purpose="evaluation_repair_evaluator",
            system=system,
            user=user,
            temperature=0.2,
            turn=turn,
            require_non_empty=True,
            empty_retries=self.config.evaluator_repair_empty_response_retries,
        )
        parsed = extract_json_object(raw)
        if isinstance(parsed, dict) and parsed:
            return parsed
        self.trajectory.add_event(
            turn=turn,
            actor="EvaluationRepairEvaluator",
            event_type="evaluation_repair_unparseable",
            content={
                "original_evaluator_purpose": original_evaluator_purpose,
                "original_failure_type": original_failure_type,
                "repair_raw_preview": self._compact_text(raw, limit=400),
                "repair_raw_empty": not str(raw or "").strip(),
                "principle": "Repair evaluator returned no parseable JSON; original explicit evaluation_failed object remains authoritative.",
            },
            sim_time=self.current_sim_time,
            visibility="evaluator_visible",
        )
        return {}

    def _runtime_quality_evidence(self, closure: ClosureAssessmentLite | None = None) -> dict[str, Any]:
        payload = {
            "case_id": self.case.case_id,
            "run_id": self.config.run_id,
            "turns_completed": self._last_completed_turn(),
            "closure": closure.to_dict() if closure is not None else {},
            "trajectory": self.trajectory.to_dict(),
            "evaluation": {},
            "metadata": {"max_turns": self.config.max_turns},
        }
        report = analyze_lite_trajectory(payload)
        metrics = report.metrics
        return {
            "status": report.status,
            "flags": report.flags,
            "notes": report.notes,
            "care_loop_shape": metrics.get("care_loop_shape"),
            "care_loop_phase_count": metrics.get("care_loop_phase_count"),
            "care_loop": metrics.get("care_loop") or {},
            "care_process_step_count": metrics.get("care_process_step_count"),
            "longitudinal_care_process": metrics.get("longitudinal_care_process") or {},
            "trajectory_progression_status": metrics.get("trajectory_progression_status"),
            "trajectory_progression": metrics.get("trajectory_progression") or {},
            "key_metrics": {
                "turns_completed": metrics.get("turns_completed"),
                "workspace_result_count": metrics.get("workspace_result_count"),
                "care_system_receipt_count": metrics.get("care_system_receipt_count"),
                "care_system_state_update_count": metrics.get("care_system_state_update_count"),
                "workspace_state_update_count": metrics.get("workspace_state_update_count"),
                "material_progress_signal_count": metrics.get("material_progress_signal_count"),
                "material_progress_turn_count": metrics.get("material_progress_turn_count"),
                "committed_world_event_count": metrics.get("committed_world_event_count"),
                "care_process_step_count": metrics.get("care_process_step_count"),
                "total_elapsed_minutes": metrics.get("total_elapsed_minutes"),
                "closure_status": metrics.get("closure_status"),
            },
        }

    def _runtime_quality_evidence_for_prompt(self, consumer: str, closure: ClosureAssessmentLite | None = None) -> dict[str, Any]:
        evidence = self._runtime_quality_evidence(closure)
        profile = self._prompt_context_profile(consumer)
        return self._bounded_prompt_payload(
            evidence,
            char_budget=profile["quality_budget"],
            text_limit=profile["preview_limit"],
            list_limit=profile["list_limit"],
            label=f"runtime_quality_evidence:{consumer}",
        )

    def _last_completed_turn(self) -> int:
        turns: list[int] = []
        for event in self.trajectory.events:
            try:
                turns.append(int(event.get("turn") or 0))
            except (TypeError, ValueError):
                pass
        return max(turns) if turns else 0

    def _evaluation_from_raw(self, raw: str) -> dict[str, Any]:
        parsed = extract_json_object(raw)
        if parsed:
            return parsed
        if not str(raw or "").strip():
            return {
                "overall": "evaluation_failed",
                "summary": "Final TrajectoryEvaluator returned empty content after runtime empty-content retries; no clinical performance judgement was produced.",
                "simulation_validity": {
                    "status": "not_assessed",
                    "rationale": (
                        "Simulation validity cannot be inferred from an empty evaluator response. "
                        "This is an evaluator/infrastructure failure marker, not a judgement that the trajectory itself was limited."
                    ),
                    "limitations": ["empty_evaluator_response"],
                },
                "infrastructure_integrity": {
                    "status": "evaluation_failed",
                    "failure_type": "empty_evaluator_response",
                    "requires_rerun_or_repair": True,
                },
                "raw_evaluator_output": raw,
            }
        return {
            "overall": "evaluation_failed",
            "summary": self._compact_text(raw, limit=240) or "TrajectoryEvaluator returned unparseable content.",
            "simulation_validity": {
                "status": "not_assessed",
                "rationale": (
                    "TrajectoryEvaluator returned content that could not be parsed into the expected evaluation object. "
                    "The runtime preserved the raw output but cannot convert parse failure into a clinical validity judgement."
                ),
                "limitations": ["evaluator_unparseable_output"],
            },
            "infrastructure_integrity": {
                "status": "evaluation_failed",
                "failure_type": "unparseable_evaluator_output",
                "requires_rerun_or_repair": True,
            },
            "raw_evaluator_output": raw,
        }

    def _weight_reconciliation_from_raw(self, raw: str) -> dict[str, Any]:
        parsed = extract_json_object(raw)
        if parsed:
            return parsed
        if not str(raw or "").strip():
            return {
                "prior_contract_weights": [],
                "trajectory_emergent_weights": [],
                "final_trajectory_specific_weights": [],
                "reweighting_rationale": "EvaluationWeightReconciler returned empty content after runtime empty-content retries; dynamic weights were not assessed.",
                "limitations": ["empty_weight_reconciler_response"],
                "infrastructure_integrity": {
                    "status": "weight_reconciliation_failed",
                    "failure_type": "empty_weight_reconciler_response",
                    "requires_rerun_or_repair": True,
                },
                "raw_weight_reconciler_output": raw,
            }
        return {
            "prior_contract_weights": [],
            "trajectory_emergent_weights": [],
            "final_trajectory_specific_weights": [],
            "reweighting_rationale": self._compact_text(raw, limit=240)
            or "EvaluationWeightReconciler returned unparseable content.",
            "limitations": ["weight_reconciler_unparseable_output"],
            "infrastructure_integrity": {
                "status": "weight_reconciliation_failed",
                "failure_type": "unparseable_weight_reconciler_output",
                "requires_rerun_or_repair": True,
            },
            "raw_weight_reconciler_output": raw,
        }

    def _weight_reconciliation_exception_fallback(
        self,
        exc: Exception,
        *,
        case_evaluation_material: dict[str, Any],
        evaluation_mode: str,
    ) -> dict[str, Any]:
        """Return a transparent fallback when the weight reconciler is unavailable.

        Weight reconciliation is a helpful LLM judgement layer, but it should not
        be a single point of failure for the final trajectory evaluation.  This
        fallback preserves authored prior weights when available, explicitly
        states that dynamic weighting was not reconciled by the separate node,
        and asks the final evaluator to judge cautiously from the case contract
        and trajectory evidence instead of treating the whole evaluation as
        failed.
        """

        prior_weights = self._prior_contract_weight_items(case_evaluation_material)
        failure_type = type(exc).__name__
        return {
            "evaluation_mode": evaluation_mode,
            "weight_reconciliation_available": False,
            "fallback_policy": "weight_reconciler_exception_preserve_final_evaluation",
            "prior_contract_weights": prior_weights,
            "trajectory_emergent_weights": [],
            "final_trajectory_specific_weights": [],
            "trajectory_specific_weights": [],
            "reweighting_rationale": (
                "EvaluationWeightReconciler did not complete. Preserve authored prior weights if present, "
                "then let the final evaluator perform cautious trajectory-specific judgement directly from "
                "the case evaluation contract, trajectory evidence, discoverability/actionability, and responsibility attribution."
            ),
            "limitations": ["weight_reconciler_exception_fallback", f"weight_reconciler_{failure_type}"],
            "infrastructure_integrity": {
                "status": "weight_reconciliation_fallback",
                "failure_type": failure_type,
                "requires_rerun_or_repair": False,
                "weight_precision_limited": True,
            },
            "responsibility_attribution_notes": [
                "Do not convert weight-reconciler failure into a doctor-performance failure.",
                "Do mention that dynamic weight precision is limited if it affects confidence in this report.",
            ],
            "raw_weight_reconciler_output": "",
            "failure_preview": self._compact_text(str(exc) or failure_type, limit=240),
        }

    def _prior_contract_weight_items(self, case_evaluation_material: dict[str, Any]) -> list[dict[str, Any]]:
        if not isinstance(case_evaluation_material, dict):
            return []
        contract = case_evaluation_material.get("evaluation_contract")
        if not isinstance(contract, dict):
            return []
        weights = contract.get("dimension_weights") or contract.get("prior_weight_profile")
        items: list[dict[str, Any]] = []
        if isinstance(weights, dict):
            for dimension, relative_weight in list(weights.items())[:16]:
                if not str(dimension or "").strip():
                    continue
                items.append(
                    {
                        "dimension": str(dimension),
                        "relative_weight": str(relative_weight),
                        "source": "case evaluation_contract prior weights",
                        "reason": "Authored case prior preserved because EvaluationWeightReconciler was unavailable.",
                    }
                )
        elif isinstance(weights, list):
            for item in weights[:16]:
                if isinstance(item, dict):
                    dimension = item.get("dimension") or item.get("dimension_id") or item.get("id")
                    if not str(dimension or "").strip():
                        continue
                    items.append(
                        {
                            "dimension": str(dimension),
                            "relative_weight": str(item.get("relative_weight") or item.get("weight") or "unspecified"),
                            "source": str(item.get("source") or "case evaluation_contract prior weights"),
                            "reason": str(item.get("reason") or "Authored case prior preserved because EvaluationWeightReconciler was unavailable."),
                        }
                    )
        return items

    def _record_failed_llm_call(
        self,
        client: LiteLLMClient,
        *,
        purpose: str,
        system: str,
        user: str,
        temperature: float,
        turn: int,
        exc: Exception,
    ) -> None:
        digest = sha256((system + "\n" + user).encode("utf-8")).hexdigest()
        metadata = self._safe_llm_call_metadata(client, temperature=temperature)
        metadata.update(
            {
                "status": metadata.get("status") or "error",
                "error_type": metadata.get("error_type") or type(exc).__name__,
                "runtime_exception_captured": True,
                "runtime_exception_preview": self._compact_text(str(exc) or type(exc).__name__, limit=240),
            }
        )
        self.trajectory.add_llm_call(
            turn=turn,
            purpose=purpose,
            prompt_digest=digest,
            result="",
            metadata=metadata,
        )

    def _complete(
        self,
        client: LiteLLMClient,
        *,
        purpose: str,
        system: str,
        user: str,
        temperature: float,
        turn: int,
        require_non_empty: bool = False,
        empty_retries: int = 0,
    ) -> str:
        digest = sha256((system + "\n" + user).encode("utf-8")).hexdigest()
        attempts = max(1, int(empty_retries) + 1) if require_non_empty else 1
        last_result = ""
        for runtime_attempt in range(1, attempts + 1):
            self._write_runtime_phase(
                phase="llm_call",
                turn=turn,
                current_stage=purpose,
                extra={"runtime_empty_retry_attempt": runtime_attempt, "runtime_empty_retry_max_attempts": attempts},
            )
            try:
                result = client.complete(purpose=purpose, system=system, user=user, temperature=temperature)
            except Exception as exc:
                self._write_runtime_phase(
                    phase="llm_error",
                    turn=turn,
                    current_stage=purpose,
                    extra={"runtime_llm_failure_type": type(exc).__name__},
                )
                metadata = self._safe_llm_call_metadata(client, temperature=temperature)
                metadata.update(
                    {
                        "runtime_empty_content_policy": "reject_and_retry" if require_non_empty else "allow",
                        "runtime_empty_retry_attempt": runtime_attempt,
                        "runtime_empty_retry_max_attempts": attempts,
                        "runtime_llm_call_failed": True,
                        "runtime_llm_failure_type": type(exc).__name__,
                        "runtime_llm_failure_preview": self._compact_text(str(exc) or type(exc).__name__, limit=500),
                    }
                )
                self.trajectory.add_llm_call(
                    turn=turn,
                    purpose=purpose,
                    prompt_digest=digest,
                    result="",
                    metadata=metadata,
                )
                raise
            last_result = result
            empty_result = not str(result or "").strip()
            metadata = self._safe_llm_call_metadata(client, temperature=temperature)
            metadata.update(
                {
                    "runtime_empty_content_policy": "reject_and_retry" if require_non_empty else "allow",
                    "runtime_empty_retry_attempt": runtime_attempt,
                    "runtime_empty_retry_max_attempts": attempts,
                    "runtime_empty_output": bool(empty_result),
                    "runtime_empty_output_rejected": bool(require_non_empty and empty_result),
                }
            )
            self.trajectory.add_llm_call(
                turn=turn,
                purpose=purpose,
                prompt_digest=digest,
                result=result,
                metadata=metadata,
            )
            if not require_non_empty or not empty_result:
                self._write_runtime_phase(phase="llm_done", turn=turn, current_stage=purpose)
                return result
            self._write_runtime_phase(phase="llm_empty_retry", turn=turn, current_stage=purpose, extra={"runtime_empty_retry_attempt": runtime_attempt})
        self._write_runtime_phase(phase="llm_done_empty", turn=turn, current_stage=purpose)
        return last_result

    def _safe_llm_call_metadata(self, client: LiteLLMClient, *, temperature: float) -> dict[str, Any]:
        """Return non-sensitive provider metadata for trajectory audit.

        Real API validation needs to prove which model each node used, but the
        trajectory must never store prompt bodies, API keys, Authorization
        headers, or raw provider errors.  OpenAICompatibleLiteLLMClient already
        records only redacted per-attempt metadata; this helper whitelists the
        safe fields and ignores everything else, including Scripted client
        prompt text.
        """

        metadata: dict[str, Any] = {"requested_temperature": temperature}
        calls = getattr(client, "calls", None)
        if not isinstance(calls, list) or not calls:
            return metadata
        latest = calls[-1]
        if not isinstance(latest, dict):
            return metadata
        allowed = {
            "purpose",
            "model",
            "base_url",
            "temperature",
            "temperature_omitted",
            "max_tokens",
            "stream",
            "timeout_seconds",
            "attempt",
            "max_attempts",
            "retry_until_success",
            "provider_prompt_sanitized",
            "provider_prompt_sha256",
            "original_provider_prompt_sha256",
            "content_filter_rejection",
            "content_filter_provider_signal",
            "content_filter_filtered_categories",
            "content_filter_retry_used",
            "content_filter_retry_count",
            "content_filter_retry_mode",
            "content_filter_retry_history",
            "content_filter_semantic_preservation",
            "retry_sleep_seconds",
            "status",
            "error_type",
            "http_status",
            "choices_count",
            "finish_reason",
            "message_content_present",
            "message_role",
            "response_text_empty",
            "response_id_present",
            "prompt_tokens",
            "completion_tokens",
            "input_tokens",
            "cached_input_tokens",
            "uncached_input_tokens",
            "output_tokens",
            "total_tokens",
            "provider_usage_available",
            "provider_cache_read_tokens",
            "provider_cache_write_tokens",
            "result_cache_hit",
            "prefix_cache_hit",
            "context_composition_ref",
        }
        for key in allowed:
            if key in latest:
                metadata[key] = latest.get(key)
        return metadata
