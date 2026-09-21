from __future__ import annotations

"""Probability boundary for the public CareLoop runtime.

CareLoop resolves every stochastic event proposed by a model through one
deterministic seeded roll. The probability boundary is intentionally small,
inspectable, and reproducible for research auditing.
"""

from hashlib import sha256
from typing import Any

from careloop.runtime_lite.models import LiteProbabilityTask


PROBABILITY_RANGES: dict[str, tuple[float, float]] = {
    "never": (0.0, 0.0),
    "almost_never": (0.01, 0.05),
    "very_low": (0.05, 0.15),
    "low": (0.15, 0.35),
    "some_chance": (0.30, 0.50),
    "moderate": (0.40, 0.65),
    "likely": (0.60, 0.90),
    "high": (0.75, 0.95),
    "very_high": (0.88, 0.98),
    "almost_certain": (0.93, 0.995),
    "always": (1.0, 1.0),
}


ALIASES: dict[str, str] = {
    "概率极低": "very_low",
    "很低概率": "very_low",
    "较低概率": "low",
    "低概率": "low",
    "可能": "some_chance",
    "有可能": "some_chance",
    "中等概率": "moderate",
    "大概率": "likely",
    "较大概率": "likely",
    "高概率": "high",
    "极大概率": "very_high",
    "几乎一定": "almost_certain",
    "必然": "always",
    "不会": "never",
}


def decide_probability_task(
    task: LiteProbabilityTask,
    *,
    case_id: str,
    run_id: str,
    turn_id: str,
    seed_scope: str,
) -> dict[str, Any]:
    return _runtime_lite_decide(task, case_id=case_id, run_id=run_id, turn_id=turn_id, seed_scope=seed_scope)


def _runtime_lite_decide(
    task: LiteProbabilityTask,
    *,
    case_id: str,
    run_id: str,
    turn_id: str,
    seed_scope: str,
) -> dict[str, Any]:
    probability = task.probability if task.probability is not None else _descriptor_midpoint(task.descriptor)
    seed_material = "||".join(
        [
            "careloop_runtime_lite_probability",
            case_id,
            run_id,
            turn_id,
            seed_scope,
            task.owner,
            task.event_id,
        ]
    )
    roll = _uniform(seed_material)
    occurred = bool(task.allowed and task.preconditions_satisfied and roll <= probability)
    status = "resolved" if task.allowed and task.preconditions_satisfied else "blocked"
    fail_closed_reason = "" if status == "resolved" else (task.fail_closed_reason or "event_not_allowed_or_preconditions_not_satisfied")
    return {
        "record_id": sha256((seed_material + "||record").encode("utf-8")).hexdigest()[:16],
        "event_id": task.event_id,
        "event_type": task.event_type,
        "owner": task.owner,
        "requesting_agent": "runtime_lite.WorldDirector",
        "status": status,
        "descriptor": {
            "label": _normalize_descriptor(task.descriptor),
            "midpoint": probability,
            "source": "runtime_lite_fallback",
        },
        "sampled_probability": round(probability, 6),
        "estimated_probability": round(probability, 6),
        "threshold_roll": None,
        "event_roll": round(roll, 6),
        "occurred": occurred,
        "seed_material_hash": sha256(seed_material.encode("utf-8")).hexdigest(),
        "rationale": task.basis,
        "fail_closed_reason": fail_closed_reason,
        "creates_world_fact": task.creates_world_fact,
        "world_fact_type": task.world_fact_type,
        "metadata": {
            **task.metadata,
            "single_roll_fixed_probability": True,
            "runtime_lite_probability_kernel": True,
            "not_llm_decided": True,
        },
    }


def _normalize_descriptor(value: str) -> str:
    label = str(value or "moderate").strip().lower().replace(" ", "_")
    return ALIASES.get(label, label if label in PROBABILITY_RANGES else "moderate")


def _descriptor_midpoint(value: str) -> float:
    label = _normalize_descriptor(value)
    low, high = PROBABILITY_RANGES.get(label, PROBABILITY_RANGES["moderate"])
    return (low + high) / 2.0


def _uniform(seed_material: str) -> float:
    digest = sha256(seed_material.encode("utf-8")).hexdigest()
    return int(digest[:16], 16) / float(16**16 - 1)
