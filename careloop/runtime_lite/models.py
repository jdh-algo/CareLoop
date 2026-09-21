from __future__ import annotations

"""Small runtime_lite data objects.

These dataclasses are intentionally not validation schemas.  They are audit and
adapter objects that make runtime decisions inspectable while keeping judgement
inside LLM-native prompts and hard-boundary services.
"""

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping


JsonDict = dict[str, Any]


def _as_dict(value: Any) -> JsonDict:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _as_str_list(value: Any) -> list[str]:
    return [str(item) for item in _as_list(value) if str(item).strip()]


def _director_event_items(raw: Mapping[str, Any]) -> list[JsonDict]:
    """Collect WorldDirector event proposals from LLM-friendly aliases.

    runtime_lite keeps the Python field name ``committed_events`` for backward
    compatibility with early tests, but the prompt should be free to use more
    natural names such as ``world_events`` or ``candidate_events``.  Candidate
    buckets default to candidate status so an LLM can express uncertainty
    without accidentally turning a possible event into actor-lived reality.
    """

    collected: list[JsonDict] = []
    seen: set[str] = set()

    def append_items(value: Any, *, default_status: str = "") -> None:
        for item in _as_list(value):
            if not isinstance(item, Mapping):
                continue
            payload = dict(item)
            if default_status and not str(payload.get("status") or "").strip():
                payload["status"] = default_status
            identity = "|".join(
                [
                    str(payload.get("event_id") or payload.get("id") or payload.get("event_key") or ""),
                    str(payload.get("title") or payload.get("name") or ""),
                    str(payload.get("description") or payload.get("event") or payload.get("beat") or ""),
                    str(payload.get("status") or ""),
                ]
            )
            if identity in seen:
                continue
            seen.add(identity)
            collected.append(payload)

    append_items(raw.get("world_events"))
    append_items(raw.get("committed_events"))
    append_items(raw.get("events"))
    for key in ("candidate_events", "event_candidates", "uncertain_events", "uncertain_event_candidates", "possible_events"):
        append_items(raw.get(key), default_status="candidate")
    return collected


def _as_float(value: Any, default: float | None = None) -> float | None:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return max(0.0, min(1.0, float(value)))
    if isinstance(value, str):
        try:
            return max(0.0, min(1.0, float(value.strip())))
        except ValueError:
            return default
    return default


def _as_number(value: Any, default: float | None = None) -> float | None:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return default
    return default


def _normalize_doctor_operation(value: Any) -> str:
    raw = str(value or "").strip()
    key = raw.lower().replace(" ", "_").replace("-", "_")
    aliases = {
        "clinical_workspace": "clinical_workspace.query",
        "clinical_workspace_query": "clinical_workspace.query",
        "clinical_workspace.query": "clinical_workspace.query",
        "workspace": "clinical_workspace.query",
        "workspace_query": "clinical_workspace.query",
        "workspace.query": "clinical_workspace.query",
        "query": "clinical_workspace.query",
        "query_workspace": "clinical_workspace.query",
        "query_records": "clinical_workspace.query",
        "records_query": "clinical_workspace.query",
        "records.query": "clinical_workspace.query",
        "record_query": "clinical_workspace.query",
        "test_results_query": "clinical_workspace.query",
        "conversation_history": "conversation_history.query",
        "conversation_history_query": "conversation_history.query",
        "conversation_history.query": "conversation_history.query",
        "chat_history": "conversation_history.query",
        "chat_history_query": "conversation_history.query",
        "chat_history.query": "conversation_history.query",
        "transcript": "conversation_history.query",
        "transcript_query": "conversation_history.query",
        "transcript.query": "conversation_history.query",
        "raw_history_query": "conversation_history.query",
        "doctor_memory": "doctor_memory.query",
        "doctor_memory_query": "doctor_memory.query",
        "doctor_memory.query": "doctor_memory.query",
        "doctor_notes": "doctor_memory.query",
        "doctor_notes_query": "doctor_memory.query",
        "doctor_notes.query": "doctor_memory.query",
        "doctor_memory_update": "doctor_memory.update",
        "doctor_memory.update": "doctor_memory.update",
        "doctor_note_update": "doctor_memory.update",
        "doctor_notes_update": "doctor_memory.update",
        "save_doctor_note": "doctor_memory.update",
        "save_note": "doctor_memory.update",
        "care_system_query": "care_system.query_status",
        "care_system.query": "care_system.query_status",
        "care_system_query_status": "care_system.query_status",
        "care_system.query_status": "care_system.query_status",
        "care_status_query": "care_system.query_status",
        "operation_status_query": "care_system.query_status",
        "order_test": "care_system.order_test",
        "test_order": "care_system.order_test",
        "lab_order": "care_system.order_test",
        "care_system.order_test": "care_system.order_test",
        "prescribe": "care_system.prescribe",
        "prescription": "care_system.prescribe",
        "medication_order": "care_system.prescribe",
        "care_system.prescribe": "care_system.prescribe",
        "followup": "care_system.schedule_followup",
        "schedule_followup": "care_system.schedule_followup",
        "care_system.schedule_followup": "care_system.schedule_followup",
        "track_result": "care_system.track_result",
        "result_tracking": "care_system.track_result",
        "follow_result": "care_system.track_result",
        "result_followup": "care_system.track_result",
        "care_system.track_result": "care_system.track_result",
        "referral": "care_system.referral",
        "refer": "care_system.referral",
        "care_system.referral": "care_system.referral",
        "emergency": "care_system.call_emergency",
        "call_ems": "care_system.call_emergency",
        "call_emergency": "care_system.call_emergency",
        "care_system.call_emergency": "care_system.call_emergency",
    }
    return aliases.get(key, raw or "clinical_workspace.query")


@dataclass
class LiteProbabilityTask:
    """A request to define a probability before one seeded occurrence roll.

    The LLM may estimate the probability from real-world facts and trajectory
    context, but it must not decide whether the event happened.  Occurrence is
    decided by the shared ProbabilityKernel.
    """

    event_id: str
    event_type: str
    owner: str = "WorldDirector"
    question: str = ""
    probability_mode: str = "runtime_llm"
    probability: float | None = None
    descriptor: str = "moderate"
    basis: str = ""
    factors: list[JsonDict] = field(default_factory=list)
    allowed: bool = True
    preconditions_satisfied: bool = True
    fail_closed_reason: str = ""
    creates_world_fact: bool = True
    world_fact_type: str | None = None
    sticky: bool = True
    metadata: JsonDict = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "LiteProbabilityTask":
        event_id = str(raw.get("event_id") or raw.get("id") or raw.get("event_key") or "").strip()
        event_type = str(raw.get("event_type") or raw.get("type") or "world_event").strip()
        return cls(
            event_id=event_id,
            event_type=event_type,
            owner=str(raw.get("owner") or "WorldDirector"),
            question=str(raw.get("question") or raw.get("event_question") or raw.get("description") or ""),
            probability_mode=str(raw.get("probability_mode") or raw.get("mode") or "runtime_llm"),
            probability=_as_float(raw.get("probability", raw.get("estimated_probability"))),
            descriptor=str(raw.get("descriptor") or raw.get("probability_descriptor") or "moderate"),
            basis=str(raw.get("basis") or raw.get("probability_basis") or raw.get("rationale") or ""),
            factors=[_as_dict(item) for item in _as_list(raw.get("factors") or raw.get("probability_factors"))],
            allowed=bool(raw.get("allowed", True)),
            preconditions_satisfied=bool(raw.get("preconditions_satisfied", True)),
            fail_closed_reason=str(raw.get("fail_closed_reason") or ""),
            creates_world_fact=bool(raw.get("creates_world_fact", True)),
            world_fact_type=raw.get("world_fact_type"),
            sticky=bool(raw.get("sticky", True)),
            metadata=_as_dict(raw.get("metadata")),
        )

    def to_probability_intent(self) -> JsonDict:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "owner": self.owner,
            "requesting_agent": "runtime_lite.WorldDirector",
            "descriptor": self.descriptor,
            "probability": self.probability,
            "probability_mode": self.probability_mode,
            "probability_basis": self.basis,
            "probability_factors": self.factors,
            "probability_task": {
                "question": self.question,
                "lite_runtime": True,
            },
            "allowed": self.allowed,
            "preconditions_satisfied": self.preconditions_satisfied,
            "fail_closed_reason": self.fail_closed_reason,
            "creates_world_fact": self.creates_world_fact,
            "world_fact_type": self.world_fact_type,
            "sticky": self.sticky,
            "metadata": self.metadata,
        }

    def to_dict(self) -> JsonDict:
        return asdict(self)


@dataclass
class TimeAdvanceRequest:
    """Director's soft request for realistic virtual time movement."""

    reason: str = ""
    scene_change: str = ""
    requested_basis: str = ""
    lower_minutes: float | None = None
    upper_minutes: float | None = None
    urgency: str = "ordinary"
    blocks_next_actor_reply: bool = False
    metadata: JsonDict = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "TimeAdvanceRequest":
        return cls(
            reason=str(raw.get("reason") or raw.get("why") or raw.get("description") or ""),
            scene_change=str(raw.get("scene_change") or raw.get("scene") or ""),
            requested_basis=str(raw.get("requested_basis") or raw.get("basis") or ""),
            lower_minutes=_as_number(raw.get("lower_minutes"), None) if raw.get("lower_minutes") is not None else None,
            upper_minutes=_as_number(raw.get("upper_minutes"), None) if raw.get("upper_minutes") is not None else None,
            urgency=str(raw.get("urgency") or "ordinary"),
            blocks_next_actor_reply=bool(raw.get("blocks_next_actor_reply", False)),
            metadata=_as_dict(raw.get("metadata")),
        )

    def to_dict(self) -> JsonDict:
        return asdict(self)


@dataclass
class LiteEventCandidate:
    """A possible or committed world beat proposed by the director."""

    event_id: str
    title: str = ""
    description: str = ""
    status: str = "committed"
    visible_to_doctor: bool = False
    visible_to_patient_or_family: bool = True
    affected_actors: list[str] = field(default_factory=list)
    probability_task: LiteProbabilityTask | None = None
    time_request: TimeAdvanceRequest | None = None
    consequences: list[str] = field(default_factory=list)
    metadata: JsonDict = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "LiteEventCandidate":
        event_id = str(raw.get("event_id") or raw.get("id") or raw.get("event_key") or "").strip()
        title = str(raw.get("title") or raw.get("name") or "")
        description = str(raw.get("description") or raw.get("event") or raw.get("beat") or "")
        probability_task = raw.get("probability_task") or raw.get("probability")
        if isinstance(probability_task, Mapping):
            probability_payload = dict(probability_task)
            if event_id and not str(probability_payload.get("event_id") or probability_payload.get("id") or "").strip():
                probability_payload["event_id"] = event_id
            if not str(probability_payload.get("event_type") or probability_payload.get("type") or "").strip():
                probability_payload["event_type"] = str(raw.get("event_type") or raw.get("type") or title or "world_event")
            if not str(probability_payload.get("question") or probability_payload.get("event_question") or "").strip():
                probability_payload["question"] = description or title
            probability_task = probability_payload
        time_request = raw.get("time_request") or raw.get("time_advance_request")
        metadata = _as_dict(raw.get("metadata"))
        for key in (
            "event_source",
            "event_class",
            "tempo_phase",
            "causal_anchor",
            "residual_thread",
            "closure_runway_effect",
            "actor_reliability_label",
            "doctor_induced_consequence",
            "external_takeover_candidate",
        ):
            if key in raw and key not in metadata:
                metadata[key] = raw.get(key)
        return cls(
            event_id=event_id,
            title=title,
            description=description,
            status=str(raw.get("status") or "committed"),
            visible_to_doctor=bool(raw.get("visible_to_doctor", False)),
            visible_to_patient_or_family=bool(raw.get("visible_to_patient_or_family", True)),
            affected_actors=_as_str_list(raw.get("affected_actors") or raw.get("actors")),
            probability_task=LiteProbabilityTask.from_mapping(probability_task) if isinstance(probability_task, Mapping) else None,
            time_request=TimeAdvanceRequest.from_mapping(time_request) if isinstance(time_request, Mapping) else None,
            consequences=_as_str_list(raw.get("consequences")),
            metadata=metadata,
        )

    def to_dict(self) -> JsonDict:
        payload = asdict(self)
        if self.probability_task is not None:
            payload["probability_task"] = self.probability_task.to_dict()
        if self.time_request is not None:
            payload["time_request"] = self.time_request.to_dict()
        return payload


@dataclass
class DoctorOperationRequest:
    """Doctor-side operation request inferred from natural language or tool text."""

    operation: str = "clinical_workspace.query"
    panels: list[str] = field(default_factory=list)
    reason: str = ""
    confidence: str = "medium"
    patient_visible: bool = False
    parameters: JsonDict = field(default_factory=dict)
    metadata: JsonDict = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "DoctorOperationRequest":
        operation = _normalize_doctor_operation(raw.get("operation") or raw.get("tool") or raw.get("action") or "clinical_workspace.query")
        return cls(
            operation=operation,
            panels=_as_str_list(raw.get("panels") or raw.get("panel") or raw.get("targets") or raw.get("target")),
            reason=str(raw.get("reason") or raw.get("rationale") or ""),
            confidence=str(raw.get("confidence") or "medium"),
            patient_visible=bool(raw.get("patient_visible", False)),
            parameters=_as_dict(raw.get("parameters") or raw.get("input") or raw.get("args")),
            metadata=_as_dict(raw.get("metadata")),
        )

    def to_dict(self) -> JsonDict:
        return asdict(self)


@dataclass
class DirectorBeat:
    """WorldDirector's judgement for one runtime step."""

    turn_id: str = ""
    scene_summary: str = ""
    doctor_move_read: str = ""
    next_world_beat: str = ""
    committed_events: list[LiteEventCandidate] = field(default_factory=list)
    probability_tasks: list[LiteProbabilityTask] = field(default_factory=list)
    time_requests: list[TimeAdvanceRequest] = field(default_factory=list)
    actor_focus: str = "patient"
    actor_situation_goal: str = ""
    should_continue: bool = True
    possible_closure: str = ""
    director_rationale: str = ""
    cautions: list[str] = field(default_factory=list)
    critical_safety_events: list[JsonDict] = field(default_factory=list)
    living_state_update: Any = field(default_factory=dict)
    metadata: JsonDict = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "DirectorBeat":
        events = [LiteEventCandidate.from_mapping(item) for item in _director_event_items(raw)]
        explicit_probability_tasks = [
            LiteProbabilityTask.from_mapping(item)
            for item in _as_list(raw.get("probability_tasks"))
            if isinstance(item, Mapping)
        ]
        explicit_time_requests = [
            TimeAdvanceRequest.from_mapping(item)
            for item in _as_list(raw.get("time_requests") or raw.get("time_advance_requests"))
            if isinstance(item, Mapping)
        ]
        probability_tasks = explicit_probability_tasks + [event.probability_task for event in events if event.probability_task is not None]
        time_requests = explicit_time_requests + [event.time_request for event in events if event.time_request is not None]
        return cls(
            turn_id=str(raw.get("turn_id") or ""),
            scene_summary=str(raw.get("scene_summary") or raw.get("current_scene") or ""),
            doctor_move_read=str(raw.get("doctor_move_read") or raw.get("doctor_interpretation") or ""),
            next_world_beat=str(raw.get("next_world_beat") or raw.get("next_beat") or raw.get("beat") or ""),
            committed_events=events,
            probability_tasks=[task for task in probability_tasks if task is not None],
            time_requests=[request for request in time_requests if request is not None],
            actor_focus=str(raw.get("actor_focus") or raw.get("next_actor") or "patient"),
            actor_situation_goal=str(raw.get("actor_situation_goal") or raw.get("patient_response_intent") or ""),
            should_continue=bool(raw.get("should_continue", True)),
            possible_closure=str(raw.get("possible_closure") or raw.get("closure_hint") or ""),
            director_rationale=str(raw.get("director_rationale") or raw.get("rationale") or ""),
            cautions=_as_str_list(raw.get("cautions") or raw.get("safety_notes")),
            critical_safety_events=[
                dict(item)
                for item in _as_list(raw.get("critical_safety_events") or raw.get("critical_safety_event"))
                if isinstance(item, Mapping)
            ],
            living_state_update=raw.get("living_state_update")
            or raw.get("world_state_update")
            or raw.get("episode_memory_update")
            or raw.get("continuity_update")
            or {},
            metadata=_as_dict(raw.get("metadata")),
        )

    def to_dict(self) -> JsonDict:
        payload = asdict(self)
        payload["committed_events"] = [item.to_dict() for item in self.committed_events]
        payload["probability_tasks"] = [item.to_dict() for item in self.probability_tasks]
        payload["time_requests"] = [item.to_dict() for item in self.time_requests]
        return payload


@dataclass
class ActorSituation:
    """Actor-local lived situation after the messenger translates world beats."""

    actor_id: str = "patient"
    actor_role: str = "patient"
    speaker_display: str = ""
    speaker_category: str = ""
    relationship_to_patient: str = ""
    scene: str = ""
    elapsed_time_visible: str = ""
    what_actor_knows_now: list[str] = field(default_factory=list)
    what_actor_may_be_confusing_or_omitting: list[str] = field(default_factory=list)
    what_actor_feels_now: list[str] = field(default_factory=list)
    practical_constraints: list[str] = field(default_factory=list)
    report_reliability_labels: list[str] = field(default_factory=list)
    immediate_goal: str = ""
    speaking_guidance: str = ""
    do_not_reveal: list[str] = field(default_factory=list)
    metadata: JsonDict = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "ActorSituation":
        return cls(
            actor_id=str(raw.get("actor_id") or raw.get("actor") or "patient"),
            actor_role=str(raw.get("actor_role") or raw.get("role") or "patient"),
            speaker_display=str(raw.get("speaker_display") or raw.get("display") or ""),
            speaker_category=str(raw.get("speaker_category") or raw.get("category") or ""),
            relationship_to_patient=str(raw.get("relationship_to_patient") or raw.get("relationship") or ""),
            scene=str(raw.get("scene") or raw.get("where_now") or ""),
            elapsed_time_visible=str(raw.get("elapsed_time_visible") or raw.get("time_visible") or ""),
            what_actor_knows_now=_as_str_list(raw.get("what_actor_knows_now") or raw.get("knows")),
            what_actor_may_be_confusing_or_omitting=_as_str_list(raw.get("what_actor_may_be_confusing_or_omitting") or raw.get("confusing_or_omitting") or raw.get("uncertain_or_omitted")),
            what_actor_feels_now=_as_str_list(raw.get("what_actor_feels_now") or raw.get("feels")),
            practical_constraints=_as_str_list(raw.get("practical_constraints") or raw.get("constraints")),
            report_reliability_labels=_as_str_list(raw.get("report_reliability_labels") or raw.get("reliability_labels")),
            immediate_goal=str(raw.get("immediate_goal") or raw.get("goal") or ""),
            speaking_guidance=str(raw.get("speaking_guidance") or raw.get("voice_guidance") or ""),
            do_not_reveal=_as_str_list(raw.get("do_not_reveal")),
            metadata=_as_dict(raw.get("metadata")),
        )

    def to_dict(self) -> JsonDict:
        return asdict(self)


@dataclass
class ActorUtterance:
    actor_id: str
    actor_role: str
    text: str
    metadata: JsonDict = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return asdict(self)


@dataclass
class ClosureAssessmentLite:
    """Post-hoc closure judgement from the trajectory, not a hard stop rule."""

    status: str = "open"
    closure_kind: str = "open_progressing"
    rationale: str = ""
    evidence: list[str] = field(default_factory=list)
    unresolved_threads: list[str] = field(default_factory=list)
    unsafe_stop_reason: str = ""
    if_continued_next_focus: str = ""
    metadata: JsonDict = field(default_factory=dict)

    @property
    def is_terminal(self) -> bool:
        kind = str(self.closure_kind or "").strip().lower()
        metadata = self.metadata if isinstance(self.metadata, Mapping) else {}
        gate = metadata.get("case_level_terminality_gate") if isinstance(metadata.get("case_level_terminality_gate"), Mapping) else {}
        if gate.get("case_terminal") is False:
            return False
        nonterminal_kinds = {
            "milestone_closed_but_not_terminal",
            "episode_milestone_closed_continue_case",
            "bounded_episode_milestone_closed",
        }
        if kind in nonterminal_kinds:
            return False
        if self.status == "closed":
            return True
        if self.status == "unsafe_stop":
            return bool(metadata.get("runtime_stop") or metadata.get("trajectory_contaminated"))
        return False

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "ClosureAssessmentLite":
        status = str(raw.get("status") or raw.get("closure_status") or "open").strip().lower()
        aliases = {
            "continue": "open",
            "ongoing": "open",
            "soft_close": "soft_closed",
            "soft_closed": "soft_closed",
            "complete": "closed",
            "completed": "closed",
            "terminal": "closed",
            "unsafe": "unsafe_stop",
            "unsafe_failure": "unsafe_stop",
        }
        return cls(
            status=aliases.get(status, status if status in {"open", "soft_closed", "closed", "unsafe_stop"} else "open"),
            closure_kind=str(raw.get("closure_kind") or raw.get("terminal_closure_kind") or raw.get("closure_type") or "").strip(),
            rationale=str(raw.get("rationale") or raw.get("reason") or ""),
            evidence=_as_str_list(raw.get("evidence")),
            unresolved_threads=_as_str_list(raw.get("unresolved_threads") or raw.get("remaining_threads")),
            unsafe_stop_reason=str(raw.get("unsafe_stop_reason") or raw.get("unsafe_reason") or ""),
            if_continued_next_focus=str(raw.get("if_continued_next_focus") or raw.get("next_focus") or ""),
            metadata=_as_dict(raw.get("metadata")),
        )

    def to_dict(self) -> JsonDict:
        return asdict(self)
