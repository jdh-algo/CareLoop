
from __future__ import annotations

"""Optional LLM-led hardening audits for runtime_lite.

This layer turns the deterministic hardening indexes into semantic judgments.
It is intentionally post-hoc/final by default: the LLM judges meaning and
benchmark validity, while deterministic code handles evidence packing, JSON
parsing, schema normalization and safe fallbacks.
"""

import json
from typing import Any, Iterable, Optional

from careloop.runtime_lite.hardening import (
    analyze_visibility_boundary,
    attach_hardening_reports,
    classify_closure_threads,
    latest_visible_exchange,
    summarize_receipt_lifecycle,
    synthesize_benchmark_validity,
    visibility_candidates,
)
from careloop.runtime_lite.json_utils import extract_json_object
from careloop.runtime_lite.prompt_loader import load_prompt

DEFAULT_HARDENING_LLM_AUDITS = ["visibility", "benchmark", "receipt", "closure"]


def run_hardening_llm_audits(
    payload: dict[str, Any],
    llm_client: Any,
    *,
    purposes: Optional[Iterable[str]] = None,
    mode: str = "final",
) -> dict[str, Any]:
    """Attach LLM-led hardening audits to *payload*.

    Failures are captured in ``payload['hardening_llm_audit_errors']`` and never
    raise to the main runner. This prevents audit infrastructure from corrupting
    the trajectory under test.
    """

    if not isinstance(payload, dict) or llm_client is None:
        return {}
    selected = [str(p).strip().lower() for p in (purposes or DEFAULT_HARDENING_LLM_AUDITS) if str(p).strip()]
    fallback = attach_hardening_reports(payload)
    errors: list[dict[str, Any]] = []
    results: dict[str, Any] = {}

    if "visibility" in selected:
        try:
            results["visibility_boundary_audit"] = llm_visibility_boundary_audit(payload, llm_client, fallback=fallback.get("visibility_boundary_audit"))
            payload["visibility_boundary_audit"] = results["visibility_boundary_audit"]
        except Exception as exc:  # pragma: no cover - defensive safety path
            errors.append(_error("visibility", exc))

    if "receipt" in selected:
        try:
            results["receipt_lifecycle_summary"] = llm_receipt_lifecycle_summary(payload, llm_client, fallback=fallback.get("receipt_lifecycle_summary"))
            payload["receipt_lifecycle_summary"] = results["receipt_lifecycle_summary"]
        except Exception as exc:  # pragma: no cover
            errors.append(_error("receipt", exc))

    if "closure" in selected:
        try:
            results["closure_thread_summary"] = llm_closure_thread_summary(payload, llm_client, fallback=fallback.get("closure_thread_summary"))
            payload["closure_thread_summary"] = results["closure_thread_summary"]
        except Exception as exc:  # pragma: no cover
            errors.append(_error("closure", exc))

    if "benchmark" in selected:
        try:
            # Benchmark validity should see the freshest visibility/receipt/closure evidence.
            results["benchmark_validity"] = llm_benchmark_validity(payload, llm_client, fallback=fallback.get("benchmark_validity"))
            payload["benchmark_validity"] = results["benchmark_validity"]
        except Exception as exc:  # pragma: no cover
            errors.append(_error("benchmark", exc))

    metadata = payload.setdefault("metadata", {})
    if isinstance(metadata, dict):
        metadata["hardening_llm_audits"] = {
            "mode": mode,
            "requested_purposes": selected,
            "completed_purposes": sorted(results),
            "error_count": len(errors),
            "auditor_model": getattr(llm_client, "model", ""),
        }
    if errors:
        payload["hardening_llm_audit_errors"] = errors
    return results


def llm_visibility_boundary_audit(payload: dict[str, Any], llm_client: Any, *, fallback: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    fallback = fallback or analyze_visibility_boundary(payload)
    evidence = _visibility_evidence_pack(payload, fallback)
    parsed = _call_json(
        llm_client,
        purpose="hardening_visibility_boundary_auditor",
        prompt_name="visibility_boundary_auditor",
        evidence=evidence,
        temperature=0.0,
    )
    return _merge_audit(
        fallback,
        parsed,
        protocol="careloop.visibility_boundary_audit.v1",
        mode="llm_semantic_audit",
        allowed_status={"no_candidates", "no_leak", "ambiguous", "clear_leak"},
        default_status=fallback.get("status", "ambiguous"),
    )


def llm_benchmark_validity(payload: dict[str, Any], llm_client: Any, *, fallback: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    fallback = fallback or synthesize_benchmark_validity(payload)
    evidence = _benchmark_evidence_pack(payload, fallback)
    parsed = _call_json(
        llm_client,
        purpose="hardening_benchmark_validity_synthesizer",
        prompt_name="benchmark_validity_synthesizer",
        evidence=evidence,
        temperature=0.0,
    )
    merged = _merge_audit(
        fallback,
        parsed,
        protocol="careloop.benchmark_validity.v1",
        mode="llm_semantic_synthesis",
        allowed_status={"include", "include_with_caveats", "secondary_only", "rerun_recommended", "invalid", "not_final"},
        default_status=fallback.get("status", "not_final"),
    )
    merged.setdefault("should_count_in_primary_benchmark", bool(fallback.get("should_count_in_primary_benchmark")))
    merged.setdefault("doctor_performance_usable", bool(fallback.get("doctor_performance_usable")))
    merged.setdefault("doctor_score_should_be_interpreted_as", fallback.get("doctor_score_should_be_interpreted_as", "caveated"))
    return merged


def llm_receipt_lifecycle_summary(payload: dict[str, Any], llm_client: Any, *, fallback: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    fallback = fallback or summarize_receipt_lifecycle(payload)
    evidence = _receipt_evidence_pack(payload, fallback)
    parsed = _call_json(
        llm_client,
        purpose="hardening_receipt_lifecycle_summarizer",
        prompt_name="receipt_lifecycle_summarizer",
        evidence=evidence,
        temperature=0.0,
    )
    merged = dict(fallback)
    if parsed:
        merged.update(parsed)
    merged["protocol"] = "careloop.receipt_lifecycle_summary.v1"
    merged["mode"] = "llm_semantic_summary"
    merged.setdefault("status", fallback.get("status", "observed"))
    return merged


def llm_closure_thread_summary(payload: dict[str, Any], llm_client: Any, *, fallback: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    fallback = fallback or classify_closure_threads(payload)
    evidence = _closure_evidence_pack(payload, fallback)
    parsed = _call_json(
        llm_client,
        purpose="hardening_closure_thread_classifier",
        prompt_name="closure_thread_classifier",
        evidence=evidence,
        temperature=0.0,
    )
    merged = dict(fallback)
    if parsed:
        merged.update(parsed)
    merged["protocol"] = "careloop.closure_thread_summary.v1"
    merged["mode"] = "llm_semantic_classifier"
    if merged.get("overall_closure_readiness") not in {"not_ready", "near_ready", "ready", "unsafe_close"}:
        merged["overall_closure_readiness"] = fallback.get("overall_closure_readiness", "not_ready")
    return merged


def _call_json(llm_client: Any, *, purpose: str, prompt_name: str, evidence: dict[str, Any], temperature: float) -> dict[str, Any]:
    system = load_prompt(prompt_name)
    user = json.dumps(evidence, ensure_ascii=False, indent=2, default=str)
    raw = llm_client.complete(purpose=purpose, system=system, user=user, temperature=temperature)
    parsed = extract_json_object(raw)
    if parsed:
        parsed.setdefault("raw_llm_response_parse_status", "json_object")
        return parsed
    return {"raw_llm_response_parse_status": "unparsed", "raw_llm_response_preview": str(raw or "")[:1200]}


def _merge_audit(
    fallback: dict[str, Any],
    parsed: dict[str, Any],
    *,
    protocol: str,
    mode: str,
    allowed_status: set[str],
    default_status: str,
) -> dict[str, Any]:
    merged = dict(fallback or {})
    if parsed:
        merged.update(parsed)
    merged["protocol"] = protocol
    merged["mode"] = mode
    if merged.get("status") not in allowed_status:
        merged["status"] = default_status
        merged["status_normalization_note"] = "LLM status missing/out of schema; fallback status retained."
    merged.setdefault("confidence", "medium")
    merged.setdefault("fallback_status", (fallback or {}).get("status"))
    return merged


def _visibility_evidence_pack(payload: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    return {
        "task": "visibility_boundary_semantic_audit",
        "case_id": payload.get("case_id"),
        "run_id": payload.get("run_id"),
        "turns_completed": payload.get("turns_completed"),
        "fallback_audit": fallback,
        "thin_candidates": visibility_candidates(payload),
        "latest_visible_exchange": latest_visible_exchange(payload, limit=10),
        "instruction": "Judge semantics, not keywords. Medical uses of stage/system/checkpoint may be normal.",
    }


def _benchmark_evidence_pack(payload: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    return {
        "task": "benchmark_validity_synthesis",
        "case_id": payload.get("case_id"),
        "run_id": payload.get("run_id"),
        "turns_completed": payload.get("turns_completed"),
        "closure": payload.get("closure"),
        "evaluation_brief": _evaluation_brief(payload),
        "quality_report": payload.get("quality_report"),
        "visibility_boundary_audit": payload.get("visibility_boundary_audit"),
        "receipt_lifecycle_summary": payload.get("receipt_lifecycle_summary"),
        "closure_thread_summary": payload.get("closure_thread_summary"),
        "api_runtime_safety": payload.get("api_runtime_safety"),
        "cli_run_metadata": metadata.get("cli_run_metadata") if isinstance(metadata, dict) else {},
        "fallback_validity": fallback,
        "instruction": "Separate doctor performance from simulator/runtime/benchmark validity.",
    }


def _receipt_evidence_pack(payload: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    return {
        "task": "receipt_lifecycle_semantic_summary",
        "case_id": payload.get("case_id"),
        "run_id": payload.get("run_id"),
        "turns_completed": payload.get("turns_completed"),
        "fallback_receipt_summary": fallback,
        "latest_visible_exchange": latest_visible_exchange(payload, limit=12),
        "instruction": "Match ordered/pending/returned/reviewed/acted/communicated/understood semantically; mark unclear when uncertain.",
    }


def _closure_evidence_pack(payload: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    return {
        "task": "closure_thread_semantic_classification",
        "case_id": payload.get("case_id"),
        "run_id": payload.get("run_id"),
        "turns_completed": payload.get("turns_completed"),
        "closure": payload.get("closure"),
        "evaluation_brief": _evaluation_brief(payload),
        "quality_report": payload.get("quality_report"),
        "receipt_lifecycle_summary": payload.get("receipt_lifecycle_summary"),
        "fallback_closure_thread_summary": fallback,
        "latest_visible_exchange": latest_visible_exchange(payload, limit=12),
        "instruction": "Advisory only; do not use a rigid pathway. Identify open/resolved care-process threads.",
    }


def _evaluation_brief(payload: dict[str, Any]) -> dict[str, Any]:
    ev = payload.get("evaluation") if isinstance(payload.get("evaluation"), dict) else {}
    return {
        "overall": ev.get("overall"),
        "evaluation_mode": ev.get("evaluation_mode"),
        "summary": ev.get("summary"),
        "simulation_validity": ev.get("simulation_validity"),
        "medical_closure_status": ev.get("medical_closure_status"),
        "doctor_contribution_to_closure": ev.get("doctor_contribution_to_closure"),
        "clinical_agency": ev.get("clinical_agency"),
        "critical_failures": ev.get("critical_failures"),
        "missed_opportunities": ev.get("missed_opportunities"),
    }


def _error(purpose: str, exc: BaseException) -> dict[str, Any]:
    return {"purpose": purpose, "type": type(exc).__name__, "message": str(exc)[:1000]}
