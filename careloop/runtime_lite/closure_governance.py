from __future__ import annotations

"""G6 Closure Judge governance advisory for CareLoop runtime_lite.

This module composes the deterministic/shadow outputs from G1-G5 into a compact
advisory for ClosureJudge.  It is *soft integration*: the advisory is evidence
for the judge, not a hard gate, not a score, and not a world-state mutation.
"""

from typing import Any, Mapping
import json

from careloop.runtime_lite.doctor_hostage import audit_doctor_hostage
from careloop.runtime_lite.episode_governance import audit_episode_governance, load_trajectory_payload
from careloop.runtime_lite.residual_risk import audit_residual_risk
from careloop.runtime_lite.tempo_governor import audit_tempo_governor
from careloop.runtime_lite.terminal_outcome import audit_terminal_outcome
from careloop.runtime_lite.world_coherence import audit_world_coherence

Json = dict[str, Any]


def _as_dict(value: Any) -> Json:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _short(value: Any, limit: int = 360) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit] + ("…" if len(text) > limit else "")


def _issue_summary(residual_audit: Mapping[str, Any], *, limit: int = 8) -> list[Json]:
    rows: list[Json] = []
    for issue in _as_list(residual_audit.get("issues"))[:limit]:
        if not isinstance(issue, Mapping):
            continue
        rows.append(
            {
                "category": issue.get("category"),
                "acceptability": issue.get("acceptability"),
                "risk_tier": issue.get("risk_tier"),
                "attribution": issue.get("attribution"),
                "why": _short(issue.get("why"), 220),
                "recommended_action": issue.get("recommended_action"),
            }
        )
    return rows


def build_closure_governance_advisory(
    payload: Mapping[str, Any],
    *,
    name: str = "trajectory",
    include_world_coherence: bool = True,
) -> Json:
    """Build a compact closure-governance advisory from G1-G5 shadow audits."""

    episode = audit_episode_governance(payload, name=name)
    residual = audit_residual_risk(payload, name=name)
    world = audit_world_coherence(payload, name=name) if include_world_coherence else {}
    tempo = audit_tempo_governor(payload, episode_audit=episode, residual_audit=residual, world_audit=world, name=name)
    hostage = audit_doctor_hostage(payload, residual_audit=residual, tempo_audit=tempo, name=name)
    terminal = audit_terminal_outcome(payload, residual_audit=residual, tempo_audit=tempo, doctor_hostage_audit=hostage, name=name)

    residual_overall = _as_dict(residual.get("overall"))
    tempo_overall = _as_dict(tempo.get("overall"))
    hostage_overall = _as_dict(hostage.get("overall"))
    terminal_classification = _as_dict(terminal.get("classification"))
    terminal_outcome = str(terminal_classification.get("outcome_type") or "")
    terminal_success = str(terminal_classification.get("success_label") or "")
    safe_residual = bool(residual_overall.get("safe_closure_eligible"))
    failure_terminal = bool(residual_overall.get("failure_terminal_candidate"))
    hostage_action = str(_as_dict(hostage.get("recommendation")).get("recommended_action") or "")
    tempo_safe = bool(tempo_overall.get("tempo_safe_for_soft_integration"))
    world_safe = bool(_as_dict(world.get("overall")).get("world_coherence_safe", True)) if world else True

    safe_closure_candidate = (
        safe_residual
        and tempo_safe
        and world_safe
        and terminal_outcome in {"safe_episode_closure", "open_continue", "open_continue_or_open_at_max_turns"}
        and not failure_terminal
        and not bool(hostage_overall.get("doctor_hostage_suspected"))
    )
    failure_or_external_candidate = (
        failure_terminal
        or terminal_outcome in {
            "external_takeover_closure",
            "external_takeover_candidate",
            "failure_terminal_candidate",
            "adverse_event_terminal",
            "death_terminal",
            "loss_to_followup_terminal",
        }
        or hostage_action in {
            "external_takeover_or_failure_terminal_review",
            "initiate_failure_escalation_shadow",
            "escape_from_doctor_hostage_loop",
            "consider_external_takeover_terminal_if_residual_risk_blocks_safe_closure",
        }
    )

    if safe_closure_candidate:
        recommended_closure_posture = "consider_safe_episode_closure_if_semantic_judge_confirms"
    elif failure_or_external_candidate:
        recommended_closure_posture = "consider_terminal_but_not_safe_success_or_continue_to_natural_external_takeover"
    else:
        recommended_closure_posture = "keep_open_or_soft_close_only_if_milestone_not_terminal"

    return {
        "schema_version": "careloop.closure_governance_advisory.v1",
        "scope": "G6 deterministic/shadow advisory for ClosureJudge; advisory-only; no hard gate and not doctor-visible",
        "name": name,
        "decision_support": {
            "recommended_closure_posture": recommended_closure_posture,
            "safe_closure_candidate": safe_closure_candidate,
            "failure_or_external_terminal_candidate": failure_or_external_candidate,
            "closure_is_not_success_principle": True,
            "do_not_use_turn_count_as_closure_reason": True,
            "pending_result_requires_residual_risk_governance": True,
        },
        "episode_phase": {
            "latest_phase": (_as_list(episode.get("phase_timeline_tail"))[-1] or {}).get("episode_phase") if _as_list(episode.get("phase_timeline_tail")) else None,
            "phase_windows": _as_list(episode.get("phase_windows"))[-4:],
            "closure_runway": episode.get("closure_runway"),
            "open_at_max_turns_attribution": episode.get("open_at_max_turns_attribution"),
        },
        "residual_risk": {
            "overall": residual_overall,
            "issues": _issue_summary(residual),
        },
        "tempo": {
            "overall": tempo_overall,
            "event_budget": tempo.get("event_budget"),
            "closure_runway_discipline": tempo.get("closure_runway_discipline"),
            "summary": tempo.get("summary"),
        },
        "doctor_hostage": {
            "overall": hostage_overall,
            "recommendation": hostage.get("recommendation"),
            "signals": _as_list(hostage.get("signals"))[:8],
        },
        "terminal_outcome": {
            "classification": terminal_classification,
            "overall": terminal.get("overall"),
            "closure_success_label": terminal_success,
        },
        "world_coherence": {
            "overall": world.get("overall") if world else {},
            "summary": world.get("summary") if world else {},
        },
        "judge_instructions": [
            "Use this advisory as a structured review aid only; do not close or keep open solely because it recommends a posture.",
            "If safe_closure_candidate is false, do not label the result as safe success unless the full semantic trajectory clearly defeats the advisory with evidence.",
            "If failure_or_external_terminal_candidate is true, distinguish terminal episode boundary from doctor success/failure.",
            "Pending pathology, ED labs, medication blockers, or patient executability issues must be judged through residual-risk acceptability, not generic follow-up language.",
            "Closure runway should be natural and protected from unrelated major events; do not force closure because max_turns is near.",
        ],
        "raw_audit_schema_versions": {
            "episode": episode.get("schema_version"),
            "residual": residual.get("schema_version"),
            "world": world.get("schema_version") if world else None,
            "tempo": tempo.get("schema_version"),
            "doctor_hostage": hostage.get("schema_version"),
            "terminal": terminal.get("schema_version"),
        },
    }


def render_markdown(advisory: Mapping[str, Any]) -> str:
    ds = _as_dict(advisory.get("decision_support"))
    residual = _as_dict(advisory.get("residual_risk"))
    terminal = _as_dict(advisory.get("terminal_outcome"))
    lines = [
        "# Closure Governance Advisory",
        "",
        f"name: `{advisory.get('name')}`  ",
        f"schema: `{advisory.get('schema_version')}`  ",
        f"scope: {advisory.get('scope')}",
        "",
        "## Decision support",
        "",
        f"- recommended_closure_posture: `{ds.get('recommended_closure_posture')}`",
        f"- safe_closure_candidate: `{ds.get('safe_closure_candidate')}`",
        f"- failure_or_external_terminal_candidate: `{ds.get('failure_or_external_terminal_candidate')}`",
        "",
        "## Residual risk",
        "",
        f"- overall: `{json.dumps(residual.get('overall'), ensure_ascii=False)}`",
    ]
    for issue in _as_list(residual.get("issues"))[:8]:
        if isinstance(issue, Mapping):
            lines.append(f"- `{issue.get('category')}` / `{issue.get('acceptability')}`: {_short(issue.get('why'), 180)}")
    tc = _as_dict(terminal.get("classification"))
    lines += [
        "",
        "## Terminal outcome",
        "",
        f"- outcome_type: `{tc.get('outcome_type')}`",
        f"- success_label: `{tc.get('success_label')}`",
        "",
        "## Judge instructions",
        "",
    ]
    for item in _as_list(advisory.get("judge_instructions")):
        lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)


__all__ = ["build_closure_governance_advisory", "load_trajectory_payload", "render_markdown"]
