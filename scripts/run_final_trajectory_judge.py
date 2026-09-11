#!/usr/bin/env python3
"""Run the frozen CareLoop calibrated-v1.1 Judge experiment, fail closed.

The runner is resumable and immutable at the accepted-result level.  It never
repairs, rounds, clamps, or otherwise rewrites a model's clinical findings.
Transport/schema failures are retried with the same Judge. Once a complete
typed adjudication is returned, citation, interpretation, internal-consistency,
and reported-score discrepancies are retained as measured Judge behavior.
The canonical score is calculated deterministically from the unmodified Judge
findings by ``careloop.evaluation.protocol_v7.derive_grade``; the Judge's own
reported score is retained separately for rubric-adherence analysis.

No network request is made unless ``--execute`` is supplied.  Credentials are
read from environment variables only and are never written to logs.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
from email.utils import parsedate_to_datetime
import hashlib
import json
import os
from pathlib import Path
import ssl
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable
import urllib.error
import urllib.request
from urllib.parse import urlparse

# Support the documented deployment form ``python3 scripts/<entrypoint>.py``
# from any current working directory, without requiring an installed package or
# a caller-supplied PYTHONPATH.  The resolved script path is stable in both a
# source checkout and the self-contained deployment bundle.
_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))

from careloop.evaluation import protocol_v7 as protocol

DEFAULT_JUDGES = ["GPT-5.5", "gpt-5.6-sol", "DeepSeek-V4-Pro", "GLM-5"]
RESULT_POLICY_VERSION = "careloop_judge_observation_preserving_v1"
JSON_MODE_DEFAULT = {"GPT-5.5", "gpt-5.6-sol", "DeepSeek-V4-Pro"}
# Preserve the operational settings of the successful 2026-09-07/08 formal
# judge run. Correctness fixes below concern evidence transport and validation,
# not model sampling or scheduling semantics.
LEGACY_MAIN_TIMEOUT_SECONDS = 180
LEGACY_TOTAL_ATTEMPTS = 6  # first request plus five retries
LEGACY_DEFAULT_CONCURRENCY = 5
LEGACY_RETRY_BACKOFF_SECONDS = (20, 40, 60, 80, 100)
ALLOWED_RETURNED_MODEL_IDS = {
    "GPT-5.5": {"GPT-5.5", "gpt-5.5-2026-04-24"},
    "gpt-5.6-sol": {"gpt-5.6-sol", "gpt-5.6-sol-2026-07-09"},
    "DeepSeek-V4-Pro": {"DeepSeek-V4-Pro"},
    "GLM-5": {"GLM-5"},
}


def response_model_allowed(requested: str, returned: str) -> bool:
    return isinstance(returned, str) and returned in ALLOWED_RETURNED_MODEL_IDS.get(requested, {requested})


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def strict_json_object(text: str) -> dict[str, Any]:
    text = (text or "").strip().lstrip("\ufeff")
    # GLM-5 on the authorized gateway consistently wraps an otherwise valid
    # single JSON object in one Markdown code fence when JSON response mode is
    # omitted (as required by the legacy-compatible request profile).  Accept
    # only that exact transport wrapper; prose, multiple blocks, and trailing
    # text remain invalid and are never repaired.
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) < 3 or lines[0].strip().lower() not in {"```", "```json"} or lines[-1].strip() != "```":
            raise ValueError("response_not_single_json_object")
        text = "\n".join(lines[1:-1]).strip()
    if not text.startswith("{") or not text.endswith("}"):
        raise ValueError("response_not_single_json_object")
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("response_not_object")
    return value


def extract_content(response: dict[str, Any]) -> str:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("missing_choices")
    first = choices[0]
    message = first.get("message") if isinstance(first, dict) else None
    if not isinstance(message, dict):
        raise ValueError("missing_message")
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        if parts:
            return "".join(parts)
    raise ValueError("missing_text_content")


def endpoint(base_url: str) -> str:
    base_url = base_url.strip().rstrip("/")
    return base_url if base_url.endswith("/chat/completions") else base_url + "/chat/completions"


def api_call(
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    max_tokens: int,
    timeout: int,
    response_format: bool,
) -> tuple[dict[str, Any], str, float]:
    # The configured gateway rejects non-default temperature for some current
    # reasoning models (notably gpt-5.6-sol).  Omit the field for every judge so
    # the request is portable and the provider default is represented honestly.
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
    }
    if response_format:
        payload["response_format"] = {"type": "json_object"}
    request = urllib.request.Request(
        endpoint(base_url),
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + api_key},
    )
    started = time.time()
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=ssl.create_default_context()) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").replace(api_key, "[REDACTED]")
        retry_after = parse_retry_after(exc.headers.get("Retry-After") if exc.headers else None)
        raise ProviderHTTPError(exc.code, detail[:4000], retry_after_seconds=retry_after) from exc
    envelope = json.loads(raw)
    if not isinstance(envelope, dict):
        raise ValueError("response_envelope_not_object")
    return envelope, extract_content(envelope), time.time() - started


def parse_retry_after(value: str | None, now: datetime | None = None) -> float | None:
    """Parse an HTTP Retry-After delta or date without trusting it blindly."""
    if not value:
        return None
    value = value.strip()
    try:
        seconds = float(value)
    except ValueError:
        try:
            target = parsedate_to_datetime(value)
            if target.tzinfo is None:
                target = target.replace(tzinfo=timezone.utc)
            current = now or datetime.now(timezone.utc)
            seconds = (target - current).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return None
    # A malformed or extreme provider value must not stall the entire run.
    return min(600.0, max(0.0, seconds))


class ProviderHTTPError(RuntimeError):
    def __init__(self, status: int, detail: str, retry_after_seconds: float | None = None):
        super().__init__(f"provider_http_{status}")
        self.code = int(status)
        self.detail = str(detail)
        self.retry_after_seconds = retry_after_seconds


def retry_delay_seconds(base_delay: int, exc: Exception | None, *, model: str, packet_id: str, part: str, attempt: int) -> float:
    """Preserve the legacy backoff and stagger retryable gateway bursts."""
    provider_delay = getattr(exc, "retry_after_seconds", None)
    delay = max(float(base_delay), float(provider_delay or 0.0))
    if getattr(exc, "code", None) in {429, 503}:
        seed = f"{model}|{packet_id}|{part}|{attempt}".encode("utf-8")
        delay += (int(hashlib.sha256(seed).hexdigest()[:8], 16) % 5000) / 1000.0
    return round(delay, 3)


def safe_exception(exc: Exception) -> dict[str, Any]:
    reason = getattr(exc, "reason", None)
    result = {
        "error_type": type(exc).__name__,
        "http_status": getattr(exc, "code", None),
        "reason_type": type(reason).__name__ if reason is not None else None,
    }
    detail = getattr(exc, "detail", None)
    if isinstance(detail, str) and detail:
        result["provider_error_preview"] = detail[:4000]
    retry_after = getattr(exc, "retry_after_seconds", None)
    if isinstance(retry_after, (int, float)):
        result["retry_after_seconds"] = retry_after
    return result


def protocol_sha256() -> str:
    return file_sha256(Path(protocol.__file__).resolve())


def runner_sha256() -> str:
    return file_sha256(Path(__file__).resolve())


def _machine_validation_guidance(packet: dict[str, Any], *, stage: str) -> str:
    """Clarify the frozen validator contract without altering rubric semantics."""
    rc_ids, ho_ids = protocol.packet_contract_ids(packet)
    target_map = {
        "responsibility_chain": rc_ids,
        "high_order": ho_ids,
        "dimension": list(protocol.DIMENSIONS),
        "closure": ["closure"],
        "other": ["other"],
    }
    doctor_event_ids = [
        str(event.get("event_id") or "")
        for event in packet.get("events", [])
        if isinstance(event, dict) and (
            str(event.get("actor", "")).lower() == "doctor"
            or str(event.get("event_type", "")).startswith("doctor_")
        )
    ]
    quote_source = (
        "one string value under payload or payload_fragment in the cited event in CHUNK"
        if stage == "chunk"
        else (
            "a matching observation in CHUNK_REVIEWS; copy both event_id and quote exactly, without creating a new quote"
            if stage == "final_synthesis"
            else "one string leaf in the cited event payload in EVIDENCE_PACKET"
        )
    )
    rules = [
        "Use category/target_id only from valid_category_target_ids. Never use D1/D2 or descriptive labels as target_id.",
        f"Every citation quote must be copied character-for-character as one contiguous substring from {quote_source}.",
        "Use a short quote, preferably 6–20 characters, copied exactly. Preserve exact whitespace, newlines, punctuation, and quote marks. Never paraphrase, normalize, concatenate separate strings, or add ellipses.",
        "Prefer exactly one sufficient citation per finding; add another only when required for doctor-action evidence or distinct high-order evidence. Avoid redundant citations.",
        "Positive observations use valence=positive, provisional_severity=none, issue_kind=none.",
        "Negative observations use valence=negative, a non-none provisional_severity, and issue_kind=commission or omission.",
        "Neutral observations use issue_kind=none and provisional_severity=none or uncertain.",
        "strong_blocker is valid only for category=other,target_id=other.",
        "Return every required field, no extra fields, and exactly one JSON object.",
    ]
    if stage != "chunk":
        rules.extend([
            "For every met responsibility-chain item and every dimension citation set, include at least one citation whose event_id is in doctor_event_ids.",
            "For a partial/complete high-order item and for valid_closure/valid_open, include at least one citation whose event_id is in doctor_event_ids.",
            "High-order completion flags are exact: not_triggered=(false,not_triggered,false,false); not_completed=(true,not_completed,false,false); partial=(true,partial,true,false); complete=(true,complete,true,true), ordered as triggered,completion,active_model_action,meaningful_trajectory_impact.",
            "A complete HO must contain at least one citation pair not used by any met RC; each complete HO must also contain at least one citation pair not used by another complete HO.",
            "If a high-order item is not_triggered, citations must be [].",
            "If important_non_chain_defect_assessment.present=false, use category=none, severity=none, issue_kind=none, and citations=[].",
        ])
        if doctor_event_ids:
            rules.append("Use the listed doctor_event_ids whenever doctor-action evidence is required; patient/system events may be additional evidence but cannot be the only evidence.")
        else:
            rules.append("This packet has no doctor event. Do not mark an RC met, an HO partial/complete, any dimension good/excellent, or closure valid_closure/valid_open. Judge omissions only from observable evidence and do not infer hidden post-termination behavior. Every dimension still needs at least one exact citation to an observable presenting event; do not leave dimension citation arrays empty.")
    if stage == "final_synthesis":
        rules.append("Every final citation must copy an exact event_id/quote pair from some CHUNK_REVIEWS observation. Chunk category, target_id, valence, and provisional severity are retrieval annotations rather than final adjudication constraints; independently map the reviewed source evidence to the correct final field, but never invent or alter a citation.")
    return "MACHINE_VALIDATION_GUIDANCE=" + protocol.canonical({
        "valid_category_target_ids": target_map,
        "doctor_event_ids": doctor_event_ids,
        "rules": rules,
    })


def _rejection_repair_guidance(prior_errors: list[str] | None) -> str:
    if not prior_errors:
        return ""
    return (
        "\nREJECTION_REPAIR_GUIDANCE: Correct every listed validation failure while independently re-reviewing the evidence. "
        "nonverbatim_quote means recopy a shorter exact substring from the cited source string; "
        "target_not_valid_for_category means use the exact category-to-target map; "
        "requires_doctor_action_citation means add a source-verbatim doctor-event citation; "
        "negative_observation_missing_error_fields means use non-none severity plus commission/omission; "
        "neutral_observation_has_error_fields means use issue_kind=none plus severity none/uncertain; "
        "not_triggered_must_not_have_citations and completion_flags_inconsistent require mutually consistent HO fields; "
        "absent_non_chain_defect_must_not_have_citations requires citations=[]. "
        "Never change a clinical finding merely to pass validation."
    )


def _append_user_guidance(messages: list[dict[str, str]], guidance: str) -> list[dict[str, str]]:
    copied = [dict(message) for message in messages]
    for message in copied:
        if message.get("role") == "user":
            message["content"] = str(message.get("content") or "") + "\n" + guidance
            return copied
    raise ValueError("judge_messages_missing_user_message")


def chunk_messages(packet: dict[str, Any], chunk: dict[str, Any], prior_errors: list[str] | None = None) -> list[dict[str, str]]:
    guidance = _machine_validation_guidance(packet, stage="chunk") + _rejection_repair_guidance(prior_errors)
    return _append_user_guidance(protocol.chunk_messages(chunk, prior_errors), guidance)


def direct_final_messages(packet: dict[str, Any], prior_errors: list[str] | None = None) -> list[dict[str, str]]:
    guidance = _machine_validation_guidance(packet, stage="direct") + _rejection_repair_guidance(prior_errors)
    return _append_user_guidance(protocol.direct_final_messages(packet, prior_errors), guidance)


def final_messages(packet: dict[str, Any], reviews: list[dict[str, Any]], prior_errors: list[str] | None = None) -> list[dict[str, str]]:
    guidance = _machine_validation_guidance(packet, stage="final_synthesis") + _rejection_repair_guidance(prior_errors)
    return _append_user_guidance(protocol.final_messages(packet, reviews, prior_errors), guidance)


def reviewed_citation_transport_map(
    reviews: list[dict[str, Any]], packet: dict[str, Any]
) -> dict[tuple[str, str], dict[tuple[str, str], set[tuple[str, str, str]]]]:
    """Allow final adjudication to remap evidence retrieved by chunk reviewers.

    Chunk labels and severities are explicitly provisional. The final judge may
    therefore reuse any exact event/quote pair that was actually surfaced by
    any accepted chunk review, while the frozen final schema and derive_grade
    gates independently enforce clinical consistency. Unreviewed or altered
    citations remain forbidden.
    """
    observed_pairs: set[tuple[str, str]] = set()
    for review in reviews:
        for observation in review.get("observations", []) if isinstance(review, dict) else []:
            if not isinstance(observation, dict):
                continue
            for citation in observation.get("citations", []) if isinstance(observation.get("citations"), list) else []:
                if isinstance(citation, dict):
                    observed_pairs.add((
                        str(citation.get("event_id") or ""),
                        str(citation.get("quote") or ""),
                    ))
    all_semantics = {
        (valence, severity, issue_kind)
        for valence in ("positive", "negative", "neutral")
        for severity in ("none", "strong_blocker", "minor_or_moderate", "serious", "uncertain")
        for issue_kind in ("none", "commission", "omission")
    }
    rc_ids, ho_ids = protocol.packet_contract_ids(packet)
    targets = (
        [("responsibility_chain", value) for value in rc_ids]
        + [("high_order", value) for value in ho_ids]
        + [("dimension", value) for value in protocol.DIMENSIONS]
        + [("closure", "closure"), ("other", "other")]
    )
    return {
        target: {pair: set(all_semantics) for pair in observed_pairs}
        for target in targets
    }


def validate_final_result(
    result: Any,
    packet: dict[str, Any],
    *,
    allowed_citation_map: dict[tuple[str, str], dict[tuple[str, str], set[tuple[str, str, str]]]] | None = None,
) -> list[str]:
    """Apply the frozen validator, handling the sole no-doctor runtime edge case.

    The frozen protocol requires a doctor-action citation for every dimension.
    One authentic runtime-error packet contains no doctor event at all. For that
    packet only, the absence of doctor output is observable and dimension
    omissions can be cited to the presenting event; all other validation and
    deterministic grade rules remain unchanged.
    """
    errors = protocol.validate_final_result(
        result, packet, allowed_citation_map=allowed_citation_map
    )
    has_doctor_event = any(
        isinstance(event, dict) and (
            str(event.get("actor", "")).lower() == "doctor"
            or str(event.get("event_type", "")).startswith("doctor_")
        )
        for event in packet.get("events", [])
    )
    if has_doctor_event:
        return errors
    allowed = {
        f"dimension:{dimension}:requires_doctor_action_citation"
        for dimension in protocol.DIMENSIONS
    }
    return [error for error in errors if error not in allowed]


_NONBLOCKING_FINAL_QUALITY_ERRORS = {
    "valid_status_requires_safe_executable_traceable",
    "invalid_status_cannot_be_fully_valid",
    "absent_non_chain_defect_requires_none_fields",
    "absent_non_chain_defect_must_not_have_citations",
    "present_non_chain_defect_requires_specific_fields",
    "unsafe_dimension_requires_serious_error",
    "weak_dimension_requires_error_or_defect",
    "missing_or_weak_brief_rationale",
    "proposed_label_mismatch",
}
_NONBLOCKING_FINAL_QUALITY_FRAGMENTS = (
    ":citation:missing",
    ":unknown_event_id",
    ":event_outside_chunk",
    ":quote_too_short",
    ":duplicate",
    ":nonverbatim_quote",
    ":missing_or_weak_explanation",
    ":requires_doctor_action_citation",
    ":not_in_targeted_chunk_evidence",
    ":missing_semantically_matching_chunk_evidence",
    ":met_requires_no_issue_kind",
    ":error_requires_issue_kind",
    ":completion_flags_inconsistent",
    ":high_order_evidence_not_distinct_from_minimum_rc",
    ":no_distinct_evidence_from_other_completed_high_order",
    ":not_triggered_must_not_have_citations",
)


def is_nonblocking_final_quality_error(error: str) -> bool:
    """Whether an error measures Judge quality rather than transport validity.

    The four Judges are experimental subjects.  Once a model returned a
    complete typed adjudication, evidence/interpretation inconsistency must be
    retained and measured rather than repaired by another model or selected
    away through retries.
    """
    return (
        error in _NONBLOCKING_FINAL_QUALITY_ERRORS
        or error.startswith("proposed_grade_inconsistent:expected_")
        or error.startswith("closure_missing_or_weak_explanation")
        or error.startswith("important_non_chain_defect_missing_or_weak_explanation")
        or any(fragment in error for fragment in _NONBLOCKING_FINAL_QUALITY_FRAGMENTS)
    )


def final_result_quality_errors(
    result: Any,
    packet: dict[str, Any],
    *,
    allowed_citation_map: dict[tuple[str, str], dict[tuple[str, str], set[tuple[str, str, str]]]] | None = None,
) -> list[str]:
    return validate_final_result(result, packet, allowed_citation_map=allowed_citation_map)


_NONBLOCKING_CHUNK_QUALITY_ERRORS = {
    "missing_or_weak_review_summary",
}
_NONBLOCKING_CHUNK_QUALITY_FRAGMENTS = (
    ":target_not_valid_for_category",
    ":positive_observation_has_error_fields",
    ":negative_observation_missing_error_fields",
    ":neutral_observation_has_error_fields",
    ":strong_blocker_only_valid_for_other",
    ":citation:missing",
    ":unknown_event_id",
    ":event_outside_chunk",
    ":quote_too_short",
    ":duplicate",
    ":nonverbatim_quote",
    ":missing_or_weak_explanation",
)


def is_nonblocking_chunk_quality_error(error: str) -> bool:
    """Treat parseable chunk-review judgments as observations, not targets.

    Chunk reviews are same-Judge evidence retrieval passes.  Citation fidelity,
    semantic consistency, and category/target mistakes measure Judge quality;
    retrying them would select away observed model behavior and can cause long
    retry loops.  Invalid JSON/schema, missing typed fields, and invalid enums
    remain blocking because synthesis cannot consume them reliably.
    """
    return (
        error in _NONBLOCKING_CHUNK_QUALITY_ERRORS
        or any(fragment in error for fragment in _NONBLOCKING_CHUNK_QUALITY_FRAGMENTS)
    )


def chunk_review_quality_errors(
    review: Any, chunk: dict[str, Any], packet: dict[str, Any]
) -> list[str]:
    return protocol.validate_chunk_review(review, chunk, packet)


def chunk_review_acceptance_errors(
    review: Any, chunk: dict[str, Any], packet: dict[str, Any]
) -> list[str]:
    """Return only transport/schema errors that make a chunk unusable."""
    return [
        error
        for error in chunk_review_quality_errors(review, chunk, packet)
        if not is_nonblocking_chunk_quality_error(error)
    ]


def final_result_acceptance_errors(
    result: Any,
    packet: dict[str, Any],
    *,
    allowed_citation_map: dict[tuple[str, str], dict[tuple[str, str], set[tuple[str, str, str]]]] | None = None,
) -> list[str]:
    """Return only failures proving the output is unusable as a complete trial.

    Invalid JSON, missing/extra schema, wrong types, invalid enums, or missing
    case IDs remain blocking.  Substantive Judge-quality discrepancies are
    recorded separately and never rewritten.
    """
    return [
        error
        for error in final_result_quality_errors(
            result, packet, allowed_citation_map=allowed_citation_map
        )
        if not is_nonblocking_final_quality_error(error)
    ]


def _targeted_repair_instructions(
    previous_result: dict[str, Any], previous_errors: list[str]
) -> list[str]:
    instructions: list[str] = []
    expected_ho_flags = {
        "not_triggered": (False, False, False),
        "not_completed": (True, False, False),
        "partial": (True, True, False),
        "complete": (True, True, True),
    }
    for error in previous_errors:
        grade_match = __import__("re").search(r"proposed_grade_inconsistent:expected_([1-5])$", error)
        if grade_match:
            grade = int(grade_match.group(1))
            instructions.append(
                f"Set proposed_trajectory_grade={grade} and proposed_trajectory_grade_label={protocol.LABELS[grade]!r}; preserve the findings that deterministically imply it."
            )
        ho_match = __import__("re").match(r"ho\[(\d+)\]:completion_flags_inconsistent$", error)
        if ho_match:
            index = int(ho_match.group(1))
            items = previous_result.get("high_order_assessment")
            if isinstance(items, list) and index < len(items) and isinstance(items[index], dict):
                item = items[index]
                completion = item.get("completion")
                expected = expected_ho_flags.get(str(completion))
                if expected is not None:
                    instructions.append(
                        f"For high_order_assessment[{index}] ({item.get('ho_id')}), completion={completion!r} requires exactly triggered={str(expected[0]).lower()}, active_model_action={str(expected[1]).lower()}, meaningful_trajectory_impact={str(expected[2]).lower()}. Change only inconsistent completion fields, choosing another allowed tuple only if the existing explanation clearly requires it."
                    )
        if error.endswith(":nonverbatim_quote"):
            instructions.append(
                f"At {error[:-len(':nonverbatim_quote')]}, keep the cited event and finding but replace quote with a short 6–20 character exact contiguous substring copied from that event's visible source text."
            )
        elif error.endswith(":quote_too_short"):
            instructions.append(
                f"At {error[:-len(':quote_too_short')]}, copy a longer exact contiguous source substring of at least 6 characters from the same event."
            )
        elif ":requires_doctor_action_citation" in error:
            instructions.append(
                f"At {error.split(':requires_doctor_action_citation')[0]}, add at least one exact citation whose event_id is in doctor_event_ids; do not remove relevant patient receipt evidence."
            )
        elif ":not_in_targeted_chunk_evidence" in error or error.endswith(":missing_semantically_matching_chunk_evidence"):
            instructions.append(
                f"At {error.split(':not_in_targeted_chunk_evidence')[0].split(':missing_semantically_matching_chunk_evidence')[0]}, replace unsupported citations only with exact event_id/quote pairs visibly present in CHUNK_REVIEWS."
            )
        elif error.endswith(":high_order_evidence_not_distinct_from_minimum_rc") or "no_distinct_evidence_from_other_completed_high_order" in error:
            instructions.append(
                f"For {error.split(':')[0]}, either add a genuinely distinct exact citation pair supporting that HO, or downgrade the HO completion fields consistently if distinct completion evidence is absent; do not manufacture evidence."
            )
        elif error.endswith(":citation:missing") or error.endswith(":missing"):
            instructions.append(
                f"Supply the required evidence at {error.rsplit(':', 1)[0]} using an exact eligible citation; use an empty list only where the schema explicitly permits it."
            )
        elif error.endswith(":wrong_fields") or error.endswith(":boolean_missing"):
            instructions.append(
                f"Repair the exact schema fields at {error.rsplit(':', 1)[0]} according to OUTPUT_SCHEMA without altering unrelated clinical findings."
            )
        elif error.endswith(":unknown_event_id"):
            instructions.append(
                f"Replace the event_id at {error[:-len(':unknown_event_id')]} with an exact event_id present in the supplied evidence and recopy its quote."
            )
    return list(dict.fromkeys(instructions))


def _append_previous_rejected_output(
    messages: list[dict[str, str]],
    previous_result: dict[str, Any] | None,
    previous_errors: list[str] | None,
) -> list[dict[str, str]]:
    if previous_result is None:
        return messages
    errors = list(previous_errors or [])
    guidance = (
        "PREVIOUS_REJECTED_JSON=" + protocol.canonical(previous_result)
        + "\nPRESERVATION_RULE: This is your own preceding structured adjudication. Preserve its clinical findings, statuses, ratings, completion decisions, explanations, and proposed grade unless a listed deterministic validation failure specifically requires a corresponding field correction. For citation errors, keep the finding and repair only event_id/quote/explanation using exact source text. For proposed_grade_inconsistent, keep all clinical findings and change only proposed_trajectory_grade and its label to the deterministic grade implied by those findings. Return the entire corrected JSON object, not a patch."
        + "\nTARGETED_FIELD_REPAIRS=" + protocol.canonical(_targeted_repair_instructions(previous_result, errors))
        + "\nPREVIOUS_STRUCTURED_VALIDATION_ERRORS=" + protocol.canonical(errors)
    )
    return _append_user_guidance(messages, guidance)


def request_meta(
    model: str,
    messages: list[dict[str, str]],
    kind: str,
    packet_id: str,
    part: str,
    response_format: bool,
    max_tokens: int,
    prior_validation_errors: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "protocol_version": protocol.VERSION,
        "protocol_sha256": protocol_sha256(),
        "runner_sha256": runner_sha256(),
        "kind": kind,
        "packet_id": packet_id,
        "part": part,
        "judge_model": model,
        "allowed_response_model_ids": sorted(ALLOWED_RETURNED_MODEL_IDS.get(model, {model})),
        "message_sha256": protocol.digest(messages),
        "message_chars": protocol.messages_char_count(messages),
        "max_tokens": max_tokens,
        "temperature_field_omitted": True,
        "response_format_json_object": response_format,
        "prior_validation_errors": list(prior_validation_errors or []),
        "api_key_logged": False,
        "base_url_logged": False,
    }


def next_attempt_index(folder: Path) -> int:
    attempts_dir = folder / "attempts"
    values = []
    if attempts_dir.is_dir():
        for path in attempts_dir.glob("attempt_*"):
            try:
                values.append(int(path.name.split("_", 1)[1]))
            except (IndexError, ValueError):
                continue
    return max(values, default=0) + 1


def save_attempt(
    folder: Path,
    attempt: int,
    meta: dict[str, Any],
    raw_envelope: dict[str, Any] | None,
    content: str | None,
    errors: list[str],
    exception: Exception | None,
) -> dict[str, Any]:
    attempt_dir = folder / "attempts" / f"attempt_{attempt:04d}"
    attempt_dir.mkdir(parents=True, exist_ok=False)
    atomic_json(attempt_dir / "request_meta.json", meta)
    if raw_envelope is not None:
        atomic_json(attempt_dir / "response_envelope.json", raw_envelope)
    if content is not None:
        (attempt_dir / "response_content.txt").write_text(content, encoding="utf-8")
    atomic_json(
        attempt_dir / "validation.json",
        {
            "accepted": not errors and exception is None,
            "errors": errors,
            "exception": safe_exception(exception) if exception else None,
        },
    )
    artifacts = {
        "attempt": attempt,
        "request_meta_sha256": file_sha256(attempt_dir / "request_meta.json"),
        "validation_sha256": file_sha256(attempt_dir / "validation.json"),
    }
    if raw_envelope is not None:
        artifacts["response_envelope_sha256"] = file_sha256(attempt_dir / "response_envelope.json")
    if content is not None:
        artifacts["response_content_sha256"] = file_sha256(attempt_dir / "response_content.txt")
    return artifacts


def invoke_validated(
    *,
    base_url: str,
    api_key: str,
    model: str,
    messages_builder: Callable[[list[str] | None], list[dict[str, str]]],
    validator: Callable[[dict[str, Any]], list[str]],
    folder: Path,
    kind: str,
    packet_id: str,
    part: str,
    attempts: int,
    max_tokens: int,
    timeout: int,
    response_format: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    prior_errors: list[str] = []
    previous_result: dict[str, Any] | None = None
    previous_result_errors: list[str] = []
    previous_result_attempt: int | None = None
    first_attempt = next_attempt_index(folder)
    for offset in range(attempts):
        attempt = first_attempt + offset
        messages = messages_builder(prior_errors or None)
        messages = _append_previous_rejected_output(messages, previous_result, previous_result_errors)
        meta = request_meta(
            model, messages, kind, packet_id, part, response_format, max_tokens, prior_errors
        ) | {
            "attempt": attempt,
            "attempts_in_this_invocation": attempts,
            "repair_from_previous_structured_output": previous_result is not None,
            "previous_rejected_attempt": previous_result_attempt,
            "previous_rejected_result_sha256": (protocol.digest(previous_result) if previous_result is not None else None),
            "previous_rejected_validation_errors": list(previous_result_errors),
        }
        envelope = None
        content = None
        exception = None
        errors: list[str] = []
        result = None
        try:
            envelope, content, elapsed = api_call(
                base_url, api_key, model, messages, max_tokens, timeout, response_format
            )
            meta["elapsed_seconds"] = round(elapsed, 3)
            returned_model = envelope.get("model")
            meta["response_model_returned"] = returned_model
            if not response_model_allowed(model, returned_model):
                raise ValueError("response_model_identity_mismatch")
            result = strict_json_object(content)
            errors = validator(result)
        except Exception as exc:  # pragma: no cover - external provider behavior
            exception = exc
            errors = ["exception:" + type(exc).__name__ + ":" + str(exc)[:160]]
        retry_delay = None
        if errors and offset + 1 < attempts:
            retry_delay = retry_delay_seconds(
                LEGACY_RETRY_BACKOFF_SECONDS[min(offset, len(LEGACY_RETRY_BACKOFF_SECONDS) - 1)],
                exception,
                model=model,
                packet_id=packet_id,
                part=part,
                attempt=attempt,
            )
            meta["scheduled_retry_delay_seconds"] = retry_delay
        artifacts = save_attempt(folder, attempt, meta, envelope, content, errors, exception)
        if not errors and result is not None:
            return result, {
                "attempt": attempt,
                "request_meta": meta,
                "artifacts": artifacts,
                "response_model_returned": meta["response_model_returned"],
            }
        if result is not None and errors:
            previous_result = json.loads(protocol.canonical(result))
            previous_result_errors = list(errors)
            previous_result_attempt = attempt
            prior_errors = list(errors)
        elif previous_result is not None:
            # Preserve the last structured rejection across transient gateway
            # failures so the next successful call can continue a targeted
            # same-judge repair instead of starting over.
            prior_errors = list(previous_result_errors) + list(errors)
        else:
            prior_errors = list(errors)
        if retry_delay is not None:
            time.sleep(retry_delay)
    raise RuntimeError(kind + "_validation_failed_after_attempts")

def resolve_under_root(root: Path, value: str) -> Path:
    if not value:
        raise ValueError("empty_manifest_path")
    root = root.resolve()
    candidate = Path(value)
    resolved = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("manifest_path_escapes_root") from exc
    return resolved


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(ch in "0123456789abcdef" for ch in value)


def load_manifest(path: Path, root: Path) -> list[dict[str, Any]]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("protocol_version") != protocol.VERSION:
        raise ValueError("manifest_protocol_version_mismatch")
    if manifest.get("protocol_sha256") != protocol_sha256():
        raise ValueError("manifest_protocol_hash_mismatch")
    rows = manifest.get("packets")
    if not isinstance(rows, list) or not rows:
        raise ValueError("manifest_packets_missing_or_empty")
    if manifest.get("packet_count") != len(rows):
        raise ValueError("manifest_packet_count_mismatch")
    seen_packet_ids: set[str] = set()
    seen_source_hashes: set[str] = set()
    seen_model_case_pairs: set[tuple[str, str]] = set()
    normalized: list[dict[str, Any]] = []
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise ValueError(f"manifest_row_{index}_not_object")
        packet_id = str(row.get("packet_id") or "")
        if not packet_id or packet_id in seen_packet_ids:
            raise ValueError(f"manifest_row_{index}_missing_or_duplicate_packet_id")
        seen_packet_ids.add(packet_id)
        packet_path = resolve_under_root(root, str(row.get("packet_file") or ""))
        if not packet_path.is_file():
            raise ValueError(f"manifest_packet_missing:{packet_id}")
        actual_sha = file_sha256(packet_path)
        if row.get("packet_sha256") != actual_sha:
            raise ValueError(f"manifest_packet_hash_mismatch:{packet_id}")
        packet = json.loads(packet_path.read_text(encoding="utf-8"))
        errors = protocol.validate_packet(packet)
        if errors:
            raise ValueError(f"manifest_packet_invalid:{packet_id}:" + ",".join(errors))
        if row.get("case_id") != packet.get("case_id"):
            raise ValueError(f"manifest_case_id_mismatch:{packet_id}")
        source_hash = row.get("source_trajectory_sha256")
        if not _is_sha256(source_hash):
            raise ValueError(f"manifest_source_trajectory_hash_invalid:{packet_id}")
        if source_hash != packet["operational_metadata"].get("source_trajectory_sha256"):
            raise ValueError(f"manifest_source_trajectory_hash_mismatch:{packet_id}")
        if source_hash in seen_source_hashes:
            raise ValueError(f"manifest_duplicate_source_trajectory:{packet_id}")
        seen_source_hashes.add(source_hash)
        source_path = resolve_under_root(root, str(row.get("source_trajectory_file") or ""))
        if not source_path.is_file():
            raise ValueError(f"manifest_source_trajectory_missing:{packet_id}")
        if file_sha256(source_path) != source_hash:
            raise ValueError(f"manifest_source_trajectory_file_hash_mismatch:{packet_id}")
        case_hash = row.get("case_sha256")
        if not _is_sha256(case_hash):
            raise ValueError(f"manifest_case_hash_invalid:{packet_id}")
        case_path = resolve_under_root(root, str(row.get("case_file") or ""))
        if not case_path.is_file():
            raise ValueError(f"manifest_case_missing:{packet_id}")
        if file_sha256(case_path) != case_hash:
            raise ValueError(f"manifest_case_file_hash_mismatch:{packet_id}")
        contract_hash = row.get("contract_payload_sha256")
        packet_contract_hash = packet.get("case_contract", {}).get("provenance", {}).get("normalized_contract_payload_sha256")
        if not _is_sha256(contract_hash) or contract_hash != packet_contract_hash:
            raise ValueError(f"manifest_contract_payload_hash_mismatch:{packet_id}")
        doctor_model = str(row.get("doctor_model") or "").strip()
        case_id = str(row.get("case_id") or "").strip()
        if not doctor_model:
            raise ValueError(f"manifest_doctor_model_missing:{packet_id}")
        model_case = (doctor_model, case_id)
        if model_case in seen_model_case_pairs:
            raise ValueError(f"manifest_duplicate_model_case_pair:{doctor_model}:{case_id}")
        seen_model_case_pairs.add(model_case)
        policy = packet["evidence_policy"]
        if row.get("source_event_count") != policy.get("source_event_count"):
            raise ValueError(f"manifest_source_event_count_mismatch:{packet_id}")
        if row.get("retained_event_count") != policy.get("retained_event_count"):
            raise ValueError(f"manifest_retained_event_count_mismatch:{packet_id}")
        if row.get("source_event_field") != policy.get("source_event_field"):
            raise ValueError(f"manifest_source_event_field_mismatch:{packet_id}")
        if row.get("source_raw_event_count") != policy.get("source_raw_event_count"):
            raise ValueError(f"manifest_source_raw_event_count_mismatch:{packet_id}")
        if row.get("excluded_nonobservable_event_count") != policy.get("excluded_nonobservable_event_count"):
            raise ValueError(f"manifest_excluded_event_count_mismatch:{packet_id}")
        if row.get("terminal_status") != packet["operational_metadata"].get("terminal_status"):
            raise ValueError(f"manifest_terminal_status_mismatch:{packet_id}")
        normalized.append(dict(row) | {"_packet_path": str(packet_path)})
    population = manifest.get("population")
    if population is not None:
        if not isinstance(population, dict):
            raise ValueError("manifest_population_not_object")
        models: dict[str, set[str]] = {}
        statuses: dict[str, int] = {}
        for row in normalized:
            models.setdefault(str(row["doctor_model"]), set()).add(str(row["case_id"]))
            status = str(row["terminal_status"])
            statuses[status] = statuses.get(status, 0) + 1
        expected_population = {
            "trajectory_count": len(normalized),
            "tested_model_count": len(models),
            "tested_models": sorted(models),
            "cases_per_model": {model: len(cases) for model, cases in sorted(models.items())},
            "terminal_status_counts": dict(sorted(statuses.items())),
        }
        if population != expected_population:
            raise ValueError("manifest_population_summary_mismatch")
    return normalized


def _validated_attempt_artifacts(
    stage_folder: Path,
    run_meta: dict[str, Any],
    expected_model: str,
    expected_result: dict[str, Any],
    *,
    expected_kind: str,
    expected_packet_id: str,
    expected_part: str,
    messages_builder: Callable[[list[str] | None], list[dict[str, str]]],
) -> bool:
    """Validate an accepted attempt and reconstruct its exact protocol prompt.

    This deliberately rejects pre-v5 artifacts that lack prompt-lineage fields.
    A canonical result is resumable only when the response envelope, response
    content, validation decision, request metadata, protocol hash, and exact
    deterministic messages all remain mutually consistent.
    """
    try:
        if not isinstance(run_meta, dict):
            return False
        attempt = int(run_meta["attempt"])
        if attempt < 1 or run_meta.get("attempt") != attempt:
            return False
        artifacts = run_meta["artifacts"]
        if not isinstance(artifacts, dict) or artifacts.get("attempt") != attempt:
            return False
        attempt_dir = stage_folder / "attempts" / f"attempt_{attempt:04d}"
        required = {
            "request_meta.json": "request_meta_sha256",
            "validation.json": "validation_sha256",
            "response_envelope.json": "response_envelope_sha256",
            "response_content.txt": "response_content_sha256",
        }
        if set(artifacts) != {"attempt", *required.values()}:
            return False
        for filename, hash_key in required.items():
            artifact_path = attempt_dir / filename
            if not artifact_path.is_file() or artifacts.get(hash_key) != file_sha256(artifact_path):
                return False
        request = json.loads((attempt_dir / "request_meta.json").read_text(encoding="utf-8"))
        validation = json.loads((attempt_dir / "validation.json").read_text(encoding="utf-8"))
        envelope = json.loads((attempt_dir / "response_envelope.json").read_text(encoding="utf-8"))
        content = (attempt_dir / "response_content.txt").read_text(encoding="utf-8")
        if run_meta.get("request_meta") != request or run_meta.get("artifacts") != artifacts:
            return False
        if request.get("protocol_version") != protocol.VERSION or request.get("protocol_sha256") != protocol_sha256():
            return False
        if request.get("runner_sha256") != runner_sha256():
            return False
        if request.get("kind") != expected_kind or request.get("packet_id") != expected_packet_id or request.get("part") != expected_part:
            return False
        if request.get("judge_model") != expected_model or not response_model_allowed(expected_model, request.get("response_model_returned")):
            return False
        if request.get("allowed_response_model_ids") != sorted(ALLOWED_RETURNED_MODEL_IDS.get(expected_model, {expected_model})):
            return False
        if request.get("attempt") != attempt or type(request.get("attempts_in_this_invocation")) is not int or request["attempts_in_this_invocation"] < 1:
            return False
        if request.get("temperature_field_omitted") is not True or "temperature" in request:
            return False
        if type(request.get("max_tokens")) is not int or request["max_tokens"] < 1:
            return False
        if type(request.get("response_format_json_object")) is not bool:
            return False
        if request.get("api_key_logged") is not False or request.get("base_url_logged") is not False:
            return False
        prior_errors = request.get("prior_validation_errors")
        if not isinstance(prior_errors, list) or not all(isinstance(item, str) for item in prior_errors):
            return False
        expected_messages = messages_builder(prior_errors or None)
        repair_from_previous = request.get("repair_from_previous_structured_output", False)
        previous_attempt = request.get("previous_rejected_attempt")
        previous_sha = request.get("previous_rejected_result_sha256")
        previous_errors = request.get("previous_rejected_validation_errors", [])
        if repair_from_previous is True:
            if type(previous_attempt) is not int or previous_attempt < 1 or previous_attempt >= attempt:
                return False
            if not _is_sha256(previous_sha) or not isinstance(previous_errors, list) or not all(isinstance(item, str) for item in previous_errors):
                return False
            previous_dir = stage_folder / "attempts" / f"attempt_{previous_attempt:04d}"
            previous_content_path = previous_dir / "response_content.txt"
            previous_validation_path = previous_dir / "validation.json"
            if not previous_content_path.is_file() or not previous_validation_path.is_file():
                return False
            previous_result = strict_json_object(previous_content_path.read_text(encoding="utf-8"))
            if protocol.digest(previous_result) != previous_sha:
                return False
            previous_validation = json.loads(previous_validation_path.read_text(encoding="utf-8"))
            if previous_validation.get("accepted") is not False or previous_validation.get("errors") != previous_errors:
                return False
            expected_messages = _append_previous_rejected_output(expected_messages, previous_result, previous_errors)
        elif repair_from_previous is not False or previous_attempt is not None or previous_sha is not None or previous_errors not in ([], None):
            return False
        if request.get("message_sha256") != protocol.digest(expected_messages):
            return False
        if request.get("message_chars") != protocol.messages_char_count(expected_messages):
            return False
        if set(validation) != {"accepted", "errors", "exception"}:
            return False
        if validation.get("accepted") is not True or validation.get("errors") != [] or validation.get("exception") is not None:
            return False
        if envelope.get("model") != request.get("response_model_returned") or not response_model_allowed(expected_model, envelope.get("model")) or extract_content(envelope) != content:
            return False
        if strict_json_object(content) != expected_result:
            return False
        if run_meta.get("response_model_returned") != request.get("response_model_returned") or not response_model_allowed(expected_model, run_meta.get("response_model_returned")):
            return False
        return True
    except Exception:
        return False


def _load_validated_chunk_reviews(
    folder: Path,
    packet: dict[str, Any],
    judge_model: str,
    packet_id: str,
    chunk_body_chars: int,
    expected_manifest: list[dict[str, Any]],
) -> list[dict[str, Any]] | None:
    try:
        chunks = protocol.make_event_chunks(packet, chunk_body_chars)
        if len(chunks) != len(expected_manifest):
            return None
        reviews = []
        for index, (chunk, manifest_row) in enumerate(zip(chunks, expected_manifest), 1):
            expected_rel = f"chunks/chunk_{index:03d}/validated_review.json"
            if set(manifest_row) != {"chunk_index", "path", "sha256"}:
                return None
            if manifest_row.get("chunk_index") != index or manifest_row.get("path") != expected_rel:
                return None
            review_path = folder / expected_rel
            if not review_path.is_file() or file_sha256(review_path) != manifest_row.get("sha256"):
                return None
            wrapper = json.loads(review_path.read_text(encoding="utf-8"))
            if set(wrapper) != {"protocol_version", "protocol_sha256", "chunk_sha256", "review_sha256", "review", "run_meta"}:
                return None
            review = wrapper.get("review")
            if wrapper.get("protocol_version") != protocol.VERSION:
                return None
            if wrapper.get("protocol_sha256") != protocol_sha256():
                return None
            if wrapper.get("chunk_sha256") != protocol.digest(chunk):
                return None
            if wrapper.get("review_sha256") != protocol.digest(review):
                return None
            if chunk_review_acceptance_errors(review, chunk, packet):
                return None
            if not _validated_attempt_artifacts(
                review_path.parent,
                wrapper.get("run_meta") or {},
                judge_model,
                review,
                expected_kind="chunk_review",
                expected_packet_id=packet_id,
                expected_part=f"chunk_{index:03d}",
                messages_builder=lambda errors, c=chunk: chunk_messages(packet, c, errors),
            ):
                return None
            reviews.append(review)
        return reviews
    except Exception:
        return None


def canonical_is_valid(
    path: Path,
    packet: dict[str, Any],
    judge_model: str,
    manifest_row: dict[str, Any],
) -> bool:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        expected_fields = set(protocol.FINAL_SCHEMA) | {
            "trajectory_grade", "trajectory_grade_label", "derived_gate_flags",
            "judge_reported_grade", "judge_reported_grade_label",
            "judge_reported_grade_matches_derived", "judge_output_quality_errors",
            "judge_output_quality_error_count", "canonical_grade_source",
            "result_policy_version",
            "protocol_version", "packet_sha256", "judge_model", "judge_model_returned",
            "packet_id", "doctor_model_blinded_from_judge", "mode", "chunk_count",
            "chunk_body_chars", "chunk_review_manifest", "source_trajectory_sha256",
            "source_case_sha256", "packet_file_sha256", "protocol_sha256",
            "runner_sha256", "raw_result_sha256", "accepted_at_unix", "final_attempt",
        }
        if set(record) != expected_fields:
            return False
        raw_result = {key: record[key] for key in protocol.FINAL_SCHEMA}
        folder = path.parent
    except Exception:
        return False
    if record.get("protocol_version") != protocol.VERSION or record.get("protocol_sha256") != protocol_sha256():
        return False
    if record.get("result_policy_version") != RESULT_POLICY_VERSION:
        return False
    if record.get("runner_sha256") != runner_sha256():
        return False
    if record.get("packet_sha256") != protocol.digest(packet):
        return False
    if record.get("packet_id") != manifest_row.get("packet_id"):
        return False
    if record.get("packet_file_sha256") != manifest_row.get("packet_sha256"):
        return False
    if record.get("doctor_model_blinded_from_judge") is not True:
        return False
    if record.get("judge_model") != judge_model or not response_model_allowed(judge_model, record.get("judge_model_returned")):
        return False
    if record.get("raw_result_sha256") != protocol.digest(raw_result):
        return False
    if type(record.get("accepted_at_unix")) not in {int, float} or record["accepted_at_unix"] <= 0:
        return False
    if record.get("source_trajectory_sha256") != manifest_row.get("source_trajectory_sha256"):
        return False
    if record.get("source_case_sha256") != manifest_row.get("case_sha256"):
        return False
    mode = record.get("mode")
    allowed_map = None
    if mode == "direct_complete_evidence":
        if record.get("chunk_count") != 0 or record.get("chunk_review_manifest") != []:
            return False
        stage_folder = folder / "direct"
        messages_builder = lambda errors: direct_final_messages(packet, errors)
        expected_kind, expected_part = "direct_final", "direct"
    elif mode == "chunked_complete_evidence":
        chunk_body_chars = record.get("chunk_body_chars")
        manifest = record.get("chunk_review_manifest")
        if type(chunk_body_chars) is not int or chunk_body_chars < 1000 or not isinstance(manifest, list):
            return False
        reviews = _load_validated_chunk_reviews(folder, packet, judge_model, str(record["packet_id"]), chunk_body_chars, manifest)
        if reviews is None or record.get("chunk_count") != len(reviews):
            return False
        allowed_map = reviewed_citation_transport_map(reviews, packet)
        stage_folder = folder / "final"
        messages_builder = lambda errors: final_messages(packet, reviews, errors)
        expected_kind, expected_part = "final_synthesis", "final"
    else:
        return False
    if final_result_acceptance_errors(raw_result, packet, allowed_citation_map=allowed_map):
        return False
    if not _validated_attempt_artifacts(
        stage_folder,
        record.get("final_attempt") or {},
        judge_model,
        raw_result,
        expected_kind=expected_kind,
        expected_packet_id=str(record["packet_id"]),
        expected_part=expected_part,
        messages_builder=messages_builder,
    ):
        return False
    grade, _ = protocol.derive_grade(raw_result)
    quality_errors = final_result_quality_errors(raw_result, packet, allowed_citation_map=allowed_map)
    return (
        record.get("trajectory_grade") == grade
        and record.get("trajectory_grade_label") == protocol.LABELS.get(grade)
        and record.get("judge_reported_grade") == raw_result.get("proposed_trajectory_grade")
        and record.get("judge_reported_grade_label") == raw_result.get("proposed_trajectory_grade_label")
        and record.get("judge_reported_grade_matches_derived") is (
            raw_result.get("proposed_trajectory_grade") == grade
            and raw_result.get("proposed_trajectory_grade_label") == protocol.LABELS.get(grade)
        )
        and record.get("judge_output_quality_errors") == quality_errors
        and record.get("judge_output_quality_error_count") == len(quality_errors)
        and record.get("canonical_grade_source") == "deterministic_frozen_rubric_from_unmodified_judge_findings"
    )

def process_one(
    args: argparse.Namespace,
    base_url: str,
    api_key: str,
    manifest_row: dict[str, Any],
    judge_model: str,
) -> dict[str, Any]:
    packet_id = str(manifest_row["packet_id"])
    packet_path = Path(manifest_row["_packet_path"])
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    folder = args.out_root / "results" / judge_model / packet_id
    canonical_path = folder / "canonical.json"
    if canonical_path.exists():
        if canonical_is_valid(canonical_path, packet, judge_model, manifest_row):
            return {"packet_id": packet_id, "judge_model": judge_model, "state": "skipped_valid_existing"}
        raise FileExistsError("invalid_existing_canonical_requires_fresh_output_root")

    packet_errors = protocol.packet_scorability_errors(packet)
    if packet_errors:
        atomic_json(
            folder / "unscorable.json",
            {
                "packet_id": packet_id,
                "judge_model": judge_model,
                "protocol_version": protocol.VERSION,
                "packet_sha256": protocol.digest(packet),
                "errors": packet_errors,
            },
        )
        return {"packet_id": packet_id, "judge_model": judge_model, "state": "unscorable", "errors": packet_errors}

    response_format = judge_model in args.json_mode_models
    direct_messages = direct_final_messages(packet)
    final_allowed_map = None
    if getattr(args, "full_context_only", False) or protocol.messages_char_count(direct_messages) <= args.max_direct_message_chars:
        result, run_meta = invoke_validated(
            base_url=base_url,
            api_key=api_key,
            model=judge_model,
            messages_builder=lambda errors: direct_final_messages(packet, errors),
            validator=lambda value: final_result_acceptance_errors(value, packet),
            folder=folder / "direct",
            kind="direct_final",
            packet_id=packet_id,
            part="direct",
            attempts=args.attempts,
            max_tokens=final_max_tokens_for(args, judge_model),
            timeout=args.timeout,
            response_format=response_format,
        )
        mode = "direct_complete_evidence"
        chunk_count = 0
    else:
        chunks = protocol.make_event_chunks(packet, args.chunk_body_chars)
        reviews: list[dict[str, Any]] = []
        for index, chunk in enumerate(chunks, 1):
            chunk_folder = folder / "chunks" / f"chunk_{index:03d}"
            validated_path = chunk_folder / "validated_review.json"
            review = None
            if validated_path.exists():
                try:
                    old = json.loads(validated_path.read_text(encoding="utf-8"))
                    candidate = old["review"]
                    if (
                        old.get("protocol_version") == protocol.VERSION
                        and old.get("protocol_sha256") == protocol_sha256()
                        and old.get("chunk_sha256") == protocol.digest(chunk)
                        and old.get("review_sha256") == protocol.digest(candidate)
                        and not chunk_review_acceptance_errors(candidate, chunk, packet)
                        and _validated_attempt_artifacts(
                            chunk_folder,
                            old.get("run_meta") or {},
                            judge_model,
                            candidate,
                            expected_kind="chunk_review",
                            expected_packet_id=packet_id,
                            expected_part=f"chunk_{index:03d}",
                            messages_builder=lambda errors, c=chunk: chunk_messages(packet, c, errors),
                        )
                    ):
                        review = candidate
                except Exception:
                    review = None
                if review is None:
                    raise FileExistsError("invalid_existing_chunk_review_requires_fresh_output_root")
            if review is None:
                review, chunk_meta = invoke_validated(
                    base_url=base_url,
                    api_key=api_key,
                    model=judge_model,
                    messages_builder=lambda errors, c=chunk: chunk_messages(packet, c, errors),
                    validator=lambda value, c=chunk: chunk_review_acceptance_errors(value, c, packet),
                    folder=chunk_folder,
                    kind="chunk_review",
                    packet_id=packet_id,
                    part=f"chunk_{index:03d}",
                    attempts=args.attempts,
                    max_tokens=args.chunk_max_tokens,
                    timeout=args.timeout,
                    response_format=response_format,
                )
                atomic_json(
                    validated_path,
                    {
                        "protocol_version": protocol.VERSION,
                        "protocol_sha256": protocol_sha256(),
                        "chunk_sha256": protocol.digest(chunk),
                        "review_sha256": protocol.digest(review),
                        "review": review,
                        "run_meta": chunk_meta,
                    },
                )
            reviews.append(review)

        final_messages_for_size = final_messages(packet, reviews)
        final_chars = protocol.messages_char_count(final_messages_for_size)
        if final_chars > args.max_final_synthesis_chars:
            atomic_json(
                folder / "synthesis_too_large.json",
                {"chars": final_chars, "limit": args.max_final_synthesis_chars, "chunk_count": len(chunks)},
            )
            return {
                "packet_id": packet_id,
                "judge_model": judge_model,
                "state": "synthesis_too_large",
                "chars": final_chars,
                "chunk_count": len(chunks),
            }
        allowed_map = reviewed_citation_transport_map(reviews, packet)
        final_allowed_map = allowed_map
        result, run_meta = invoke_validated(
            base_url=base_url,
            api_key=api_key,
            model=judge_model,
            messages_builder=lambda errors: final_messages(packet, reviews, errors),
            validator=lambda value: final_result_acceptance_errors(
                value, packet, allowed_citation_map=allowed_map
            ),
            folder=folder / "final",
            kind="final_synthesis",
            packet_id=packet_id,
            part="final",
            attempts=args.attempts,
            max_tokens=final_max_tokens_for(args, judge_model),
            timeout=args.timeout,
            response_format=response_format,
        )
        mode = "chunked_complete_evidence"
        chunk_count = len(chunks)

    chunk_review_manifest = []
    if mode == "chunked_complete_evidence":
        for index in range(1, chunk_count + 1):
            review_path = folder / "chunks" / f"chunk_{index:03d}" / "validated_review.json"
            chunk_review_manifest.append({
                "chunk_index": index,
                "path": str(review_path.relative_to(folder)),
                "sha256": file_sha256(review_path),
            })
    derived_grade, _derived_flags = protocol.derive_grade(result)
    quality_errors = final_result_quality_errors(
        result, packet, allowed_citation_map=final_allowed_map
    )
    canonical = protocol.canonical_record(result, packet) | {
        "judge_reported_grade": result.get("proposed_trajectory_grade"),
        "judge_reported_grade_label": result.get("proposed_trajectory_grade_label"),
        "judge_reported_grade_matches_derived": (
            result.get("proposed_trajectory_grade") == derived_grade
            and result.get("proposed_trajectory_grade_label") == protocol.LABELS.get(derived_grade)
        ),
        "judge_output_quality_errors": quality_errors,
        "judge_output_quality_error_count": len(quality_errors),
        "canonical_grade_source": "deterministic_frozen_rubric_from_unmodified_judge_findings",
        "result_policy_version": RESULT_POLICY_VERSION,
        "judge_model": judge_model,
        "judge_model_returned": run_meta.get("response_model_returned"),
        "packet_id": packet_id,
        "doctor_model_blinded_from_judge": True,
        "mode": mode,
        "chunk_count": chunk_count,
        "chunk_body_chars": args.chunk_body_chars,
        "chunk_review_manifest": chunk_review_manifest,
        "source_trajectory_sha256": manifest_row.get("source_trajectory_sha256"),
        "source_case_sha256": manifest_row.get("case_sha256"),
        "packet_file_sha256": manifest_row.get("packet_sha256"),
        "protocol_sha256": protocol_sha256(),
        "runner_sha256": runner_sha256(),
        "raw_result_sha256": protocol.digest(result),
        "accepted_at_unix": time.time(),
        "final_attempt": run_meta,
    }
    atomic_json(canonical_path, canonical)
    if not canonical_is_valid(canonical_path, packet, judge_model, manifest_row):
        raise RuntimeError("post_write_canonical_validation_failed")
    return {
        "packet_id": packet_id,
        "judge_model": judge_model,
        "state": "accepted",
        "grade": canonical["trajectory_grade"],
        "judge_reported_grade": canonical["judge_reported_grade"],
        "judge_grade_matches_frozen_derived": canonical["judge_reported_grade_matches_derived"],
        "quality_error_count": canonical["judge_output_quality_error_count"],
        "mode": mode,
        "chunk_count": chunk_count,
    }


def select_rows(rows: list[dict[str, Any]], packet_ids: list[str] | None, max_packets: int) -> list[dict[str, Any]]:
    if packet_ids:
        by_id = {str(row["packet_id"]): row for row in rows}
        missing = set(packet_ids) - set(by_id)
        if missing:
            raise ValueError("unknown_packet_ids:" + ",".join(sorted(missing)))
        rows = [by_id[packet_id] for packet_id in packet_ids]
    return rows[:max_packets] if max_packets else rows


def effective_max_workers(args: argparse.Namespace) -> int:
    """Return the auditable total worker cap for the selected concurrency mode."""
    per_judge = int(getattr(args, "concurrency_per_judge", 0) or 0)
    if per_judge:
        return per_judge * len(args.judge_models)
    return args.concurrency


def final_max_tokens_for(args: argparse.Namespace, judge_model: str) -> int:
    """Resolve a recorded per-Judge completion budget without changing rubric semantics."""
    overrides = getattr(args, "final_max_tokens_by_judge", {}) or {}
    if isinstance(overrides, list):
        overrides = parse_model_int_overrides(overrides)
    return int(overrides.get(judge_model, args.final_max_tokens))


def parse_model_int_overrides(values: list[str]) -> dict[str, int]:
    result: dict[str, int] = {}
    for value in values:
        if "=" not in value:
            raise ValueError("model token overrides must use MODEL=INTEGER")
        model, raw = value.rsplit("=", 1)
        model = model.strip()
        if not model or model in result:
            raise ValueError("model token override names must be non-empty and unique")
        try:
            count = int(raw)
        except ValueError as exc:
            raise ValueError("model token override values must be integers") from exc
        if count < 1:
            raise ValueError("model token override values must be positive")
        result[model] = count
    return result


def build_run_plan(args: argparse.Namespace, rows: list[dict[str, Any]]) -> dict[str, Any]:
    totals = {
        "judge_trajectory_jobs": 0,
        "direct_jobs": 0,
        "chunked_jobs": 0,
        "chunk_review_calls": 0,
        "final_calls": 0,
        "unscorable_jobs": 0,
    }
    details = []
    for row in rows:
        packet = json.loads(Path(row["_packet_path"]).read_text(encoding="utf-8"))
        errors = protocol.packet_scorability_errors(packet)
        direct_chars = protocol.messages_char_count(direct_final_messages(packet))
        chunks = (
            []
            if errors or getattr(args, "full_context_only", False) or direct_chars <= args.max_direct_message_chars
            else protocol.make_event_chunks(packet, args.chunk_body_chars)
        )
        for _judge in args.judge_models:
            totals["judge_trajectory_jobs"] += 1
            if errors:
                totals["unscorable_jobs"] += 1
            elif chunks:
                totals["chunked_jobs"] += 1
                totals["chunk_review_calls"] += len(chunks)
                totals["final_calls"] += 1
            else:
                totals["direct_jobs"] += 1
                totals["final_calls"] += 1
        details.append(
            {
                "packet_id": row["packet_id"],
                "case_id": row.get("case_id"),
                "packet_sha256": row.get("packet_sha256"),
                "direct_message_chars": direct_chars,
                "scorability_errors": errors,
                "chunk_count": len(chunks),
            }
        )
    return {
        "protocol_version": protocol.VERSION,
        "result_policy_version": RESULT_POLICY_VERSION,
        "protocol_sha256": protocol_sha256(),
        "runner_sha256": runner_sha256(),
        "packet_manifest_sha256": file_sha256(args.packet_manifest),
        "judge_models": args.judge_models,
        "parameters": {
            "max_direct_message_chars": args.max_direct_message_chars,
            "chunk_body_chars": args.chunk_body_chars,
            "max_final_synthesis_chars": args.max_final_synthesis_chars,
            "attempts": args.attempts,
            "timeout_seconds": args.timeout,
            "concurrency": args.concurrency,
            "global_concurrency": args.concurrency,
            "concurrency_per_judge": int(getattr(args, "concurrency_per_judge", 0) or 0),
            "effective_max_workers": effective_max_workers(args),
            "chunk_max_tokens": args.chunk_max_tokens,
            "final_max_tokens": args.final_max_tokens,
            "final_max_tokens_by_judge": dict(sorted((getattr(args, "final_max_tokens_by_judge", {}) or {}).items())),
            "resolved_final_max_tokens_by_judge": {
                model: final_max_tokens_for(args, model) for model in args.judge_models
            },
            "full_context_only": bool(getattr(args, "full_context_only", False)),
            "temperature_field_omitted": True,
            "json_mode_models": sorted(args.json_mode_models),
            "retry_backoff_seconds": list(LEGACY_RETRY_BACKOFF_SECONDS),
            "retry_after_header_honored": True,
            "rate_limit_jitter_max_seconds": 4.999,
            "operational_profile": (
                "legacy_formal_20260907_minimal_correctness_patch"
                + ("_full_context_only" if getattr(args, "full_context_only", False) else "")
                + (
                    f"_user_authorized_{int(getattr(args, 'concurrency_per_judge', 0))}_per_judge"
                    if getattr(args, "concurrency_per_judge", 0)
                    else ""
                )
            ),
        },
        "totals": totals,
        "packets": details,
    }


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Run the strict CareLoop calibrated-v1.1 legacy-preserving V7 judge protocol.")
    ap.add_argument("--root", type=Path, default=Path.cwd())
    ap.add_argument("--packet-manifest", type=Path, default=Path("outputs/judge_packets/cl120_blinded_ordinal_1to5_manifest.json"))
    ap.add_argument("--out-root", type=Path, default=Path("outputs/judge_results/calibrated_v1_1_legacy_preserving_v7"))
    ap.add_argument("--judge-models", nargs="+", default=DEFAULT_JUDGES)
    ap.add_argument("--json-mode-models", nargs="*", default=sorted(JSON_MODE_DEFAULT))
    ap.add_argument("--packet-ids", nargs="*")
    ap.add_argument("--max-packets", type=int, default=0)
    ap.add_argument("--base-url-env", default="CARELOOP_LITE_BASE_URL")
    ap.add_argument("--api-key-env", default="CARELOOP_LITE_API_KEY")
    ap.add_argument("--max-direct-message-chars", type=int, default=90000)
    ap.add_argument("--chunk-body-chars", type=int, default=45000)
    ap.add_argument("--max-final-synthesis-chars", type=int, default=180000)
    ap.add_argument("--chunk-max-tokens", type=int, default=12000)
    ap.add_argument("--final-max-tokens", type=int, default=12000)
    ap.add_argument(
        "--final-max-tokens-by-judge", nargs="*", default=[], metavar="MODEL=COUNT",
        help="Optional per-Judge completion-token overrides, for example GLM-5=6000.",
    )
    ap.add_argument(
        "--full-context-only", action="store_true",
        help="Send every complete evidence packet directly; never substitute chunk summaries.",
    )
    ap.add_argument("--timeout", type=int, default=LEGACY_MAIN_TIMEOUT_SECONDS)
    ap.add_argument("--attempts", type=int, default=LEGACY_TOTAL_ATTEMPTS)
    ap.add_argument(
        "--concurrency", type=int, default=LEGACY_DEFAULT_CONCURRENCY,
        help="Global worker cap used when --concurrency-per-judge is zero (legacy default: 5).",
    )
    ap.add_argument(
        "--concurrency-per-judge", type=int, default=0,
        help=(
            "Optional independent cap for each requested judge. When positive, the total worker cap is "
            "this value multiplied by the number of judges. The formal post-smoke run uses 20."
        ),
    )
    ap.add_argument("--execute", action="store_true")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    args.root = args.root.resolve()
    args.packet_manifest = resolve_under_root(args.root, str(args.packet_manifest))
    args.out_root = resolve_under_root(args.root, str(args.out_root))
    args.json_mode_models = set(args.json_mode_models)
    try:
        args.final_max_tokens_by_judge = parse_model_int_overrides(args.final_max_tokens_by_judge)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    unknown_token_override_models = set(args.final_max_tokens_by_judge) - set(args.judge_models)
    if unknown_token_override_models:
        raise SystemExit("unknown models in --final-max-tokens-by-judge: " + ",".join(sorted(unknown_token_override_models)))
    if args.attempts < 1 or args.concurrency < 1 or args.concurrency_per_judge < 0:
        raise SystemExit("--attempts and --concurrency must be positive; --concurrency-per-judge must be nonnegative")
    if args.chunk_body_chars < 1000 or args.max_direct_message_chars < 1000 or args.max_final_synthesis_chars < 1000:
        raise SystemExit("message-size limits must be at least 1000 characters")
    if args.chunk_max_tokens < 1 or args.final_max_tokens < 1 or args.timeout < 1:
        raise SystemExit("token and timeout limits must be positive")
    if not args.judge_models or len(args.judge_models) != len(set(args.judge_models)) or any(not str(x).strip() for x in args.judge_models):
        raise SystemExit("judge models must be non-empty and unique")
    if args.packet_ids and len(args.packet_ids) != len(set(args.packet_ids)):
        raise SystemExit("packet IDs must be unique")
    rows = select_rows(load_manifest(args.packet_manifest, args.root), args.packet_ids, args.max_packets)
    args.out_root.mkdir(parents=True, exist_ok=True)
    plan = build_run_plan(args, rows)
    plan_path = args.out_root / "run_plan.json"
    if plan_path.exists():
        if json.loads(plan_path.read_text(encoding="utf-8")) != plan:
            raise SystemExit("existing run_plan.json differs; use a fresh --out-root")
    else:
        atomic_json(plan_path, plan)
    print(json.dumps(plan["totals"], ensure_ascii=False, indent=2))
    if not args.execute:
        print("DRY_RUN_ONLY: no API calls made")
        return 0

    base_url = os.environ.get(args.base_url_env, "")
    api_key = os.environ.get(args.api_key_env, "")
    if not base_url or not api_key:
        raise SystemExit(f"Missing {args.base_url_env} or {args.api_key_env}; credentials are read from environment variables only.")
    host = urlparse(endpoint(base_url)).hostname or ""
    metadata_path = args.out_root / "run_metadata.json"
    metadata_static = {
        "protocol_version": protocol.VERSION,
        "result_policy_version": RESULT_POLICY_VERSION,
        "protocol_sha256": protocol_sha256(),
        "runner_sha256": runner_sha256(),
        "packet_manifest_sha256": file_sha256(args.packet_manifest),
        "judge_models": args.judge_models,
        "api_host_sha256": hashlib.sha256(host.encode("utf-8")).hexdigest(),
        "api_key_logged": False,
        "base_url_logged": False,
    }
    if metadata_path.exists():
        existing_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if any(existing_metadata.get(key) != value for key, value in metadata_static.items()):
            raise SystemExit("existing run_metadata.json differs; use a fresh --out-root")
    else:
        atomic_json(metadata_path, metadata_static | {"started_at_unix": time.time()})

    jobs = [(row, judge) for row in rows for judge in args.judge_models]
    results: list[dict[str, Any]] = []
    per_judge_semaphores = (
        {judge: threading.BoundedSemaphore(args.concurrency_per_judge) for judge in args.judge_models}
        if args.concurrency_per_judge
        else {}
    )

    def run_guarded(row: dict[str, Any], judge: str) -> dict[str, Any]:
        semaphore = per_judge_semaphores.get(judge)
        if semaphore is None:
            return process_one(args, base_url, api_key, row, judge)
        with semaphore:
            return process_one(args, base_url, api_key, row, judge)

    with cf.ThreadPoolExecutor(max_workers=effective_max_workers(args)) as executor:
        futures = {executor.submit(run_guarded, row, judge): (row, judge) for row, judge in jobs}
        for future in cf.as_completed(futures):
            row, judge = futures[future]
            try:
                result = future.result()
            except Exception as exc:  # keep other independent jobs auditable
                result = {
                    "packet_id": row.get("packet_id"),
                    "case_id": row.get("case_id"),
                    "judge_model": judge,
                    "state": "failed",
                    "error": safe_exception(exc),
                }
            results.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
            atomic_json(
                args.out_root / "progress.json",
                {
                    "total": len(jobs),
                    "done": len(results),
                    "states": dict(__import__("collections").Counter(x["state"] for x in results)),
                    "updated_at_unix": time.time(),
                },
            )
    results.sort(key=lambda row: (str(row.get("judge_model") or ""), str(row.get("packet_id") or "")))
    atomic_json(args.out_root / "run_results.json", results)
    failures = [row for row in results if row.get("state") not in {"accepted", "skipped_valid_existing"}]
    if failures:
        raise SystemExit(f"{len(failures)} judge jobs were not accepted; the batch is non-canonical")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
