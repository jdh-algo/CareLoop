"""Deterministic tests for the friction-centered multi-Judge agreement analysis."""
from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "analyze_friction_judge_agreement_v0_3.py"
SPEC = importlib.util.spec_from_file_location("friction_judge_agreement_v0_3", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_icc_absolute_is_one_for_identical_judges():
    matrix = [
        [0.1, 0.1, 0.1, 0.1],
        [0.4, 0.4, 0.4, 0.4],
        [0.9, 0.9, 0.9, 0.9],
    ]
    one, mean, *_ = MODULE.icc_absolute(matrix)
    assert abs(one - 1.0) < 1e-12
    assert abs(mean - 1.0) < 1e-12


def test_icc_average_is_not_lower_than_single_judge_for_noisy_ratings():
    matrix = [
        [0.10, 0.20, 0.05, 0.15],
        [0.40, 0.55, 0.35, 0.45],
        [0.70, 0.60, 0.80, 0.65],
        [0.95, 0.85, 0.90, 1.00],
    ]
    one, mean, *_ = MODULE.icc_absolute(matrix)
    assert 0.0 < one < 1.0
    assert one <= mean <= 1.0


def test_spearman_handles_ties_and_inverse_order():
    assert abs(MODULE.spearman([1, 2, 2, 4], [10, 20, 20, 40]) - 1.0) < 1e-12
    assert abs(MODULE.spearman([1, 2, 3, 4], [4, 3, 2, 1]) + 1.0) < 1e-12


def test_reported_metric_set_is_complete_and_unique():
    keys = [key for key, _ in MODULE.METRICS]
    assert len(keys) == 8
    assert len(set(keys)) == 8
    assert keys[:2] == [
        "friction_capability_composite",
        "careloop_real_world_robustness",
    ]
