from __future__ import annotations

"""Non-weighted CareLoop interface discipline observations.

This module intentionally reports thin objective routing/interface signals only.
It must not assign clinical quality grades, change closure, or participate in
weighted trajectory scoring.
"""

from collections import Counter
import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping


INTERFACE_SIGNAL_KEYS = [
    "doctor_envelope_routing_count",
    "doctor_envelope_valid_count",
    "doctor_envelope_invalid_count",
    "doctor_envelope_repaired_count",
    "doctor_envelope_format_warning_count",
    "doctor_envelope_invalid_fallback_count",
    "care_route_count",
    "workspace_route_count",
    "doctor_workspace_message_count",
    "clinical_workspace_response_count",
    "workspace_round_cap_reached_count",
    "workspace_round_cap_enforced_count",
    "max_workspace_round_observed",
    "patient_facing_boundary_check_count",
    "patient_facing_boundary_candidate_event_count",
    "patient_facing_boundary_rewrite_count",
    "raw_envelope_patient_visible_count",
    "workspace_artifact_patient_visible_count",
]


# Keep these patterns deliberately narrow.  Do not flag broad medical words such
# as "stage" or "system" alone; they can be normal patient language.
RAW_ENVELOPE_PATTERNS = [
    re.compile(r"^\s*```?(?:json)?\s*\{\s*[\"']to[\"']\s*:\s*[\"'](?:care|workspace)[\"']", re.IGNORECASE | re.DOTALL),
    re.compile(r"^\s*\{\s*[\"']to[\"']\s*:\s*[\"'](?:care|workspace)[\"']\s*,\s*[\"']content[\"']\s*:", re.IGNORECASE | re.DOTALL),
]

WORKSPACE_ARTIFACT_PATTERNS = [
    re.compile(r"\bClinical\s+Workspace\b", re.IGNORECASE),
    re.compile(r"\bruntime_lite\b", re.IGNORECASE),
    re.compile(r"\bClosureJudge\b", re.IGNORECASE),
    re.compile(r"\bWorldDirector\b", re.IGNORECASE),
    re.compile(r"\bstage_after_[A-Za-z0-9_]+\b", re.IGNORECASE),
    re.compile(r"医生工作备注已保存"),
    re.compile(r"医生草稿"),
]


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _iter_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict):
                    rows.append(item)
    except OSError:
        return []
    return rows


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def patient_visible_interface_artifact_counts(transcript: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    raw_envelope_count = 0
    workspace_artifact_count = 0
    for item in transcript or []:
        if not isinstance(item, Mapping):
            continue
        if not bool(item.get("patient_visible", True)):
            continue
        text = str(item.get("text") or "")
        if not text:
            continue
        if any(pattern.search(text) for pattern in RAW_ENVELOPE_PATTERNS):
            raw_envelope_count += 1
        if any(pattern.search(text) for pattern in WORKSPACE_ARTIFACT_PATTERNS):
            workspace_artifact_count += 1
    return {
        "raw_envelope_patient_visible_count": raw_envelope_count,
        "workspace_artifact_patient_visible_count": workspace_artifact_count,
    }


def interface_discipline_signals_from_events(
    events: Iterable[Mapping[str, Any]],
    transcript: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, int]:
    signals = {key: 0 for key in INTERFACE_SIGNAL_KEYS}
    max_workspace_round = 0
    for event in events or []:
        if not isinstance(event, Mapping):
            continue
        event_type = str(event.get("event_type") or "")
        content = event.get("content") if isinstance(event.get("content"), Mapping) else {}
        metadata = event.get("metadata") if isinstance(event.get("metadata"), Mapping) else {}
        if event_type == "doctor_envelope_routing":
            signals["doctor_envelope_routing_count"] += 1
            valid = bool(content.get("valid"))
            if valid:
                signals["doctor_envelope_valid_count"] += 1
            else:
                signals["doctor_envelope_invalid_count"] += 1
            if bool(content.get("repaired_from_malformed_json")):
                signals["doctor_envelope_repaired_count"] += 1
            route = str(content.get("to") or "").strip().lower()
            if route == "care":
                signals["care_route_count"] += 1
            elif route == "workspace":
                signals["workspace_route_count"] += 1
            max_workspace_round = max(max_workspace_round, _as_int(content.get("workspace_round"), 0))
        elif event_type == "doctor_envelope_format_warning":
            signals["doctor_envelope_format_warning_count"] += 1
            if str(content.get("fallback_route") or "").strip().lower() == "care":
                signals["doctor_envelope_invalid_fallback_count"] += 1
        elif event_type == "doctor_workspace_message":
            signals["doctor_workspace_message_count"] += 1
            max_workspace_round = max(max_workspace_round, _as_int(metadata.get("workspace_round"), 0))
        elif event_type == "clinical_workspace_response":
            signals["clinical_workspace_response_count"] += 1
            max_workspace_round = max(max_workspace_round, _as_int(metadata.get("workspace_round") or content.get("workspace_round"), 0))
        elif event_type == "doctor_workspace_round_cap_reached":
            signals["workspace_round_cap_reached_count"] += 1
            max_workspace_round = max(max_workspace_round, _as_int(content.get("workspace_rounds"), 0))
        elif event_type == "doctor_workspace_round_cap_enforced":
            signals["workspace_round_cap_enforced_count"] += 1
            max_workspace_round = max(max_workspace_round, _as_int(content.get("workspace_rounds"), 0))
        elif event_type == "patient_facing_boundary_check":
            signals["patient_facing_boundary_check_count"] += 1
            candidate_count = _as_int(content.get("candidate_count"), 0)
            if candidate_count > 0:
                signals["patient_facing_boundary_candidate_event_count"] += 1
            status = str(content.get("status") or "").strip().lower()
            if bool(content.get("rewrite_applied")) or status in {"rewritten", "rewritten_by_fallback"}:
                signals["patient_facing_boundary_rewrite_count"] += 1
    signals["max_workspace_round_observed"] = max_workspace_round
    if transcript is not None:
        signals.update(patient_visible_interface_artifact_counts(transcript))
    return signals


def interface_observation_level(signals: Mapping[str, Any]) -> str:
    raw_leak = _as_int(signals.get("raw_envelope_patient_visible_count"), 0)
    artifact_leak = _as_int(signals.get("workspace_artifact_patient_visible_count"), 0)
    if raw_leak or artifact_leak:
        return "patient_visible_leak_observed"
    if _as_int(signals.get("patient_facing_boundary_rewrite_count"), 0) > 0:
        return "boundary_rewrite_needed"
    if _as_int(signals.get("workspace_round_cap_enforced_count"), 0) > 0:
        return "workspace_loop_pressure"
    if _as_int(signals.get("doctor_envelope_invalid_fallback_count"), 0) > 0:
        return "invalid_fallback_no_leakage"
    repairs = _as_int(signals.get("doctor_envelope_repaired_count"), 0)
    if repairs >= 3:
        return "repeated_format_repairs_no_leakage"
    if repairs > 0:
        return "minor_format_repairs_no_leakage"
    return "clean"


def interface_observation_summary(signals: Mapping[str, Any], level: str) -> str:
    repairs = _as_int(signals.get("doctor_envelope_repaired_count"), 0)
    fallbacks = _as_int(signals.get("doctor_envelope_invalid_fallback_count"), 0)
    rewrites = _as_int(signals.get("patient_facing_boundary_rewrite_count"), 0)
    cap_enforced = _as_int(signals.get("workspace_round_cap_enforced_count"), 0)
    raw_leaks = _as_int(signals.get("raw_envelope_patient_visible_count"), 0)
    artifact_leaks = _as_int(signals.get("workspace_artifact_patient_visible_count"), 0)
    if level == "clean":
        return "No interface routing discipline issue was observed. This is a non-weighted deployment/interface note only."
    pieces = []
    if repairs:
        pieces.append(f"{repairs} malformed envelope repair(s)")
    if fallbacks:
        pieces.append(f"{fallbacks} invalid-envelope fallback(s)")
    if cap_enforced:
        pieces.append(f"{cap_enforced} workspace loop cap enforcement event(s)")
    if rewrites:
        pieces.append(f"{rewrites} patient-facing boundary rewrite event(s)")
    if raw_leaks:
        pieces.append(f"{raw_leaks} raw envelope patient-visible leak(s)")
    if artifact_leaks:
        pieces.append(f"{artifact_leaks} workspace/internal artifact patient-visible leak(s)")
    detail = "; ".join(pieces) if pieces else "interface signal present"
    return f"Observed {detail}. This observation is non-weighted and does not alter the clinical trajectory grade."


def make_interface_discipline_observation(signals: Mapping[str, Any]) -> dict[str, Any]:
    normalized = {key: _as_int(signals.get(key), 0) for key in INTERFACE_SIGNAL_KEYS}
    level = interface_observation_level(normalized)
    return {
        "observation_name": "care_workspace_routing_discipline",
        "weighted": False,
        "grade_affecting": False,
        "observation_level": level,
        "summary": interface_observation_summary(normalized, level),
        "signals": normalized,
        "interpretation_policy": {
            "not_clinical_quality_score": True,
            "not_weighted_in_overall_grade": True,
            "does_not_change_closure_or_quality_status": True,
            "use": "deployment/interface discipline reminder only",
        },
    }


def attach_interface_observation(evaluation: dict[str, Any], observation: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(evaluation, dict):
        return evaluation
    obs = dict(observation)
    evaluation["non_weighted_interface_observations"] = obs
    existing = evaluation.get("non_weighted_observations")
    if not isinstance(existing, list):
        existing = []
    filtered = [
        item for item in existing
        if not (isinstance(item, Mapping) and item.get("observation_name") == obs.get("observation_name"))
    ]
    filtered.append(obs)
    evaluation["non_weighted_observations"] = filtered
    return evaluation


def interface_observation_from_events(
    events: Iterable[Mapping[str, Any]],
    transcript: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    return make_interface_discipline_observation(interface_discipline_signals_from_events(events, transcript))


def interface_observation_from_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    evaluation = payload.get("evaluation") if isinstance(payload.get("evaluation"), Mapping) else {}
    direct = evaluation.get("non_weighted_interface_observations") if isinstance(evaluation, Mapping) else None
    if isinstance(direct, Mapping) and isinstance(direct.get("signals"), Mapping):
        return make_interface_discipline_observation(direct.get("signals") or {})
    for item in evaluation.get("non_weighted_observations") or [] if isinstance(evaluation, Mapping) else []:
        if isinstance(item, Mapping) and item.get("observation_name") == "care_workspace_routing_discipline" and isinstance(item.get("signals"), Mapping):
            return make_interface_discipline_observation(item.get("signals") or {})
    events = payload.get("events") if isinstance(payload.get("events"), list) else []
    transcript = payload.get("transcript") if isinstance(payload.get("transcript"), list) else []
    return interface_observation_from_events(events, transcript)


def interface_observation_from_case_dir(case_dir: Path) -> dict[str, Any]:
    trajectory = _read_json(case_dir / "trajectory.json")
    checkpoint = _read_json(case_dir / "trajectory.checkpoint.json")
    payload = trajectory or checkpoint
    events = payload.get("events") if isinstance(payload.get("events"), list) else []
    transcript = payload.get("transcript") if isinstance(payload.get("transcript"), list) else []
    ledger_events = _iter_jsonl(case_dir / "ledger" / "events.jsonl")
    ledger_transcript = _iter_jsonl(case_dir / "ledger" / "transcript.jsonl")
    if ledger_events:
        events = ledger_events
    if ledger_transcript:
        transcript = ledger_transcript
    return interface_observation_from_events(events, transcript)


def sum_interface_signals(observations: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    totals: Counter[str] = Counter()
    for observation in observations or []:
        signals = observation.get("signals") if isinstance(observation, Mapping) and isinstance(observation.get("signals"), Mapping) else {}
        for key in INTERFACE_SIGNAL_KEYS:
            if key == "max_workspace_round_observed":
                totals[key] = max(totals.get(key, 0), _as_int(signals.get(key), 0))
            else:
                totals[key] += _as_int(signals.get(key), 0)
    return {key: int(totals.get(key, 0)) for key in INTERFACE_SIGNAL_KEYS}
