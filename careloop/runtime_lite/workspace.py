from __future__ import annotations

"""Thin doctor-side operation system for runtime_lite."""

from dataclasses import dataclass, field
from datetime import datetime
import re
from typing import Any, Mapping

from careloop.runtime_lite.case_loader import LiteCase
from careloop.runtime_lite.visibility_boundary import (
    STANDARD_WORKSPACE_PANELS,
    redact_evaluator_only,
    visibility_provenance,
)


PANEL_ALIASES: dict[str, str] = {
    "chart": "records",
    "emr": "records",
    "prior_records": "records",
    "病历": "records",
    "既往病历": "records",
    "检查": "test_results",
    "检验": "test_results",
    "报告": "documents",
    "用药": "medications",
    "药物": "medications",
    "交通": "care_access",
    "可及性": "care_access",
    "家属": "family_context",
    "时间线": "timeline",
    "索引": "record_index",
    "病历索引": "record_index",
    "文书索引": "record_index",
}


DEFAULT_PANELS = list(STANDARD_WORKSPACE_PANELS)


@dataclass
class LiteClinicalWorkspace:
    case: LiteCase
    uploaded_record_ids: set[str] = field(default_factory=set)
    authorized_record_ids: set[str] = field(default_factory=set)
    imported_record_ids: set[str] = field(default_factory=set)
    runtime_records: list[dict[str, Any]] = field(default_factory=list)

    @property
    def contract(self) -> dict[str, Any]:
        return self.case.workspace_contract or {}

    def query(
        self,
        panels: list[str] | None = None,
        *,
        reason: str = "",
        current_sim_time: str = "",
        parameters: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        requested = self._normalize_panels(panels or [])
        filters = self._filters_from_reason_and_parameters(reason, parameters or {})
        visible_records, withheld_records = self._split_records(current_sim_time=current_sim_time)
        filtered_records = self._filter_records(visible_records, filters)
        payload: dict[str, Any] = {
            "workspace_name": "Clinical Workspace / 临床工作台",
            "interaction_model": "doctor_side_operation_system_not_patient_chat",
            "reason": reason,
            "current_sim_time": current_sim_time,
            "requested_panels": requested,
            "available_panels": self.available_panels(),
            "query_filters": filters,
            "anti_cheat_boundary": {
                "retrieval_only": True,
                "problem_oriented_summary": False,
                "clinical_relevance_ranking": False,
                "principle": "Clinical Workspace returns neutral source/index material. It does not reveal case focus, scoring criteria, or which records are most important.",
            },
            "access_policy": {
                "requires_doctor_request": True,
                "same_hospital_or_authorized_records_visible": True,
                "external_records_withheld_until_patient_upload_or_authorized_import": True,
                "future_records_withheld_until_virtual_time_or_world_event": True,
                "visible_record_count": len(visible_records),
                "filtered_visible_record_count": len(filtered_records),
                "withheld_record_count": len(withheld_records),
                "withheld_record_notice": self._withheld_notice(withheld_records),
                "runtime_uploaded_record_ids": sorted(self.uploaded_record_ids),
                "runtime_authorized_record_ids": sorted(self.authorized_record_ids),
                "runtime_imported_record_ids": sorted(self.imported_record_ids),
            },
            "panels": {},
        }
        for panel in requested:
            payload["panels"][panel] = self._panel(panel, filtered_records, visible_records, current_sim_time=current_sim_time, filters=filters)
        payload["summary"] = self._summary(payload)
        payload["doctor_visible_text"] = self._doctor_visible_text(payload)
        return redact_evaluator_only(payload)

    def available_panels(self) -> list[str]:
        policy = self.contract.get("retrieval_policy") if isinstance(self.contract.get("retrieval_policy"), Mapping) else {}
        allowed = policy.get("allowed_panels") if isinstance(policy.get("allowed_panels"), list) else []
        if allowed:
            return [self._normalize_panel(item) for item in allowed]
        found = [key for key in DEFAULT_PANELS if key in self.contract]
        if self.contract.get("ehr_record_index") and "record_index" not in found:
            found.insert(0, "record_index")
        return found or DEFAULT_PANELS

    def _normalize_panels(self, panels: list[str]) -> list[str]:
        allowed = set(self.available_panels())
        normalized: list[str] = []
        for item in panels or []:
            panel = self._normalize_panel(item)
            if panel in allowed and panel not in normalized:
                normalized.append(panel)
        return normalized or [panel for panel in ["records", "test_results", "documents", "care_access"] if panel in allowed] or list(allowed)

    def apply_world_events(self, committed_world_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Apply structured workspace updates from committed world events.

        WorldDirector remains responsible for deciding that a patient uploaded a
        report, granted authorization, or an import completed.  The workspace
        only applies explicit updates after the event has been committed by the
        runtime.
        """

        applied: list[dict[str, Any]] = []
        for event in committed_world_events:
            for update in self._workspace_updates_from_event(event):
                action = str(update.get("action") or update.get("type") or "").strip().lower()
                record_ids = self._record_ids_from_update(update)
                if not record_ids:
                    applied.append({
                        "applied": False,
                        "action": action,
                        "reason": "missing_record_id",
                        "source_event_id": event.get("event_id"),
                    })
                    continue
                for record_id in record_ids:
                    if action in {"mark_uploaded", "patient_uploaded", "upload_record", "uploaded", "record_uploaded"}:
                        self.uploaded_record_ids.add(record_id)
                        self._ensure_runtime_record_from_update(event, update, record_id, "uploaded")
                        applied.append(self._applied_update(event, update, record_id, "uploaded"))
                    elif action in {"authorize_import", "mark_authorized", "authorized", "record_authorized"}:
                        self.authorized_record_ids.add(record_id)
                        self._ensure_runtime_record_from_update(event, update, record_id, "authorized")
                        applied.append(self._applied_update(event, update, record_id, "authorized"))
                    elif action in {"import_record", "mark_imported", "imported", "record_imported"}:
                        self.imported_record_ids.add(record_id)
                        self._ensure_runtime_record_from_update(event, update, record_id, "imported")
                        applied.append(self._applied_update(event, update, record_id, "imported"))
                    else:
                        applied.append({
                            "applied": False,
                            "action": action,
                            "record_id": record_id,
                            "reason": "unknown_workspace_update_action",
                            "source_event_id": event.get("event_id"),
                        })
        return applied

    def ingest_care_system_updates(self, updates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Create workspace-visible runtime records from care-system results."""

        created: list[dict[str, Any]] = []
        existing_ids = {self._record_id(record) for record in self.runtime_records}
        for update in updates:
            if not update.get("applied"):
                continue
            if update.get("new_status") != "result_available":
                continue
            result = update.get("result")
            if result in (None, ""):
                continue
            receipt = update.get("receipt_snapshot") if isinstance(update.get("receipt_snapshot"), Mapping) else {}
            receipt_id = str(update.get("receipt_id") or receipt.get("receipt_id") or "").strip()
            if not receipt_id:
                continue
            record_id = f"{receipt_id}_result"
            if record_id in existing_ids:
                continue
            summary = result.get("summary") if isinstance(result, Mapping) else str(result)
            record = {
                "record_id": record_id,
                "encounter_scope": "runtime_generated",
                "source_institution_name": "CareLoop 中心医院",
                "encounter_time": update.get("source_event_id"),
                "record_type": "test_result",
                "title": f"Result for {receipt.get('summary') or receipt_id}",
                "summary": summary,
                "results": result,
                "available_in_workspace": True,
                "runtime_generated": True,
                "source_receipt_id": receipt_id,
                "source_operation": update.get("operation"),
            }
            self.runtime_records.append(record)
            existing_ids.add(record_id)
            created.append({"created": True, "record_id": record_id, "source_receipt_id": receipt_id})
        return created

    def _normalize_panel(self, panel: Any) -> str:
        raw = str(panel or "").strip()
        lowered = raw.lower()
        return PANEL_ALIASES.get(raw, PANEL_ALIASES.get(lowered, lowered))

    def _workspace_updates_from_event(self, event: Mapping[str, Any]) -> list[dict[str, Any]]:
        candidates: list[Any] = []
        metadata = event.get("metadata") if isinstance(event.get("metadata"), Mapping) else {}
        for container in [event, metadata]:
            if not isinstance(container, Mapping):
                continue
            if isinstance(container.get("workspace_updates"), list):
                candidates.extend(container.get("workspace_updates") or [])
            elif isinstance(container.get("workspace_update"), Mapping):
                candidates.append(container.get("workspace_update"))
        return [dict(item) for item in candidates if isinstance(item, Mapping)]

    def _record_ids_from_update(self, update: Mapping[str, Any]) -> list[str]:
        raw_ids: list[Any] = []
        if update.get("record_id") is not None:
            raw_ids.append(update.get("record_id"))
        if update.get("id") is not None:
            raw_ids.append(update.get("id"))
        if isinstance(update.get("record_ids"), list):
            raw_ids.extend(update.get("record_ids") or [])
        deduped: list[str] = []
        for raw in raw_ids:
            record_id = str(raw or "").strip()
            if record_id and record_id not in deduped:
                deduped.append(record_id)
        return deduped

    def _applied_update(
        self,
        event: Mapping[str, Any],
        update: Mapping[str, Any],
        record_id: str,
        applied_as: str,
    ) -> dict[str, Any]:
        return {
            "applied": True,
            "action": update.get("action") or update.get("type"),
            "record_id": record_id,
            "applied_as": applied_as,
            "source_event_id": event.get("event_id"),
            "source_event_title": event.get("title"),
        }

    def _ensure_runtime_record_from_update(
        self,
        event: Mapping[str, Any],
        update: Mapping[str, Any],
        record_id: str,
        applied_as: str,
    ) -> None:
        if any(self._record_id(record) == record_id for record in self.runtime_records):
            return
        record_type = str(update.get("record_type") or update.get("panel") or "uploaded_record").strip() or "uploaded_record"
        title = str(update.get("title") or event.get("title") or f"Uploaded record {record_id}").strip()
        summary = update.get("summary") or update.get("value") or update.get("description") or event.get("description")
        record: dict[str, Any] = {
            "record_id": record_id,
            "encounter_scope": "runtime_uploaded" if applied_as == "uploaded" else f"runtime_{applied_as}",
            "source_institution_name": str(update.get("source_institution_name") or update.get("source") or "patient/family upload"),
            "encounter_time": event.get("sim_time") or event.get("turn") or event.get("event_id"),
            "record_type": record_type,
            "title": title,
            "summary": summary or "患者/家属已上传或授权此资料，但导演未提供可结构化摘要；医生应结合患者描述继续核验原文/照片。",
            "available_in_workspace": True,
            "runtime_generated": True,
            "source_event_id": event.get("event_id"),
            "source_event_title": event.get("title"),
            "workspace_update_action": update.get("action") or update.get("type"),
        }
        for key in ["results", "medications", "reliability", "value"]:
            if update.get(key) not in (None, "", [], {}):
                record[key] = update.get(key)
        self.runtime_records.append(record)

    def _split_records(self, *, current_sim_time: str = "") -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        records = self._base_records()
        records.extend(self.runtime_records)
        visible: list[dict[str, Any]] = []
        withheld: list[dict[str, Any]] = []
        for item in records:
            if not isinstance(item, Mapping):
                continue
            record = dict(item)
            if self._record_visible(record, current_sim_time=current_sim_time):
                visible.append(self._redact_record(record))
            else:
                withheld.append(self._withheld_record_stub(record))
        return visible, withheld

    def _base_records(self) -> list[dict[str, Any]]:
        """Collect authored chart material from legacy and FCCT case shapes.

        Cases have gone through several iterations.  Some keep records under
        workspace_contract.records, some use a top-level medical_records list,
        and some only point to the list via workspace_contract.medical_records_location.
        The workspace should not silently ignore any of those authored sources;
        visibility is still enforced record by record below.
        """

        collected: list[dict[str, Any]] = []
        for source in self._candidate_record_sources():
            collected.extend(self._records_from_source(source))
        deduped: list[dict[str, Any]] = []
        seen: set[str] = set()
        for index, record in enumerate(collected, start=1):
            identity = self._record_identity(record, fallback=f"authored_record_{index}")
            if identity in seen:
                continue
            seen.add(identity)
            deduped.append(record)
        return deduped

    def _candidate_record_sources(self) -> list[Any]:
        raw = self.case.raw if isinstance(self.case.raw, Mapping) else {}
        sources: list[Any] = []
        if isinstance(self.contract.get("records"), list):
            sources.append(self.contract.get("records"))
        if isinstance(raw.get("medical_records"), list):
            sources.append(raw.get("medical_records"))
        location = str(self.contract.get("medical_records_location") or "").strip()
        if location:
            located = self._get_dotted_path(raw, location)
            if located is not None:
                sources.append(located)
        # Anti-cheat hardening: hidden_simulation_state is evaluator/world-backstage
        # material, not a workspace source.  Future cases that need a hidden-area
        # record to become doctor-retrievable must copy it into an explicit
        # workspace source and mark it available/authorized/imported.
        return sources

    def _records_from_source(self, source: Any) -> list[dict[str, Any]]:
        if isinstance(source, Mapping):
            source = source.get("records") or source.get("items") or source.get("medical_records") or []
        if not isinstance(source, list):
            return []
        records: list[dict[str, Any]] = []
        for index, item in enumerate(source, start=1):
            if isinstance(item, Mapping):
                records.append(dict(item))
            elif str(item).strip():
                # Legacy longitudinal cases sometimes authored in-system chart
                # notes as strings.  Treat them as current-institution notes,
                # not as hidden truth, so the doctor can request the underlying detail.
                records.append(
                    {
                        "record_id": f"current_institution_note_{index:03d}",
                        "encounter_scope": "in_house",
                        "source_scope": "in_house",
                        "source_institution_name": "CareLoop 中心医院",
                        "record_type": "clinical_record_note",
                        "title": "CareLoop 中心医院既往记录摘要片段",
                        "summary": str(item).strip(),
                    }
                )
        return records

    def _get_dotted_path(self, raw: Mapping[str, Any], path: str) -> Any:
        current: Any = raw
        for part in [item for item in str(path or "").split(".") if item]:
            if isinstance(current, Mapping) and part in current:
                current = current.get(part)
            else:
                return None
        return current

    def _record_identity(self, record: Mapping[str, Any], *, fallback: str = "") -> str:
        record_id = self._record_id(record)
        if record_id:
            return record_id
        pieces = [str(record.get(key) or "") for key in ["encounter_time", "title", "summary", "value"]]
        identity = "|".join(piece for piece in pieces if piece)
        return identity or fallback

    def _record_visible(self, record: Mapping[str, Any], *, current_sim_time: str = "") -> bool:
        record_id = self._record_id(record)
        if record_id in self.uploaded_record_ids or record_id in self.authorized_record_ids or record_id in self.imported_record_ids:
            return True
        if record.get("evaluator_only") or record.get("hidden_truth") or record.get("backstage_only"):
            return False
        if record.get("available_in_workspace") is False:
            return False
        if not self._record_time_visible(record, current_sim_time=current_sim_time):
            return False
        if record.get("patient_uploaded") or record.get("uploaded_by_patient") or record.get("authorized_import"):
            return True
        scope = " ".join(
            str(record.get(key) or "")
            for key in ["encounter_scope", "source_scope", "source_institution_id", "source_institution_name"]
        ).lower()
        if any(token in scope for token in ["external", "outside_hospital", "outside", "外院", "外部", "其他医院"]):
            return False
        return True

    def _record_time_visible(self, record: Mapping[str, Any], *, current_sim_time: str = "") -> bool:
        if record.get("runtime_generated") or str(record.get("encounter_scope") or "").startswith("runtime_"):
            return True
        record_time = self._parse_time(record.get("encounter_time") or record.get("time") or record.get("date"))
        if record_time is None:
            return True
        cutoff = self._parse_time(current_sim_time) or self._parse_time(self.case.raw.get("case_start_datetime"))
        if cutoff is None:
            return True
        return record_time <= cutoff

    def _parse_time(self, value: Any) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        text = text.replace("T", " ").replace("Z", "").strip()
        match = re.search(r"\d{4}-\d{1,2}-\d{1,2}(?:[ T]\d{1,2}:\d{1,2}(?::\d{1,2})?)?", text)
        if not match:
            return None
        candidate = match.group(0).replace("T", " ")
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                return datetime.strptime(candidate, fmt)
            except ValueError:
                continue
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            return None

    def _filters_from_reason_and_parameters(self, reason: str, parameters: Mapping[str, Any]) -> dict[str, Any]:
        filters: dict[str, Any] = {}
        for key in ["record_id", "record_ids", "department", "record_type", "start_time", "end_time", "keyword", "keywords"]:
            value = parameters.get(key) if isinstance(parameters, Mapping) else None
            if value not in (None, "", [], {}):
                filters[key] = value
        text = str(reason or "")
        ids = re.findall(r"\behr_\d{3,}\b", text, flags=re.IGNORECASE)
        if ids and "record_ids" not in filters and "record_id" not in filters:
            filters["record_ids"] = [item.lower() for item in ids]
        return filters

    def _filter_records(self, records: list[dict[str, Any]], filters: Mapping[str, Any]) -> list[dict[str, Any]]:
        if not filters:
            return records
        out: list[dict[str, Any]] = []
        record_ids = set(str(item).strip().lower() for item in (filters.get("record_ids") or []) if str(item).strip())
        if filters.get("record_id"):
            record_ids.add(str(filters.get("record_id")).strip().lower())
        department = str(filters.get("department") or "").strip().lower()
        record_type = str(filters.get("record_type") or "").strip().lower()
        keyword_values = filters.get("keywords") if isinstance(filters.get("keywords"), list) else []
        keywords = [str(item).strip().lower() for item in keyword_values if str(item).strip()]
        if filters.get("keyword"):
            keywords.append(str(filters.get("keyword")).strip().lower())
        start = self._parse_time(filters.get("start_time"))
        end = self._parse_time(filters.get("end_time"))
        for record in records:
            rid = self._record_id(record).lower()
            if record_ids and rid not in record_ids:
                continue
            if department and department not in str(record.get("department") or "").lower():
                continue
            if record_type and record_type not in str(record.get("record_type") or "").lower():
                continue
            if start or end:
                rt = self._parse_time(record.get("encounter_time"))
                if rt is None:
                    continue
                if start and rt < start:
                    continue
                if end and rt > end:
                    continue
            if keywords:
                blob = " ".join(str(record.get(k) or "") for k in ["record_id", "encounter_time", "department", "record_type", "title", "summary", "value", "results", "medications", "diagnoses"]).lower()
                if not all(keyword in blob for keyword in keywords):
                    continue
            out.append(record)
        return out

    def _record_id(self, record: Mapping[str, Any]) -> str:
        return str(record.get("record_id") or record.get("id") or "").strip()

    def _redact_record(self, record: Mapping[str, Any]) -> dict[str, Any]:
        allowed_keys = [
            "record_id",
            "id",
            "encounter_scope",
            "source_institution_name",
            "encounter_time",
            "department",
            "record_type",
            "title",
            "summary",
            "diagnoses",
            "results",
            "medications",
            "reliability",
            "value",
        ]
        redacted = {key: record.get(key) for key in allowed_keys if key in record}
        source_type = "runtime_generated_record" if record.get("runtime_generated") else "workspace_record"
        if record.get("patient_uploaded") or record.get("uploaded_by_patient"):
            source_type = "patient_held_record"
        if record.get("authorized_import") or record.get("imported"):
            source_type = "authorized_import"
        redacted["provenance"] = visibility_provenance(
            route="workspace_route",
            source_type=source_type,
            visibility="doctor_visible",
            reliability=str(record.get("reliability") or "source_or_semisource_record"),
            notes="Clinical Workspace retrieved this neutral source/index material only after doctor request and visibility filtering.",
        )
        return redacted

    def _withheld_record_stub(self, record: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "record_id": record.get("record_id") or record.get("id"),
            "encounter_scope": record.get("encounter_scope"),
            "source_institution_name": record.get("source_institution_name"),
            "encounter_time": record.get("encounter_time"),
            "record_type": record.get("record_type"),
            "title": record.get("title"),
            "withheld_reason": "外院/未上传/未授权导入资料，医生工作台不能直接展示具体内容。",
        }

    def _panel(self, panel: str, visible_records: list[dict[str, Any]], all_visible_records: list[dict[str, Any]] | None = None, *, current_sim_time: str = "", filters: Mapping[str, Any] | None = None) -> Any:
        if panel == "record_index":
            base_for_index = all_visible_records if all_visible_records is not None else visible_records
            index = self._visible_record_index(base_for_index, current_sim_time=current_sim_time)
            if filters:
                index = self._filter_records(index, filters)
            return {
                "result_type": "neutral_ehr_index",
                "items": index,
                "record_count": len(index),
                "provenance": visibility_provenance(route="workspace_route", source_type="workspace_record", visibility="doctor_visible", reliability="neutral_index"),
                "limitations": "这是中性的院内EHR文书索引；不是病情摘要，也不提示题目重点。医生需要按索引、时间、科室或文书类型主动打开相关记录。",
            }
        if panel == "records":
            base_for_index = all_visible_records if all_visible_records is not None else visible_records
            return {
                "result_type": "source_or_semisource_records",
                "items": visible_records,
                "record_index": self._visible_record_index(base_for_index, current_sim_time=current_sim_time),
                "provenance": visibility_provenance(route="workspace_route", source_type="workspace_record", visibility="doctor_visible", reliability="source_or_semisource_records"),
                "limitations": "仅 CareLoop 联合医疗网络中已授权/已同步资料、CareLoop 中心医院资料或患者已上传资料可见；未授权、未同步或未来才产生的资料不会直接展示。返回的是原始/半原始病历材料，不是题目重点摘要，也不按临床重要性排序。",
            }
        if panel == "documents":
            return self._documents_panel(visible_records)
        if panel == "test_results":
            return self._test_results_panel(visible_records)
        if panel == "medications":
            return self._medications_panel(visible_records)
        if panel in {"care_access", "family_context", "timeline"}:
            return self.contract.get(panel) or {}
        return self.contract.get(panel) or {}

    def _visible_record_index(self, visible_records: list[dict[str, Any]], *, current_sim_time: str = "") -> list[dict[str, Any]]:
        authored_index = self.contract.get("ehr_record_index") if isinstance(self.contract.get("ehr_record_index"), list) else []
        visible_ids = {self._record_id(record) for record in visible_records if self._record_id(record)}
        if authored_index:
            items = []
            for item in authored_index:
                if not isinstance(item, Mapping):
                    continue
                stub = dict(item)
                rid = self._record_id(stub)
                if visible_ids and rid and rid not in visible_ids:
                    continue
                if not self._record_time_visible(stub, current_sim_time=current_sim_time):
                    continue
                items.append({
                    key: stub.get(key)
                    for key in ["record_id", "encounter_time", "department", "record_type", "title", "source_institution_name"]
                    if key in stub
                })
            return items
        return [
            {
                key: record.get(key)
                for key in ["record_id", "encounter_time", "department", "record_type", "title", "source_institution_name"]
                if key in record
            }
            for record in visible_records
        ]

    def _documents_panel(self, visible_records: list[dict[str, Any]]) -> dict[str, Any]:
        direct_docs = self.contract.get("documents") if isinstance(self.contract.get("documents"), list) else []
        safe_docs = [
            item
            for item in direct_docs
            if isinstance(item, Mapping) and self._direct_document_visible(item)
        ]
        record_docs = [
            item
            for item in visible_records
            if str(item.get("record_type") or "").lower() in {"document", "report", "imaging_report", "lab_report", "visit_note"}
        ]
        return {"items": record_docs + [dict(item) for item in safe_docs], "limitations": "未上传的外院原始报告不直接展示。"}

    def _direct_document_visible(self, item: Mapping[str, Any]) -> bool:
        item_id = self._record_id(item)
        if item_id in self.uploaded_record_ids or item_id in self.authorized_record_ids or item_id in self.imported_record_ids:
            return True
        if item.get("available_in_workspace") is False:
            return False
        if "not uploaded" in str(item.get("value", "")).lower():
            return False
        if item.get("patient_uploaded") or item.get("uploaded_by_patient") or item.get("authorized_import"):
            return True
        scope = str(item.get("encounter_scope") or item.get("source_scope") or "").lower()
        if scope in {"in_house", "same_hospital", "current_institution", "本院"}:
            return True
        # Missing provenance is treated conservatively: the doctor can ask the
        # patient to upload the document, but the workspace should not turn an
        # unmarked sidecar note into a retrieved report.
        return False

    def _direct_panel_item_visible(self, item: Mapping[str, Any]) -> bool:
        if item.get("evaluator_only") or item.get("hidden_truth") or item.get("backstage_only"):
            return False
        if item.get("available_in_workspace") is False:
            return False
        if item.get("patient_uploaded") or item.get("uploaded_by_patient") or item.get("authorized_import"):
            return True
        scope = str(item.get("encounter_scope") or item.get("source_scope") or "").lower()
        if scope in {"in_house", "same_hospital", "current_institution", "runtime_generated", "本院"}:
            return True
        # Conservative default for direct panel sidecars: authored direct panels
        # are visible only when marked available/authorized/uploaded/same-hospital.
        return bool(item.get("available_in_workspace") is True)

    def _test_results_panel(self, visible_records: list[dict[str, Any]]) -> dict[str, Any]:
        direct_results = self.contract.get("test_results") if isinstance(self.contract.get("test_results"), list) else []
        record_results = [item for item in visible_records if item.get("results")]
        safe_direct_results = [
            self._redact_record(item)
            for item in direct_results
            if isinstance(item, Mapping) and self._direct_panel_item_visible(item)
        ]
        return {"items": record_results + safe_direct_results}

    def _medications_panel(self, visible_records: list[dict[str, Any]]) -> dict[str, Any]:
        direct_meds = self.contract.get("medications") if isinstance(self.contract.get("medications"), list) else []
        record_meds = [item for item in visible_records if item.get("medications")]
        safe_direct_meds = [
            self._redact_record(item)
            for item in direct_meds
            if isinstance(item, Mapping) and self._direct_panel_item_visible(item)
        ]
        return {
            "items": record_meds + safe_direct_meds,
            "reliability_note": "患者记忆、药盒照片、外院处方需要区分可靠性；未上传药盒不会自动可见。",
        }

    def _withheld_notice(self, withheld_records: list[dict[str, Any]]) -> str:
        if not withheld_records:
            return "未发现被过滤的外院/未授权资料。"
        titles = "；".join(str(item.get("title") or item.get("record_id")) for item in withheld_records[:5])
        return f"存在 {len(withheld_records)} 条资料不可直接调阅：{titles}。请让患者上传或授权调取，紧急情况不要等待资料。"

    def _summary(self, payload: Mapping[str, Any]) -> str:
        panels = payload.get("panels") if isinstance(payload.get("panels"), Mapping) else {}
        parts = ["医生主动调阅了临床工作台。"]
        if "records" in panels:
            count = len((panels["records"] or {}).get("items") or [])
            parts.append(f"可见既往/CareLoop 中心医院记录 {count} 条。")
        if "test_results" in panels:
            count = len((panels["test_results"] or {}).get("items") or [])
            parts.append(f"可见检查/检验结果 {count} 项。")
        access = payload.get("access_policy") if isinstance(payload.get("access_policy"), Mapping) else {}
        if access.get("withheld_record_count"):
            parts.append("有外院或未上传资料被过滤。")
        return " ".join(parts)

    def _doctor_visible_text(self, payload: Mapping[str, Any]) -> str:
        lines = ["[Clinical Workspace / 临床工作台 · 医生侧工具结果]", str(payload.get("summary") or "")]
        access = payload.get("access_policy") if isinstance(payload.get("access_policy"), Mapping) else {}
        if access.get("withheld_record_count"):
            lines.append(str(access.get("withheld_record_notice")))
        panels = payload.get("panels") if isinstance(payload.get("panels"), Mapping) else {}
        for name, value in panels.items():
            lines.append(f"{name}: {self._compact(value)}")
        return "\n".join(lines)

    def _compact(self, value: Any) -> str:
        if isinstance(value, Mapping):
            if "items" in value:
                return self._compact(value.get("items")) or "暂无"
            return "；".join(f"{k}={v}" for k, v in list(value.items())[:8])
        if isinstance(value, list):
            chunks: list[str] = []
            for item in value[:5]:
                if isinstance(item, Mapping):
                    title = item.get("title") or item.get("record_id") or item.get("id") or "item"
                    summary = item.get("summary") or item.get("value") or item.get("results") or item.get("medications") or ""
                    chunks.append(f"{title}: {summary}")
                else:
                    chunks.append(str(item))
            if len(value) > 5:
                chunks.append(f"另有 {len(value) - 5} 项")
            return "；".join(chunks)
        return str(value)
