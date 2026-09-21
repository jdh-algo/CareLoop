"""Lightweight public CareLoop runtime.

This package provides reusable components for generating longitudinal simulated
care trajectories from CareLoop case contracts. It is intended for research
evaluation of patient-facing AI doctor behavior and is not a clinical deployment
system.
"""

from careloop.runtime_lite.case_loader import LiteCase, load_lite_case
from careloop.runtime_lite.care_system import LiteCareSystem
from careloop.runtime_lite.llm import OpenAICompatibleLiteLLMClient, ScriptedLiteLLMClient
from careloop.runtime_lite.evaluation_dimensions import (
    EVALUATION_DIMENSION_CATALOG_VERSION,
    evaluation_dimension_catalog_for_prompt,
    evaluation_dimension_ids,
)
from careloop.runtime_lite.models import (
    ActorSituation,
    ActorUtterance,
    ClosureAssessmentLite,
    DirectorBeat,
    DoctorOperationRequest,
    LiteEventCandidate,
    LiteProbabilityTask,
    TimeAdvanceRequest,
)
from careloop.runtime_lite.runner import LiteCareLoopRunner, LiteRuntimeConfig, LiteRunResult

__all__ = [
    "ActorSituation",
    "ActorUtterance",
    "ClosureAssessmentLite",
    "DirectorBeat",
    "DoctorOperationRequest",
    "EVALUATION_DIMENSION_CATALOG_VERSION",
    "LiteCareLoopRunner",
    "LiteCareSystem",
    "LiteCase",
    "LiteEventCandidate",
    "LiteProbabilityTask",
    "LiteRuntimeConfig",
    "LiteRunResult",
    "OpenAICompatibleLiteLLMClient",
    "ScriptedLiteLLMClient",
    "TimeAdvanceRequest",
    "evaluation_dimension_catalog_for_prompt",
    "evaluation_dimension_ids",
    "load_lite_case",
]
