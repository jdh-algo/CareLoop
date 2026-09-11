from __future__ import annotations

"""Structural visibility boundary helpers for runtime_lite.

This module deliberately enforces only thin structural boundaries.  It does not
ban clinical words or diagnoses: patient-facing medicine naturally contains such
words.  The goal is to keep evaluator/backstage fields, hidden truth, scoring
rubrics, and case-author hints out of doctor-visible and patient-visible paths.
"""

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

EVALUATOR_ONLY_KEYS: frozenset[str] = frozenset(
    {
        "evaluation_contract",
        "score_blend_contract",
        "episode_completion_contract",
        "clinical_termination_contract",
        "termination",
        "dimension_weights",
        "prior_weight_profile",
        "dimension_weight_rationales",
        "coverage_link",
        "must_test_capabilities",
        "milestone_not_terminal",
        "false_closure_traps",
        "expected_closure_evidence",
        "clinical_focus_for_evaluator_only",
        "case_taxonomy",
        "coverage",
        "hidden_simulation_state",
        "hidden_world_material",
        "root_truth",
        "case_world_invariant",
        "simulation_contract",
        "clinical_anchor",
        "dynamic_event_space",
        "visibility_contract",
        "case_contracts",
        "evaluation_material_hint",
        "evaluator_notes",
        "authoring_hints",
        "gold_path",
        "reference_trajectory",
    }
)

# Keys that are acceptable internally but should not be presented to the tested
# doctor as opening/session identifiers, because future case authors may encode
# the diagnosis or trap in them.
DOCTOR_OPENING_IDENTITY_KEYS: frozenset[str] = frozenset({"case_id", "raw_case_id", "source_case_id"})


VISIBILITY_LEVELS: frozenset[str] = frozenset(
    {
        "doctor_visible",
        "actor_known",
        "actor_visible",
        "world_internal",
        "evaluator_only",
    }
)

SOURCE_TYPES: frozenset[str] = frozenset(
    {
        "workspace_record",
        "patient_self_report",
        "family_report",
        "caregiver_report",
        "observed_event",
        "system_notification",
        "patient_held_record",
        "authorized_import",
        "runtime_generated_record",
        "world_internal",
        "evaluator_only",
        "unknown",
    }
)

DOCTOR_VISIBLE_SOURCE_TYPES: frozenset[str] = frozenset(
    {
        "workspace_record",
        "patient_self_report",
        "family_report",
        "caregiver_report",
        "observed_event",
        "system_notification",
        "patient_held_record",
        "authorized_import",
        "runtime_generated_record",
        "unknown",
    }
)

ACTOR_DISCLOSURE_SOURCE_TYPES: frozenset[str] = frozenset(
    {"patient_self_report", "family_report", "caregiver_report", "observed_event", "patient_held_record", "unknown"}
)


def normalize_visibility(value: Any, *, default: str = "world_internal") -> str:
    text = str(value or "").strip() or default
    return text if text in VISIBILITY_LEVELS else default


def normalize_source_type(value: Any, *, default: str = "unknown") -> str:
    text = str(value or "").strip() or default
    return text if text in SOURCE_TYPES else default


def visibility_provenance(
    *,
    route: str,
    source_type: str,
    visibility: str = "doctor_visible",
    known_by: Sequence[Any] | None = None,
    reliability: str = "",
    natural_actor_disclosure: bool | None = None,
    notes: str = "",
) -> dict[str, Any]:
    """Small provenance envelope for doctor-visible facts.

    This is deliberately thin.  It does not decide clinical truth and it does
    not ban clinical vocabulary.  It only records how information reached the
    tested doctor so audits can distinguish system leakage from ordinary
    patient/family disclosure.
    """

    src = normalize_source_type(source_type)
    payload: dict[str, Any] = {
        "route": str(route or "").strip() or "unknown",
        "source_type": src,
        "visibility": normalize_visibility(visibility, default="doctor_visible"),
        "known_by": [str(item) for item in (known_by or []) if str(item).strip()],
        "reliability": str(reliability or "").strip(),
        "principle": (
            "Leakage is provenance-based: system/workspace/evaluator-only hidden truth reaching the doctor is a leak; "
            "patient/family disclosure of facts they plausibly know is normal simulated care, even when sensitive or inaccurate."
        ),
    }
    if natural_actor_disclosure is not None:
        payload["natural_actor_disclosure"] = bool(natural_actor_disclosure)
    if notes:
        payload["notes"] = notes
    return payload


def redact_for_doctor(value: Any) -> Any:
    """Doctor-visible redaction alias used by runtime/workspace code."""

    return redact_evaluator_only(value)


def redact_for_actor(value: Any) -> Any:
    """Actor-visible structural redaction.

    Actors may know private facts, but they must not receive evaluator-only or
    backend scaffolding.  Semantic actor-knowledge judgement remains LLM-led in
    prompts; this helper only strips structural hidden/evaluator containers.
    """

    return redact_evaluator_only(value)


def audit_doctor_visible_provenance(value: Any, *, payload_name: str = "doctor_visible_payload") -> dict[str, Any]:
    structural = audit_visible_payload(value, payload_name=payload_name, include_opening_identity_keys=False)
    provenance_entries: list[dict[str, Any]] = []
    missing_provenance_paths: list[str] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, Mapping):
            prov = node.get("provenance")
            if isinstance(prov, Mapping):
                entry = {
                    "path": path + ".provenance",
                    "route": str(prov.get("route") or ""),
                    "source_type": normalize_source_type(prov.get("source_type")),
                    "visibility": normalize_visibility(prov.get("visibility"), default="doctor_visible"),
                    "natural_actor_disclosure": prov.get("natural_actor_disclosure"),
                }
                provenance_entries.append(entry)
            for key, item in node.items():
                walk(item, f"{path}.{key}")
        elif isinstance(node, list):
            for idx, item in enumerate(node[:200]):
                walk(item, f"{path}[{idx}]")

    walk(value, "$")
    actor_disclosures = [
        item for item in provenance_entries
        if item.get("source_type") in ACTOR_DISCLOSURE_SOURCE_TYPES
    ]
    system_or_workspace = [
        item for item in provenance_entries
        if item.get("source_type") in {"workspace_record", "system_notification", "authorized_import", "runtime_generated_record"}
    ]
    invalid_provenance_entries = [
        item for item in provenance_entries
        if item.get("source_type") in {"world_internal", "evaluator_only"}
        or item.get("visibility") in {"world_internal", "evaluator_only"}
    ]
    return {
        "payload_name": payload_name,
        "structural_leak_found": bool(structural.get("structural_leak_found")),
        "forbidden_key_hits": structural.get("forbidden_key_hits") or [],
        "provenance_entry_count": len(provenance_entries),
        "actor_disclosure_count": len(actor_disclosures),
        "system_or_workspace_source_count": len(system_or_workspace),
        "invalid_provenance_entries": invalid_provenance_entries,
        "missing_provenance_paths": missing_provenance_paths,
        "pass": not structural.get("structural_leak_found") and not invalid_provenance_entries,
        "principle": "Clinical terms are not leakage. Audit source/provenance and hidden/evaluator-only structural contamination.",
    }

STANDARD_WORKSPACE_PANELS: tuple[str, ...] = (
    "record_index",
    "records",
    "documents",
    "test_results",
    "medications",
    "care_access",
    "family_context",
    "timeline",
)


def neutral_session_label(raw: Mapping[str, Any] | None, *, fallback: str = "CareLoop Session") -> str:
    """Return a doctor-visible neutral session label.

    Prefer explicit case labels such as L01 because they are not clinical hints.
    Avoid raw case_id/title because historical cases often encoded diagnoses.
    """

    raw = raw if isinstance(raw, Mapping) else {}
    for container_key in ("coverage", "case_taxonomy"):
        container = raw.get(container_key)
        if isinstance(container, Mapping):
            label = str(container.get("case_label") or container.get("label") or "").strip()
            if label:
                return f"CareLoop Session {label}"
    case_id = str(raw.get("case_id") or "").strip()
    for token in case_id.replace("-", "_").split("_"):
        if len(token) == 3 and token[0].upper() == "L" and token[1:].isdigit():
            return f"CareLoop Session {token.upper()}"
    return fallback


def redact_evaluator_only(value: Any) -> Any:
    """Recursively remove evaluator-only keys from a doctor/patient-visible payload."""

    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, item in value.items():
            key_str = str(key)
            if key_str in EVALUATOR_ONLY_KEYS:
                continue
            out[key_str] = redact_evaluator_only(item)
        return out
    if isinstance(value, list):
        return [redact_evaluator_only(item) for item in value]
    if isinstance(value, tuple):
        return [redact_evaluator_only(item) for item in value]
    return deepcopy(value)


def forbidden_key_hits(value: Any, *, include_opening_identity_keys: bool = False, path: str = "$", limit: int = 100) -> list[dict[str, str]]:
    """Find structural forbidden keys in a candidate visible payload."""

    forbidden = set(EVALUATOR_ONLY_KEYS)
    if include_opening_identity_keys:
        forbidden |= set(DOCTOR_OPENING_IDENTITY_KEYS)
    hits: list[dict[str, str]] = []

    def walk(node: Any, current_path: str) -> None:
        if len(hits) >= limit:
            return
        if isinstance(node, Mapping):
            for key, item in node.items():
                key_str = str(key)
                child_path = f"{current_path}.{key_str}"
                if key_str in forbidden:
                    hits.append({"path": child_path, "key": key_str})
                    if len(hits) >= limit:
                        return
                walk(item, child_path)
        elif isinstance(node, list):
            for idx, item in enumerate(node[:200]):
                walk(item, f"{current_path}[{idx}]")

    walk(value, path)
    return hits


def nonstandard_panel_names(panels: Sequence[Any] | None) -> list[str]:
    allowed = set(STANDARD_WORKSPACE_PANELS)
    out: list[str] = []
    for panel in panels or []:
        name = str(panel or "").strip()
        if name and name not in allowed and name not in out:
            out.append(name)
    return out


def audit_visible_payload(value: Any, *, payload_name: str, include_opening_identity_keys: bool = False) -> dict[str, Any]:
    """Thin structural anti-cheat audit for a doctor/patient-visible payload."""

    hits = forbidden_key_hits(value, include_opening_identity_keys=include_opening_identity_keys)
    return {
        "payload_name": payload_name,
        "structural_leak_found": bool(hits),
        "forbidden_key_hits": hits,
        "principle": "Thin structural audit only; no clinical keyword bans are applied.",
    }
