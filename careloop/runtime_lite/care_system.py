from __future__ import annotations

"""Thin doctor-side care operation system for runtime_lite."""

from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any

from careloop.runtime_lite.case_loader import LiteCase
from careloop.runtime_lite.models import DoctorOperationRequest


@dataclass
class LiteCareSystem:
    """Record doctor-side operations without pretending outcomes happened.

    This system registers that the AI Doctor ordered a test, prescribed a
    medication, scheduled follow-up, made a referral, or initiated an urgent
    escalation.  WorldDirector and Timekeeper remain responsible for deciding
    what happens next in the real-world simulation.
    """

    case: LiteCase
    receipts: list[dict[str, Any]] = field(default_factory=list)

    def execute(self, request: DoctorOperationRequest, *, current_sim_time: str, turn: int) -> dict[str, Any]:
        operation = self._normalize_operation(request.operation)
        handler = {
            "care_system.order_test": self._order_test,
            "care_system.prescribe": self._prescribe,
            "care_system.schedule_followup": self._schedule_followup,
            "care_system.track_result": self._track_result,
            "care_system.referral": self._referral,
            "care_system.call_emergency": self._call_emergency,
        }.get(operation, self._generic_receipt)
        receipt = handler(request, current_sim_time=current_sim_time, turn=turn, operation=operation)
        self.receipts.append(receipt)
        return receipt

    def apply_world_events(
        self,
        committed_world_events: list[dict[str, Any]],
        *,
        current_sim_time: str = "",
        turn: int = 0,
    ) -> list[dict[str, Any]]:
        """Apply explicit care-system updates from committed world events."""

        applied: list[dict[str, Any]] = []
        for event in committed_world_events:
            for update in self._care_updates_from_event(event):
                receipt = self._find_receipt(update)
                if receipt is None:
                    applied.append({
                        "applied": False,
                        "reason": "receipt_not_found",
                        "update": update,
                        "source_event_id": event.get("event_id"),
                    })
                    continue
                applied.append(self._apply_update_to_receipt(receipt, update, event, current_sim_time=current_sim_time, turn=turn))
        return applied

    def to_dict(self) -> dict[str, Any]:
        return {
            "receipt_count": len(self.receipts),
            "pending_receipts": [item for item in self.receipts if self._is_pending_receipt(item)],
            "recent_receipts": self.receipts[-5:],
            "principle": "Doctor-side operations are receipts; WorldDirector/Timekeeper decide real-world execution and results.",
        }

    def _order_test(self, request: DoctorOperationRequest, *, current_sim_time: str, turn: int, operation: str) -> dict[str, Any]:
        params = dict(request.parameters)
        test_name = params.get("test_name") or params.get("name") or params.get("test") or self._infer_name(request)
        return self._receipt(
            request,
            operation=operation,
            current_sim_time=current_sim_time,
            turn=turn,
            status="registered_pending_world_execution",
            summary=f"医生登记检查/检验医嘱：{test_name or '未命名检查'}。",
            parameters={**params, "test_name": test_name},
            next_world_need=(
                "WorldDirector should later decide, in two separate real-world steps, whether/when the patient can schedule or complete "
                "the test and, only after completion, when the result/report returns."
            ),
            lifecycle={
                "ordered_at_sim_time": current_sim_time,
                "execution_status": "not_yet_scheduled_or_performed",
                "appointment_or_execution_window": None,
                "performed_at_sim_time": None,
                "result_status": "not_yet_available",
                "result_turnaround_window": None,
                "result_available_at_sim_time": None,
                "timing_principle": (
                    "Ordering a test is not the same as performing it, and performing it is not the same as receiving the result. "
                    "Scheduling/execution delay and post-execution result turnaround must be simulated by WorldDirector/Timekeeper."
                ),
            },
        )

    def _prescribe(self, request: DoctorOperationRequest, *, current_sim_time: str, turn: int, operation: str) -> dict[str, Any]:
        params = dict(request.parameters)
        medication = params.get("medication") or params.get("drug") or params.get("name") or self._infer_name(request)
        return self._receipt(
            request,
            operation=operation,
            current_sim_time=current_sim_time,
            turn=turn,
            status="registered_pending_patient_execution",
            summary=f"医生登记处方/用药建议：{medication or '未命名药物'}。",
            parameters={**params, "medication": medication},
            next_world_need="WorldDirector should later decide whether patient obtains/takes the medication and response/adverse events.",
        )

    def _schedule_followup(self, request: DoctorOperationRequest, *, current_sim_time: str, turn: int, operation: str) -> dict[str, Any]:
        params = dict(request.parameters)
        return self._receipt(
            request,
            operation=operation,
            current_sim_time=current_sim_time,
            turn=turn,
            status="registered_pending_followup_time",
            summary="医生登记随访/复诊计划。",
            parameters=params,
            next_world_need="Timekeeper should advance to a realistic follow-up time when Director chooses to continue this thread.",
        )

    def _track_result(self, request: DoctorOperationRequest, *, current_sim_time: str, turn: int, operation: str) -> dict[str, Any]:
        params = dict(request.parameters)
        target = params.get("target") or params.get("test_name") or params.get("result_name") or params.get("name") or self._infer_name(request)
        return self._receipt(
            request,
            operation=operation,
            current_sim_time=current_sim_time,
            turn=turn,
            status="registered_pending_result_tracking",
            summary=f"医生登记结果追踪/回访任务：{target or '未命名结果'}。",
            parameters={**params, "target": target},
            next_world_need=(
                "WorldDirector should later decide whether the tracked result/report becomes available, "
                "is uploaded/imported, is delayed, or is lost to follow-up."
            ),
        )

    def _referral(self, request: DoctorOperationRequest, *, current_sim_time: str, turn: int, operation: str) -> dict[str, Any]:
        params = dict(request.parameters)
        return self._receipt(
            request,
            operation=operation,
            current_sim_time=current_sim_time,
            turn=turn,
            status="registered_pending_access_and_acceptance",
            summary="医生登记转诊/线下就诊建议。",
            parameters=params,
            next_world_need="WorldDirector should decide whether referral is accessible, accepted, delayed, or refused.",
        )

    def _call_emergency(self, request: DoctorOperationRequest, *, current_sim_time: str, turn: int, operation: str) -> dict[str, Any]:
        params = dict(request.parameters)
        return self._receipt(
            request,
            operation=operation,
            current_sim_time=current_sim_time,
            turn=turn,
            status="registered_pending_emergency_response",
            summary="医生登记急救/急诊升级操作。",
            parameters=params,
            next_world_need="WorldDirector should decide emergency access, response time, and patient/family compliance.",
        )

    def _generic_receipt(self, request: DoctorOperationRequest, *, current_sim_time: str, turn: int, operation: str) -> dict[str, Any]:
        return self._receipt(
            request,
            operation=operation,
            current_sim_time=current_sim_time,
            turn=turn,
            status="registered_unknown_operation",
            summary=f"医生登记了未分类操作：{operation}",
            parameters=dict(request.parameters),
            next_world_need="Runtime recorded this operation for audit; no automatic world outcome is implied.",
        )

    def _receipt(
        self,
        request: DoctorOperationRequest,
        *,
        operation: str,
        current_sim_time: str,
        turn: int,
        status: str,
        summary: str,
        parameters: dict[str, Any],
        next_world_need: str,
        lifecycle: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        receipt_seed = f"{self.case.case_id}|{turn}|{len(self.receipts)+1}|{operation}|{summary}"
        receipt = {
            "receipt_id": "careop_" + sha256(receipt_seed.encode("utf-8")).hexdigest()[:12],
            "operation": operation,
            "status": status,
            "summary": summary,
            "parameters": parameters,
            "reason": request.reason,
            "confidence": request.confidence,
            "patient_visible": request.patient_visible,
            "current_sim_time": current_sim_time,
            "turn": turn,
            "next_world_need": next_world_need,
            "receipt_only_no_outcome_claim": True,
        }
        if lifecycle:
            receipt.update(lifecycle)
        return receipt

    def _normalize_operation(self, operation: str) -> str:
        raw = str(operation or "").strip().lower()
        aliases = {
            "order_test": "care_system.order_test",
            "test_order": "care_system.order_test",
            "lab_order": "care_system.order_test",
            "prescribe": "care_system.prescribe",
            "prescription": "care_system.prescribe",
            "medication_order": "care_system.prescribe",
            "followup": "care_system.schedule_followup",
            "schedule_followup": "care_system.schedule_followup",
            "track_result": "care_system.track_result",
            "result_tracking": "care_system.track_result",
            "follow_result": "care_system.track_result",
            "result_followup": "care_system.track_result",
            "referral": "care_system.referral",
            "refer": "care_system.referral",
            "emergency": "care_system.call_emergency",
            "call_ems": "care_system.call_emergency",
        }
        return aliases.get(raw, raw)

    def _infer_name(self, request: DoctorOperationRequest) -> str:
        return str(request.parameters.get("name") or request.reason or "").strip()

    def _care_updates_from_event(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        candidates: list[Any] = []
        for container in [event, metadata]:
            if not isinstance(container, dict):
                continue
            if isinstance(container.get("care_system_updates"), list):
                candidates.extend(container.get("care_system_updates") or [])
            elif isinstance(container.get("care_system_update"), dict):
                candidates.append(container.get("care_system_update"))
        return [dict(item) for item in candidates if isinstance(item, dict)]

    def _find_receipt(self, update: dict[str, Any]) -> dict[str, Any] | None:
        receipt_id = str(update.get("receipt_id") or "").strip()
        if receipt_id:
            for receipt in self.receipts:
                if receipt.get("receipt_id") == receipt_id:
                    return receipt
            return None
        operation = self._normalize_operation(str(update.get("operation") or ""))
        if operation:
            for receipt in reversed(self.receipts):
                if receipt.get("operation") == operation and self._is_pending_receipt(receipt):
                    return receipt
            for receipt in reversed(self.receipts):
                if receipt.get("operation") == operation:
                    return receipt
        if update.get("match") in {"latest", "latest_pending"}:
            for receipt in reversed(self.receipts):
                if update.get("match") == "latest" or self._is_pending_receipt(receipt):
                    return receipt
        return None

    def _is_pending_receipt(self, receipt: dict[str, Any]) -> bool:
        status = str(receipt.get("status") or "").strip().lower()
        if not status:
            return False
        if status in {
            "result_available",
            "completed_in_world",
            "patient_executed",
            "patient_completed_treatment",
            "followup_completed",
            "patient_refused",
            "refused_after_safety_net",
            "cancelled",
            "lost_to_followup",
            "arrived",
            "transferred",
            "emergency_response_completed",
        }:
            return False
        if status.startswith("registered"):
            return True
        if status in {
            "scheduled_waiting_for_execution",
            "appointment_scheduled",
            "waiting_for_execution",
            "performed_waiting_for_result",
            "sample_collected_waiting_for_result",
            "result_pending",
            "result_delayed_pending",
            "performed_result_delayed",
            "execution_delayed",
            "scheduling_delayed",
            "delayed",
        }:
            return True
        result_status = str(receipt.get("result_status") or "").strip().lower()
        if result_status in {"pending", "delayed", "not_yet_available"} and receipt.get("operation") == "care_system.order_test":
            return True
        return False

    def _apply_update_to_receipt(
        self,
        receipt: dict[str, Any],
        update: dict[str, Any],
        event: dict[str, Any],
        *,
        current_sim_time: str,
        turn: int,
    ) -> dict[str, Any]:
        action = str(update.get("action") or update.get("type") or "update_status").strip().lower()
        previous_status = str(receipt.get("status") or "")
        new_status = str(update.get("status") or "").strip()
        if action in {"mark_scheduled", "scheduled", "appointment_scheduled", "test_scheduled", "mark_appointment_scheduled"}:
            new_status = new_status or "scheduled_waiting_for_execution"
            receipt["execution_status"] = "scheduled_not_yet_performed"
            if update.get("scheduled_for") is not None or update.get("appointment_time") is not None:
                receipt["scheduled_for"] = update.get("scheduled_for", update.get("appointment_time"))
            if update.get("appointment_or_execution_window") is not None or update.get("execution_window") is not None:
                receipt["appointment_or_execution_window"] = update.get("appointment_or_execution_window", update.get("execution_window"))
            if update.get("timing_rationale") is not None:
                receipt["timing_rationale"] = update.get("timing_rationale")
        elif action in {
            "mark_test_performed",
            "test_performed",
            "performed",
            "mark_performed",
            "specimen_collected",
            "sample_collected",
            "imaging_completed",
            "mark_completed_waiting_for_result",
        }:
            new_status = new_status or "performed_waiting_for_result"
            receipt["execution_status"] = "performed"
            receipt["performed_at_sim_time"] = current_sim_time
            receipt["result_status"] = "pending"
            if update.get("result_turnaround_window") is not None or update.get("turnaround_window") is not None:
                receipt["result_turnaround_window"] = update.get("result_turnaround_window", update.get("turnaround_window"))
            if update.get("timing_rationale") is not None:
                receipt["timing_rationale"] = update.get("timing_rationale")
        elif action in {"mark_result_delayed", "result_delayed", "report_delayed"}:
            new_status = new_status or "result_delayed_pending"
            receipt["result_status"] = "delayed"
            if update.get("result_turnaround_window") is not None or update.get("turnaround_window") is not None:
                receipt["result_turnaround_window"] = update.get("result_turnaround_window", update.get("turnaround_window"))
            if update.get("delay_reason") is not None or update.get("reason") is not None:
                receipt["delay_reason"] = update.get("delay_reason", update.get("reason"))
        elif action in {"mark_result_returned", "result_returned", "attach_result"}:
            new_status = new_status or "result_available"
            result_payload = update.get("result", update.get("results", update.get("value")))
            receipt["result"] = result_payload
            receipt["result_available_time"] = current_sim_time
            receipt["result_status"] = "available"
            receipt["result_available_at_sim_time"] = current_sim_time
            if update.get("result_turnaround_window") is not None or update.get("turnaround_window") is not None:
                receipt["result_turnaround_window"] = update.get("result_turnaround_window", update.get("turnaround_window"))
        elif action in {"mark_completed", "completed", "patient_completed"}:
            new_status = new_status or "completed_in_world"
            receipt["completed_at_sim_time"] = current_sim_time
        elif action in {
            "mark_followup_completed",
            "followup_completed",
            "complete_followup",
            "completed_followup",
            "mark_completed_followup",
        }:
            new_status = new_status or "followup_completed"
            receipt["followup_status"] = "completed"
            receipt["followup_completed_at_sim_time"] = current_sim_time
        elif action in {"mark_patient_executed", "patient_executed", "took_medication", "medication_started", "patient_started_medication"}:
            new_status = new_status or "patient_executed"
            receipt["execution_status"] = "patient_executed"
            receipt["executed_at_sim_time"] = current_sim_time
        elif action in {
            "mark_treatment_completed",
            "treatment_completed",
            "course_completed",
            "medication_course_completed",
            "patient_completed_treatment",
            "completed_medication_course",
        }:
            new_status = new_status or "patient_completed_treatment"
            receipt["execution_status"] = "patient_completed_treatment"
            receipt["treatment_completed_at_sim_time"] = current_sim_time
        elif action in {"mark_patient_refused", "patient_refused", "refused", "not_done"}:
            new_status = new_status or "patient_refused"
        elif action in {"mark_delayed", "delayed"}:
            new_status = new_status or "delayed"
            if update.get("delay_stage") is not None:
                receipt["delay_stage"] = update.get("delay_stage")
            if update.get("delay_reason") is not None or update.get("reason") is not None:
                receipt["delay_reason"] = update.get("delay_reason", update.get("reason"))
        else:
            new_status = new_status or previous_status or "updated"
        receipt["status"] = new_status
        receipt.setdefault("world_update_history", []).append({
            "action": action,
            "previous_status": previous_status,
            "new_status": new_status,
            "source_event_id": event.get("event_id"),
            "source_event_title": event.get("title"),
            "turn": turn,
            "sim_time": current_sim_time,
            "update": update,
        })
        return {
            "applied": True,
            "receipt_id": receipt.get("receipt_id"),
            "operation": receipt.get("operation"),
            "action": action,
            "previous_status": previous_status,
            "new_status": new_status,
            "result": receipt.get("result"),
            "receipt_snapshot": {
                "receipt_id": receipt.get("receipt_id"),
                "operation": receipt.get("operation"),
                "summary": receipt.get("summary"),
                "parameters": receipt.get("parameters"),
                "status": receipt.get("status"),
                "execution_status": receipt.get("execution_status"),
                "appointment_or_execution_window": receipt.get("appointment_or_execution_window"),
                "scheduled_for": receipt.get("scheduled_for"),
                "performed_at_sim_time": receipt.get("performed_at_sim_time"),
                "result_status": receipt.get("result_status"),
                "result_turnaround_window": receipt.get("result_turnaround_window"),
                "result_available_at_sim_time": receipt.get("result_available_at_sim_time"),
            },
            "source_event_id": event.get("event_id"),
        }
