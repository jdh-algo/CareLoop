
from __future__ import annotations

"""Lightweight FCCT-2 hardening helpers.

Design contract:
- Code enforces boundaries, indexing, schemas, persistence and safe fallbacks.
- Semantic/clinical judgments should be LLM-led when wired; deterministic logic here
  is deliberately thin and conservative.
- No keyword hard ban on ordinary medical language. Words such as "stage",
  "system" and "checkpoint" are only candidates when used in runtime/meta
  semantics, not when used naturally (e.g. CKD stage 3, hospital system,
  checkpoint inhibitor).
"""

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable, Optional, Union

FORMAL_CARELOOP_INTERNAL_MODEL = "GPT-5.5"
FORMAL_DOCTOR_MODELS = [
    "DeepSeek-V3.2",
    "DeepSeek-V4-Pro",
    "Doubao-Seed-2.0-lite",
    "GLM-5",
    "GPT-5.4",
    "Kimi K2",
    "MiniMax M3",
    "GPT-5.6 Sol",
    "Qwen3.8-Max",
    "Qwen3-30B-A3B-Instruct-2507",
]
HARDENING_VERSION = "fcct2.formal_cross_model_hardening.v1"
P07_FORMAL_MIXED_COMPLEXITY_MANIFEST_SCHEMA = "careloop.p07_formal_mixed_complexity_manifest.v1"

# Thin candidate triggers. These are NOT forbidden words; they only select text
# for semantic adjudication/fallback review.
_RUNTIME_SEMANTIC_PATTERNS = [
    r"\bClosureJudge\b",
    r"\bWorldDirector\b",
    r"\bLiteCareLoopRunner\b",
    r"\bruntime_lite\b",
    r"\btrajectory_evaluator\b",
    r"\bdoctor_operation_router\b",
    r"\bclinical_memory_steward\b",
    r"\bactor_realism_degrader\b",
    r"\bstage_after_[A-Za-z0-9_]+\b",
    r"\bdoctor_operation_registered\b",
    r"\bopen_progressing\b",
    r"\bclosure_status\b",
    r"\bevent_type\b",
    r"\bllm purpose\b",
    r"\btraceback \(most recent call last\)\b",
    r"\bAPI key\b",
    r"\braw API error\b",
]
_RUNTIME_RE = re.compile("|".join("(?:%s)" % p for p in _RUNTIME_SEMANTIC_PATTERNS), re.IGNORECASE)

_SAFE_MEDICAL_FALSE_POSITIVE_RE = re.compile(
    r"\b(?:CKD|cancer|tumou?r|肾病|癌|肿瘤|慢性肾病)\s+stage\s*[0-9ivx]+\b|"
    r"\bcheckpoint inhibitor\b|免疫检查点抑制剂|医院系统|医疗系统|系统感染",
    re.IGNORECASE,
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def manifest_hash(path: Union[str, Path]) -> str:
    p = Path(path)
    data = p.read_bytes()
    return hashlib.sha256(data).hexdigest()


def safe_model_name(model: str) -> str:
    value = str(model or "").strip() or "unknown_model"
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value)
    value = value.replace(".", "_")
    return value.strip("_") or "unknown_model"


def build_model_role_contract(sim_model: str, doctor_model: str, *, formal: bool = False) -> dict[str, Any]:
    sim = str(sim_model or "").strip()
    doctor = str(doctor_model or "").strip()
    return {
        "protocol": "careloop.model_role_contract.v1",
        "formal_fcct2_cross_model": bool(formal),
        "careloop_internal_model": sim,
        "doctor_model": doctor,
        "roles": {
            "simulator_llm": sim,
            "world_director": sim,
            "patient_actor": sim,
            "family_actor": sim,
            "diagnostic_service_simulator": sim,
            "clinical_memory_steward": sim,
            "closure_judge": sim,
            "trajectory_evaluator": sim,
            "hardening_auditor": sim,
            "doctor": doctor,
            "doctor_after_tool": doctor,
            "doctor_self_context": doctor,
        },
    }


def validate_formal_model_contract(
    *,
    sim_model: str,
    doctor_model: str,
    internal_model: str = FORMAL_CARELOOP_INTERNAL_MODEL,
    allowed_doctor_models: Optional[Iterable[str]] = None,
) -> None:
    internal = str(internal_model or FORMAL_CARELOOP_INTERNAL_MODEL).strip()
    allowed = [str(item).strip() for item in (allowed_doctor_models or FORMAL_DOCTOR_MODELS) if str(item).strip()]
    sim = str(sim_model or "").strip()
    doctor = str(doctor_model or "").strip()
    if sim != internal:
        raise RuntimeError(
            "formal FCCT-2 run requires CareLoop internal/sim model "
            f"{internal!r}; got sim_model={sim!r}"
        )
    if doctor not in allowed:
        raise RuntimeError(
            "formal FCCT-2 run doctor_model must be one of "
            f"{allowed!r}; got {doctor!r}. Model names are case-sensitive."
        )


def _is_p07_formal_mixed_complexity_manifest(data: dict[str, Any]) -> bool:
    schema = str(data.get("schema_version") or data.get("protocol") or "").strip()
    return schema == P07_FORMAL_MIXED_COMPLEXITY_MANIFEST_SCHEMA


def load_formal_manifest(path: Union[str, Path]) -> dict[str, Any]:
    """Load and validate a formal experiment manifest.

    Backward compatibility is intentional: legacy FCCT-2 manifests remain the
    original exactly-10/L01-L10 contract.  P07 explicitly upgrades only the
    manifest *capacity* so the same runner, closure judge, trajectory evaluator,
    safety rules, early-closure mechanism, and sidecar contract can run a
    20-case mixed-complexity panel.  This function must not encode clinical
    difficulty or alter closure semantics.
    """

    manifest_path = Path(path)
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError("formal manifest must be a JSON object")
    cases = data.get("cases")
    if not isinstance(cases, list):
        raise RuntimeError("formal manifest cases must be a list")

    if _is_p07_formal_mixed_complexity_manifest(data):
        declared_count = data.get("case_count")
        if declared_count is not None:
            try:
                declared_count_int = int(declared_count)
            except (TypeError, ValueError) as exc:
                raise RuntimeError("P07 formal manifest case_count must be an integer") from exc
            if declared_count_int != len(cases):
                raise RuntimeError(
                    "P07 formal manifest case_count does not match cases length: "
                    f"case_count={declared_count_int} len(cases)={len(cases)}"
                )
        if len(cases) < 1:
            raise RuntimeError("P07 formal manifest must contain at least one case")
        labels: list[str] = []
        case_ids: list[str] = []
        for idx, item in enumerate(cases, start=1):
            if not isinstance(item, dict):
                raise RuntimeError(f"P07 formal manifest case #{idx} must be a JSON object")
            label = str(item.get("case_label") or item.get("label") or "").strip()
            case_id = str(item.get("case_id") or "").strip()
            case_path = str(item.get("path") or item.get("case_path") or "").strip()
            if not label:
                raise RuntimeError(f"P07 formal manifest case #{idx} missing case_label")
            if not case_id:
                raise RuntimeError(f"P07 formal manifest case {label} missing case_id")
            if not case_path:
                raise RuntimeError(f"P07 formal manifest case {label} missing path")
            labels.append(label)
            case_ids.append(case_id)
        duplicate_labels = sorted(k for k, v in Counter(labels).items() if v > 1)
        duplicate_case_ids = sorted(k for k, v in Counter(case_ids).items() if v > 1)
        if duplicate_labels:
            raise RuntimeError("P07 formal manifest duplicate case labels: " + ", ".join(duplicate_labels))
        if duplicate_case_ids:
            raise RuntimeError("P07 formal manifest duplicate case_ids: " + ", ".join(duplicate_case_ids))
        data.setdefault("manifest_hash", manifest_hash(manifest_path))
        return data

    if len(cases) != 10:
        raise RuntimeError("formal FCCT-2 manifest must contain exactly 10 cases")
    labels = [str(item.get("case_label") or item.get("label") or "") for item in cases if isinstance(item, dict)]
    missing = ["L%02d" % i for i in range(1, 11) if "L%02d" % i not in labels]
    if missing:
        raise RuntimeError("formal FCCT-2 manifest missing case labels: " + ", ".join(missing))
    data.setdefault("manifest_hash", manifest_hash(manifest_path))
    return data


def build_status_index(payload: dict[str, Any], *, output_kind: str = "final") -> dict[str, Any]:
    hardening = attach_hardening_reports(payload)
    closure = payload.get("closure") if isinstance(payload.get("closure"), dict) else {}
    evaluation = payload.get("evaluation") if isinstance(payload.get("evaluation"), dict) else {}
    quality = payload.get("quality_report") if isinstance(payload.get("quality_report"), dict) else {}
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    cli_meta = metadata.get("cli_run_metadata") if isinstance(metadata.get("cli_run_metadata"), dict) else {}
    runtime_error = payload.get("runtime_error") if isinstance(payload.get("runtime_error"), dict) else {}
    return {
        "protocol": "careloop.runtime_lite.status_index.v1",
        "hardening_version": HARDENING_VERSION,
        "output_kind": output_kind,
        "updated_at": utc_now_iso(),
        "case_id": payload.get("case_id"),
        "run_id": payload.get("run_id"),
        "turns_completed": payload.get("turns_completed"),
        "max_turns": cli_meta.get("max_turns"),
        "closure_status": closure.get("status"),
        "closure_kind": closure.get("closure_kind"),
        "evaluation_mode": evaluation.get("evaluation_mode"),
        "evaluation_overall": evaluation.get("overall"),
        "quality_status": quality.get("status"),
        "quality_flags": quality.get("flags") or [],
        "benchmark_validity_status": (hardening.get("benchmark_validity") or {}).get("status"),
        "benchmark_primary": (hardening.get("benchmark_validity") or {}).get("should_count_in_primary_benchmark"),
        "visibility_boundary_status": (hardening.get("visibility_boundary_audit") or {}).get("status"),
        "visibility_boundary_max_severity": (hardening.get("visibility_boundary_audit") or {}).get("max_severity"),
        "receipt_lifecycle_status": (hardening.get("receipt_lifecycle_summary") or {}).get("status"),
        "closure_thread_readiness": (hardening.get("closure_thread_summary") or {}).get("overall_closure_readiness"),
        "runtime_error_type": runtime_error.get("type") or "none",
        "runtime_error_message_present": bool(runtime_error.get("message")),
        "formal_fcct2_cross_model": bool(cli_meta.get("formal_fcct2_cross_model")),
        "careloop_internal_model": cli_meta.get("careloop_internal_model") or cli_meta.get("sim_model"),
        "doctor_model": cli_meta.get("doctor_model"),
        "experiment_id": cli_meta.get("experiment_id"),
        "manifest_hash": cli_meta.get("manifest_hash"),
        "hardening_llm_audits": metadata.get("hardening_llm_audits") if isinstance(metadata.get("hardening_llm_audits"), dict) else {},
    }


def latest_visible_exchange(payload: dict[str, Any], *, limit: int = 8) -> dict[str, Any]:
    traj = payload.get("trajectory") if isinstance(payload.get("trajectory"), dict) else {}
    transcript = traj.get("transcript") if isinstance(traj.get("transcript"), list) else []
    items = []
    for msg in transcript[-limit:]:
        if not isinstance(msg, dict):
            continue
        items.append(
            {
                "turn": msg.get("turn"),
                "sim_time": msg.get("sim_time"),
                "speaker": msg.get("speaker"),
                "speaker_display": msg.get("speaker_display"),
                "speaker_category": msg.get("speaker_category"),
                "event_type": msg.get("event_type"),
                "text": msg.get("text"),
            }
        )
    return {
        "protocol": "careloop.latest_visible_exchange.v1",
        "case_id": payload.get("case_id"),
        "run_id": payload.get("run_id"),
        "turns_completed": payload.get("turns_completed"),
        "message_count": len(items),
        "messages": items,
    }


def _visible_text_records(payload: dict[str, Any]) -> list[dict[str, Any]]:
    records = []
    traj = payload.get("trajectory") if isinstance(payload.get("trajectory"), dict) else {}
    transcript = traj.get("transcript") if isinstance(traj.get("transcript"), list) else []
    for i, msg in enumerate(transcript):
        if not isinstance(msg, dict):
            continue
        text = str(msg.get("text") or "")
        if text:
            records.append(
                {
                    "source": "transcript",
                    "index": i,
                    "turn": msg.get("turn"),
                    "speaker": msg.get("speaker"),
                    "event_type": msg.get("event_type"),
                    "text": text,
                }
            )
    events = traj.get("events") if isinstance(traj.get("events"), list) else []
    for i, event in enumerate(events):
        if not isinstance(event, dict):
            continue
        visibility = str(event.get("visibility") or "")
        event_type = str(event.get("event_type") or "")
        if visibility not in {"patient_visible", "doctor_visible"} and event_type not in {
            "doctor_message",
            "patient_message",
            "family_message",
            "doctor_system_notification",
        }:
            continue
        content = event.get("content") if isinstance(event.get("content"), dict) else {}
        text = content.get("text") if isinstance(content, dict) else None
        if not text:
            continue
        records.append(
            {
                "source": "event",
                "index": i,
                "turn": event.get("turn"),
                "speaker": event.get("actor"),
                "event_type": event_type,
                "visibility": visibility,
                "text": str(text),
            }
        )
    return records


def visibility_candidates(payload: dict[str, Any], *, max_candidates: int = 30) -> list[dict[str, Any]]:
    candidates = []
    seen = set()
    for rec in _visible_text_records(payload):
        text = rec.get("text") or ""
        for match in _RUNTIME_RE.finditer(text):
            start = max(0, match.start() - 90)
            end = min(len(text), match.end() + 120)
            excerpt = text[start:end].replace("\n", " ")
            key = (rec.get("source"), rec.get("index"), match.group(0), excerpt)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(
                {
                    "turn": rec.get("turn"),
                    "source": rec.get("source"),
                    "index": rec.get("index"),
                    "speaker": rec.get("speaker"),
                    "event_type": rec.get("event_type"),
                    "visibility": rec.get("visibility"),
                    "trigger": match.group(0),
                    "excerpt": excerpt,
                    "medical_false_positive_candidate": bool(_SAFE_MEDICAL_FALSE_POSITIVE_RE.search(excerpt)),
                }
            )
            if len(candidates) >= max_candidates:
                return candidates
    return candidates


def analyze_visibility_boundary(payload: dict[str, Any]) -> dict[str, Any]:
    candidates = visibility_candidates(payload)
    if not candidates:
        return {
            "protocol": "careloop.visibility_boundary_audit.v1",
            "mode": "thin_deterministic_fallback_llm_ready",
            "status": "no_candidates",
            "max_severity": "none",
            "candidate_count": 0,
            "medical_language_false_positive_guard": True,
            "benchmark_impact": "none",
            "evidence": [],
            "recommended_action": "none",
            "notes": ["No thin runtime/meta candidate was found in visible transcript/events."],
        }
    high_risk = []
    ambiguous = []
    for cand in candidates:
        trigger = str(cand.get("trigger") or "")
        excerpt = str(cand.get("excerpt") or "")
        if cand.get("medical_false_positive_candidate") and trigger.lower() in {"stage", "system", "checkpoint"}:
            ambiguous.append(cand)
            continue
        if re.search(r"ClosureJudge|WorldDirector|stage_after_|doctor_operation_registered|open_progressing|runtime_lite|traceback|raw API", excerpt, re.I):
            high_risk.append(cand)
        else:
            ambiguous.append(cand)
    status = "clear_leak" if high_risk else "ambiguous"
    severity = "major" if any(re.search(r"traceback|API key|raw API", str(c.get("excerpt") or ""), re.I) for c in high_risk) else ("moderate" if high_risk else "minor")
    return {
        "protocol": "careloop.visibility_boundary_audit.v1",
        "mode": "thin_deterministic_fallback_llm_ready",
        "status": status,
        "max_severity": severity,
        "candidate_count": len(candidates),
        "medical_language_false_positive_guard": True,
        "benchmark_impact": "rerun_recommended" if status == "clear_leak" and severity in {"moderate", "major"} else "minor_validity_caveat",
        "evidence": high_risk[:10] if high_risk else ambiguous[:10],
        "recommended_action": "rerun_case" if status == "clear_leak" and severity in {"moderate", "major"} else "include_with_caveat",
        "notes": [
            "Fallback only: candidates are selected by thin runtime/meta triggers; final semantic adjudication should be LLM-led.",
            "No ordinary medical word is hard-banned."
        ],
    }


def summarize_receipt_lifecycle(payload: dict[str, Any]) -> dict[str, Any]:
    traj = payload.get("trajectory") if isinstance(payload.get("trajectory"), dict) else {}
    events = traj.get("events") if isinstance(traj.get("events"), list) else []
    receipt_events = []
    workspace_results = []
    doctor_ops = []
    for event in events:
        if not isinstance(event, dict):
            continue
        et = event.get("event_type")
        item = {
            "turn": event.get("turn"),
            "event_type": et,
            "actor": event.get("actor"),
            "sim_time": event.get("sim_time"),
            "content_preview": _brief(event.get("content")),
        }
        if et == "doctor_care_system_receipt":
            receipt_events.append(item)
        elif et == "doctor_workspace_result":
            workspace_results.append(item)
        elif et in {"doctor_operation", "doctor_operation_registered"}:
            doctor_ops.append(item)
    status = "no_receipts" if not receipt_events and not workspace_results else "observed"
    return {
        "protocol": "careloop.receipt_lifecycle_summary.v1",
        "mode": "deterministic_index_llm_ready",
        "status": status,
        "doctor_operation_count": len(doctor_ops),
        "care_system_receipt_count": len(receipt_events),
        "workspace_result_count": len(workspace_results),
        "recent_receipts": receipt_events[-12:],
        "recent_workspace_results": workspace_results[-12:],
        "pending_actionable_items": [],
        "stale_unreviewed_results": [],
        "notes": ["Fallback summary indexes receipt/result events only; semantic lifecycle matching is intended to be LLM-led."],
    }


def classify_closure_threads(payload: dict[str, Any], receipt_summary: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    closure = payload.get("closure") if isinstance(payload.get("closure"), dict) else {}
    evaluation = payload.get("evaluation") if isinstance(payload.get("evaluation"), dict) else {}
    status = closure.get("status")
    if status == "closed":
        readiness = "ready"
    elif status in {"unsafe", "error"}:
        readiness = "unsafe_close"
    else:
        readiness = "not_ready"
    receipt_summary = receipt_summary or summarize_receipt_lifecycle(payload)
    threads = [
        {"thread": "presenting_concern", "status": "resolved" if status == "closed" else "open", "closure_relevance": "high"},
        {"thread": "active_treatment", "status": "resolved" if status == "closed" else "open", "closure_relevance": "high"},
        {"thread": "test_result_receipt", "status": "observed" if receipt_summary.get("care_system_receipt_count") or receipt_summary.get("workspace_result_count") else "not_observed", "closure_relevance": "medium"},
        {"thread": "followup_and_safety_net", "status": "resolved" if status == "closed" else "open", "closure_relevance": "high"},
    ]
    return {
        "protocol": "careloop.closure_thread_summary.v1",
        "mode": "deterministic_fallback_llm_ready",
        "overall_closure_readiness": readiness,
        "threads": threads,
        "main_open_threads": [t["thread"] for t in threads if t.get("status") == "open"],
        "closure_status": status,
        "evaluation_overall": evaluation.get("overall"),
        "notes": ["Fallback only; final closure-thread semantics should be LLM-led and advisory, not a hard gate."],
    }


def classify_runtime_error_for_safe_retry(payload_or_error: Any) -> dict[str, Any]:
    if isinstance(payload_or_error, dict):
        err = payload_or_error.get("runtime_error") if isinstance(payload_or_error.get("runtime_error"), dict) else payload_or_error
        message = str(err.get("message") or err.get("error") or "")
        err_type = str(err.get("type") or "")
    else:
        message = str(payload_or_error or "")
        err_type = type(payload_or_error).__name__ if payload_or_error is not None else "none"
    text = (err_type + " " + message).lower()
    if not text.strip() or text.strip() == "none":
        klass, safe, action = "none", True, "none"
    elif any(x in text for x in ["unauthorized", "forbidden", "permission", "invalid api key", "auth"]):
        klass, safe, action = "auth_permission", False, "refresh_auth"
    elif any(x in text for x in ["rate limit", "429", "too many requests"]):
        klass, safe, action = "rate_limit", True, "wait_and_retry"
    elif any(x in text for x in ["timed out", "timeout", "connection", "network", "vpn", "dns", "temporarily unavailable", "502", "503", "504"]):
        klass, safe, action = "transient_network", True, "retry"
    elif any(x in text for x in ["json", "parse", "malformed"]):
        klass, safe, action = "malformed_json", True, "limited_rescue"
    elif any(x in text for x in ["empty output", "empty response"]):
        klass, safe, action = "model_empty_output", True, "rescue_or_retry"
    else:
        klass, safe, action = "unknown", False, "stop_for_review"
    return {
        "protocol": "careloop.api_runtime_safety.v1",
        "runtime_error_class": klass,
        "safe_to_retry": bool(safe),
        "safe_to_resume_from_checkpoint": bool(safe),
        "doctor_visible_contamination_risk": "possible" if klass != "none" else "none",
        "recommended_action": action,
        "error_type": err_type,
        "message_preview": message[:500],
    }


def synthesize_benchmark_validity(payload: dict[str, Any], *, visibility_audit: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    closure = payload.get("closure") if isinstance(payload.get("closure"), dict) else {}
    evaluation = payload.get("evaluation") if isinstance(payload.get("evaluation"), dict) else {}
    quality = payload.get("quality_report") if isinstance(payload.get("quality_report"), dict) else {}
    runtime_error = payload.get("runtime_error") if isinstance(payload.get("runtime_error"), dict) else {}
    visibility_audit = visibility_audit or analyze_visibility_boundary(payload)
    reasons = []
    caveats = []
    closure_status = closure.get("status")
    if closure_status != "closed":
        return {
            "protocol": "careloop.benchmark_validity.v1",
            "mode": "deterministic_fallback_llm_ready",
            "status": "not_final",
            "should_count_in_primary_benchmark": False,
            "doctor_performance_usable": False,
            "doctor_score_should_be_interpreted_as": "not_usable_yet",
            "main_reasons": ["Trajectory is not closed/final."],
            "simulator_quality_caveats": [],
            "runtime_caveats": [],
            "recommended_action": "continue_or_rerun",
            "confidence": "medium",
        }
    if runtime_error:
        reasons.append("Runtime error was recorded.")
        return _validity_payload("rerun_recommended", False, True, "caveated", reasons, [], [{"type": "runtime_error"}], "rerun", "medium")
    if visibility_audit.get("status") == "clear_leak":
        reasons.append("Visible content has runtime/meta leakage candidates.")
        return _validity_payload("rerun_recommended", False, True, "caveated", reasons, [], [visibility_audit], "rerun", "medium")
    qstatus = str(quality.get("status") or "")
    qflags = [str(x) for x in (quality.get("flags") or [])]
    if qstatus == "fail":
        reasons.append("Quality report is fail; doctor performance may still be usable as caveated evidence.")
        return _validity_payload("rerun_recommended", False, True, "caveated", reasons, [{"type": flag, "severity": "fail"} for flag in qflags], [], "rerun", "medium")
    high_risk_flags = [flag for flag in qflags if "overcooperation_high_risk" in flag or "internal_runtime" in flag]
    review_flags = [flag for flag in qflags if flag not in high_risk_flags]
    if high_risk_flags:
        caveats.extend({"type": flag, "severity": "review"} for flag in high_risk_flags)
        reasons.append("High-risk simulator/actor quality caveats are present.")
        return _validity_payload("include_with_caveats", False, True, "caveated", reasons, caveats, [], "include_with_caveat", "medium")
    if qstatus == "review" or review_flags:
        caveats.extend({"type": flag, "severity": "review"} for flag in review_flags)
        reasons.append("Review-level simulator/actor caveats are present.")
        return _validity_payload("include_with_caveats", True, True, "primary_caveated", reasons, caveats, [], "include_with_caveat", "medium")
    reasons.append("Closed trajectory has no deterministic hardening caveats.")
    return _validity_payload("include", True, True, "primary", reasons, [], [], "include", "medium")


def _validity_payload(status: str, primary: bool, usable: bool, interpretation: str, reasons: list, simulator_caveats: list, runtime_caveats: list, action: str, confidence: str) -> dict[str, Any]:
    return {
        "protocol": "careloop.benchmark_validity.v1",
        "mode": "deterministic_fallback_llm_ready",
        "status": status,
        "should_count_in_primary_benchmark": bool(primary),
        "doctor_performance_usable": bool(usable),
        "doctor_score_should_be_interpreted_as": interpretation,
        "main_reasons": reasons,
        "simulator_quality_caveats": simulator_caveats,
        "runtime_caveats": runtime_caveats,
        "recommended_action": action,
        "confidence": confidence,
    }


def attach_hardening_reports(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    visibility = payload.get("visibility_boundary_audit") if isinstance(payload.get("visibility_boundary_audit"), dict) else None
    if visibility is None:
        visibility = analyze_visibility_boundary(payload)
        payload["visibility_boundary_audit"] = visibility
    if not isinstance(payload.get("receipt_lifecycle_summary"), dict):
        payload["receipt_lifecycle_summary"] = summarize_receipt_lifecycle(payload)
    if not isinstance(payload.get("closure_thread_summary"), dict):
        payload["closure_thread_summary"] = classify_closure_threads(payload, payload.get("receipt_lifecycle_summary"))
    if not isinstance(payload.get("api_runtime_safety"), dict):
        payload["api_runtime_safety"] = classify_runtime_error_for_safe_retry(payload)
    if not isinstance(payload.get("benchmark_validity"), dict):
        payload["benchmark_validity"] = synthesize_benchmark_validity(payload, visibility_audit=visibility)
    metadata = payload.setdefault("metadata", {})
    if isinstance(metadata, dict):
        metadata.setdefault("hardening_version", HARDENING_VERSION)
        metadata.setdefault("hardening_attached_at", utc_now_iso())
    return {
        "visibility_boundary_audit": payload.get("visibility_boundary_audit"),
        "benchmark_validity": payload.get("benchmark_validity"),
        "receipt_lifecycle_summary": payload.get("receipt_lifecycle_summary"),
        "closure_thread_summary": payload.get("closure_thread_summary"),
        "api_runtime_safety": payload.get("api_runtime_safety"),
    }



def build_resume_manifest(output_dir: Union[str, Path], payload: dict[str, Any], *, output_kind: str = "final") -> dict[str, Any]:
    """Build a lightweight resume/safety manifest without hashing huge trajectories.

    The manifest records file presence, size and mtime for recovery decisions.
    It deliberately avoids reading multi-hundred-MB trajectory files on every
    checkpoint; a deeper integrity validator can be run explicitly before a
    resume attempt.
    """

    out = Path(output_dir)
    checkpoint = out / "trajectory.checkpoint.json"
    final = out / "trajectory.json"

    def stat(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {"path": str(path), "exists": False}
        st = path.stat()
        return {"path": str(path), "exists": True, "size_bytes": st.st_size, "mtime_epoch": st.st_mtime}

    api = payload.get("api_runtime_safety") if isinstance(payload.get("api_runtime_safety"), dict) else classify_runtime_error_for_safe_retry(payload)
    turns = int(payload.get("turns_completed") or 0)
    closure = payload.get("closure") if isinstance(payload.get("closure"), dict) else {}
    checkpoint_stat = stat(checkpoint)
    final_stat = stat(final)
    if final_stat.get("exists") and closure.get("status") == "closed":
        safety = "final_complete_no_resume_needed"
    elif checkpoint_stat.get("exists"):
        safety = "checkpoint_candidate_available"
    else:
        safety = "no_checkpoint_available"
    return {
        "protocol": "careloop.resume_manifest.v1",
        "hardening_version": HARDENING_VERSION,
        "updated_at": utc_now_iso(),
        "output_kind": output_kind,
        "case_id": payload.get("case_id"),
        "run_id": payload.get("run_id"),
        "last_complete_turn": turns,
        "next_turn_to_run": turns + 1,
        "closure_status": closure.get("status"),
        "resume_safety": safety,
        "safe_to_resume_from_checkpoint": bool(api.get("safe_to_resume_from_checkpoint", safety == "checkpoint_candidate_available")),
        "api_runtime_safety": api,
        "trajectory_checkpoint": checkpoint_stat,
        "trajectory_final": final_stat,
        "notes": [
            "Lightweight recovery record only; validate checkpoint JSON before an actual resume.",
            "Runtime/API errors must not be inserted into patient- or doctor-visible trajectory content.",
        ],
    }

def write_hardening_artifacts(
    output_dir: Union[str, Path],
    payload: dict[str, Any],
    *,
    output_kind: str = "final",
    include_deep_audits: bool = True,
) -> None:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    reports = attach_hardening_reports(payload)
    artifacts = {
        "status.json": build_status_index(payload, output_kind=output_kind),
        "heartbeat.json": {
            "protocol": "careloop.heartbeat.v1",
            "hardening_version": HARDENING_VERSION,
            "output_kind": output_kind,
            "updated_at": utc_now_iso(),
            "case_id": payload.get("case_id"),
            "run_id": payload.get("run_id"),
            "turns_completed": payload.get("turns_completed"),
        },
        "latest_visible_exchange.json": latest_visible_exchange(payload),
        "latest_closure.json": payload.get("closure") if isinstance(payload.get("closure"), dict) else {},
        "api_runtime_safety.json": reports.get("api_runtime_safety") or {},
        "resume_manifest.json": build_resume_manifest(out, payload, output_kind=output_kind),
    }
    if include_deep_audits:
        artifacts.update(
            {
                "visibility_boundary_audit.json": reports.get("visibility_boundary_audit") or {},
                "benchmark_validity.json": reports.get("benchmark_validity") or {},
                "receipt_lifecycle_summary.json": reports.get("receipt_lifecycle_summary") or {},
                "closure_thread_summary.json": reports.get("closure_thread_summary") or {},
            }
        )
    for name, obj in artifacts.items():
        (out / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def hardening_summary_markdown_lines(payload: dict[str, Any]) -> list[str]:
    reports = attach_hardening_reports(payload)
    validity = reports.get("benchmark_validity") or {}
    visibility = reports.get("visibility_boundary_audit") or {}
    receipt = reports.get("receipt_lifecycle_summary") or {}
    closure_threads = reports.get("closure_thread_summary") or {}
    api = reports.get("api_runtime_safety") or {}
    return [
        f"- hardening_version: {HARDENING_VERSION}",
        f"- benchmark_validity: {validity.get('status')} / primary={validity.get('should_count_in_primary_benchmark')} / doctor_usable={validity.get('doctor_performance_usable')}",
        f"- visibility_boundary: {visibility.get('status')} / severity={visibility.get('max_severity')} / candidates={visibility.get('candidate_count')}",
        f"- receipt_lifecycle: receipts={receipt.get('care_system_receipt_count')} / workspace_results={receipt.get('workspace_result_count')}",
        f"- closure_thread_readiness: {closure_threads.get('overall_closure_readiness')}",
        f"- api_runtime_safety: {api.get('runtime_error_class')} / action={api.get('recommended_action')}",
    ]


def hardening_counts_from_summaries(summaries: list[dict[str, Any]]) -> dict[str, Any]:
    def count(key: str) -> dict[str, int]:
        return dict(Counter(str(item.get(key) or "") for item in summaries))
    return {
        "benchmark_validity_counts": count("benchmark_validity_status"),
        "benchmark_primary_count": sum(1 for item in summaries if item.get("benchmark_primary")),
        "visibility_boundary_status_counts": count("visibility_boundary_status"),
        "visibility_boundary_max_severity_counts": count("visibility_boundary_max_severity"),
        "closure_thread_readiness_counts": count("closure_thread_readiness"),
        "receipt_lifecycle_status_counts": count("receipt_lifecycle_status"),
    }


def _brief(value: Any, limit: int = 240) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] + ("..." if len(text) > limit else "")
