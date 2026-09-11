from __future__ import annotations

"""Case loading for runtime_lite.

The loader treats old v6 cases as rich source material.  It does not require the
old runtime contract to become the new flow skeleton.
"""

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Mapping

from careloop.runtime_lite.visibility_boundary import neutral_session_label, redact_evaluator_only


@dataclass
class LiteCase:
    case_id: str
    title: str
    path: Path
    raw: dict[str, Any]
    initial_actor: str = "patient"
    initial_message: str = ""
    initial_chat_metadata: dict[str, Any] = field(default_factory=dict)
    patient_voice_profile: dict[str, Any] = field(default_factory=dict)
    actor_profiles: dict[str, Any] = field(default_factory=dict)
    actor_knowledge_contract: dict[str, Any] = field(default_factory=dict)
    temporal_contract: dict[str, Any] = field(default_factory=dict)
    workspace_contract: dict[str, Any] = field(default_factory=dict)
    hidden_world_material: dict[str, Any] = field(default_factory=dict)
    evaluation_material: dict[str, Any] = field(default_factory=dict)

    def director_context(self) -> dict[str, Any]:
        """Full world context visible to the WorldDirector."""

        return {
            "case_id": self.case_id,
            "title": self.title,
            "initial_chat": {
                "speaker": self.initial_actor,
                "message": self.initial_message,
            },
            "patient_voice_profile": self.patient_voice_profile,
            "actor_profiles": self.actor_profiles,
            "actor_knowledge_contract": self.actor_knowledge_contract,
            "temporal_contract": self.temporal_contract,
            "environmental_and_special_event_context": {
                "care_setting": self.raw.get("care_setting"),
                "region_or_location_context": self.raw.get("region_or_location_context"),
                "seasonal_context": self.raw.get("seasonal_context"),
                "special_event_opportunity_space": self.raw.get("special_event_opportunity_space") or {},
            },
            "workspace_contract": self.workspace_contract,
            "hidden_world_material": self.hidden_world_material,
            "evaluation_material_hint": self.evaluation_material,
        }

    def doctor_visible_opening(self) -> dict[str, Any]:
        """Opening information that the tested doctor may see without tools.

        Do not expose raw case_id/title or evaluator-only author metadata here:
        historical case identifiers often encode diagnosis, traps, or scoring
        intent.  The tested doctor receives a neutral session label plus the
        current patient/family message and neutral workspace capabilities.
        """

        speaker_identity = self.actor_identity(self.initial_actor, self.initial_chat_metadata.get("speaker_category", ""))
        care_network_history_summary = summarize_current_institution_history_for_doctor(self.raw, self.workspace_contract)
        legacy_current_institution_summary = dict(care_network_history_summary)
        legacy_current_institution_summary["module"] = "current_institution_history_summary"
        session_label = neutral_session_label(self.raw)
        payload = {
            "session": {
                "session_label": session_label,
                "session_type": "continuous_care_consultation",
                "case_start_datetime": self.raw.get("case_start_datetime") or self.temporal_contract.get("start_datetime"),
                "anti_cheat_note": "Neutral session metadata only; raw case identifiers and evaluator-only case focus are not shown.",
            },
            # Keep a neutral title alias for older prompt consumers, but never
            # expose the raw authored title because it may contain diagnosis hints.
            "title": session_label,
            "latest_patient_message": {
                "speaker": self.initial_actor,
                "speaker_display": speaker_identity["display"],
                "speaker_category": speaker_identity["speaker_category"],
                "relationship_to_patient": speaker_identity["relationship_to_patient"],
                "text": self.initial_message,
            },
            "conversation_context": (
                f"当前对话消息来自{speaker_identity['display']}。"
                if speaker_identity.get("display")
                else "当前对话消息来自患者或其照护者。"
            ),
            "care_network_positioning": {
                "network_label": str(self.workspace_contract.get("care_network_label") or "CareLoop 联合医疗网络"),
                "tested_doctor_role": "cross_institution_continuous_care_ai_doctor",
                "access_principle": (
                    "医生可按需调取联合医疗网络中已授权、已同步或患者上传的跨机构资料；"
                    "未授权、未同步、未来才产生或质量不足的资料不会自动展示。"
                ),
            },
            "care_network_history_summary": care_network_history_summary,
            "current_institution_history_summary": legacy_current_institution_summary,
            "available_workspace": summarize_workspace_for_doctor(self.workspace_contract),
        }
        return redact_evaluator_only(payload)

    def actor_identity(self, actor_id: str = "", actor_role: str = "") -> dict[str, str]:
        """Return a doctor/actor-readable identity label without exposing hidden truth."""

        actor_key = str(actor_id or actor_role or "patient").strip()
        role_key = str(actor_role or "").strip()
        profile = _find_actor_profile(self.actor_profiles, actor_key, role_key)
        metadata = self.initial_chat_metadata if actor_key == self.initial_actor else {}

        category = str(
            profile.get("speaker_category")
            or profile.get("category")
            or metadata.get("speaker_category")
            or ""
        ).strip()
        relationship = str(
            profile.get("relationship_to_patient")
            or profile.get("relationship")
            or metadata.get("relationship_to_patient")
            or ""
        ).strip()
        display = str(
            profile.get("display")
            or profile.get("speaker_display")
            or profile.get("name")
            or metadata.get("speaker_display")
            or ""
        ).strip()

        category = _infer_speaker_category(actor_key, role_key, category, relationship)
        if not display:
            display = _infer_speaker_display(actor_key, role_key, category, relationship)
        if not relationship:
            relationship = "self" if category == "patient" else ""
        return {
            "actor_id": actor_key or "patient",
            "actor_role": role_key or actor_key or "patient",
            "display": display,
            "speaker_category": category,
            "relationship_to_patient": relationship,
        }


def load_lite_case(path: str | Path) -> LiteCase:
    case_path = Path(path)
    if case_path.is_dir():
        case_path = case_path / "case.json"
    with case_path.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    initial_chat = raw.get("initial_chat") if isinstance(raw.get("initial_chat"), Mapping) else {}
    initial_chat_metadata = initial_chat.get("metadata") if isinstance(initial_chat.get("metadata"), Mapping) else {}
    actor_profiles = _select_actor_profiles(raw, _load_sidecar_json(case_path.parent / "actor_profiles.json"))
    return LiteCase(
        case_id=str(raw.get("case_id") or case_path.parent.name),
        title=str(raw.get("title") or raw.get("case_metadata", {}).get("title") or case_path.parent.name),
        path=case_path,
        raw=raw,
        initial_actor=str(initial_chat.get("speaker") or "patient"),
        initial_message=str(initial_chat.get("message") or ""),
        initial_chat_metadata=dict(initial_chat_metadata),
        patient_voice_profile=dict(raw.get("patient_voice_profile") or {}),
        actor_profiles=actor_profiles,
        actor_knowledge_contract=dict(raw.get("actor_knowledge_contract") or raw.get("information_provenance_contract") or {}),
        temporal_contract=dict(raw.get("temporal_contract") or {}),
        workspace_contract=dict(raw.get("workspace_contract") or {}),
        hidden_world_material=_select_hidden_world_material(raw),
        evaluation_material=_select_evaluation_material(raw),
    )


def _load_sidecar_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        value = json.load(f)
    return value if isinstance(value, dict) else {}


def _select_actor_profiles(raw: dict[str, Any], sidecar: dict[str, Any]) -> dict[str, Any]:
    """Collect actor identity material from old/new cases without making it a flow schema."""

    raw_profiles = raw.get("actor_profiles")
    family_reality = raw.get("family_reality_profile") if isinstance(raw.get("family_reality_profile"), Mapping) else {}
    actor_map: dict[str, dict[str, Any]] = {}
    actors: list[dict[str, Any]] = []

    def add_profile(item: Mapping[str, Any]) -> None:
        profile = dict(item)
        actor_id = str(profile.get("actor_id") or profile.get("id") or profile.get("name") or "").strip()
        if not actor_id:
            return
        actor_map[actor_id] = profile
        actors.append(profile)

    if isinstance(raw_profiles, list):
        for item in raw_profiles:
            if isinstance(item, Mapping):
                add_profile(item)
    elif isinstance(raw_profiles, Mapping):
        for item in raw_profiles.get("actors") or []:
            if isinstance(item, Mapping):
                add_profile(item)
        for key, value in raw_profiles.items():
            if key in {"actors", "actor_map"}:
                continue
            if isinstance(value, Mapping):
                profile = {"actor_id": key, **dict(value)}
                add_profile(profile)

    if isinstance(sidecar.get("actors"), list):
        for item in sidecar.get("actors") or []:
            if isinstance(item, Mapping):
                add_profile(item)
    elif sidecar:
        for key, value in sidecar.items():
            if isinstance(value, Mapping):
                profile = {"actor_id": key, **dict(value)}
                add_profile(profile)

    return {
        "actors": actors,
        "actor_map": actor_map,
        "family_reality_profile": dict(family_reality),
        "sidecar": sidecar,
        "raw_actor_profiles": raw_profiles if isinstance(raw_profiles, (list, dict)) else {},
    }


def _find_actor_profile(actor_profiles: Mapping[str, Any], actor_id: str, actor_role: str) -> dict[str, Any]:
    actor_map = actor_profiles.get("actor_map") if isinstance(actor_profiles.get("actor_map"), Mapping) else {}
    for key in [actor_id, actor_role]:
        if key and isinstance(actor_map.get(key), Mapping):
            return dict(actor_map[key])
    for item in actor_profiles.get("actors") or []:
        if not isinstance(item, Mapping):
            continue
        if str(item.get("actor_id") or "") in {actor_id, actor_role}:
            return dict(item)
    return {}


def _infer_speaker_category(actor_id: str, actor_role: str, category: str, relationship: str) -> str:
    raw = " ".join([actor_id, actor_role, category, relationship]).lower()
    if any(token in raw for token in ["patient", "患者", "本人", "self"]):
        return "patient"
    family_tokens = [
        "family",
        "caregiver",
        "relative",
        "daughter",
        "son",
        "wife",
        "husband",
        "spouse",
        "mother",
        "father",
        "parent",
        "家属",
        "照护",
        "陪护",
        "女儿",
        "儿子",
        "妻子",
        "丈夫",
        "老伴",
        "母亲",
        "父亲",
        "妈妈",
        "爸爸",
    ]
    if any(token in raw for token in family_tokens):
        return "family"
    return category or "patient"


def _infer_speaker_display(actor_id: str, actor_role: str, category: str, relationship: str) -> str:
    raw = actor_id or actor_role
    rel = relationship.lower()
    labels = {
        "daughter": "患者女儿",
        "son": "患者儿子",
        "wife": "患者妻子",
        "husband": "患者丈夫",
        "spouse": "患者配偶",
        "mother": "患者母亲",
        "father": "患者父亲",
        "caregiver": "照护者",
    }
    if rel in labels:
        return labels[rel]
    if raw in {"daughter", "女儿"}:
        return "患者女儿"
    if raw in {"son", "儿子"}:
        return "患者儿子"
    if raw in {"wife", "妻子"}:
        return "患者妻子"
    if raw in {"husband", "丈夫"}:
        return "患者丈夫"
    if category == "family":
        return raw or "患者家属"
    return "患者本人"


def _select_hidden_world_material(raw: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "root_truth",
        "hidden_simulation_state",
        "case_world_invariant",
        "simulation_contract",
        "clinical_anchor",
        "dynamic_event_space",
        "case_taxonomy",
        "real_world_narrative_complexity",
        "visibility_contract",
        "case_contracts",
    ]
    return {key: raw.get(key) for key in keys if raw.get(key) is not None}


def _select_evaluation_material(raw: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "evaluation_contract",
        "score_blend_contract",
        "episode_completion_contract",
        "clinical_termination_contract",
        "termination",
    ]
    return {key: raw.get(key) for key in keys if raw.get(key) is not None}


def summarize_workspace_for_doctor(workspace_contract: Mapping[str, Any]) -> dict[str, Any]:
    """Expose capability, not hidden content, to the tested doctor."""

    default_panels = ["record_index", "records", "documents", "test_results", "medications", "care_access", "family_context", "timeline"]
    panels: list[str] = []
    retrieval_policy = workspace_contract.get("retrieval_policy") if isinstance(workspace_contract.get("retrieval_policy"), Mapping) else {}
    allowed_panels = retrieval_policy.get("allowed_panels") if isinstance(retrieval_policy.get("allowed_panels"), list) else []
    if allowed_panels:
        panels = [str(panel) for panel in allowed_panels if str(panel).strip()]
    for key in ["record_index", "records", "documents", "test_results", "medications", "care_access", "family_context", "timeline"]:
        if key in workspace_contract and key not in panels:
            panels.append(key)
    available_tools = workspace_contract.get("available_tools") if isinstance(workspace_contract.get("available_tools"), list) else []
    has_workspace_query = any(str(tool).strip() == "clinical_workspace.query" for tool in available_tools)
    if not panels and (has_workspace_query or workspace_contract.get("active_retrieval_required")):
        panels = list(default_panels)
    ehr_index = workspace_contract.get("ehr_record_index") if isinstance(workspace_contract.get("ehr_record_index"), list) else []
    if ehr_index and "record_index" not in panels:
        panels.insert(0, "record_index")
    return {
        "can_request_prior_records": "records" in panels or "timeline" in panels or bool(ehr_index),
        "can_request_test_results": "test_results" in panels,
        "can_request_care_access_context": "care_access" in panels,
        "available_panels": panels,
        "retrieval_policy_summary": retrieval_policy,
        "available_tools": [str(tool) for tool in available_tools],
        "ehr_record_index_available": bool(ehr_index),
        "ehr_record_count": len(ehr_index),
        "ehr_record_index_preview": ehr_index[:40],
        "record_index_note": "Only a neutral EHR index preview is shown at opening. Ask the Clinical Workspace to inspect records/documents by id, time, department, or document type; no problem-oriented summary is provided.",
        "instruction_to_doctor": (
            workspace_contract.get("doctor_instruction")
            or "如需既往病历、检查结果、用药/药房记录、出入院文书、家庭/可及性信息，请主动按索引、时间、科室或文书类型调取；系统不提供题目重点摘要。"
        ),
    }


def summarize_current_institution_history_for_doctor(raw: Mapping[str, Any], workspace_contract: Mapping[str, Any]) -> dict[str, Any]:
    """Standard doctor-visible module for current-institution history.

    Anti-cheat boundary: by default this module exposes capability and record
    counts only, not a CareLoop-generated problem-oriented chart summary.  The
    doctor should inspect the neutral EHR index and source/semisource records.
    A legacy case may explicitly opt in with retrieval_policy.problem_oriented_summary_allowed=true.
    """

    records = _current_institution_records_for_opening(raw, workspace_contract)
    retrieval_policy = workspace_contract.get("retrieval_policy") if isinstance(workspace_contract.get("retrieval_policy"), Mapping) else {}
    allow_problem_summary = bool(retrieval_policy.get("problem_oriented_summary_allowed") is True)
    summary = _high_level_history_summary(records) if allow_problem_summary else ""
    network_label = str(workspace_contract.get("care_network_label") or "CareLoop 联合医疗网络")
    institution_label = str(workspace_contract.get("current_institution_label") or "CareLoop 中心医院")
    return {
        "module": "care_network_history_summary",
        "network_label": network_label,
        "institution_label": institution_label,
        "legacy_module_alias": "current_institution_history_summary",
        "has_care_network_records": bool(records),
        "has_current_institution_records": bool(records),
        "source_record_count": len(records),
        "summary_level": "neutral_index_only" if not allow_problem_summary else "legacy_problem_oriented_summary_opt_in",
        "summary": summary,
        "anti_cheat_note": "No CareLoop-generated problem-oriented summary is provided; use Clinical Workspace / 临床工作台 to inspect the neutral EHR index and source records.",
        "detail_access": {
            "can_request_detail": True,
            "instruction_to_doctor": (
                "如果需要查看 CareLoop 联合医疗网络中已授权/已同步/已上传的具体病历、报告、检查结果、用药清单或时间线，请明确提出要调取哪些资料；"
                "系统会按权限返回可见内容，未授权、未同步、未来才产生或质量不足的资料不会直接展示；系统不提供题目重点摘要。"
            ),
        },
    }


def _current_institution_records_for_opening(raw: Mapping[str, Any], workspace_contract: Mapping[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for source in _candidate_record_sources(raw, workspace_contract):
        records.extend(_records_from_source(source))
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, record in enumerate(records, start=1):
        if not _same_hospital_record_visible_at_opening(record):
            continue
        record_id = str(record.get("record_id") or record.get("id") or "").strip()
        identity = record_id or "|".join(str(record.get(key) or "") for key in ["encounter_time", "title", "summary", "value"])
        if not identity:
            identity = f"record_{index}"
        if identity in seen:
            continue
        seen.add(identity)
        deduped.append(record)
    return deduped


def _candidate_record_sources(raw: Mapping[str, Any], workspace_contract: Mapping[str, Any]) -> list[Any]:
    sources: list[Any] = []
    if isinstance(workspace_contract.get("records"), list):
        sources.append(workspace_contract.get("records"))
    if isinstance(workspace_contract.get("care_network_records"), list):
        sources.append(workspace_contract.get("care_network_records"))
    if isinstance(raw.get("care_network_records"), list):
        sources.append(raw.get("care_network_records"))
    if isinstance(raw.get("medical_records"), list):
        sources.append(raw.get("medical_records"))
    location = str(workspace_contract.get("medical_records_location") or "").strip()
    if location:
        located = _get_dotted_path(raw, location)
        if located is not None:
            sources.append(located)
    world_state = raw.get("hidden_simulation_state") if isinstance(raw.get("hidden_simulation_state"), Mapping) else {}
    world_state = world_state.get("world_state") if isinstance(world_state.get("world_state"), Mapping) else {}
    if isinstance(world_state.get("medical_records"), list):
        sources.append(world_state.get("medical_records"))
    return sources


def _records_from_source(source: Any) -> list[dict[str, Any]]:
    if isinstance(source, Mapping):
        source = source.get("records") or source.get("items") or source.get("medical_records") or []
    records: list[dict[str, Any]] = []
    if not isinstance(source, list):
        return records
    for index, item in enumerate(source, start=1):
        if isinstance(item, Mapping):
            records.append(dict(item))
        elif str(item).strip():
            records.append(
                {
                    "record_id": f"current_institution_note_{index:03d}",
                    "encounter_scope": "in_house",
                    "source_scope": "in_house",
                    "source_institution_name": "CareLoop 中心医院",
                    "record_type": "clinical_record_note",
                    "title": "CareLoop 联合医疗网络既往记录摘要片段",
                    "summary": str(item).strip(),
                }
            )
    return records


def _get_dotted_path(raw: Mapping[str, Any], path: str) -> Any:
    current: Any = raw
    for part in [item for item in path.split(".") if item]:
        if isinstance(current, Mapping) and part in current:
            current = current.get(part)
        else:
            return None
    return current


def _same_hospital_record_visible_at_opening(record: Mapping[str, Any]) -> bool:
    if record.get("visible_at_opening") is False:
        return False
    if record.get("available_in_workspace") is False:
        return False
    scope = " ".join(
        str(record.get(key) or "")
        for key in ["encounter_scope", "source_scope", "source_institution_id", "source_institution_name"]
    ).lower()
    external_tokens = ["external", "outside_hospital", "outside", "外院", "外部", "其他医院"]
    auth_text = " ".join(
        str(record.get(key) or "")
        for key in ["authorization_status", "availability", "sync_status", "network_status", "visibility"]
    ).lower()
    explicitly_not_visible = any(
        token in auth_text
        for token in [
            "not_authorized",
            "unauthorized",
            "authorization_denied",
            "not_synced",
            "unavailable",
            "hidden",
            "未授权",
            "未同步",
            "不可见",
            "不可调取",
        ]
    )
    externally_visible = (
        not explicitly_not_visible
        and (
            record.get("visible_at_opening") is True
            or record.get("available_in_workspace") is True
            or any(token in auth_text for token in ["authorized", "granted", "uploaded", "imported", "synced", "available", "开场可见", "已授权", "已上传", "已导入", "已同步"])
        )
    )
    if any(token in scope for token in external_tokens):
        return externally_visible
    # CareLoop ICN-1 treats authorized/synced network artifacts such as
    # pharmacy-network refills, employer-clinic notes, community follow-up and
    # internet-clinic records as care-network material.  They are visible in the
    # high-level opening summary only when the authored case marks them as
    # authorized/uploaded/imported/synced/available; otherwise they still require
    # later upload or authorization and must not leak at opening.
    if externally_visible:
        return True
    same_tokens = ["in_house", "same_hospital", "current_institution", "current", "careloop_network", "care_network", "integrated_network", "a院", "本院", "当前机构", "联合医疗网络"]
    if any(token in scope for token in same_tokens):
        return True
    # Authored workspace_contract.records without explicit external provenance is
    # treated as authorized CareLoop-network material for the high-level opening
    # summary.  Detailed unavailable external material still needs upload,
    # authorization, or synchronization.
    return not scope.strip()


def _high_level_history_summary(records: list[dict[str, Any]]) -> str:
    if not records:
        return ""
    parts: list[str] = []
    for record in records[:6]:
        time = str(record.get("encounter_time") or record.get("time") or "").strip()
        title = str(record.get("title") or record.get("record_type") or "CareLoop 联合医疗网络记录").strip()
        summary = str(record.get("summary") or record.get("value") or "").strip()
        if not summary and record.get("results") not in (None, "", [], {}):
            summary = f"检查/检验结果：{record.get('results')}"
        if not summary and record.get("medications") not in (None, "", [], {}):
            summary = f"相关用药：{record.get('medications')}"
        line = " / ".join(bit for bit in [time, title, summary] if bit)
        if line:
            parts.append(line[:280])
    if len(records) > 6:
        parts.append(f"另有 {len(records) - 6} 条 CareLoop 联合医疗网络记录可按需调取。")
    return "；".join(parts)
