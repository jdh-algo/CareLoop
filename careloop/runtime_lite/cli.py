from __future__ import annotations

"""Command-line entry for runtime_lite."""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import glob
import json
import os
from pathlib import Path
import random
from typing import Any

from careloop.runtime_lite.anti_cheat_audit import audit_doctor_visible_opening, audit_workspace_result, combine_audits
from careloop.runtime_lite.case_loader import load_lite_case
from careloop.runtime_lite.workspace import LiteClinicalWorkspace
from careloop.runtime_lite.llm import OpenAICompatibleLiteLLMClient, ScriptedLiteLLMClient
from careloop.runtime_lite.models import ClosureAssessmentLite
from careloop.runtime_lite.quality import analyze_lite_trajectory, quality_report_markdown
from careloop.runtime_lite.runner import LiteCareLoopRunner, LiteRuntimeConfig
from careloop.runtime_lite.hardening_llm import run_hardening_llm_audits
from careloop.runtime_lite.hardening import (
    FORMAL_CARELOOP_INTERNAL_MODEL,
    FORMAL_DOCTOR_MODELS,
    attach_hardening_reports,
    build_model_role_contract,
    hardening_counts_from_summaries,
    hardening_summary_markdown_lines,
    validate_formal_model_contract,
    write_hardening_artifacts,
)


def _env_flag(name: str, *, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run CareLoop_FCCT-1 runtime_lite on one or more cases.")
    parser.add_argument("--case", action="append", default=[], help="Path to a case JSON file or a directory containing case.json. May be repeated.")
    parser.add_argument("--case-list", action="append", default=[], help="Path to a JSON or text manifest of case paths. May be repeated.")
    parser.add_argument("--case-glob", action="append", default=[], help="Glob pattern for case JSON files/directories. May be repeated.")
    parser.add_argument("--sample-size", type=int, default=0, help="If >0, run a reproducible random sample from the resolved cases.")
    parser.add_argument("--sample-seed", default="runtime_lite", help="Seed used with --sample-size.")
    parser.add_argument("--max-turns", type=int, default=10)
    parser.add_argument("--parallelism", type=int, default=1, help="Number of cases to run concurrently. Default 1 preserves sequential behavior.")
    parser.add_argument(
        "--long-run-300",
        action="store_true",
        default=_env_flag("CARELOOP_LITE_LONG_RUN_300"),
        help=(
            "Formal long-horizon validation preset: max_turns=300, parallelism=10, "
            "eventful clinical memory with a 3-turn forced refresh, latest-only checkpoints, "
            "and unbounded transient LLM retry with 30-60 second jitter."
        ),
    )
    parser.add_argument(
        "--long-run-1000",
        action="store_true",
        default=_env_flag("CARELOOP_LITE_LONG_RUN_1000"),
        help=(
            "Extended longitudinal validation preset: max_turns=1000 with the same "
            "thin-rule, eventful-memory, latest-checkpoint and retry-until-success settings "
            "as --long-run-300. Use when 300 turns truncates an otherwise progressing case."
        ),
    )
    parser.add_argument("--run-id", default="")
    parser.add_argument("--client", choices=["scripted", "openai-compatible"], default="scripted")
    parser.add_argument("--base-url", default=os.environ.get("CARELOOP_LITE_BASE_URL", ""))
    parser.add_argument("--api-key-env", default="CARELOOP_LITE_API_KEY")
    parser.add_argument("--model", default=os.environ.get("CARELOOP_LITE_MODEL", ""))
    parser.add_argument("--sim-model", default=os.environ.get("CARELOOP_LITE_SIM_MODEL", ""))
    parser.add_argument("--doctor-model", default=os.environ.get("CARELOOP_LITE_DOCTOR_MODEL", ""))
    parser.add_argument("--doctor-model-api-id", default=os.environ.get("CARELOOP_LITE_DOCTOR_MODEL_API_ID", ""), help="Optional provider-specific model id used for tested-doctor API calls while preserving the formal doctor_model label in benchmark metadata.")
    parser.add_argument("--formal-fcct2-cross-model", action="store_true", default=_env_flag("CARELOOP_FCCT2_FORMAL_CROSS_MODEL"), help="Enable formal FCCT-2 model-role guards and benchmark metadata.")
    parser.add_argument("--careloop-internal-model", default=os.environ.get("CARELOOP_FCCT2_INTERNAL_MODEL", ""), help="Formal CareLoop internal simulator/evaluator model. Formal FCCT-2 default is GPT-5.5.")
    parser.add_argument("--formal-doctor-models", default=os.environ.get("CARELOOP_FCCT2_DOCTOR_MODELS", ",".join(FORMAL_DOCTOR_MODELS)), help="Comma-separated allowed tested doctor models for formal FCCT-2 runs; names are case-sensitive.")
    parser.add_argument("--experiment-id", default=os.environ.get("CARELOOP_FCCT2_EXPERIMENT_ID", ""), help="Optional formal experiment id written into run metadata.")
    parser.add_argument("--manifest-path", default=os.environ.get("CARELOOP_FCCT2_MANIFEST_PATH", ""), help="Optional formal manifest path written into run metadata.")
    parser.add_argument("--manifest-hash", default=os.environ.get("CARELOOP_FCCT2_MANIFEST_HASH", ""), help="Optional formal manifest hash written into run metadata.")
    parser.add_argument("--hardening-sidecars", choices=["on", "off"], default=os.environ.get("CARELOOP_FCCT2_HARDENING_SIDECARS", "on"), help="Write lightweight hardening sidecars such as status.json and benchmark_validity.json.")
    parser.add_argument("--hardening-llm-audits", choices=["", "off", "final"], default=os.environ.get("CARELOOP_FCCT2_HARDENING_LLM_AUDITS", ""), help="Run optional LLM-led hardening audits. Empty means final in formal mode and off otherwise.")
    parser.add_argument("--hardening-llm-audit-purposes", default=os.environ.get("CARELOOP_FCCT2_HARDENING_LLM_AUDIT_PURPOSES", "visibility,benchmark,receipt,closure"), help="Comma-separated hardening LLM audit purposes: visibility,benchmark,receipt,closure.")
    parser.add_argument("--formal-runtime-advisory", action="store_true", default=_env_flag("CARELOOP_FCCT2_RUNTIME_ADVISORY"), help="In formal mode, enable all optional LLM-led runtime advisory nodes. Advisory only; no hard clinical gates.")
    parser.add_argument("--enable-actor-realism-controller-v2", action="store_true", default=_env_flag("CARELOOP_LITE_ACTOR_REALISM_CONTROLLER_V2"), help="Enable optional LLM-led actor realism v2 advisory.")
    parser.add_argument("--enable-friction-coverage-planner", action="store_true", default=_env_flag("CARELOOP_LITE_FRICTION_COVERAGE_PLANNER"), help="Enable optional LLM-led friction coverage advisory for WorldDirector.")
    parser.add_argument("--enable-closure-thread-advisory", action="store_true", default=_env_flag("CARELOOP_LITE_CLOSURE_THREAD_ADVISORY"), help="Enable optional LLM-led semantic closure-thread advisory before ClosureJudge.")
    parser.add_argument("--enable-receipt-lifecycle-advisory", action="store_true", default=_env_flag("CARELOOP_LITE_RECEIPT_LIFECYCLE_ADVISORY"), help="Enable optional LLM-led receipt lifecycle advisory before ClosureJudge.")
    parser.add_argument("--enable-episode-governance-closure-advisory", action="store_true", default=_env_flag("CARELOOP_LITE_EPISODE_GOVERNANCE_CLOSURE_ADVISORY"), help="Enable deterministic G1-G5 Episode Governance advisory for ClosureJudge. Advisory only; no hard clinical gate.")
    parser.add_argument("--actor-realism-v2-interval-turns", type=int, default=int(os.environ.get("CARELOOP_LITE_ACTOR_REALISM_V2_INTERVAL_TURNS", "4")))
    parser.add_argument("--friction-coverage-interval-turns", type=int, default=int(os.environ.get("CARELOOP_LITE_FRICTION_COVERAGE_INTERVAL_TURNS", "6")))
    parser.add_argument("--closure-thread-advisory-interval-turns", type=int, default=int(os.environ.get("CARELOOP_LITE_CLOSURE_THREAD_ADVISORY_INTERVAL_TURNS", "4")))
    parser.add_argument("--receipt-lifecycle-advisory-interval-turns", type=int, default=int(os.environ.get("CARELOOP_LITE_RECEIPT_LIFECYCLE_ADVISORY_INTERVAL_TURNS", "4")))
    parser.add_argument("--timeout-seconds", type=float, default=float(os.environ.get("CARELOOP_LITE_TIMEOUT_SECONDS", "120")))
    parser.add_argument(
        "--llm-purpose-timeouts-json",
        default=os.environ.get("CARELOOP_LITE_PURPOSE_TIMEOUTS_JSON", ""),
        help=(
            "Optional JSON object mapping LLM purpose names to request timeouts in seconds. "
            "This is an infrastructure fallback for slow background nodes, e.g. "
            "{\"clinical_memory_steward\":35,\"trajectory_evaluator\":60}."
        ),
    )
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=int(os.environ.get("CARELOOP_LITE_MAX_OUTPUT_TOKENS", "0")),
        help="0 means do not send a max_tokens limit.",
    )
    parser.add_argument("--llm-stream", action="store_true", default=_env_flag("CARELOOP_LITE_STREAM"), help="Use streaming chat-completions responses for OpenAI-compatible clients.")
    parser.add_argument("--llm-omit-temperature", action="store_true", default=_env_flag("CARELOOP_LITE_OMIT_TEMPERATURE"), help="Do not send a temperature field; useful for gateways whose default temperature should be used.")
    parser.add_argument(
        "--llm-content-filter-clinical-context-retry",
        dest="llm_content_filter_clinical_context_retry",
        action="store_true",
        default=not _env_flag("CARELOOP_LITE_DISABLE_CONTENT_FILTER_CLINICAL_CONTEXT_RETRY"),
        help=(
            "On an explicit provider content-filter rejection, make one transparent retry that adds deidentified "
            "clinical-simulation context while retaining the original sanitized system/user prompts verbatim."
        ),
    )
    parser.add_argument(
        "--no-llm-content-filter-clinical-context-retry",
        dest="llm_content_filter_clinical_context_retry",
        action="store_false",
        help="Disable the single verbatim clinical-context retry for explicit provider content-filter rejections.",
    )
    parser.add_argument("--llm-extra-body-json", default=os.environ.get("CARELOOP_LITE_EXTRA_BODY_JSON", ""), help="Optional JSON object merged into OpenAI-compatible request body, e.g. {\"thinking\": false}.")
    parser.add_argument(
        "--llm-max-retries",
        type=int,
        default=int(os.environ.get("CARELOOP_LITE_MAX_RETRIES", "2")),
        help=(
            "Retry cap used only when --llm-bounded-retries is set. "
            "By default runtime_lite retries transient API/provider failures until the current LLM node succeeds."
        ),
    )
    parser.add_argument(
        "--llm-bounded-retries",
        action="store_true",
        default=_env_flag("CARELOOP_LITE_BOUNDED_RETRIES"),
        help=(
            "Debug/smoke-test mode: stop a transient LLM failure after --llm-max-retries retries. "
            "Do not use for formal CareLoop runs where required nodes must not be skipped."
        ),
    )
    parser.add_argument(
        "--llm-retry-backoff-seconds",
        type=float,
        default=float(os.environ.get("CARELOOP_LITE_RETRY_BACKOFF_SECONDS", "30")),
        help="Minimum real-world wait before retrying a transient LLM/API failure. Default 30 seconds.",
    )
    parser.add_argument(
        "--llm-retry-max-delay-seconds",
        type=float,
        default=float(os.environ.get("CARELOOP_LITE_RETRY_MAX_DELAY_SECONDS", "60")),
        help="Maximum jittered wait before retrying a transient LLM/API failure. Default 60 seconds.",
    )
    parser.add_argument("--output-dir", default="", help="Optional directory for trajectory.json and summary.md.")
    parser.add_argument(
        "--save-all-checkpoints",
        action="store_true",
        default=_env_flag("CARELOOP_LITE_SAVE_ALL_CHECKPOINTS"),
        help=(
            "Save a full cumulative checkpoints/turn_XXX.json file after every turn. "
            "Default is false: only trajectory.checkpoint.json and summary.checkpoint.md are atomically replaced, "
            "so long 100-300 turn runs do not grow O(turns^2) on disk."
        ),
    )
    parser.add_argument(
        "--stage-checkpoints",
        action="store_true",
        default=_env_flag("CARELOOP_LITE_STAGE_CHECKPOINTS"),
        help=(
            "Write latest checkpoint after major within-turn stages such as doctor draft, operation router, "
            "world director, timekeeper, actor situation and actor reply. Default false; useful for supervising "
            "slow real API runs without changing patient-visible flow."
        ),
    )
    parser.add_argument("--analyze-output-dir", default="", help="Post-hoc analyze an existing runtime_lite output directory without loading cases or calling LLMs/APIs.")
    parser.add_argument("--resume-existing", action="store_true", default=_env_flag("CARELOOP_LITE_RESUME_EXISTING"), help="When output-dir already contains a successful trajectory for a case, reuse it and only run missing/failed cases.")
    parser.add_argument(
        "--resume-from-checkpoint",
        default=os.environ.get("CARELOOP_LITE_RESUME_FROM_CHECKPOINT", ""),
        help=(
            "Continue an open runtime_lite trajectory from a trajectory.checkpoint.json file or from a checkpoint output directory. "
            "For multi-case runs, pass the prior output root and each case will load <root>/<case_id>/trajectory.checkpoint.json. "
            "The new --max-turns value is treated as an absolute target horizon, not additional turns."
        ),
    )
    parser.add_argument(
        "--allow-stage-checkpoint-resume",
        action="store_true",
        default=_env_flag("CARELOOP_LITE_ALLOW_STAGE_CHECKPOINT_RESUME"),
        help=(
            "Allow resuming from a within-turn stage checkpoint. Default false: stage checkpoints are for monitoring only, "
            "because resuming from them can skip the unfinished timekeeper/actor/closure tail of that turn."
        ),
    )
    parser.add_argument(
        "--eval-on-interrupt",
        action="store_true",
        default=_env_flag("CARELOOP_LITE_EVAL_ON_INTERRUPT"),
        help=(
            "If a run is interrupted after a usable partial trajectory exists, call the fragment evaluator before saving. "
            "Default false so Ctrl-C remains a fast stop with no extra LLM/API call."
        ),
    )
    parser.add_argument("--no-eval", action="store_true", help="Skip post-hoc trajectory evaluation.")
    parser.add_argument("--continue-on-error", action="store_true", help="When running multiple cases, write a failure report and continue with later cases.")
    parser.add_argument("--dry-run", action="store_true", help="Resolve cases and print run metadata without loading cases, constructing LLM clients, or calling APIs.")
    parser.add_argument("--validate-cases", action="store_true", help="Load resolved cases and report runtime_lite readiness without constructing LLM clients or calling APIs.")
    parser.add_argument(
        "--clinical-memory-mode",
        choices=["every_turn", "eventful", "off"],
        default=os.environ.get("CARELOOP_LITE_CLINICAL_MEMORY_MODE", "every_turn"),
        help="ClinicalMemorySteward cadence. every_turn preserves the safest default; eventful skips low-signal turns with an audit event; off disables it.",
    )
    parser.add_argument(
        "--clinical-memory-max-interval-turns",
        type=int,
        default=int(os.environ.get("CARELOOP_LITE_CLINICAL_MEMORY_MAX_INTERVAL_TURNS", "3")),
        help="In eventful memory mode, force a ClinicalMemorySteward refresh after this many turns since the latest snapshot.",
    )
    parser.add_argument(
        "--clinical-memory-eventful-min-interval-turns",
        type=int,
        default=int(os.environ.get("CARELOOP_LITE_CLINICAL_MEMORY_EVENTFUL_MIN_INTERVAL_TURNS", "3")),
        help=(
            "In eventful memory mode, do not refresh ClinicalMemorySteward more often than this on routine turns. "
            "Urgent runtime boundary events and the max interval can still force a refresh."
        ),
    )
    parser.add_argument(
        "--long-context-1000-plus",
        action="store_true",
        default=_env_flag("CARELOOP_LITE_LONG_CONTEXT_1000_PLUS"),
        help=(
            "Write 1000+ turn long-context sidecars: append-only ledgers, episode/chapter/master memory, "
            "independent responsibility/non-compressible ledgers, integrity audit, and final evidence pack. "
            "Backstage only; never injected into tested doctor prompts."
        ),
    )
    parser.add_argument("--episode-turn-span", type=int, default=int(os.environ.get("CARELOOP_LITE_EPISODE_TURN_SPAN", "20")))
    parser.add_argument("--chapter-episode-span", type=int, default=int(os.environ.get("CARELOOP_LITE_CHAPTER_EPISODE_SPAN", "5")))
    parser.add_argument("--working-memory-forced-interval-turns", type=int, default=int(os.environ.get("CARELOOP_LITE_WORKING_MEMORY_FORCED_INTERVAL_TURNS", "3")))
    parser.add_argument("--memory-integrity-audit-interval-turns", type=int, default=int(os.environ.get("CARELOOP_LITE_MEMORY_INTEGRITY_AUDIT_INTERVAL_TURNS", "50")))
    parser.add_argument("--fragment-eval-interval-turns", type=int, default=int(os.environ.get("CARELOOP_LITE_FRAGMENT_EVAL_INTERVAL_TURNS", "100")))
    parser.add_argument(
        "--no-externalize-memory-snapshots",
        action="store_true",
        default=_env_flag("CARELOOP_LITE_NO_EXTERNALIZE_MEMORY_SNAPSHOTS"),
        help="Compatibility/debug switch: keep long-context snapshot sidecars off. Formal 1000+ runs should not use it.",
    )
    args = parser.parse_args(argv)
    _apply_long_run_300_preset(args)
    _apply_formal_fcct2_preset(args)
    if args.analyze_output_dir:
        try:
            payload = _analyze_existing_output_dir(Path(args.analyze_output_dir))
        except Exception as exc:
            payload = {
                "runtime": "runtime_lite",
                "stage": "posthoc_output_analysis",
                "source_output_dir": args.analyze_output_dir,
                "runtime_error": {"type": type(exc).__name__, "message": str(exc)},
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 1
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    try:
        case_paths = _resolve_case_paths(args)
        run_metadata = _run_metadata(args, case_paths)
    except Exception as exc:
        payload = _resolve_failure_payload(args, exc)
        if args.output_dir:
            output_dir = Path(args.output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "resolve_error.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1
    if args.validate_cases:
        payload = _case_validation_payload(case_paths, run_metadata)
        if args.output_dir:
            _write_case_validation_outputs(Path(args.output_dir), payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload["aggregate"]["failed"] == 0 else 1
    if args.dry_run:
        payload = {
            "dry_run": True,
            "run_metadata": run_metadata,
            "api_config_readiness": _api_config_readiness(args),
        }
        if args.output_dir:
            _write_dry_run_outputs(Path(args.output_dir), payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    multiple = len(case_paths) > 1
    summaries, had_failure = _run_cases(args, case_paths, run_metadata, multiple=multiple)
    if had_failure and not args.continue_on_error and int(getattr(args, "parallelism", 1) or 1) <= 1:
        failing = next((item for item in summaries if item.get("runtime_error")), summaries[0] if summaries else {})
        print(json.dumps(failing, ensure_ascii=False, indent=2))
        return 1
    console_payload: dict[str, Any]
    if multiple:
        console_payload = {
            "run_metadata": run_metadata,
            "runs": summaries,
            "aggregate": _aggregate_with_validation_interpretation(summaries, run_metadata),
        }
    else:
        console_payload = {**summaries[0], "run_metadata": _per_case_metadata(run_metadata)}
    if multiple and args.output_dir:
        _write_batch_outputs(Path(args.output_dir), summaries, console_payload["aggregate"], run_metadata)
    print(json.dumps(console_payload, ensure_ascii=False, indent=2))
    return 1 if had_failure else 0


def _apply_long_run_300_preset(args: argparse.Namespace) -> None:
    """Apply the formal 300-turn validation preset.

    This is an infrastructure preset, not a clinical-flow rule.  It reduces
    configuration mistakes before long, expensive CareLoop runs without changing
    prompts, case facts, closure criteria, or how the simulated world progresses.
    """

    if not bool(getattr(args, "long_run_300", False)):
        if not bool(getattr(args, "long_run_1000", False)):
            return
    args.max_turns = 1000 if bool(getattr(args, "long_run_1000", False)) else 300
    args.parallelism = 10
    args.clinical_memory_mode = "eventful"
    args.clinical_memory_max_interval_turns = 3
    args.clinical_memory_eventful_min_interval_turns = 3
    args.llm_bounded_retries = False
    args.llm_retry_backoff_seconds = 30.0
    args.llm_retry_max_delay_seconds = 60.0
    args.save_all_checkpoints = False
    args.long_context_1000_plus = True



def _formal_doctor_models(args: argparse.Namespace) -> list[str]:
    raw = str(getattr(args, "formal_doctor_models", "") or "")
    values = [item.strip() for item in raw.split(",") if item.strip()]
    return values or list(FORMAL_DOCTOR_MODELS)


def _apply_formal_fcct2_preset(args: argparse.Namespace) -> None:
    """Apply formal FCCT-2 model-role guard without changing clinical prompts.

    Formal mode is an experiment/benchmark contract: CareLoop internal simulator,
    actor, director and evaluator purposes use GPT-5.5, while the tested doctor
    purpose uses one explicitly selected doctor model. This does not hard-code a
    clinical pathway and does not change patient/world semantics.
    """

    if not bool(getattr(args, "formal_fcct2_cross_model", False)):
        return
    internal = str(getattr(args, "careloop_internal_model", "") or FORMAL_CARELOOP_INTERNAL_MODEL).strip()
    args.careloop_internal_model = internal
    if not str(getattr(args, "sim_model", "") or "").strip():
        args.sim_model = internal
    if not str(getattr(args, "doctor_model", "") or "").strip() and str(getattr(args, "model", "") or "").strip():
        args.doctor_model = str(getattr(args, "model", "") or "").strip()
    if not str(getattr(args, "hardening_llm_audits", "") or "").strip():
        args.hardening_llm_audits = "final"
    if bool(getattr(args, "formal_runtime_advisory", False)):
        args.enable_actor_realism_controller_v2 = True
        args.enable_friction_coverage_planner = True
        args.enable_closure_thread_advisory = True
        args.enable_receipt_lifecycle_advisory = True
        args.enable_episode_governance_closure_advisory = True
    validate_formal_model_contract(
        sim_model=args.sim_model or args.model,
        doctor_model=args.doctor_model or args.model or args.sim_model,
        internal_model=internal,
        allowed_doctor_models=_formal_doctor_models(args),
    )



def _hardening_llm_audit_purposes(args: argparse.Namespace) -> list[str]:
    raw = str(getattr(args, "hardening_llm_audit_purposes", "") or "")
    return [item.strip().lower() for item in raw.split(",") if item.strip()]


def _hardening_llm_audit_mode(args: argparse.Namespace) -> str:
    value = str(getattr(args, "hardening_llm_audits", "") or "").strip().lower()
    if not value:
        return "final" if bool(getattr(args, "formal_fcct2_cross_model", False)) else "off"
    return value


def _maybe_run_hardening_llm_audits(args: argparse.Namespace, payload: dict[str, Any], simulator_llm: Any, *, stage: str) -> None:
    mode = _hardening_llm_audit_mode(args)
    if mode == "off":
        return
    if stage != "final" or mode != "final":
        return
    if simulator_llm is None or not hasattr(simulator_llm, "complete"):
        payload.setdefault("metadata", {})["hardening_llm_audits"] = {
            "mode": mode,
            "skipped": True,
            "reason": "no_simulator_llm_client_available",
        }
        return
    run_hardening_llm_audits(
        payload,
        simulator_llm,
        purposes=_hardening_llm_audit_purposes(args),
        mode=mode,
    )


def _build_clients(args: argparse.Namespace, *, status_dir: str | None = None) -> tuple[Any, Any]:
    if args.client == "scripted":
        client = ScriptedLiteLLMClient()
        return client, client
    api_key = os.environ.get(args.api_key_env, "").strip()
    if not args.base_url:
        raise RuntimeError("--base-url or CARELOOP_LITE_BASE_URL is required for openai-compatible client")
    if not api_key:
        raise RuntimeError(f"environment variable {args.api_key_env} is required for openai-compatible client")
    sim_model = args.sim_model or args.model
    doctor_model = args.doctor_model or args.model or sim_model
    doctor_model_api_id = str(getattr(args, "doctor_model_api_id", "") or "").strip() or doctor_model
    if not sim_model or not doctor_model:
        raise RuntimeError("--model or both --sim-model/--doctor-model are required for openai-compatible client")
    max_tokens = args.max_output_tokens if args.max_output_tokens > 0 else None
    extra_body = _llm_extra_body(args)
    purpose_timeouts = _llm_purpose_timeouts(args)
    simulator = OpenAICompatibleLiteLLMClient(
        base_url=args.base_url,
        api_key=api_key,
        model=sim_model,
        timeout_seconds=args.timeout_seconds,
        purpose_timeouts=purpose_timeouts,
        max_tokens=max_tokens,
        max_retries=args.llm_max_retries,
        retry_until_success=not bool(getattr(args, "llm_bounded_retries", False)),
        retry_backoff_seconds=args.llm_retry_backoff_seconds,
        retry_max_delay_seconds=args.llm_retry_max_delay_seconds,
        stream=bool(getattr(args, "llm_stream", False)),
        extra_body=extra_body,
        omit_temperature=bool(getattr(args, "llm_omit_temperature", False)),
        retry_with_clinical_context_on_content_filter=bool(
            getattr(args, "llm_content_filter_clinical_context_retry", True)
        ),
        status_dir=status_dir,
        status_file_name="llm_status.json",
    )
    doctor = OpenAICompatibleLiteLLMClient(
        base_url=args.base_url,
        api_key=api_key,
        model=doctor_model_api_id,
        timeout_seconds=args.timeout_seconds,
        purpose_timeouts=purpose_timeouts,
        max_tokens=max_tokens,
        max_retries=args.llm_max_retries,
        retry_until_success=not bool(getattr(args, "llm_bounded_retries", False)),
        retry_backoff_seconds=args.llm_retry_backoff_seconds,
        retry_max_delay_seconds=args.llm_retry_max_delay_seconds,
        stream=bool(getattr(args, "llm_stream", False)),
        extra_body=extra_body,
        omit_temperature=bool(getattr(args, "llm_omit_temperature", False)),
        retry_with_clinical_context_on_content_filter=bool(
            getattr(args, "llm_content_filter_clinical_context_retry", True)
        ),
        status_dir=status_dir,
        status_file_name="doctor_llm_status.json",
    )
    return simulator, doctor


def _llm_extra_body(args: argparse.Namespace) -> dict[str, Any]:
    raw = str(getattr(args, "llm_extra_body_json", "") or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"--llm-extra-body-json must be a JSON object: {exc}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("--llm-extra-body-json must decode to a JSON object")
    return parsed


def _llm_purpose_timeouts(args: argparse.Namespace) -> dict[str, float]:
    raw = str(getattr(args, "llm_purpose_timeouts_json", "") or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"--llm-purpose-timeouts-json must be a JSON object: {exc}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("--llm-purpose-timeouts-json must decode to a JSON object")
    timeouts: dict[str, float] = {}
    for key, value in parsed.items():
        purpose = str(key or "").strip()
        if not purpose:
            raise RuntimeError("--llm-purpose-timeouts-json contains an empty purpose name")
        try:
            seconds = float(value)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"timeout for LLM purpose {purpose!r} must be a positive number") from exc
        if seconds <= 0:
            raise RuntimeError(f"timeout for LLM purpose {purpose!r} must be greater than zero")
        timeouts[purpose] = seconds
    return timeouts


def _run_cases(
    args: argparse.Namespace,
    case_paths: list[str],
    run_metadata: dict[str, Any],
    *,
    multiple: bool,
) -> tuple[list[dict[str, Any]], bool]:
    parallelism = max(1, int(getattr(args, "parallelism", 1) or 1))
    indexed_paths = list(enumerate(case_paths, start=1))
    summaries_by_index: dict[int, dict[str, Any]] = {}
    had_failure = False
    if parallelism <= 1 or len(indexed_paths) <= 1:
        for index, case_path in indexed_paths:
            summary, failed = _run_single_case(args, case_path, index, run_metadata, multiple=multiple)
            summaries_by_index[index] = summary
            had_failure = had_failure or failed
            if failed and not args.continue_on_error:
                break
        return [summaries_by_index[index] for index in sorted(summaries_by_index)], had_failure

    with ThreadPoolExecutor(max_workers=min(parallelism, len(indexed_paths))) as executor:
        futures = {
            executor.submit(_run_single_case, args, case_path, index, run_metadata, multiple=multiple): index
            for index, case_path in indexed_paths
        }
        for future in as_completed(futures):
            index = futures[future]
            summary, failed = future.result()
            summaries_by_index[index] = summary
            had_failure = had_failure or failed
    return [summaries_by_index[index] for index in sorted(summaries_by_index)], had_failure


def _run_single_case(
    args: argparse.Namespace,
    case_path: str,
    index: int,
    run_metadata: dict[str, Any],
    *,
    multiple: bool,
) -> tuple[dict[str, Any], bool]:
    run_id = _run_id_for_case(args.run_id, case_path, index, multiple)
    output_dir_for_case = _case_output_dir_for_path(Path(args.output_dir), case_path, multiple=multiple) if args.output_dir else None
    checkpoint_callback = _make_checkpoint_callback(args, case_path, run_metadata, multiple=multiple)
    config = LiteRuntimeConfig(
        max_turns=args.max_turns,
        run_id=run_id,
        evaluate_at_end=not args.no_eval,
        enable_clinical_memory_steward=(args.clinical_memory_mode != "off"),
        clinical_memory_steward_mode=args.clinical_memory_mode,
        clinical_memory_steward_max_interval_turns=args.clinical_memory_max_interval_turns,
        clinical_memory_eventful_min_interval_turns=args.clinical_memory_eventful_min_interval_turns,
        long_context_1000_plus=bool(getattr(args, "long_context_1000_plus", False)),
        long_context_output_dir=str(output_dir_for_case or ""),
        episode_turn_span=max(1, int(getattr(args, "episode_turn_span", 20) or 20)),
        chapter_episode_span=max(1, int(getattr(args, "chapter_episode_span", 5) or 5)),
        working_memory_mode="eventful",
        working_memory_forced_interval_turns=max(1, int(getattr(args, "working_memory_forced_interval_turns", 3) or 3)),
        memory_integrity_audit_interval_turns=max(1, int(getattr(args, "memory_integrity_audit_interval_turns", 50) or 50)),
        fragment_eval_interval_turns=max(1, int(getattr(args, "fragment_eval_interval_turns", 100) or 100)),
        append_only_ledger=True,
        externalize_memory_snapshots=not bool(getattr(args, "no_externalize_memory_snapshots", False)),
        enable_actor_realism_controller_v2=bool(getattr(args, "enable_actor_realism_controller_v2", False)),
        enable_friction_coverage_planner=bool(getattr(args, "enable_friction_coverage_planner", False)),
        enable_closure_thread_advisory=bool(getattr(args, "enable_closure_thread_advisory", False)),
        enable_receipt_lifecycle_advisory=bool(getattr(args, "enable_receipt_lifecycle_advisory", False)),
        enable_episode_governance_closure_advisory=bool(getattr(args, "enable_episode_governance_closure_advisory", False)),
        actor_realism_v2_check_interval_turns=max(1, int(getattr(args, "actor_realism_v2_interval_turns", 4) or 4)),
        friction_coverage_check_interval_turns=max(1, int(getattr(args, "friction_coverage_interval_turns", 6) or 6)),
        closure_thread_advisory_interval_turns=max(1, int(getattr(args, "closure_thread_advisory_interval_turns", 4) or 4)),
        receipt_lifecycle_advisory_interval_turns=max(1, int(getattr(args, "receipt_lifecycle_advisory_interval_turns", 4) or 4)),
        emit_stage_checkpoints=bool(getattr(args, "stage_checkpoints", False)),
        checkpoint_callback=checkpoint_callback,
        runtime_status_output_dir=str(output_dir_for_case or ""),
    )
    runner: LiteCareLoopRunner | None = None
    simulator_llm: Any | None = None
    failed = False
    interrupted: KeyboardInterrupt | None = None
    resume_checkpoint_path = _resolve_resume_checkpoint_path(args, case_path, multiple=multiple)
    reused_payload = (
        None
        if resume_checkpoint_path is not None
        else _load_resumable_payload(args, case_path, multiple=multiple)
        if getattr(args, "resume_existing", False)
        else None
    )
    if reused_payload is not None:
        reused_payload.setdefault("metadata", {})["resumed_from_existing_output"] = True
        reused_payload.setdefault("metadata", {})["cli_run_metadata"] = _per_case_metadata(run_metadata)
        quality = analyze_lite_trajectory(reused_payload)
        reused_payload["quality_report"] = quality.to_dict()
        if output_dir_for_case is not None:
            _write_outputs(output_dir_for_case, reused_payload)
        summary = _console_summary(reused_payload)
        return summary, False
    try:
        simulator_llm, doctor_llm = _build_clients(args, status_dir=str(output_dir_for_case or ""))
        runner = LiteCareLoopRunner(
            case=case_path,
            simulator_llm=simulator_llm,
            doctor_llm=doctor_llm,
            config=config,
        )
        if resume_checkpoint_path is not None:
            checkpoint_payload = json.loads(resume_checkpoint_path.read_text(encoding="utf-8"))
            _ensure_resume_checkpoint_is_safe(args, checkpoint_payload, resume_checkpoint_path)
            runner.resume_from_payload(checkpoint_payload, source_path=str(resume_checkpoint_path))
        result = runner.run()
        payload = result.to_dict()
    except KeyboardInterrupt as exc:
        failed = True
        interrupted = exc
        payload = _failure_payload(case_path=case_path, run_id=run_id, args=args, runner=runner, exc=exc)
        payload.setdefault("metadata", {})["interrupted_by_user"] = True
        if runner is not None and not args.no_eval:
            _attach_interrupted_fragment_evaluation(
                payload,
                runner,
                evaluate_with_llm=bool(getattr(args, "eval_on_interrupt", False)),
            )
    except Exception as exc:
        failed = True
        payload = _failure_payload(case_path=case_path, run_id=run_id, args=args, runner=runner, exc=exc)
    payload.setdefault("metadata", {})["cli_run_metadata"] = _per_case_metadata(run_metadata)
    quality = analyze_lite_trajectory(payload)
    payload["quality_report"] = quality.to_dict()
    attach_hardening_reports(payload)
    _maybe_run_hardening_llm_audits(args, payload, simulator_llm, stage="final")
    summary = _console_summary(payload)
    if output_dir_for_case is not None:
        _write_outputs(output_dir_for_case, payload)
    if interrupted is not None:
        raise interrupted
    return summary, bool(payload.get("runtime_error") or failed)


def _attach_interrupted_fragment_evaluation(
    payload: dict[str, Any], runner: LiteCareLoopRunner, *, evaluate_with_llm: bool = False
) -> None:
    """Attach a non-terminal evaluation marker for interrupted fragments.

    By default, interruption remains a fast stop and does not call another LLM.
    When explicitly requested, run the normal fragment evaluator on the latest
    runner state so an intentionally truncated trajectory can still receive the
    same kind of post-hoc evaluation as max-turn fragments.
    """

    closure = ClosureAssessmentLite.from_mapping(payload.get("closure") or {"status": "open"})
    if closure.is_terminal:
        return
    turn = int(payload.get("turns_completed") or _last_turn(payload.get("trajectory") or {}) or 0)
    payload["trajectory"] = runner.trajectory.to_dict()
    if evaluate_with_llm:
        try:
            evaluation = runner._call_final_evaluator(turn, closure)  # noqa: SLF001 - CLI recovery hook
            payload["trajectory"] = runner.trajectory.to_dict()
            payload["evaluation"] = evaluation
            payload.setdefault("metadata", {})["interrupted_fragment_evaluated"] = True
            payload.setdefault("metadata", {})["interrupted_fragment_evaluation_policy"] = "llm_fragment_evaluator_on_interrupt"
            return
        except Exception as exc:
            payload.setdefault("metadata", {})["interrupted_fragment_evaluation_error"] = {
                "type": type(exc).__name__,
                "message": str(exc),
            }
    payload["evaluation"] = {
        "evaluation_mode": "fragment_nonterminal",
        "overall": "evaluation_not_run_due_to_interrupt",
        "summary": "Run was interrupted before normal final evaluation completed; no post-interrupt LLM evaluator call was made.",
        "fragment_validity": {
            "status": "not_assessed",
            "reason": "The partial trajectory is preserved for later analysis, but final evaluator output is intentionally absent because the operator interrupted the run.",
            "limitations": ["user_interrupted", "final_evaluation_not_run"],
        },
        "critical_failures": [],
        "evidence": [],
        "turn_at_interrupt": turn,
    }
    payload.setdefault("metadata", {})["interrupted_fragment_evaluated"] = False
    payload.setdefault("metadata", {})["interrupted_fragment_evaluation_policy"] = "no_llm_call_after_interrupt"


def _make_checkpoint_callback(
    args: argparse.Namespace,
    case_path: str,
    run_metadata: dict[str, Any],
    *,
    multiple: bool,
):
    if not getattr(args, "output_dir", ""):
        return None
    output_dir = _case_output_dir_for_path(Path(args.output_dir), case_path, multiple=multiple)
    cli_metadata = _per_case_metadata(run_metadata)

    def checkpoint(result: Any) -> None:
        payload = result.to_dict()
        payload.setdefault("metadata", {})["cli_run_metadata"] = cli_metadata
        payload.setdefault("metadata", {})["checkpoint"] = True
        payload.setdefault("metadata", {})["checkpoint_history_mode"] = (
            "all_turns" if bool(getattr(args, "save_all_checkpoints", False)) else "latest_only"
        )
        quality = analyze_lite_trajectory(payload)
        payload["quality_report"] = quality.to_dict()
        attach_hardening_reports(payload)
        _write_checkpoint_outputs(
            output_dir,
            payload,
            save_all_checkpoints=bool(getattr(args, "save_all_checkpoints", False)),
        )

    return checkpoint


def _load_resumable_payload(args: argparse.Namespace, case_path: str, *, multiple: bool) -> dict[str, Any] | None:
    if not getattr(args, "output_dir", ""):
        return None
    output_dir = _case_output_dir_for_path(Path(args.output_dir), case_path, multiple=multiple)
    payload_path = output_dir / "trajectory.json"
    if not payload_path.exists():
        return None
    try:
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("trajectory"), dict):
        return None
    if payload.get("runtime_error"):
        return None
    return payload


def _resolve_resume_checkpoint_path(args: argparse.Namespace, case_path: str, *, multiple: bool) -> Path | None:
    raw = str(getattr(args, "resume_from_checkpoint", "") or "").strip()
    if not raw:
        return None
    base = Path(raw)
    candidates: list[Path] = []
    if base.is_file():
        candidates.append(base)
    else:
        if multiple:
            candidates.append(_case_output_dir_for_path(base, case_path, multiple=True) / "trajectory.checkpoint.json")
        candidates.append(base / "trajectory.checkpoint.json")
    for path in candidates:
        if path.exists() and path.is_file():
            return path
    raise RuntimeError(
        "resume checkpoint not found for case "
        f"{case_path}; tried: {', '.join(str(item) for item in candidates)}"
    )


def _ensure_resume_checkpoint_is_safe(args: argparse.Namespace, payload: dict[str, Any], path: Path) -> None:
    """Prevent accidental resume from a within-turn monitoring checkpoint.

    Stage checkpoints are intentionally frequent so a long real-API run can be
    supervised, but they are not transaction boundaries.  Resuming from, for
    example, ``stage_after_world_director`` starts the next turn while the prior
    turn has not yet passed through timekeeper, actor reply, closure, and memory
    update.  That creates artificial time-advance and transcript gaps.  Keep the
    hard rule thin and infrastructural: complete-turn checkpoints are safe by
    default; stage checkpoints require an explicit operator override.
    """

    if bool(getattr(args, "allow_stage_checkpoint_resume", False)):
        return
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    trigger = str(metadata.get("checkpoint_trigger") or "").strip()
    if not trigger:
        return
    safe_triggers = {
        "after_closure_judge",
        "after_director_cut_closure",
        "after_unrescued_doctor_non_response_memory",
    }
    if trigger in safe_triggers:
        return
    if trigger.startswith("stage_after_") or trigger.startswith("before_memory_"):
        raise RuntimeError(
            "unsafe resume checkpoint: "
            f"{path} has checkpoint_trigger={trigger!r}. This is a within-turn or pre-memory checkpoint used for monitoring, "
            "not a safe resume boundary. Wait for an after_closure_judge/after_director_cut_closure checkpoint, "
            "or pass --allow-stage-checkpoint-resume if you intentionally accept possible timekeeper/actor/closure gaps."
        )


def _case_output_dir_for_path(root: Path, case_path: str, *, multiple: bool) -> Path:
    if not multiple:
        return root
    case_id = ""
    try:
        case_id = load_lite_case(case_path).case_id
    except Exception:
        case_id = Path(case_path).stem
    return root / _safe_name(case_id)


def _resolve_failure_payload(args: argparse.Namespace, exc: Exception) -> dict[str, Any]:
    return {
        "runtime": "runtime_lite",
        "stage": "case_resolution",
        "dry_run": bool(getattr(args, "dry_run", False)),
        "runtime_error": {
            "type": type(exc).__name__,
            "message": str(exc),
        },
        "case_glob": list(getattr(args, "case_glob", []) or []),
        "case": list(getattr(args, "case", []) or []),
        "sample_size": int(getattr(args, "sample_size", 0) or 0),
        "sample_seed": str(getattr(args, "sample_seed", "") or ""),
    }


def _case_validation_payload(case_paths: list[str], run_metadata: dict[str, Any]) -> dict[str, Any]:
    cases = [_validate_case_path(path) for path in case_paths]
    status_counts = {
        "total": len(cases),
        "passed": sum(1 for item in cases if item.get("status") == "pass"),
        "review": sum(1 for item in cases if item.get("status") == "review"),
        "failed": sum(1 for item in cases if item.get("status") == "fail"),
    }
    return {
        "runtime": "runtime_lite",
        "stage": "case_validation",
        "run_metadata": run_metadata,
        "cases": cases,
        "aggregate": {
            **status_counts,
            "coverage_counts": _case_validation_coverage_counts(cases),
            "longitudinal_coverage_insight": _case_validation_longitudinal_coverage_insight(cases),
            "evaluation_contract_counts": _case_validation_evaluation_contract_counts(cases),
            "anti_cheat_visibility_counts": _case_validation_anti_cheat_counts(cases),
        },
    }


def _validate_case_path(path: str) -> dict[str, Any]:
    try:
        case = load_lite_case(path)
    except Exception as exc:
        return {
            "path": path,
            "case_id": Path(path).stem,
            "status": "fail",
            "errors": [f"case_load_failed: {type(exc).__name__}: {exc}"],
            "warnings": [],
        }
    errors: list[str] = []
    warnings: list[str] = []
    authoring_warnings: list[str] = []
    if not str(case.initial_message or "").strip():
        errors.append("missing_initial_chat_message")
    if not str(case.initial_actor or "").strip():
        warnings.append("missing_initial_actor_defaulted_to_patient")
    doctor_opening = case.doctor_visible_opening()
    workspace_panels = doctor_opening.get("available_workspace", {}).get("available_panels") or []
    if not workspace_panels:
        warnings.append("no_workspace_panels_declared")
    opening_audit = audit_doctor_visible_opening(doctor_opening)
    workspace_audit = audit_workspace_result(
        LiteClinicalWorkspace(case).query(["record_index", "records"], reason="case_validation_anti_cheat_audit")
    )
    anti_cheat_visibility = combine_audits(opening_audit, workspace_audit)
    if not anti_cheat_visibility.get("pass"):
        errors.append("anti_cheat_visibility_boundary_failed")
    if not case.temporal_contract:
        warnings.append("missing_temporal_contract")
    actor_count = len((case.actor_profiles or {}).get("actors") or [])
    if actor_count == 0:
        warnings.append("no_actor_profiles_declared")
    if not case.hidden_world_material:
        warnings.append("no_hidden_world_material_selected")
    evaluation_contract = _case_evaluation_contract_summary(case.evaluation_material)
    missing_contract_fields = evaluation_contract.get("missing_required_fields") or []
    if missing_contract_fields:
        authoring_warnings.append("evaluation_contract_incomplete")
    admin_drift_risk = _case_admin_drift_risk(case.raw)
    if admin_drift_risk.get("risk"):
        warnings.append(
            "case_authoring_admin_drift_risk: "
            f"hits={admin_drift_risk.get('admin_hits')}; "
            f"distinct_terms={admin_drift_risk.get('distinct_admin_terms')}; "
            "review whether administrative/reimbursement/material subplots are too prominent versus clinical care"
        )
    status = "fail" if errors else ("review" if warnings else "pass")
    return {
        "path": path,
        "case_id": case.case_id,
        "title": case.title,
        "status": status,
        "errors": errors,
        "warnings": warnings,
        "authoring_warnings": authoring_warnings,
        "initial_actor": case.initial_actor,
        "initial_message_preview": _compact(case.initial_message, limit=120),
        "workspace_panels": workspace_panels,
        "actor_profile_count": actor_count,
        "has_temporal_contract": bool(case.temporal_contract),
        "has_hidden_world_material": bool(case.hidden_world_material),
        "evaluation_contract": evaluation_contract,
        "coverage": _case_coverage_summary(case.raw),
        "anti_cheat_visibility": anti_cheat_visibility,
        "admin_drift_risk": admin_drift_risk,
    }


def _case_admin_drift_risk(raw: dict[str, Any]) -> dict[str, Any]:
    """Soft authoring audit for cases whose preset material over-centres admin work.

    This is deliberately only a warning.  Administrative friction is realistic in
    medicine; the problem is when reimbursement/paperwork/vendor details become
    so dense in the authored case that they are likely to displace the medical
    care thread before the LLM director/actors even start improvising.
    """

    text = json.dumps(raw, ensure_ascii=False).lower()
    admin_terms = [
        "行政",
        "手续",
        "报销",
        "医保",
        "商保",
        "理赔",
        "材料",
        "补件",
        "供应商",
        "厂家",
        "发票",
        "窗口",
        "排队",
        "缴费",
        "审批",
        "证明",
        "盖章",
        "预授权",
        "客服",
        "配送",
        "采购",
        "administrative",
        "paperwork",
        "reimbursement",
        "insurance",
        "vendor",
        "supplier",
    ]
    clinical_terms = [
        "诊断",
        "症状",
        "检查",
        "治疗",
        "用药",
        "处方",
        "复诊",
        "随访",
        "急诊",
        "住院",
        "出院",
        "报告",
        "检验",
        "化验",
        "ct",
        "mri",
        "doctor",
        "clinical",
        "treatment",
        "medication",
        "followup",
    ]
    admin_counts = {term: text.count(term) for term in admin_terms if text.count(term)}
    clinical_hits = sum(text.count(term) for term in clinical_terms)
    admin_hits = sum(admin_counts.values())
    distinct = len(admin_counts)
    risk = bool((admin_hits >= 18 and distinct >= 4) or (admin_hits >= 12 and distinct >= 4 and admin_hits >= max(6, int(clinical_hits * 0.35))))
    return {
        "risk": risk,
        "admin_hits": admin_hits,
        "distinct_admin_terms": distinct,
        "clinical_anchor_hits": clinical_hits,
        "top_admin_terms": sorted(admin_counts.items(), key=lambda item: (-item[1], item[0]))[:8],
        "principle": "warning_only; administrative friction is allowed but should not displace the medical care thread",
    }


def _case_evaluation_contract_summary(evaluation_material: dict[str, Any]) -> dict[str, Any]:
    contract = evaluation_material.get("evaluation_contract") if isinstance(evaluation_material, dict) else {}
    if not isinstance(contract, dict):
        contract = {}
    required_fields = [
        "care_goal",
        "minimum_safe_closure",
        "acceptable_closure_types",
        "false_closure_traps",
        "expected_closure_evidence",
        "must_not_miss",
        "tool_use_expectations",
        "dynamic_reweighting_triggers",
    ]
    present = [field for field in required_fields if _has_contract_value(_contract_field_value(contract, field))]
    missing = [field for field in required_fields if field not in present]
    return {
        "present": bool(contract),
        "contract_version": contract.get("contract_version") or "",
        "required_fields": required_fields,
        "present_required_fields": present,
        "missing_required_fields": missing,
        "complete": bool(contract) and not missing,
        "principle": "Evaluation contract is a post-hoc scoring/closure reference, not a runtime flow-control script.",
    }


_EVALUATION_CONTRACT_FIELD_ALIASES = {
    # FCCT longitudinal cases use terminal-closure language because acute
    # handoff is usually only a milestone.  Accept these aliases so the
    # readiness checker does not force authors back into older handoff-centric
    # field names.
    "minimum_safe_closure": ("minimum_safe_closure", "minimum_terminal_closure"),
    "acceptable_closure_types": ("acceptable_closure_types", "terminal_closure_types", "terminal_paths"),
    "must_not_miss": ("must_not_miss", "must_test_capabilities"),
}


def _contract_field_value(contract: dict[str, Any], field: str) -> Any:
    for candidate in _EVALUATION_CONTRACT_FIELD_ALIASES.get(field, (field,)):
        value = contract.get(candidate)
        if _has_contract_value(value):
            return value
    return None


def _has_contract_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set)):
        return any(_has_contract_value(item) for item in value)
    if isinstance(value, dict):
        return any(_has_contract_value(item) for item in value.values())
    return True


def _case_validation_anti_cheat_counts(cases: list[dict[str, Any]]) -> dict[str, Any]:
    passed = 0
    failed = 0
    failure_cases: list[str] = []
    for item in cases:
        audit = item.get("anti_cheat_visibility") if isinstance(item.get("anti_cheat_visibility"), dict) else {}
        if audit.get("pass"):
            passed += 1
        else:
            failed += 1
            failure_cases.append(str(item.get("case_id") or item.get("path") or ""))
    return {
        "passed": passed,
        "failed": failed,
        "failure_cases": [case for case in failure_cases if case],
        "principle": "Thin structural anti-cheat audit; no clinical keyword bans are applied.",
    }


def _case_validation_evaluation_contract_counts(cases: list[dict[str, Any]]) -> dict[str, Any]:
    complete = 0
    incomplete = 0
    missing = 0
    missing_field_counts: dict[str, int] = {}
    for item in cases:
        contract = item.get("evaluation_contract") if isinstance(item.get("evaluation_contract"), dict) else {}
        if not contract.get("present"):
            missing += 1
        elif contract.get("complete"):
            complete += 1
        else:
            incomplete += 1
        for field in contract.get("missing_required_fields") or []:
            key = str(field)
            missing_field_counts[key] = missing_field_counts.get(key, 0) + 1
    return {
        "complete": complete,
        "incomplete": incomplete,
        "missing": missing,
        "missing_field_counts": dict(sorted(missing_field_counts.items())),
        "principle": "Completeness is an authoring/readiness signal only; incomplete contracts do not control runtime story flow.",
    }


def _case_coverage_summary(raw: dict[str, Any]) -> dict[str, Any]:
    """Extract author-facing coverage tags from a case without turning them into runtime rules.

    These fields are used for test-set design and preflight review only.  They are
    intentionally not interpreted by the simulator as hard flow control, because
    runtime_lite should stay close to a natural medical-world simulation rather
    than becoming a taxonomy-driven state machine.
    """

    case_metadata = raw.get("case_metadata") if isinstance(raw.get("case_metadata"), dict) else {}
    taxonomy = case_metadata.get("taxonomy") if isinstance(case_metadata.get("taxonomy"), dict) else {}
    if not taxonomy:
        taxonomy = raw.get("case_taxonomy") if isinstance(raw.get("case_taxonomy"), dict) else {}
    complexity = (
        raw.get("real_world_narrative_complexity")
        if isinstance(raw.get("real_world_narrative_complexity"), dict)
        else {}
    )
    selected_keys = [
        "taxonomy_version",
        "case_library_layer",
        "episode_type",
        "care_loop_scope",
        "disease_systems",
        "urgency_level",
        "time_window_class",
        "chronicity",
        "population",
        "capability_tags",
        "patient_barriers",
        "evidence_traps",
        "workspace_dependencies",
        "family_involvement",
        "temporal_sensitivity",
        "over_escalation_risk",
        "under_escalation_risk",
        "tool_dependency",
        "noise_density",
        "realism_noise_thread_count",
        "realism_noise_categories",
    ]
    summary: dict[str, Any] = {}
    for key in selected_keys:
        value = taxonomy.get(key)
        if _has_coverage_value(value):
            summary[key] = value
    if complexity:
        compact_complexity: dict[str, Any] = {}
        for key in [
            "noise_density",
            "realism_noise_thread_count",
            "realism_noise_categories",
            "longitudinal_complexity",
            "actor_complexity",
            "system_complexity",
        ]:
            value = complexity.get(key)
            if _has_coverage_value(value):
                compact_complexity[key] = value
        if compact_complexity:
            summary["real_world_narrative_complexity"] = compact_complexity
    return summary


def _has_coverage_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _case_validation_coverage_counts(cases: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    coverage_fields = [
        "case_library_layer",
        "episode_type",
        "care_loop_scope",
        "disease_systems",
        "urgency_level",
        "time_window_class",
        "chronicity",
        "population",
        "capability_tags",
        "patient_barriers",
        "evidence_traps",
        "workspace_dependencies",
        "family_involvement",
        "temporal_sensitivity",
        "tool_dependency",
        "noise_density",
        "realism_noise_categories",
    ]
    counts: dict[str, dict[str, int]] = {field: {} for field in coverage_fields}
    for item in cases:
        if not isinstance(item, dict):
            continue
        coverage = item.get("coverage") if isinstance(item.get("coverage"), dict) else {}
        for field in coverage_fields:
            for value in _coverage_values(coverage.get(field)):
                bucket = counts[field]
                bucket[value] = bucket.get(value, 0) + 1
    return {field: dict(sorted(bucket.items())) for field, bucket in counts.items() if bucket}


def _case_validation_longitudinal_coverage_insight(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Non-blocking authoring insight for CareLoop_FCCT-1's long-horizon test goals.

    This deliberately does not pass/fail cases.  It only makes the validation
    report easier to read when checking whether a selected test batch can
    exercise record retrieval, result follow-up, action execution, monitoring,
    and real-world barriers instead of only acute handoff chat.
    """

    signal_specs: dict[str, dict[str, set[str]]] = {
        "record_or_workspace_dependent": {
            "workspace_dependencies": {"prior_records", "records", "test_results", "documents", "medications"},
            "capability_tags": {"workspace_required", "workspace_useful", "document_upload_required", "external_record_dependency"},
        },
        "result_followup_or_tracking": {
            "episode_type": {"result_followup"},
            "care_loop_scope": {"result_tracking", "diagnostic_loop_closure", "serial_result_followup"},
            "capability_tags": {"result_followup", "followup_required", "document_upload_required", "external_record_dependency", "culture_interpretation", "source_tracking"},
        },
        "longitudinal_monitoring_or_stabilization": {
            "episode_type": {"chronic_management", "multimorbidity_management"},
            "care_loop_scope": {"monitoring_loop_closure", "longitudinal_stabilization", "longitudinal_management", "secondary_prevention", "rehabilitation_followup", "chronic_management_stabilization", "longitudinal_surveillance", "post_discharge_takeover"},
            "capability_tags": {"chronic_longitudinal", "post_discharge_followup", "multimorbidity_management", "longitudinal_management", "longitudinal_monitoring", "discontinuity_takeover"},
        },
        "patient_execution_barrier": {
            "capability_tags": {"patient_execution_barrier", "resource_constraint", "communication_risk", "family_or_caregiver_needed", "wrong_execution_recovery", "low_literacy_decoding", "medication_reconciliation", "dose_reconciliation", "inhaler_technique_recovery", "cost_barrier_management"},
            "patient_barriers": {"low_health_literacy", "caregiver_incomplete_records", "caregiver_anxiety", "old_new_med_confusion", "wrong_stopping", "inhaler_confusion", "cost_driven_underuse", "wrong_dose", "early_stop", "drug_name_confusion", "cost_fear"},
        },
        "acute_handoff_or_responsibility_transfer": {
            "care_loop_scope": {"acute_handoff", "responsibility_transfer", "acute_triage", "ed_handoff_milestone", "urgent_triage", "handoff_milestone"},
            "capability_tags": {"acute_time_sensitive", "handoff_required", "acute_triage", "red_flag_detection", "safety_netting"},
        },
        "low_risk_self_management": {
            "episode_type": {"low_risk_self_management"},
            "care_loop_scope": {"safe_self_management"},
            "capability_tags": {"low_risk_self_management", "over_escalation_risk"},
        },
    }
    cases_by_signal: dict[str, list[str]] = {name: [] for name in signal_specs}
    for item in cases:
        if not isinstance(item, dict):
            continue
        coverage = item.get("coverage") if isinstance(item.get("coverage"), dict) else {}
        case_id = str(item.get("case_id") or item.get("path") or "").strip()
        for signal, fields in signal_specs.items():
            matched = False
            for field, accepted_values in fields.items():
                values = set(_coverage_values(coverage.get(field)))
                if values & accepted_values:
                    matched = True
                    break
            if matched and case_id:
                cases_by_signal[signal].append(case_id)
    counts = {signal: len(case_ids) for signal, case_ids in cases_by_signal.items()}
    return {
        "principle": "Authoring/test-set coverage insight only; runtime_lite does not use these tags as flow-control rules.",
        "signal_counts": dict(sorted(counts.items())),
        "cases_by_signal": {signal: case_ids for signal, case_ids in sorted(cases_by_signal.items()) if case_ids},
        "longitudinal_or_result_loop_case_count": len(
            set(cases_by_signal["result_followup_or_tracking"])
            | set(cases_by_signal["longitudinal_monitoring_or_stabilization"])
        ),
        "record_or_workspace_dependent_case_count": counts.get("record_or_workspace_dependent", 0),
        "patient_execution_barrier_case_count": counts.get("patient_execution_barrier", 0),
    }


def _coverage_values(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, dict):
        return [json.dumps(value, ensure_ascii=False, sort_keys=True)]
    text = str(value or "").strip()
    return [text] if text else []


def _resolve_case_paths(args: argparse.Namespace) -> list[str]:
    raw_paths: list[str] = [str(item) for item in (args.case or []) if str(item).strip()]
    for manifest in getattr(args, "case_list", []) or []:
        raw_paths.extend(_case_paths_from_manifest(str(manifest)))
    unmatched_patterns: list[str] = []
    non_case_patterns: list[str] = []
    for pattern in args.case_glob or []:
        matches = sorted(glob.glob(str(pattern)))
        if not matches:
            unmatched_patterns.append(str(pattern))
            continue
        case_like_matches = [match for match in matches if _is_case_like_path(match)]
        if not case_like_matches:
            non_case_patterns.append(str(pattern))
            continue
        raw_paths.extend(case_like_matches)
    if unmatched_patterns:
        raise RuntimeError("case glob pattern matched no files: " + ", ".join(unmatched_patterns))
    if non_case_patterns:
        raise RuntimeError("case glob pattern matched files but no runnable case files: " + ", ".join(non_case_patterns))
    deduped: list[str] = []
    seen: set[str] = set()
    for raw in raw_paths:
        path = Path(raw)
        key = str(path.resolve()) if path.exists() else str(path)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(raw)
    if not deduped:
        raise RuntimeError("at least one --case or --case-glob match is required")
    sample_size = max(0, int(getattr(args, "sample_size", 0) or 0))
    if sample_size and sample_size < len(deduped):
        rng = random.Random(str(getattr(args, "sample_seed", "runtime_lite")))
        selected_indices = sorted(rng.sample(range(len(deduped)), sample_size))
        deduped = [deduped[index] for index in selected_indices]
    return deduped


def _case_paths_from_manifest(path: str) -> list[str]:
    manifest_path = Path(path)
    if not manifest_path.exists():
        raise RuntimeError(f"case list file does not exist: {path}")
    text = manifest_path.read_text(encoding="utf-8")
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        values = [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]
    else:
        if isinstance(raw, dict):
            entries = raw.get("cases") or raw.get("case_paths") or raw.get("paths") or []
        elif isinstance(raw, list):
            entries = raw
        else:
            entries = []
        values = []
        for entry in entries:
            if isinstance(entry, str):
                values.append(entry)
            elif isinstance(entry, dict):
                value = entry.get("path") or entry.get("case") or entry.get("case_path")
                if value:
                    values.append(str(value))
    resolved: list[str] = []
    for value in values:
        candidate = Path(str(value))
        if candidate.is_absolute():
            resolved.append(str(candidate))
            continue
        relative_to_manifest = manifest_path.parent / candidate
        if relative_to_manifest.exists():
            resolved.append(str(relative_to_manifest))
        else:
            resolved.append(str(candidate))
    if not resolved:
        raise RuntimeError(f"case list file contains no case paths: {path}")
    return resolved


def _is_case_like_path(path: str) -> bool:
    candidate = Path(path)
    if candidate.is_dir():
        return (candidate / "case.json").exists()
    if candidate.name == "case.json":
        return True
    if candidate.suffix.lower() != ".json" or not candidate.exists():
        return False
    try:
        with candidate.open("r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return False
    if not isinstance(raw, dict):
        return False
    initial_chat = raw.get("initial_chat") if isinstance(raw.get("initial_chat"), dict) else {}
    return bool(str(initial_chat.get("message") or "").strip())


def _run_metadata(args: argparse.Namespace, case_paths: list[str]) -> dict[str, Any]:
    sim_model = args.sim_model or args.model
    doctor_model = args.doctor_model or args.model or sim_model
    doctor_model_api_id = str(getattr(args, "doctor_model_api_id", "") or "").strip() or doctor_model
    return {
        "runtime": "runtime_lite",
        "client": args.client,
        "long_run_300_preset": bool(getattr(args, "long_run_300", False)),
        "long_run_1000_preset": bool(getattr(args, "long_run_1000", False)),
        "max_turns": args.max_turns,
        "parallelism": max(1, int(getattr(args, "parallelism", 1) or 1)),
        "evaluate_at_end": not args.no_eval,
        "eval_on_interrupt": bool(getattr(args, "eval_on_interrupt", False)),
        "continue_on_error": bool(args.continue_on_error),
        "resume_existing": bool(getattr(args, "resume_existing", False)),
        "resume_from_checkpoint": str(getattr(args, "resume_from_checkpoint", "") or ""),
        "case_count": len(case_paths),
        "case_paths": case_paths,
        "case_list": list(getattr(args, "case_list", []) or []),
        "case_glob": list(args.case_glob or []),
        "sample_size": int(args.sample_size or 0),
        "sample_seed": str(args.sample_seed or ""),
        "model": args.model,
        "sim_model": sim_model,
        "doctor_model": doctor_model,
        "doctor_model_api_id": doctor_model_api_id,
        "doctor_model_api_id_overridden": bool(doctor_model_api_id and doctor_model_api_id != doctor_model),
        "formal_fcct2_cross_model": bool(getattr(args, "formal_fcct2_cross_model", False)),
        "careloop_internal_model": (getattr(args, "careloop_internal_model", "") or sim_model),
        "formal_doctor_models": _formal_doctor_models(args),
        "model_role_contract": build_model_role_contract(sim_model, doctor_model, formal=bool(getattr(args, "formal_fcct2_cross_model", False))),
        "experiment_id": str(getattr(args, "experiment_id", "") or ""),
        "manifest_path": str(getattr(args, "manifest_path", "") or ""),
        "manifest_hash": str(getattr(args, "manifest_hash", "") or ""),
        "hardening_sidecars": str(getattr(args, "hardening_sidecars", "on") or "on"),
        "hardening_llm_audits": _hardening_llm_audit_mode(args),
        "hardening_llm_audit_purposes": _hardening_llm_audit_purposes(args),
        "runtime_advisory": {
            "formal_runtime_advisory": bool(getattr(args, "formal_runtime_advisory", False)),
            "actor_realism_controller_v2": bool(getattr(args, "enable_actor_realism_controller_v2", False)),
            "friction_coverage_planner": bool(getattr(args, "enable_friction_coverage_planner", False)),
            "closure_thread_advisory": bool(getattr(args, "enable_closure_thread_advisory", False)),
            "receipt_lifecycle_advisory": bool(getattr(args, "enable_receipt_lifecycle_advisory", False)),
            "interval_turns": {
                "actor_realism_v2": max(1, int(getattr(args, "actor_realism_v2_interval_turns", 4) or 4)),
                "friction_coverage": max(1, int(getattr(args, "friction_coverage_interval_turns", 6) or 6)),
                "closure_thread_advisory": max(1, int(getattr(args, "closure_thread_advisory_interval_turns", 4) or 4)),
                "receipt_lifecycle_advisory": max(1, int(getattr(args, "receipt_lifecycle_advisory_interval_turns", 4) or 4)),
            },
            "principle": "LLM-led advisory only; no hard clinical pathway gates or keyword bans.",
        },
        "base_url": _redacted_base_url(args.base_url),
        "timeout_seconds": args.timeout_seconds,
        "llm_purpose_timeouts": _llm_purpose_timeouts(args),
        "max_output_tokens": args.max_output_tokens if args.max_output_tokens > 0 else None,
        "llm_stream": bool(getattr(args, "llm_stream", False)),
        "llm_omit_temperature": bool(getattr(args, "llm_omit_temperature", False)),
        "llm_content_filter_clinical_context_retry": bool(
            getattr(args, "llm_content_filter_clinical_context_retry", True)
        ),
        "llm_content_filter_retry_policy": "single_verbatim_clinical_context_wrapper_v1_then_fail",
        "llm_extra_body_keys": sorted(_llm_extra_body(args).keys()),
        "llm_max_retries": args.llm_max_retries,
        "llm_retry_until_success": not bool(getattr(args, "llm_bounded_retries", False)),
        "llm_retry_backoff_seconds": args.llm_retry_backoff_seconds,
        "llm_retry_max_delay_seconds": args.llm_retry_max_delay_seconds,
        "stage_checkpoints": bool(getattr(args, "stage_checkpoints", False)),
        "clinical_memory_mode": args.clinical_memory_mode,
        "clinical_memory_max_interval_turns": args.clinical_memory_max_interval_turns,
        "clinical_memory_eventful_min_interval_turns": max(1, int(getattr(args, "clinical_memory_eventful_min_interval_turns", 3) or 3)),
        "long_context": {
            "long_context_1000_plus": bool(getattr(args, "long_context_1000_plus", False)),
            "episode_turn_span": max(1, int(getattr(args, "episode_turn_span", 20) or 20)),
            "chapter_episode_span": max(1, int(getattr(args, "chapter_episode_span", 5) or 5)),
            "working_memory_mode": "eventful",
            "working_memory_forced_interval_turns": max(1, int(getattr(args, "working_memory_forced_interval_turns", 3) or 3)),
            "memory_integrity_audit_interval_turns": max(1, int(getattr(args, "memory_integrity_audit_interval_turns", 50) or 50)),
            "fragment_eval_interval_turns": max(1, int(getattr(args, "fragment_eval_interval_turns", 100) or 100)),
            "append_only_ledger": True,
            "externalize_memory_snapshots": not bool(getattr(args, "no_externalize_memory_snapshots", False)),
            "doctor_visibility_boundary": "CareLoop long-context memory is backstage only; tested doctor must manage its own context via allowed doctor-visible operations.",
        },
    }


def _api_config_readiness(args: argparse.Namespace) -> dict[str, Any]:
    """Non-sensitive readiness check for real API runs.

    This intentionally reports only booleans, redacted URLs, model names and
    missing field names.  The API key value is never returned or logged.
    """

    sim_model = args.sim_model or args.model
    doctor_model = args.doctor_model or args.model or sim_model
    doctor_model_api_id = str(getattr(args, "doctor_model_api_id", "") or "").strip() or doctor_model
    api_key_env = str(getattr(args, "api_key_env", "CARELOOP_LITE_API_KEY") or "CARELOOP_LITE_API_KEY")
    checks = {
        "base_url": bool(str(getattr(args, "base_url", "") or "").strip()),
        "api_key_env": bool(os.environ.get(api_key_env, "").strip()),
        "sim_model": bool(str(sim_model or "").strip()),
        "doctor_model": bool(str(doctor_model or "").strip()),
    }
    missing = [name for name, present in checks.items() if not present]
    ready = bool(getattr(args, "client", "") == "scripted" or not missing)
    return {
        "client": getattr(args, "client", ""),
        "ready": ready,
        "redacted_base_url": _redacted_base_url(getattr(args, "base_url", "")),
        "base_url_set": checks["base_url"],
        "api_key_env_name": api_key_env,
        "api_key_env_set": checks["api_key_env"],
        "sim_model": sim_model,
        "sim_model_set": checks["sim_model"],
        "doctor_model": doctor_model,
        "doctor_model_set": checks["doctor_model"],
        "doctor_model_api_id": doctor_model_api_id,
        "doctor_model_api_id_overridden": bool(doctor_model_api_id and doctor_model_api_id != doctor_model),
        "formal_fcct2_cross_model": bool(getattr(args, "formal_fcct2_cross_model", False)),
        "careloop_internal_model": str(getattr(args, "careloop_internal_model", "") or sim_model or ""),
        "llm_stream": bool(getattr(args, "llm_stream", False)),
        "llm_omit_temperature": bool(getattr(args, "llm_omit_temperature", False)),
        "llm_extra_body_keys": sorted(_llm_extra_body(args).keys()),
        "llm_purpose_timeout_keys": sorted(_llm_purpose_timeouts(args).keys()),
        "hardening_llm_audits": _hardening_llm_audit_mode(args),
        "missing_for_openai_compatible": [] if getattr(args, "client", "") == "scripted" else missing,
        "key_value_logged": False,
    }


def _per_case_metadata(run_metadata: dict[str, Any]) -> dict[str, Any]:
    compact = dict(run_metadata)
    compact.pop("case_paths", None)
    return compact


def _redacted_base_url(base_url: str) -> str:
    return str(base_url or "").strip().split("?")[0]


def _default_run_id(case_path: str) -> str:
    stem = Path(case_path).stem or Path(case_path).name or "case"
    return f"runtime_lite_{stem}"


def _run_id_for_case(raw_run_id: str, case_path: str, index: int, multiple: bool) -> str:
    if raw_run_id and not multiple:
        return raw_run_id
    base = raw_run_id or _default_run_id(case_path)
    return f"{base}_{index:02d}" if multiple else base


def _console_summary(payload: dict[str, Any]) -> dict[str, Any]:
    trajectory = payload.get("trajectory") or {}
    events = trajectory.get("events") or []
    quality_metrics = (payload.get("quality_report") or {}).get("metrics") or {}
    real_world_friction = quality_metrics.get("real_world_friction") or {}
    actor_cooperation_realism = quality_metrics.get("actor_cooperation_realism") or {}
    external_progression_dependency = quality_metrics.get("external_progression_dependency") or {}
    clinical_core_loop = quality_metrics.get("clinical_core_loop") or {}
    longitudinal_care_process = quality_metrics.get("longitudinal_care_process") or {}
    trajectory_progression = quality_metrics.get("trajectory_progression") or {}
    memory_integrity = _memory_integrity_summary_from_trajectory(trajectory)
    payload_metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    long_context_metadata = payload_metadata.get("long_context") if isinstance(payload_metadata.get("long_context"), dict) else {}
    evaluation = payload.get("evaluation") if isinstance(payload.get("evaluation"), dict) else {}
    simulation_validity = (
        evaluation.get("simulation_validity") if isinstance(evaluation.get("simulation_validity"), dict) else {}
    )
    fragment_validity = evaluation.get("fragment_validity") if isinstance(evaluation.get("fragment_validity"), dict) else {}
    evaluation_validity = simulation_validity or fragment_validity
    prior_contract_weights = (
        evaluation.get("prior_contract_weights") if isinstance(evaluation.get("prior_contract_weights"), list) else []
    )
    trajectory_emergent_weights = (
        evaluation.get("trajectory_emergent_weights")
        if isinstance(evaluation.get("trajectory_emergent_weights"), list)
        else []
    )
    final_trajectory_weights = (
        evaluation.get("final_trajectory_specific_weights")
        if isinstance(evaluation.get("final_trajectory_specific_weights"), list)
        else []
    )
    trajectory_weights = evaluation.get("trajectory_specific_weights") if isinstance(evaluation.get("trajectory_specific_weights"), list) else []
    if not trajectory_weights and final_trajectory_weights:
        trajectory_weights = final_trajectory_weights
    critical_failures = evaluation.get("critical_failures") if isinstance(evaluation.get("critical_failures"), list) else []
    repair_metadata = evaluation.get("repair_metadata") if isinstance(evaluation.get("repair_metadata"), dict) else {}
    infrastructure_integrity = (
        evaluation.get("infrastructure_integrity") if isinstance(evaluation.get("infrastructure_integrity"), dict) else {}
    )
    hardening = attach_hardening_reports(payload)
    benchmark_validity = hardening.get("benchmark_validity") or {}
    visibility_boundary = hardening.get("visibility_boundary_audit") or {}
    receipt_lifecycle = hardening.get("receipt_lifecycle_summary") or {}
    closure_threads = hardening.get("closure_thread_summary") or {}
    api_runtime_safety = hardening.get("api_runtime_safety") or {}
    return {
        "case_id": payload.get("case_id"),
        "benchmark_validity_status": benchmark_validity.get("status", ""),
        "benchmark_primary": bool(benchmark_validity.get("should_count_in_primary_benchmark")),
        "doctor_performance_usable": bool(benchmark_validity.get("doctor_performance_usable")),
        "visibility_boundary_status": visibility_boundary.get("status", ""),
        "visibility_boundary_max_severity": visibility_boundary.get("max_severity", ""),
        "receipt_lifecycle_status": receipt_lifecycle.get("status", ""),
        "closure_thread_readiness": closure_threads.get("overall_closure_readiness", ""),
        "api_runtime_error_class": api_runtime_safety.get("runtime_error_class", ""),
        "run_id": payload.get("run_id"),
        "turns_completed": payload.get("turns_completed"),
        "closure": payload.get("closure", {}).get("status"),
        "evaluation_mode": evaluation.get("evaluation_mode"),
        "evaluation_overall": evaluation.get("overall"),
        "evaluation_simulation_validity_status": evaluation_validity.get("status", ""),
        "evaluation_closure_quality": evaluation.get("closure_quality", ""),
        "evaluation_claim_strength": evaluation.get("evaluation_claim_strength", ""),
        "evaluation_repair_attempted": bool(repair_metadata.get("repair_attempted")),
        "evaluation_repair_used": bool(repair_metadata.get("repair_used")),
        "evaluation_repair_success": bool(repair_metadata.get("repair_success")),
        "evaluation_original_evaluator_purpose": repair_metadata.get("original_evaluator_purpose", ""),
        "evaluation_original_failure_type": repair_metadata.get("original_failure_type")
        or infrastructure_integrity.get("main_evaluator_failure_type")
        or infrastructure_integrity.get("failure_type")
        or "",
        "evaluation_prior_contract_weight_count": len(prior_contract_weights),
        "evaluation_trajectory_emergent_weight_count": len(trajectory_emergent_weights),
        "evaluation_final_trajectory_weight_count": len(final_trajectory_weights),
        "evaluation_dynamic_weight_count": len(trajectory_weights),
        "evaluation_critical_failure_count": len(critical_failures),
        "event_count": len(events),
        "transcript_count": len(trajectory.get("transcript") or []),
        "llm_call_count": len(trajectory.get("llm_calls") or []),
        "llm_model_counts": _llm_model_counts_from_trajectory(trajectory),
        "llm_purpose_model_counts": _llm_purpose_model_counts_from_trajectory(trajectory),
        "workspace_result_count": sum(1 for event in events if event.get("event_type") == "doctor_workspace_result"),
        "care_system_receipt_count": sum(1 for event in events if event.get("event_type") == "doctor_care_system_receipt"),
        "doctor_system_notification_count": quality_metrics.get("doctor_system_notification_count", 0),
        "living_state_update_count": quality_metrics.get("living_state_update_count", 0),
        "committed_world_event_count": quality_metrics.get("committed_world_event_count", 0),
        "non_occurred_event_candidate_count": quality_metrics.get("non_occurred_event_candidate_count", 0),
        "care_loop_phase_count": quality_metrics.get("care_loop_phase_count", 0),
        "care_loop_shape": quality_metrics.get("care_loop_shape", ""),
        "care_loop": quality_metrics.get("care_loop") or {},
        "clinical_core_loop": clinical_core_loop,
        "clinical_core_loop_status": quality_metrics.get("clinical_core_loop_status", ""),
        "clinical_core_stage_count": quality_metrics.get("clinical_core_stage_count", 0),
        "care_process_step_count": quality_metrics.get("care_process_step_count", 0),
        "longitudinal_care_process": longitudinal_care_process,
        "trajectory_progression": trajectory_progression,
        "trajectory_progression_status": quality_metrics.get("trajectory_progression_status", ""),
        "material_progress_signal_count": quality_metrics.get("material_progress_signal_count", 0),
        "material_progress_turn_count": quality_metrics.get("material_progress_turn_count", 0),
        "substantive_progress_signal_count": trajectory_progression.get("substantive_progress_signal_count", 0),
        "mechanical_progress_only": bool(trajectory_progression.get("mechanical_progress_only")),
        "mechanical_repetition_loop": bool(trajectory_progression.get("mechanical_repetition_loop")),
        "last_material_progress_turn": trajectory_progression.get("last_material_progress_turn", 0),
        "open_at_max_but_progressing": bool(trajectory_progression.get("open_at_max_but_progressing")),
        "memory_integrity": memory_integrity,
        "long_context_enabled": bool(long_context_metadata.get("enabled")),
        "long_context_available": bool(long_context_metadata.get("available")),
        "long_context_manifest": long_context_metadata.get("manifest", ""),
        "long_context_final_evidence_pack": long_context_metadata.get("final_evidence_pack", ""),
        "clinical_memory_snapshot_count": memory_integrity.get("snapshot_count", 0),
        "clinical_memory_skip_count": memory_integrity.get("skip_count", 0),
        "memory_integrity_latest_status": memory_integrity.get("latest_status", ""),
        "memory_integrity_carried_forward_count": memory_integrity.get("carried_forward_pending_receipt_count", 0),
        "memory_integrity_missing_pending_count": memory_integrity.get("missing_pending_receipt_count", 0),
        "memory_integrity_non_compressible_kernel_count": memory_integrity.get("non_compressible_kernel_item_count", 0),
        "memory_integrity_carried_forward_kernel_count": memory_integrity.get("carried_forward_non_compressible_kernel_count", 0),
        "memory_integrity_missing_kernel_count": memory_integrity.get("missing_non_compressible_kernel_count", 0),
        "real_world_friction": real_world_friction,
        "real_world_friction_signal_count": quality_metrics.get("real_world_friction_signal_count", 0),
        "doctor_adaptation_after_friction_count": quality_metrics.get("doctor_adaptation_after_friction_count", 0),
        "actor_cooperation_realism": actor_cooperation_realism,
        "actor_high_cooperation_risk_status": quality_metrics.get("actor_high_cooperation_risk_status", ""),
        "actor_overstructured_message_count": quality_metrics.get("actor_overstructured_message_count", 0),
        "external_progression_dependency": external_progression_dependency,
        "external_progression_dependency_status": quality_metrics.get("external_progression_dependency_status", ""),
        "total_elapsed_minutes": quality_metrics.get("total_elapsed_minutes", 0),
        "last_sim_time": quality_metrics.get("last_sim_time", ""),
        "quality_status": (payload.get("quality_report") or {}).get("status"),
        "quality_flags": (payload.get("quality_report") or {}).get("flags") or [],
        "runtime_error": (payload.get("runtime_error") or {}).get("message", ""),
        "resumed_from_existing_output": bool((payload.get("metadata") or {}).get("resumed_from_existing_output")),
    }


def _aggregate_summary(summaries: list[dict[str, Any]]) -> dict[str, Any]:
    total_turns = sum(int(item.get("turns_completed") or 0) for item in summaries)
    total_llm_calls = sum(int(item.get("llm_call_count") or 0) for item in summaries)
    total_committed_world_events = sum(int(item.get("committed_world_event_count") or 0) for item in summaries)
    total_non_occurred_candidates = sum(int(item.get("non_occurred_event_candidate_count") or 0) for item in summaries)
    total_doctor_system_notifications = sum(int(item.get("doctor_system_notification_count") or 0) for item in summaries)
    total_living_state_updates = sum(int(item.get("living_state_update_count") or 0) for item in summaries)
    total_friction_signals = sum(int(item.get("real_world_friction_signal_count") or 0) for item in summaries)
    total_doctor_adaptations = sum(int(item.get("doctor_adaptation_after_friction_count") or 0) for item in summaries)
    total_actor_overstructured_messages = sum(int(item.get("actor_overstructured_message_count") or 0) for item in summaries)
    total_care_loop_phases = sum(int(item.get("care_loop_phase_count") or 0) for item in summaries)
    total_clinical_core_stages = sum(int(item.get("clinical_core_stage_count") or 0) for item in summaries)
    total_care_process_steps = sum(int(item.get("care_process_step_count") or 0) for item in summaries)
    total_material_progress_signals = sum(int(item.get("material_progress_signal_count") or 0) for item in summaries)
    total_material_progress_turns = sum(int(item.get("material_progress_turn_count") or 0) for item in summaries)
    total_substantive_progress_signals = sum(int(item.get("substantive_progress_signal_count") or 0) for item in summaries)
    mechanical_progress_only_count = sum(1 for item in summaries if item.get("mechanical_progress_only"))
    mechanical_repetition_loop_count = sum(1 for item in summaries if item.get("mechanical_repetition_loop"))
    total_elapsed_minutes = sum(float(item.get("total_elapsed_minutes") or 0) for item in summaries)
    total_memory_carried = sum(int(item.get("memory_integrity_carried_forward_count") or 0) for item in summaries)
    total_memory_missing = sum(int(item.get("memory_integrity_missing_pending_count") or 0) for item in summaries)
    total_memory_kernel = sum(int(item.get("memory_integrity_non_compressible_kernel_count") or 0) for item in summaries)
    total_memory_kernel_carried = sum(int(item.get("memory_integrity_carried_forward_kernel_count") or 0) for item in summaries)
    total_memory_kernel_missing = sum(int(item.get("memory_integrity_missing_kernel_count") or 0) for item in summaries)
    total_memory_skips = sum(int(item.get("clinical_memory_skip_count") or 0) for item in summaries)
    total_eval_prior_weight_items = sum(int(item.get("evaluation_prior_contract_weight_count") or 0) for item in summaries)
    total_eval_emergent_weight_items = sum(int(item.get("evaluation_trajectory_emergent_weight_count") or 0) for item in summaries)
    total_eval_final_weight_items = sum(int(item.get("evaluation_final_trajectory_weight_count") or 0) for item in summaries)
    total_eval_weight_items = sum(int(item.get("evaluation_dynamic_weight_count") or 0) for item in summaries)
    total_eval_critical_failures = sum(int(item.get("evaluation_critical_failure_count") or 0) for item in summaries)
    total_eval_repair_attempted = sum(1 for item in summaries if item.get("evaluation_repair_attempted"))
    total_eval_repair_used = sum(1 for item in summaries if item.get("evaluation_repair_used"))
    total_eval_repair_success = sum(1 for item in summaries if item.get("evaluation_repair_success"))
    hardening_counts = hardening_counts_from_summaries(summaries)
    return {
        "case_count": len(summaries),
        **hardening_counts,
        "quality_status_counts": {
            status: sum(1 for item in summaries if item.get("quality_status") == status)
            for status in sorted({str(item.get("quality_status")) for item in summaries})
        },
        "closure_status_counts": {
            status: sum(1 for item in summaries if item.get("closure") == status)
            for status in sorted({str(item.get("closure")) for item in summaries})
        },
        "evaluation_overall_counts": _value_counts(summaries, "evaluation_overall"),
        "evaluation_mode_counts": _value_counts(summaries, "evaluation_mode"),
        "evaluation_simulation_validity_status_counts": _value_counts(summaries, "evaluation_simulation_validity_status"),
        "evaluation_claim_strength_counts": _value_counts(summaries, "evaluation_claim_strength"),
        "evaluation_repair_attempted_count": total_eval_repair_attempted,
        "evaluation_repair_used_count": total_eval_repair_used,
        "evaluation_repair_success_count": total_eval_repair_success,
        "evaluation_original_failure_type_counts": _value_counts(summaries, "evaluation_original_failure_type"),
        "evaluation_prior_contract_weight_item_count": total_eval_prior_weight_items,
        "evaluation_trajectory_emergent_weight_item_count": total_eval_emergent_weight_items,
        "evaluation_final_trajectory_weight_item_count": total_eval_final_weight_items,
        "evaluation_dynamic_weight_item_count": total_eval_weight_items,
        "evaluation_critical_failure_count": total_eval_critical_failures,
        "quality_flag_counts": _flag_counts(summaries),
        "total_turns_completed": total_turns,
        "total_llm_calls": total_llm_calls,
        "average_llm_calls_per_turn": round(total_llm_calls / total_turns, 3) if total_turns else None,
        "llm_model_counts": _aggregate_nested_counts(summaries, "llm_model_counts"),
        "llm_purpose_model_counts": _aggregate_nested_counts(summaries, "llm_purpose_model_counts"),
        "total_committed_world_events": total_committed_world_events,
        "average_committed_world_events_per_turn": round(total_committed_world_events / total_turns, 3) if total_turns else None,
        "total_non_occurred_event_candidates": total_non_occurred_candidates,
        "total_doctor_system_notifications": total_doctor_system_notifications,
        "total_living_state_updates": total_living_state_updates,
        "total_real_world_friction_signals": total_friction_signals,
        "total_doctor_adaptations_after_friction": total_doctor_adaptations,
        "real_world_friction_category_counts": _aggregate_friction_category_counts(summaries),
        "actor_high_cooperation_risk_status_counts": _value_counts(summaries, "actor_high_cooperation_risk_status"),
        "total_actor_overstructured_message_count": total_actor_overstructured_messages,
        "external_progression_dependency_status_counts": _value_counts(summaries, "external_progression_dependency_status"),
        "trajectory_progression_status_counts": _value_counts(summaries, "trajectory_progression_status"),
        "clinical_core_loop_status_counts": _value_counts(summaries, "clinical_core_loop_status"),
        "average_clinical_core_stage_count": round(total_clinical_core_stages / len(summaries), 3) if summaries else None,
        "total_material_progress_signal_count": total_material_progress_signals,
        "total_material_progress_turn_count": total_material_progress_turns,
        "total_substantive_progress_signal_count": total_substantive_progress_signals,
        "average_material_progress_signals_per_case": round(total_material_progress_signals / len(summaries), 3) if summaries else None,
        "average_substantive_progress_signals_per_case": round(total_substantive_progress_signals / len(summaries), 3) if summaries else None,
        "mechanical_progress_only_count": mechanical_progress_only_count,
        "mechanical_repetition_loop_count": mechanical_repetition_loop_count,
        "open_at_max_but_progressing_count": sum(1 for item in summaries if item.get("open_at_max_but_progressing")),
        "care_loop_shape_counts": _value_counts(summaries, "care_loop_shape"),
        "average_care_loop_phase_count": round(total_care_loop_phases / len(summaries), 3) if summaries else None,
        "average_care_process_step_count": round(total_care_process_steps / len(summaries), 3) if summaries else None,
        "clinical_memory_snapshot_count": sum(int(item.get("clinical_memory_snapshot_count") or 0) for item in summaries),
        "clinical_memory_skip_count": total_memory_skips,
        "memory_integrity_status_counts": _value_counts(summaries, "memory_integrity_latest_status"),
        "memory_integrity_carried_forward_pending_receipt_count": total_memory_carried,
        "memory_integrity_missing_pending_receipt_count": total_memory_missing,
        "memory_integrity_non_compressible_kernel_item_count": total_memory_kernel,
        "memory_integrity_carried_forward_non_compressible_kernel_count": total_memory_kernel_carried,
        "memory_integrity_missing_non_compressible_kernel_count": total_memory_kernel_missing,
        "total_elapsed_minutes": round(total_elapsed_minutes, 3),
        "runtime_error_count": sum(1 for item in summaries if item.get("runtime_error")),
        "resumed_existing_count": sum(1 for item in summaries if item.get("resumed_from_existing_output")),
    }


def _aggregate_with_validation_interpretation(
    summaries: list[dict[str, Any]], run_metadata: dict[str, Any]
) -> dict[str, Any]:
    aggregate = _aggregate_summary(summaries)
    aggregate["validation_interpretation"] = _batch_validation_interpretation(summaries, aggregate, run_metadata)
    return aggregate


def _batch_validation_interpretation(
    summaries: list[dict[str, Any]],
    aggregate: dict[str, Any],
    run_metadata: dict[str, Any],
) -> dict[str, Any]:
    """Interpret batch evidence for review without controlling runtime flow."""

    case_count = int(aggregate.get("case_count") or len(summaries) or 0)
    total_turns = int(aggregate.get("total_turns_completed") or 0)
    max_turns = _safe_int(run_metadata.get("max_turns"), default=0)
    client = str(run_metadata.get("client") or "")
    flag_counts = aggregate.get("quality_flag_counts") if isinstance(aggregate.get("quality_flag_counts"), dict) else {}
    shape_counts = aggregate.get("care_loop_shape_counts") if isinstance(aggregate.get("care_loop_shape_counts"), dict) else {}
    progression_counts = (
        aggregate.get("trajectory_progression_status_counts")
        if isinstance(aggregate.get("trajectory_progression_status_counts"), dict)
        else {}
    )
    runtime_errors = int(aggregate.get("runtime_error_count") or 0)
    committed_world_events = int(aggregate.get("total_committed_world_events") or 0)
    material_progress_signals = int(aggregate.get("total_material_progress_signal_count") or 0)
    material_progress_turns = int(aggregate.get("total_material_progress_turn_count") or 0)
    substantive_progress_signals = int(aggregate.get("total_substantive_progress_signal_count") or 0)
    mechanical_progress_only = int(aggregate.get("mechanical_progress_only_count") or 0)
    mechanical_repetition_loop = int(aggregate.get("mechanical_repetition_loop_count") or 0)
    open_at_max_but_progressing = int(aggregate.get("open_at_max_but_progressing_count") or 0)
    living_updates = int(aggregate.get("total_living_state_updates") or 0)
    friction_signals = int(aggregate.get("total_real_world_friction_signals") or 0)
    friction_adaptations = int(aggregate.get("total_doctor_adaptations_after_friction") or 0)
    memory_missing = int(aggregate.get("memory_integrity_missing_pending_receipt_count") or 0)
    memory_kernel_missing = int(aggregate.get("memory_integrity_missing_non_compressible_kernel_count") or 0)
    eval_overall_counts = (
        aggregate.get("evaluation_overall_counts") if isinstance(aggregate.get("evaluation_overall_counts"), dict) else {}
    )
    eval_sim_validity_counts = (
        aggregate.get("evaluation_simulation_validity_status_counts")
        if isinstance(aggregate.get("evaluation_simulation_validity_status_counts"), dict)
        else {}
    )
    eval_dynamic_weight_items = int(aggregate.get("evaluation_dynamic_weight_item_count") or 0)
    eval_critical_failures = int(aggregate.get("evaluation_critical_failure_count") or 0)
    systemic_cutoff = max(2, (case_count + 1) // 2) if case_count else 2

    no_world_progression = int(flag_counts.get("weak_world_progression_no_committed_events") or 0)
    open_at_max = int(flag_counts.get("open_at_max_turns") or 0)
    possible_stasis = int(flag_counts.get("possible_trajectory_stasis") or 0)
    problematic_open_at_max = max(0, open_at_max - open_at_max_but_progressing)
    unfinished_but_progressing = int(progression_counts.get("unfinished_but_progressing") or 0)
    progression_positive = (
        int(progression_counts.get("progressing") or 0)
        + int(progression_counts.get("unfinished_but_progressing") or 0)
        + int(progression_counts.get("limited_progress") or 0)
    )
    repeated_actor = int(flag_counts.get("possible_actor_stasis_repeated_text") or 0)
    repeated_doctor = int(flag_counts.get("possible_doctor_stasis_repeated_text") or 0)
    premature = int(flag_counts.get("possible_premature_closure") or 0)
    closed_without_world = int(flag_counts.get("closed_without_committed_world_progression") or 0)
    internal_leakage = sum(
        int(flag_counts.get(flag) or 0)
        for flag in [
            "workspace_result_leaked_into_patient_or_family_chat",
            "internal_runtime_terms_leaked_to_actor",
            "internal_runtime_terms_leaked_to_doctor_visible_content",
        ]
    )

    evidence_rich_shapes = {
        "action_execution_loop",
        "ordered_result_returned_loop",
        "result_to_action_followup_loop",
    }
    evidence_rich_case_count = sum(int(shape_counts.get(shape) or 0) for shape in evidence_rich_shapes)
    plan_only_case_count = sum(
        int(shape_counts.get(shape) or 0)
        for shape in ["conversation_only", "doctor_plan_or_receipt_only", "handoff_or_emergency_only"]
    )

    concerns: list[str] = []
    strengths: list[str] = []
    recommended_next_review: list[str] = []

    if client == "scripted":
        concerns.append("scripted client can verify wiring/reporting but cannot prove natural simulation quality")
        recommended_next_review.append("run the same batch with real JD Cloud models before judging story quality")
    if runtime_errors:
        concerns.append(f"{runtime_errors} case(s) have runtime/model infrastructure errors")
    if internal_leakage:
        concerns.append(f"{internal_leakage} visibility-boundary leakage flag(s) require immediate review")
    if memory_missing:
        concerns.append(f"{memory_missing} pending responsibility item(s) were missing after memory carry-forward")
    if memory_kernel_missing:
        concerns.append(f"{memory_kernel_missing} non-compressible clinical memory kernel item(s) were missing after carry-forward")
    if possible_stasis >= systemic_cutoff:
        concerns.append("trajectory progression audit suggests systemic stasis across the batch")
    elif possible_stasis:
        concerns.append(f"{possible_stasis} case(s) have possible trajectory stasis signals")
    if mechanical_repetition_loop >= systemic_cutoff:
        concerns.append("material progress is systemically mechanical/repetitive rather than substantive")
    elif mechanical_repetition_loop:
        concerns.append(f"{mechanical_repetition_loop} case(s) show mechanical repetition loop signals")
    elif mechanical_progress_only:
        concerns.append(f"{mechanical_progress_only} case(s) have only mechanical progress signals")
    if no_world_progression >= systemic_cutoff:
        concerns.append("world progression appears systemically weak across the batch")
    elif no_world_progression:
        concerns.append(f"{no_world_progression} case(s) lack committed world progression")
    if problematic_open_at_max >= systemic_cutoff:
        concerns.append("many cases are still open at max_turns without enough progression evidence")
    elif problematic_open_at_max:
        concerns.append(f"{problematic_open_at_max} open-at-max case(s) need stasis-vs-long-course review")
    if repeated_actor or repeated_doctor:
        concerns.append("repetition/stasis flags are present and should be checked against transcript quality")
    if premature or closed_without_world:
        concerns.append("possible premature or surface-level closure flags are present")
    if int(eval_sim_validity_counts.get("invalid") or 0):
        concerns.append("TrajectoryEvaluator marked one or more trajectories invalid")
    if eval_critical_failures:
        concerns.append(f"TrajectoryEvaluator reported {eval_critical_failures} critical failure item(s)")
    if evidence_rich_case_count:
        strengths.append(f"{evidence_rich_case_count} case(s) show action/result/execution-loop style evidence")
    if progression_positive:
        strengths.append(f"{progression_positive} case(s) show at least limited trajectory progression evidence")
    if substantive_progress_signals:
        strengths.append(f"{substantive_progress_signals} substantive progress signal(s) were recorded")
    if open_at_max_but_progressing:
        strengths.append(f"{open_at_max_but_progressing} open-at-max case(s) appear unfinished but still progressing")
    if int(eval_sim_validity_counts.get("usable") or 0):
        strengths.append(f"{int(eval_sim_validity_counts.get('usable') or 0)} trajectory evaluation(s) are marked usable")
    if eval_dynamic_weight_items:
        strengths.append(f"{eval_dynamic_weight_items} trajectory-specific dynamic weight item(s) were produced")
    if committed_world_events:
        strengths.append(f"{committed_world_events} committed world event(s) were recorded")
    if living_updates:
        strengths.append(f"{living_updates} living-state update(s) were recorded")
    if friction_signals:
        strengths.append(f"{friction_signals} real-world friction signal(s) were detected")
    if friction_adaptations:
        strengths.append(f"{friction_adaptations} doctor adaptation-after-friction signal(s) were detected")

    if not evidence_rich_case_count and case_count:
        recommended_next_review.append("inspect whether trajectories remain plan-only/handoff-only instead of exercising care execution")
    if possible_stasis or mechanical_repetition_loop or (no_world_progression and material_progress_signals == 0):
        recommended_next_review.append("inspect WorldDirector → ActorSituationMessenger → actor handoff for event propagation")
    if material_progress_signals and substantive_progress_signals == 0:
        recommended_next_review.append("inspect whether progress signals are only mechanical time/tool loops rather than lived clinical progress")
    if problematic_open_at_max:
        recommended_next_review.append("read problematic open-at-max cases to decide whether they are realistic unfinished care or framework stasis")
    if open_at_max_but_progressing:
        recommended_next_review.append("sample open-at-max-but-progressing cases to decide whether max_turns should be raised rather than treating them as stalled")
    if friction_signals and not friction_adaptations:
        recommended_next_review.append("inspect whether doctors are adapting plans to cost/transport/family/literacy barriers")
    if eval_critical_failures:
        recommended_next_review.append("inspect evaluator critical failures against raw transcript and hidden case facts")
    if int(eval_sim_validity_counts.get("invalid") or 0):
        recommended_next_review.append("separate invalid simulation trajectories from doctor-performance conclusions")
    if not recommended_next_review:
        recommended_next_review.append("sample full trajectories and compare evaluator conclusions against raw transcript evidence")

    if client == "scripted":
        status = "scripted_wiring_check_only"
    elif (
        runtime_errors
        or internal_leakage
        or memory_missing
        or memory_kernel_missing
        or possible_stasis >= systemic_cutoff
        or mechanical_repetition_loop >= systemic_cutoff
        or (no_world_progression >= systemic_cutoff and material_progress_signals == 0)
    ):
        status = "needs_runtime_investigation"
    elif (
        problematic_open_at_max >= systemic_cutoff
        or possible_stasis
        or premature
        or closed_without_world
        or repeated_actor >= systemic_cutoff
        or eval_critical_failures
        or int(eval_sim_validity_counts.get("invalid") or 0)
    ):
        status = "needs_close_review"
    else:
        status = "ready_for_deep_review"

    headline_by_status = {
        "scripted_wiring_check_only": "This batch can validate wiring/report generation only; real LLM trajectories are still required.",
        "needs_runtime_investigation": "This batch has infrastructure, visibility, memory, or systemic progression signals that must be investigated before judging doctor performance.",
        "needs_close_review": "This batch ran, but closure/stasis signals need close trajectory review before accepting it as natural long-course care.",
        "ready_for_deep_review": "This batch has no obvious aggregate runtime blocker; proceed to detailed trajectory and evaluator review.",
    }
    return {
        "protocol": "careloop.runtime_lite.batch_validation_interpretation.v1",
        "posthoc_only_not_runtime_control": True,
        "scope": {
            "client": client,
            "case_count": case_count,
            "max_turns": max_turns,
            "total_turns_completed": total_turns,
            "is_30_turn_broad_noise_candidate": bool(max_turns == 30 and case_count >= 8),
        },
        "status": status,
        "headline": headline_by_status[status],
        "strengths": strengths,
        "concerns": concerns,
        "progression_evidence": {
            "trajectory_progression_status_counts": progression_counts,
            "material_progress_signal_count": material_progress_signals,
            "material_progress_turn_count": material_progress_turns,
            "substantive_progress_signal_count": substantive_progress_signals,
            "mechanical_progress_only_case_count": mechanical_progress_only,
            "mechanical_progress_only_case_ids": _case_ids_with_value(summaries, "mechanical_progress_only", True),
            "mechanical_repetition_loop_case_count": mechanical_repetition_loop,
            "mechanical_repetition_loop_case_ids": _case_ids_with_value(summaries, "mechanical_repetition_loop", True),
            "committed_world_event_count": committed_world_events,
            "living_state_update_count": living_updates,
            "possible_stasis_case_count": possible_stasis,
            "possible_stasis_case_ids": _case_ids_with_value(summaries, "trajectory_progression_status", "possible_stasis"),
            "weak_world_progression_case_count": no_world_progression,
            "weak_world_progression_case_ids": _case_ids_with_flag(summaries, "weak_world_progression_no_committed_events"),
            "open_at_max_turns_case_count": open_at_max,
            "open_at_max_turns_case_ids": _case_ids_with_flag(summaries, "open_at_max_turns"),
            "open_at_max_but_progressing_case_count": open_at_max_but_progressing,
            "open_at_max_but_progressing_case_ids": _case_ids_with_value(summaries, "open_at_max_but_progressing", True),
            "problematic_open_at_max_case_count": problematic_open_at_max,
            "unfinished_but_progressing_case_count": unfinished_but_progressing,
            "unfinished_but_progressing_case_ids": _case_ids_with_value(
                summaries, "trajectory_progression_status", "unfinished_but_progressing"
            ),
        },
        "closed_loop_evidence": {
            "care_loop_shape_counts": shape_counts,
            "evidence_rich_case_count": evidence_rich_case_count,
            "plan_or_handoff_only_case_count": plan_only_case_count,
            "premature_or_surface_closure_case_ids": sorted(
                set(
                    _case_ids_with_flag(summaries, "possible_premature_closure")
                    + _case_ids_with_flag(summaries, "closed_without_committed_world_progression")
                )
            )[:12],
        },
        "longitudinal_and_noise_evidence": {
            "average_care_loop_phase_count": aggregate.get("average_care_loop_phase_count"),
            "average_care_process_step_count": aggregate.get("average_care_process_step_count"),
            "total_real_world_friction_signals": friction_signals,
            "total_doctor_adaptations_after_friction": friction_adaptations,
            "friction_category_counts": aggregate.get("real_world_friction_category_counts") or {},
            "non_compressible_memory_kernel_item_count": aggregate.get("memory_integrity_non_compressible_kernel_item_count"),
            "missing_non_compressible_memory_kernel_count": memory_kernel_missing,
        },
        "evaluation_evidence": {
            "evaluation_overall_counts": eval_overall_counts,
            "simulation_validity_status_counts": eval_sim_validity_counts,
            "trajectory_specific_dynamic_weight_item_count": eval_dynamic_weight_items,
            "critical_failure_item_count": eval_critical_failures,
        },
        "model_and_call_evidence": {
            "average_llm_calls_per_turn": aggregate.get("average_llm_calls_per_turn"),
            "llm_model_counts": aggregate.get("llm_model_counts") or {},
            "llm_purpose_model_counts": aggregate.get("llm_purpose_model_counts") or {},
        },
        "recommended_next_review": recommended_next_review,
    }


def _case_ids_with_flag(summaries: list[dict[str, Any]], flag: str, *, limit: int = 12) -> list[str]:
    ids: list[str] = []
    for item in summaries:
        if flag in (item.get("quality_flags") or []):
            case_id = str(item.get("case_id") or "").strip()
            if case_id:
                ids.append(case_id)
    return ids[:limit]


def _case_ids_with_value(summaries: list[dict[str, Any]], key: str, value: Any, *, limit: int = 12) -> list[str]:
    ids: list[str] = []
    for item in summaries:
        if item.get(key) == value:
            case_id = str(item.get("case_id") or "").strip()
            if case_id:
                ids.append(case_id)
    return ids[:limit]


def _safe_int(value: Any, *, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _memory_integrity_summary_from_trajectory(trajectory: dict[str, Any]) -> dict[str, Any]:
    events = trajectory.get("events") or []
    snapshots = [
        event.get("content")
        for event in events
        if event.get("event_type") == "clinical_memory_snapshot" and isinstance(event.get("content"), dict)
    ]
    skips = [
        event.get("content")
        for event in events
        if event.get("event_type") == "clinical_memory_skip" and isinstance(event.get("content"), dict)
    ]
    audits = [
        snapshot.get("memory_integrity_audit")
        for snapshot in snapshots
        if isinstance(snapshot.get("memory_integrity_audit"), dict)
    ]
    latest = audits[-1] if audits else {}
    carried_ids: list[str] = []
    missing_ids: list[str] = []
    carried_kernel_keys: list[str] = []
    missing_kernel_keys: list[str] = []
    kernel_item_count = 0
    high_impact_signal_count = 0
    for audit in audits:
        carried_ids.extend(str(item) for item in audit.get("carried_forward_pending_receipt_ids") or [] if item)
        missing_ids.extend(str(item) for item in audit.get("missing_pending_receipt_ids_after_carry_forward") or [] if item)
        carried_kernel_keys.extend(str(item) for item in audit.get("carried_forward_non_compressible_kernel_keys") or [] if item)
        missing_kernel_keys.extend(str(item) for item in audit.get("missing_non_compressible_kernel_keys_after_carry_forward") or [] if item)
        kernel_item_count += int(audit.get("non_compressible_kernel_item_count") or 0)
        high_impact_signal_count += int(audit.get("high_impact_recent_signal_count") or 0)
    return {
        "snapshot_count": len(snapshots),
        "skip_count": len(skips),
        "latest_skip_reason": str((skips[-1] or {}).get("reason") or "") if skips else "",
        "audit_count": len(audits),
        "latest_status": latest.get("status", ""),
        "carried_forward_pending_receipt_count": len(carried_ids),
        "carried_forward_pending_receipt_ids": list(dict.fromkeys(carried_ids))[-12:],
        "missing_pending_receipt_count": len(missing_ids),
        "missing_pending_receipt_ids": list(dict.fromkeys(missing_ids))[-12:],
        "non_compressible_kernel_item_count": kernel_item_count,
        "carried_forward_non_compressible_kernel_count": len(carried_kernel_keys),
        "carried_forward_non_compressible_kernel_keys": list(dict.fromkeys(carried_kernel_keys))[-12:],
        "missing_non_compressible_kernel_count": len(missing_kernel_keys),
        "missing_non_compressible_kernel_keys": list(dict.fromkeys(missing_kernel_keys))[-12:],
        "high_impact_recent_signal_count": high_impact_signal_count,
        "latest_source_anchor_coverage": latest.get("source_anchor_coverage") if isinstance(latest.get("source_anchor_coverage"), dict) else {},
    }


def _flag_counts(summaries: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in summaries:
        for flag in item.get("quality_flags") or []:
            key = str(flag)
            counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _value_counts(items: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = str(item.get(key) or "").strip()
        if not value:
            continue
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _aggregate_nested_counts(items: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        nested = item.get(key) if isinstance(item.get(key), dict) else {}
        for nested_key, value in nested.items():
            try:
                counts[str(nested_key)] = counts.get(str(nested_key), 0) + int(value or 0)
            except (TypeError, ValueError):
                continue
    return dict(sorted(counts.items()))


def _llm_model_counts_from_trajectory(trajectory: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for call in trajectory.get("llm_calls") or []:
        if not isinstance(call, dict):
            continue
        metadata = call.get("metadata") if isinstance(call.get("metadata"), dict) else {}
        model = str(metadata.get("model") or "").strip()
        if not model:
            continue
        counts[model] = counts.get(model, 0) + 1
    return dict(sorted(counts.items()))


def _llm_purpose_model_counts_from_trajectory(trajectory: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for call in trajectory.get("llm_calls") or []:
        if not isinstance(call, dict):
            continue
        purpose = str(call.get("purpose") or "").strip()
        metadata = call.get("metadata") if isinstance(call.get("metadata"), dict) else {}
        model = str(metadata.get("model") or "").strip()
        if not purpose or not model:
            continue
        key = f"{purpose}:{model}"
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _aggregate_friction_category_counts(summaries: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in summaries:
        friction = item.get("real_world_friction") if isinstance(item.get("real_world_friction"), dict) else {}
        category_counts = friction.get("friction_category_counts") if isinstance(friction.get("friction_category_counts"), dict) else {}
        for category, value in category_counts.items():
            try:
                increment = int(value)
            except (TypeError, ValueError):
                continue
            key = str(category)
            counts[key] = counts.get(key, 0) + increment
    return dict(sorted(counts.items()))


def _failure_payload(
    *,
    case_path: str,
    run_id: str,
    args: argparse.Namespace,
    runner: LiteCareLoopRunner | None,
    exc: BaseException,
) -> dict[str, Any]:
    message = str(exc) or type(exc).__name__
    if len(message) > 4000:
        message = message[:4000] + "...[truncated]"
    case_id = getattr(getattr(runner, "case", None), "case_id", Path(case_path).stem)
    trajectory = runner.trajectory.to_dict() if runner is not None else _empty_failed_trajectory(str(case_id), run_id)
    return {
        "case_id": case_id,
        "run_id": run_id,
        "turns_completed": _last_turn(trajectory),
        "closure": {
            "status": "open",
            "rationale": "runtime stopped because an infrastructure/model call failed before closure could be judged.",
            "unresolved_threads": ["runtime_error"],
        },
        "trajectory": trajectory,
        "evaluation": {},
        "runtime_error": {
            "type": type(exc).__name__,
            "message": message,
            "case_path": str(case_path),
            "continue_on_error": bool(args.continue_on_error),
        },
        "metadata": {
            "max_turns": args.max_turns,
            "runtime": "runtime_lite",
            "principle": "thin rules, LLM-native soft judgement, hard boundaries only",
            "failed_before_normal_completion": True,
        },
    }


def _empty_failed_trajectory(case_id: str, run_id: str) -> dict[str, Any]:
    return {
        "protocol": "careloop.runtime_lite.trajectory.v1",
        "case_id": case_id,
        "run_id": run_id,
        "created_at": "",
        "events": [],
        "transcript": [],
        "llm_calls": [],
        "probability_ledger": [],
    }


def _last_turn(trajectory: dict[str, Any]) -> int:
    turns: list[int] = []
    for event in trajectory.get("events") or []:
        if isinstance(event, dict):
            try:
                turns.append(int(event.get("turn") or 0))
            except (TypeError, ValueError):
                pass
    return max(turns) if turns else 0


def _analyze_existing_output_dir(output_dir: Path) -> dict[str, Any]:
    if not output_dir.exists() or not output_dir.is_dir():
        raise RuntimeError(f"output directory does not exist: {output_dir}")
    payload_paths = _existing_payload_paths(output_dir)
    if not payload_paths:
        raise RuntimeError(f"no runtime_lite trajectory.json payloads found under: {output_dir}")
    summaries: list[dict[str, Any]] = []
    for payload_path in payload_paths:
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("trajectory"), dict):
            raise RuntimeError(f"not a runtime_lite payload with trajectory: {payload_path}")
        # Post-hoc analysis is an audit pass, not a trajectory rewrite.  Keep the
        # original trajectory/evaluation artifact immutable so a later analysis
        # cannot accidentally erase final evaluator output by reading a checkpoint
        # or a lower-fidelity recovery payload.
        payload = dict(payload)
        payload["trajectory"] = dict(payload.get("trajectory") or {})
        quality = analyze_lite_trajectory(payload)
        payload["quality_report"] = quality.to_dict()
        payload.setdefault("metadata", {})["posthoc_quality_reanalyzed"] = True
        payload.setdefault("metadata", {})["posthoc_source_payload"] = str(payload_path)
        payload.setdefault("metadata", {})["posthoc_source_was_checkpoint"] = payload_path.name == "trajectory.checkpoint.json"
        _write_posthoc_case_outputs(payload_path.parent, payload)
        summaries.append(_console_summary(payload))
    run_metadata = _posthoc_run_metadata(output_dir, payload_paths, summaries)
    aggregate = _aggregate_with_validation_interpretation(summaries, run_metadata)
    if len(summaries) > 1:
        _write_posthoc_batch_outputs(output_dir, summaries, aggregate, run_metadata)
        return {"run_metadata": run_metadata, "runs": summaries, "aggregate": aggregate}
    single = {**summaries[0], "run_metadata": run_metadata, "aggregate": aggregate}
    (output_dir / "posthoc_analysis_summary.json").write_text(json.dumps(single, ensure_ascii=False, indent=2), encoding="utf-8")
    return single


def _existing_payload_paths(output_dir: Path) -> list[Path]:
    root_payload = _preferred_existing_payload_path(output_dir)
    if root_payload is not None:
        return [root_payload]
    payloads: list[Path] = []
    for child in sorted(path for path in output_dir.iterdir() if path.is_dir()):
        selected = _preferred_existing_payload_path(child)
        if selected is not None:
            payloads.append(selected)
    return payloads


def _preferred_existing_payload_path(case_output_dir: Path) -> Path | None:
    final_path = case_output_dir / "trajectory.json"
    checkpoint_path = case_output_dir / "trajectory.checkpoint.json"
    final_payload = _read_payload_for_selection(final_path)
    checkpoint_payload = _read_payload_for_selection(checkpoint_path)
    if final_payload is None and checkpoint_payload is None:
        return None
    if final_payload is not None:
        # trajectory.json is the authoritative finished/interrupted artifact.
        # Even when it records a runtime_error, it may also contain an interrupt
        # fragment evaluation or other final-state audit fields that checkpoints
        # intentionally do not have.  Never let a checkpoint supersede it during
        # post-hoc analysis.
        return final_path
    return checkpoint_path


def _read_payload_for_selection(path: Path) -> dict[str, Any] | None:
    if not path.exists() or not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("trajectory"), dict):
        return None
    return payload


def _payload_turns_completed(payload: dict[str, Any]) -> int:
    try:
        return int(payload.get("turns_completed") or 0)
    except (TypeError, ValueError):
        return 0


def _posthoc_run_metadata(output_dir: Path, payload_paths: list[Path], summaries: list[dict[str, Any]]) -> dict[str, Any]:
    original: dict[str, Any] = {}
    batch_summary = output_dir / "batch_summary.json"
    if batch_summary.exists():
        try:
            batch_payload = json.loads(batch_summary.read_text(encoding="utf-8"))
            if isinstance(batch_payload, dict) and isinstance(batch_payload.get("run_metadata"), dict):
                original = dict(batch_payload["run_metadata"])
        except Exception:
            original = {}
    if not original and payload_paths:
        try:
            first_payload = json.loads(payload_paths[0].read_text(encoding="utf-8"))
            metadata = first_payload.get("metadata") if isinstance(first_payload, dict) else {}
            cli_metadata = metadata.get("cli_run_metadata") if isinstance(metadata, dict) and isinstance(metadata.get("cli_run_metadata"), dict) else {}
            original = dict(cli_metadata)
        except Exception:
            original = {}
    original.update(
        {
            "runtime": "runtime_lite",
            "stage": "posthoc_output_analysis",
            "source_output_dir": str(output_dir),
            "case_count": len(summaries),
            "reanalyzed_payloads": [str(path) for path in payload_paths],
            "no_llm_or_api_calls": True,
        }
    )
    return original



def _hardening_sidecars_enabled(payload: dict[str, Any]) -> bool:
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    cli_metadata = metadata.get("cli_run_metadata") if isinstance(metadata.get("cli_run_metadata"), dict) else {}
    return str(cli_metadata.get("hardening_sidecars") or "on").strip().lower() != "off"

def _write_runtime_phase_sidecar(output_dir: Path, payload: dict[str, Any], *, phase: str, final_file_written: bool = False) -> None:
    """Write a lightweight final runtime phase marker without prompts/responses/secrets."""

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        closure = payload.get("closure") if isinstance(payload.get("closure"), dict) else {}
        evaluation = payload.get("evaluation") if isinstance(payload.get("evaluation"), dict) else {}
        phase_payload = {
            "protocol": "careloop.runtime_phase.v1",
            "updated_at": _utc_timestamp(),
            "phase": phase,
            "turns_completed": int(payload.get("turns_completed") or 0),
            "max_turns": (payload.get("metadata") or {}).get("max_turns") if isinstance(payload.get("metadata"), dict) else None,
            "current_stage": phase,
            "closure_status": closure.get("status"),
            "closure_terminal": bool(closure.get("is_terminal")),
            "evaluation_overall": evaluation.get("overall"),
            "final_file_written": bool(final_file_written),
            "safe_sidecar": True,
            "redaction_policy": "no_prompt_no_response_no_api_key_no_authorization_header",
        }
        path = output_dir / "runtime_phase.json"
        _write_text_atomic(path, json.dumps(phase_payload, ensure_ascii=False, indent=2))
    except Exception:
        return


def _utc_timestamp() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_outputs(output_dir: Path, payload: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    attach_hardening_reports(payload)
    (output_dir / "trajectory.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if payload.get("quality_report"):
        (output_dir / "quality_report.json").write_text(json.dumps(payload["quality_report"], ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "summary.md").write_text(_summary_markdown(payload), encoding="utf-8")
    if _hardening_sidecars_enabled(payload):
        write_hardening_artifacts(output_dir, payload, output_kind="final", include_deep_audits=True)
    _write_runtime_phase_sidecar(output_dir, payload, phase="exited", final_file_written=True)


def _write_posthoc_case_outputs(output_dir: Path, payload: dict[str, Any]) -> None:
    """Write non-destructive post-hoc analysis artifacts for one case.

    A post-hoc pass must never rewrite trajectory.json or summary.md.  Those are
    the primary evidence artifacts from the actual run; overwriting them can
    destroy final evaluator output, especially when both trajectory.json and a
    checkpoint are present for an interrupted trajectory.
    """

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "posthoc_analysis_payload.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if payload.get("quality_report"):
        (output_dir / "posthoc_quality_report.json").write_text(json.dumps(payload["quality_report"], ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "posthoc_summary.md").write_text(_summary_markdown(payload), encoding="utf-8")


def _write_text_atomic(path: Path, text: str) -> None:
    """Write a checkpoint file by replacing the previous complete file.

    Checkpoints are recovery artifacts.  A partially written checkpoint is worse
    than an older complete checkpoint, so write to a sibling temporary file and
    replace atomically within the same directory.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    tmp_path.write_text(text, encoding="utf-8")
    tmp_path.replace(path)


def _write_checkpoint_outputs(output_dir: Path, payload: dict[str, Any], *, save_all_checkpoints: bool = False) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    attach_hardening_reports(payload)
    turn = int(payload.get("turns_completed") or 0)
    latest_path = output_dir / "trajectory.checkpoint.json"
    latest_summary_path = output_dir / "summary.checkpoint.md"
    checkpoint_json = json.dumps(payload, ensure_ascii=False, indent=2)
    _write_text_atomic(latest_path, checkpoint_json)
    _write_text_atomic(latest_summary_path, _summary_markdown(payload))
    if _hardening_sidecars_enabled(payload):
        write_hardening_artifacts(output_dir, payload, output_kind="checkpoint", include_deep_audits=False)
    _write_runtime_phase_sidecar(output_dir, payload, phase="checkpoint", final_file_written=False)
    if not save_all_checkpoints:
        return
    checkpoint_dir = output_dir / "checkpoints"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = checkpoint_dir / f"turn_{turn:03d}.json"
    _write_text_atomic(checkpoint_path, checkpoint_json)


def _write_batch_outputs(output_dir: Path, summaries: list[dict[str, Any]], aggregate: dict[str, Any], run_metadata: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    if "validation_interpretation" not in aggregate:
        aggregate = dict(aggregate)
        aggregate["validation_interpretation"] = _batch_validation_interpretation(summaries, aggregate, run_metadata)
    payload = {"run_metadata": run_metadata, "runs": summaries, "aggregate": aggregate}
    (output_dir / "batch_summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "batch_report.md").write_text(_batch_report_markdown(summaries, aggregate, run_metadata), encoding="utf-8")


def _write_posthoc_batch_outputs(output_dir: Path, summaries: list[dict[str, Any]], aggregate: dict[str, Any], run_metadata: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    if "validation_interpretation" not in aggregate:
        aggregate = dict(aggregate)
        aggregate["validation_interpretation"] = _batch_validation_interpretation(summaries, aggregate, run_metadata)
    payload = {"run_metadata": run_metadata, "runs": summaries, "aggregate": aggregate}
    (output_dir / "posthoc_batch_summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "posthoc_batch_report.md").write_text(_batch_report_markdown(summaries, aggregate, run_metadata), encoding="utf-8")


def _write_dry_run_outputs(output_dir: Path, payload: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "dry_run.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_case_validation_outputs(output_dir: Path, payload: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "case_validation.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "case_validation_report.md").write_text(_case_validation_markdown(payload), encoding="utf-8")


def _case_validation_markdown(payload: dict[str, Any]) -> str:
    run_metadata = payload.get("run_metadata") or {}
    aggregate = payload.get("aggregate") or {}
    lines = [
        "# runtime_lite case validation report",
        "",
        "## Run metadata",
        "",
        f"- client: {run_metadata.get('client')}",
        f"- case_count: {run_metadata.get('case_count')}",
        f"- case_glob: {json.dumps(run_metadata.get('case_glob') or [], ensure_ascii=False)}",
        f"- sample_size: {run_metadata.get('sample_size')}",
        f"- sample_seed: {run_metadata.get('sample_seed')}",
        "",
        "## Aggregate",
        "",
        f"- total: {aggregate.get('total')}",
        f"- passed: {aggregate.get('passed')}",
        f"- review: {aggregate.get('review')}",
        f"- failed: {aggregate.get('failed')}",
        f"- evaluation_contract_counts: {json.dumps(aggregate.get('evaluation_contract_counts') or {}, ensure_ascii=False)}",
        "",
        "## Coverage counts",
        "",
        *_coverage_counts_markdown_lines(aggregate.get("coverage_counts") or {}),
        "",
        "## Longitudinal coverage insight",
        "",
        *_longitudinal_coverage_markdown_lines(aggregate.get("longitudinal_coverage_insight") or {}),
        "",
        "## Cases",
        "",
        "| status | case_id | title | initial_actor | episode | capabilities | barriers | noise | eval_contract | workspace_panels | warnings | errors |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for item in payload.get("cases") or []:
        if not isinstance(item, dict):
            continue
        coverage = item.get("coverage") if isinstance(item.get("coverage"), dict) else {}
        evaluation_contract = item.get("evaluation_contract") if isinstance(item.get("evaluation_contract"), dict) else {}
        lines.append(
            "| "
            + " | ".join(
                [
                    _md_cell(item.get("status")),
                    _md_cell(item.get("case_id")),
                    _md_cell(item.get("title")),
                    _md_cell(item.get("initial_actor")),
                    _md_cell(coverage.get("episode_type") or coverage.get("case_library_layer")),
                    _md_cell(_coverage_join(coverage.get("capability_tags"))),
                    _md_cell(_coverage_join(coverage.get("patient_barriers"))),
                    _md_cell(
                        _coverage_join(coverage.get("realism_noise_categories"))
                        or str(coverage.get("noise_density") or "")
                    ),
                    _md_cell(_case_validation_contract_cell(evaluation_contract)),
                    _md_cell(", ".join(str(panel) for panel in item.get("workspace_panels") or [])),
                    _md_cell(", ".join(str(warning) for warning in item.get("warnings") or [])),
                    _md_cell(", ".join(str(error) for error in item.get("errors") or [])),
                ]
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def _longitudinal_coverage_markdown_lines(insight: dict[str, Any]) -> list[str]:
    if not isinstance(insight, dict) or not insight:
        return ["- none"]
    lines = [
        f"- principle: {_md_cell(insight.get('principle') or '')}",
        f"- longitudinal_or_result_loop_case_count: {insight.get('longitudinal_or_result_loop_case_count', 0)}",
        f"- record_or_workspace_dependent_case_count: {insight.get('record_or_workspace_dependent_case_count', 0)}",
        f"- patient_execution_barrier_case_count: {insight.get('patient_execution_barrier_case_count', 0)}",
    ]
    signal_counts = insight.get("signal_counts") if isinstance(insight.get("signal_counts"), dict) else {}
    if signal_counts:
        compact_counts = ", ".join(f"{key}:{value}" for key, value in sorted(signal_counts.items()))
        lines.append(f"- signal_counts: {compact_counts}")
    cases_by_signal = insight.get("cases_by_signal") if isinstance(insight.get("cases_by_signal"), dict) else {}
    if cases_by_signal:
        lines.append("- cases_by_signal:")
        for signal, case_ids in sorted(cases_by_signal.items()):
            lines.append(f"  - {signal}: {_coverage_join(case_ids)}")
    return lines


def _coverage_counts_markdown_lines(coverage_counts: dict[str, Any]) -> list[str]:
    if not coverage_counts:
        return ["- none"]
    preferred_fields = [
        "episode_type",
        "case_library_layer",
        "urgency_level",
        "time_window_class",
        "disease_systems",
        "capability_tags",
        "patient_barriers",
        "realism_noise_categories",
        "workspace_dependencies",
    ]
    lines: list[str] = []
    for field in preferred_fields:
        counts = coverage_counts.get(field)
        if not isinstance(counts, dict) or not counts:
            continue
        compact = ", ".join(f"{key}:{value}" for key, value in sorted(counts.items()))
        lines.append(f"- {field}: {compact}")
    return lines or ["- none"]


def _coverage_join(value: Any) -> str:
    if isinstance(value, (list, tuple, set)):
        return ", ".join(str(item) for item in value if str(item).strip())
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value or "")


def _case_validation_contract_cell(contract: dict[str, Any]) -> str:
    if not isinstance(contract, dict) or not contract.get("present"):
        return "missing"
    if contract.get("complete"):
        return "complete"
    missing = contract.get("missing_required_fields") or []
    if missing:
        return "incomplete: " + ",".join(str(item) for item in missing[:4]) + ("..." if len(missing) > 4 else "")
    return "incomplete"


def _md_cell(value: Any) -> str:
    text = " ".join(str(value or "").split())
    return text.replace("|", "\\|")


def _batch_report_markdown(summaries: list[dict[str, Any]], aggregate: dict[str, Any], run_metadata: dict[str, Any]) -> str:
    lines = [
        "# runtime_lite batch report",
        "",
        "## Run metadata",
        "",
        f"- client: {run_metadata.get('client')}",
        f"- long_run_300_preset: {run_metadata.get('long_run_300_preset')}",
        f"- max_turns: {run_metadata.get('max_turns')}",
        f"- parallelism: {run_metadata.get('parallelism')}",
        f"- evaluate_at_end: {run_metadata.get('evaluate_at_end')}",
        f"- resume_existing: {run_metadata.get('resume_existing')}",
        f"- sim_model: {run_metadata.get('sim_model') or 'n/a'}",
        f"- doctor_model: {run_metadata.get('doctor_model') or 'n/a'}",
        f"- sample_size: {run_metadata.get('sample_size')}",
        f"- sample_seed: {run_metadata.get('sample_seed')}",
        f"- case_glob: {json.dumps(run_metadata.get('case_glob') or [], ensure_ascii=False)}",
        f"- timeout_seconds: {run_metadata.get('timeout_seconds')}",
        f"- llm_purpose_timeouts: {json.dumps(run_metadata.get('llm_purpose_timeouts') or {}, ensure_ascii=False)}",
        f"- llm_max_retries: {run_metadata.get('llm_max_retries')}",
        f"- llm_retry_until_success: {run_metadata.get('llm_retry_until_success')}",
        f"- llm_retry_delay_seconds: {run_metadata.get('llm_retry_backoff_seconds')}–{run_metadata.get('llm_retry_max_delay_seconds')}",
        "",
        "## Aggregate",
        "",
        f"- case_count: {aggregate.get('case_count')}",
        f"- total_turns_completed: {aggregate.get('total_turns_completed')}",
        f"- total_llm_calls: {aggregate.get('total_llm_calls')}",
        f"- average_llm_calls_per_turn: {aggregate.get('average_llm_calls_per_turn')}",
        f"- llm_model_counts: {json.dumps(aggregate.get('llm_model_counts') or {}, ensure_ascii=False)}",
        f"- llm_purpose_model_counts: {json.dumps(aggregate.get('llm_purpose_model_counts') or {}, ensure_ascii=False)}",
        f"- total_committed_world_events: {aggregate.get('total_committed_world_events')}",
        f"- average_committed_world_events_per_turn: {aggregate.get('average_committed_world_events_per_turn')}",
        f"- total_non_occurred_event_candidates: {aggregate.get('total_non_occurred_event_candidates')}",
        f"- total_doctor_system_notifications: {aggregate.get('total_doctor_system_notifications')}",
        f"- total_living_state_updates: {aggregate.get('total_living_state_updates')}",
        f"- total_real_world_friction_signals: {aggregate.get('total_real_world_friction_signals')}",
        f"- total_doctor_adaptations_after_friction: {aggregate.get('total_doctor_adaptations_after_friction')}",
        f"- real_world_friction_category_counts: {json.dumps(aggregate.get('real_world_friction_category_counts') or {}, ensure_ascii=False)}",
        f"- actor_high_cooperation_risk_status_counts: {json.dumps(aggregate.get('actor_high_cooperation_risk_status_counts') or {}, ensure_ascii=False)}",
        f"- total_actor_overstructured_message_count: {aggregate.get('total_actor_overstructured_message_count')}",
        f"- external_progression_dependency_status_counts: {json.dumps(aggregate.get('external_progression_dependency_status_counts') or {}, ensure_ascii=False)}",
        f"- trajectory_progression_status_counts: {json.dumps(aggregate.get('trajectory_progression_status_counts') or {}, ensure_ascii=False)}",
        f"- clinical_core_loop_status_counts: {json.dumps(aggregate.get('clinical_core_loop_status_counts') or {}, ensure_ascii=False)}",
        f"- average_clinical_core_stage_count: {aggregate.get('average_clinical_core_stage_count')}",
        f"- total_material_progress_signal_count: {aggregate.get('total_material_progress_signal_count')}",
        f"- total_material_progress_turn_count: {aggregate.get('total_material_progress_turn_count')}",
        f"- total_substantive_progress_signal_count: {aggregate.get('total_substantive_progress_signal_count')}",
        f"- average_material_progress_signals_per_case: {aggregate.get('average_material_progress_signals_per_case')}",
        f"- average_substantive_progress_signals_per_case: {aggregate.get('average_substantive_progress_signals_per_case')}",
        f"- mechanical_progress_only_count: {aggregate.get('mechanical_progress_only_count')}",
        f"- mechanical_repetition_loop_count: {aggregate.get('mechanical_repetition_loop_count')}",
        f"- open_at_max_but_progressing_count: {aggregate.get('open_at_max_but_progressing_count')}",
        f"- average_care_loop_phase_count: {aggregate.get('average_care_loop_phase_count')}",
        f"- average_care_process_step_count: {aggregate.get('average_care_process_step_count')}",
        f"- care_loop_shape_counts: {json.dumps(aggregate.get('care_loop_shape_counts') or {}, ensure_ascii=False)}",
        f"- clinical_memory_snapshot_count: {aggregate.get('clinical_memory_snapshot_count')}",
        f"- clinical_memory_skip_count: {aggregate.get('clinical_memory_skip_count')}",
        f"- memory_integrity_status_counts: {json.dumps(aggregate.get('memory_integrity_status_counts') or {}, ensure_ascii=False)}",
        f"- memory_integrity_carried_forward_pending_receipt_count: {aggregate.get('memory_integrity_carried_forward_pending_receipt_count')}",
        f"- memory_integrity_missing_pending_receipt_count: {aggregate.get('memory_integrity_missing_pending_receipt_count')}",
        f"- memory_integrity_non_compressible_kernel_item_count: {aggregate.get('memory_integrity_non_compressible_kernel_item_count')}",
        f"- memory_integrity_carried_forward_non_compressible_kernel_count: {aggregate.get('memory_integrity_carried_forward_non_compressible_kernel_count')}",
        f"- memory_integrity_missing_non_compressible_kernel_count: {aggregate.get('memory_integrity_missing_non_compressible_kernel_count')}",
        f"- total_elapsed_minutes: {aggregate.get('total_elapsed_minutes')}",
        f"- runtime_error_count: {aggregate.get('runtime_error_count')}",
        f"- resumed_existing_count: {aggregate.get('resumed_existing_count')}",
        f"- closure_status_counts: {json.dumps(aggregate.get('closure_status_counts') or {}, ensure_ascii=False)}",
        f"- evaluation_mode_counts: {json.dumps(aggregate.get('evaluation_mode_counts') or {}, ensure_ascii=False)}",
        f"- evaluation_overall_counts: {json.dumps(aggregate.get('evaluation_overall_counts') or {}, ensure_ascii=False)}",
        f"- benchmark_validity_counts: {json.dumps(aggregate.get('benchmark_validity_counts') or {}, ensure_ascii=False)}",
        f"- benchmark_primary_count: {aggregate.get('benchmark_primary_count')}",
        f"- visibility_boundary_status_counts: {json.dumps(aggregate.get('visibility_boundary_status_counts') or {}, ensure_ascii=False)}",
        f"- visibility_boundary_max_severity_counts: {json.dumps(aggregate.get('visibility_boundary_max_severity_counts') or {}, ensure_ascii=False)}",
        f"- evaluation_simulation_validity_status_counts: {json.dumps(aggregate.get('evaluation_simulation_validity_status_counts') or {}, ensure_ascii=False)}",
        f"- evaluation_claim_strength_counts: {json.dumps(aggregate.get('evaluation_claim_strength_counts') or {}, ensure_ascii=False)}",
        f"- evaluation_repair_attempted_count: {aggregate.get('evaluation_repair_attempted_count')}",
        f"- evaluation_repair_used_count: {aggregate.get('evaluation_repair_used_count')}",
        f"- evaluation_repair_success_count: {aggregate.get('evaluation_repair_success_count')}",
        f"- evaluation_original_failure_type_counts: {json.dumps(aggregate.get('evaluation_original_failure_type_counts') or {}, ensure_ascii=False)}",
        f"- evaluation_prior_contract_weight_item_count: {aggregate.get('evaluation_prior_contract_weight_item_count')}",
        f"- evaluation_trajectory_emergent_weight_item_count: {aggregate.get('evaluation_trajectory_emergent_weight_item_count')}",
        f"- evaluation_final_trajectory_weight_item_count: {aggregate.get('evaluation_final_trajectory_weight_item_count')}",
        f"- evaluation_dynamic_weight_item_count: {aggregate.get('evaluation_dynamic_weight_item_count')}",
        f"- evaluation_critical_failure_count: {aggregate.get('evaluation_critical_failure_count')}",
        f"- quality_status_counts: {json.dumps(aggregate.get('quality_status_counts') or {}, ensure_ascii=False)}",
        f"- quality_flag_counts: {json.dumps(aggregate.get('quality_flag_counts') or {}, ensure_ascii=False)}",
        "",
        "## Validation interpretation",
        "",
        *_validation_interpretation_markdown_lines(aggregate.get("validation_interpretation") or {}),
        "",
        "## Care-loop evidence by run",
        "",
        "| case_id | progression | material | care_loop | phases | care process | result chain | operations | panels | memory integrity | friction/adaptation | flags |",
        "|---|---|---|---|---:|---|---|---|---|---|---|---|",
    ]
    for item in summaries:
        case_id = str(item.get("case_id") or "")
        case_link = f"[{case_id}]({_safe_name(case_id)}/summary.md)" if case_id else ""
        care_loop = item.get("care_loop") if isinstance(item.get("care_loop"), dict) else {}
        care_process = (
            item.get("longitudinal_care_process")
            if isinstance(item.get("longitudinal_care_process"), dict)
            else {}
        )
        friction = item.get("real_world_friction") if isinstance(item.get("real_world_friction"), dict) else {}
        lines.append(
            "| "
            + " | ".join(
                [
                    case_link,
                    _md_cell(item.get("trajectory_progression_status") or "n/a"),
                    _md_cell(
                        f"signals:{item.get('material_progress_signal_count') or 0}; "
                        f"substantive:{item.get('substantive_progress_signal_count') or 0}; "
                        f"mechanical_only:{_yes_no(item.get('mechanical_progress_only'))}; "
                        f"mechanical_loop:{_yes_no(item.get('mechanical_repetition_loop'))}; "
                        f"turns:{item.get('material_progress_turn_count') or 0}; "
                        f"last:T{item.get('last_material_progress_turn') or 0}"
                    ),
                    _md_cell(item.get("care_loop_shape")),
                    str(item.get("care_loop_phase_count") or 0),
                    _md_cell(_batch_care_process_summary(care_process)),
                    _md_cell(_batch_result_chain_summary(care_loop)),
                    _md_cell(_operation_counts_summary(care_loop.get("operation_counts"))),
                    _md_cell(", ".join(str(panel) for panel in care_loop.get("queried_panels") or []) or "none"),
                    _md_cell(_batch_memory_integrity_summary(item.get("memory_integrity") if isinstance(item.get("memory_integrity"), dict) else {})),
                    _md_cell(_batch_friction_summary(item, friction)),
                    _md_cell(", ".join(str(flag) for flag in item.get("quality_flags") or []) or "none"),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
        "## Runs",
        "",
        "| case_id | turns | closure | eval_mode | eval | sim_validity | quality | progression | care_loop | phases | llm_calls | llm_models | world_events | material_signals | living_memory | memory_snapshots | memory_skips | system_notices | elapsed_min | receipts | workspace_results | eval_weights | crit_failures | resumed | flags | error |",
        "|---|---:|---|---|---|---|---|---|---|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|",
        ]
    )
    for item in summaries:
        case_id = str(item.get("case_id") or "")
        case_link = f"[{case_id}]({_safe_name(case_id)}/summary.md)" if case_id else ""
        flags = ", ".join(str(flag) for flag in item.get("quality_flags") or [])
        error = "yes" if item.get("runtime_error") else ""
        lines.append(
            "| "
            + " | ".join(
                [
                    case_link,
                    str(item.get("turns_completed") or 0),
                    str(item.get("closure") or ""),
                    str(item.get("evaluation_mode") or ""),
                    str(item.get("evaluation_overall") or ""),
                    str(item.get("evaluation_simulation_validity_status") or ""),
                    str(item.get("quality_status") or ""),
                    str(item.get("trajectory_progression_status") or ""),
                    str(item.get("care_loop_shape") or ""),
                    str(item.get("care_loop_phase_count") or 0),
                    str(item.get("llm_call_count") or 0),
                    _md_cell(_counts_summary(item.get("llm_model_counts"))),
                    str(item.get("committed_world_event_count") or 0),
                    str(item.get("material_progress_signal_count") or 0),
                    str(item.get("living_state_update_count") or 0),
                    str(item.get("clinical_memory_snapshot_count") or 0),
                    str(item.get("clinical_memory_skip_count") or 0),
                    str(item.get("doctor_system_notification_count") or 0),
                    str(item.get("total_elapsed_minutes") or 0),
                    str(item.get("care_system_receipt_count") or 0),
                    str(item.get("workspace_result_count") or 0),
                    str(item.get("evaluation_dynamic_weight_count") or 0),
                    str(item.get("evaluation_critical_failure_count") or 0),
                    "yes" if item.get("resumed_from_existing_output") else "",
                    flags.replace("|", "\\|"),
                    error,
                ]
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def _validation_interpretation_markdown_lines(interpretation: dict[str, Any]) -> list[str]:
    if not isinstance(interpretation, dict) or not interpretation:
        return ["- not available"]
    scope = interpretation.get("scope") if isinstance(interpretation.get("scope"), dict) else {}
    progression = (
        interpretation.get("progression_evidence")
        if isinstance(interpretation.get("progression_evidence"), dict)
        else {}
    )
    closed_loop = (
        interpretation.get("closed_loop_evidence")
        if isinstance(interpretation.get("closed_loop_evidence"), dict)
        else {}
    )
    longitudinal = (
        interpretation.get("longitudinal_and_noise_evidence")
        if isinstance(interpretation.get("longitudinal_and_noise_evidence"), dict)
        else {}
    )
    evaluation = (
        interpretation.get("evaluation_evidence")
        if isinstance(interpretation.get("evaluation_evidence"), dict)
        else {}
    )
    lines = [
        f"- status: {interpretation.get('status')}",
        f"- headline: {interpretation.get('headline')}",
        f"- posthoc_only_not_runtime_control: {interpretation.get('posthoc_only_not_runtime_control')}",
        f"- scope: {json.dumps(scope, ensure_ascii=False)}",
        f"- progression: {json.dumps(progression, ensure_ascii=False)}",
        f"- closed_loop: {json.dumps(closed_loop, ensure_ascii=False)}",
        f"- longitudinal_and_noise: {json.dumps(longitudinal, ensure_ascii=False)}",
        f"- evaluation: {json.dumps(evaluation, ensure_ascii=False)}",
    ]
    strengths = interpretation.get("strengths") if isinstance(interpretation.get("strengths"), list) else []
    concerns = interpretation.get("concerns") if isinstance(interpretation.get("concerns"), list) else []
    next_review = (
        interpretation.get("recommended_next_review")
        if isinstance(interpretation.get("recommended_next_review"), list)
        else []
    )
    lines.append(f"- strengths: {'; '.join(str(item) for item in strengths) if strengths else 'none'}")
    lines.append(f"- concerns: {'; '.join(str(item) for item in concerns) if concerns else 'none'}")
    lines.append(f"- recommended_next_review: {'; '.join(str(item) for item in next_review) if next_review else 'none'}")
    return lines


def _batch_result_chain_summary(care_loop: dict[str, Any]) -> str:
    result_signals = len(care_loop.get("result_signal_turns") or [])
    bits = [
        f"signals:{result_signals}",
        f"tracking:{_yes_no(care_loop.get('result_tracking_registered'))}",
        f"reviewed:{_yes_no(care_loop.get('result_reviewed_after_return'))}",
        f"actioned:{_yes_no(care_loop.get('doctor_actioned_after_result_return'))}",
        f"executed_after_action:{_yes_no(care_loop.get('patient_execution_after_result_action'))}",
        f"followup:{_yes_no(care_loop.get('followup_scheduled'))}",
    ]
    return "; ".join(bits)


def _batch_care_process_summary(care_process: dict[str, Any]) -> str:
    if not isinstance(care_process, dict) or not care_process:
        return "none"
    flags = care_process.get("step_flags") if isinstance(care_process.get("step_flags"), dict) else {}
    bits = [
        f"steps:{care_process.get('step_count') or 0}",
        f"questions:{len(care_process.get('doctor_question_turns') or [])}",
        f"history:{_yes_no(flags.get('history_or_medication_context_explored'))}",
        f"execution:{_yes_no(flags.get('real_world_execution_context_explored'))}",
        f"records:{_yes_no(flags.get('records_or_reports_requested_or_queried'))}",
        f"safety_net:{_yes_no(flags.get('safety_net_present'))}",
        f"followup:{_yes_no(flags.get('followup_or_monitoring_present'))}",
    ]
    return "; ".join(bits)


def _batch_memory_integrity_summary(memory: dict[str, Any]) -> str:
    if not isinstance(memory, dict) or not memory:
        return "none"
    bits = [
        f"snapshots:{memory.get('snapshot_count') or 0}",
        f"skips:{memory.get('skip_count') or 0}",
        f"audits:{memory.get('audit_count') or 0}",
        f"latest:{memory.get('latest_status') or 'n/a'}",
        f"carried:{memory.get('carried_forward_pending_receipt_count') or 0}",
        f"missing:{memory.get('missing_pending_receipt_count') or 0}",
        f"kernel:{memory.get('non_compressible_kernel_item_count') or 0}",
        f"kernel_carried:{memory.get('carried_forward_non_compressible_kernel_count') or 0}",
        f"kernel_missing:{memory.get('missing_non_compressible_kernel_count') or 0}",
        f"high_impact_signals:{memory.get('high_impact_recent_signal_count') or 0}",
    ]
    if memory.get("latest_skip_reason"):
        bits.append(f"latest_skip:{memory.get('latest_skip_reason')}")
    return "; ".join(bits)


def _operation_counts_summary(operation_counts: Any) -> str:
    if not isinstance(operation_counts, dict) or not operation_counts:
        return "none"
    return ", ".join(f"{operation}:{count}" for operation, count in sorted(operation_counts.items()))


def _batch_friction_summary(item: dict[str, Any], friction: dict[str, Any]) -> str:
    categories = friction.get("friction_category_counts") if isinstance(friction.get("friction_category_counts"), dict) else {}
    category_text = ", ".join(f"{key}:{value}" for key, value in sorted(categories.items())) or "none"
    return (
        f"signals:{item.get('real_world_friction_signal_count') or 0}; "
        f"adaptations:{item.get('doctor_adaptation_after_friction_count') or 0}; "
        f"categories:{category_text}"
    )


def _summary_markdown(payload: dict[str, Any]) -> str:
    trajectory = payload.get("trajectory") or {}
    closure = payload.get("closure") or {}
    evaluation = payload.get("evaluation") or {}
    quality = payload.get("quality_report") or analyze_lite_trajectory(payload).to_dict()
    lines = [
        f"# runtime_lite run: {payload.get('case_id')}",
        "",
        f"- run_id: {payload.get('run_id')}",
        f"- turns_completed: {payload.get('turns_completed')}",
        f"- closure: {closure.get('status')}",
        f"- evaluation_mode: {evaluation.get('evaluation_mode')}",
        f"- evaluation_overall: {evaluation.get('overall')}",
        f"- runtime_error: {(payload.get('runtime_error') or {}).get('message', '') or 'none'}",
        f"- events: {len(trajectory.get('events') or [])}",
        f"- transcript messages: {len(trajectory.get('transcript') or [])}",
        f"- llm calls: {len(trajectory.get('llm_calls') or [])}",
        f"- llm model counts: {_counts_summary(_llm_model_counts_from_trajectory(trajectory))}",
        f"- llm purpose/model counts: {_counts_summary(_llm_purpose_model_counts_from_trajectory(trajectory))}",
        f"- care-system receipts: {sum(1 for event in trajectory.get('events') or [] if event.get('event_type') == 'doctor_care_system_receipt')}",
        f"- doctor system notifications: {sum(1 for event in trajectory.get('events') or [] if event.get('event_type') == 'doctor_system_notification')}",
        f"- quality_status: {quality.get('status')}",
        f"- quality_flags: {', '.join(quality.get('flags') or []) if quality else 'none'}",
        f"- trajectory_progression_status: {((quality.get('metrics') or {}).get('trajectory_progression_status') if quality else '')}",
        f"- care_loop_shape: {((quality.get('metrics') or {}).get('care_loop_shape') if quality else '')}",
        f"- care_loop_phase_count: {((quality.get('metrics') or {}).get('care_loop_phase_count') if quality else '')}",
        f"- care_process_step_count: {((quality.get('metrics') or {}).get('care_process_step_count') if quality else '')}",
        "",
        "## Hardening brief",
        "",
        *hardening_summary_markdown_lines(payload),
        "",
        "## Trajectory audit brief",
        "",
        *_trajectory_audit_brief_lines(payload, quality),
        "",
        "## Transcript",
        "",
    ]
    for item in trajectory.get("transcript") or []:
        speaker = item.get("speaker_display") or item.get("speaker")
        raw_speaker = item.get("speaker")
        category = item.get("speaker_category")
        relationship = item.get("relationship_to_patient")
        identity_bits = [str(bit) for bit in [category, relationship] if bit]
        identity = f" [{' / '.join(identity_bits)}]" if identity_bits else ""
        suffix = f" ({raw_speaker})" if raw_speaker and raw_speaker != speaker else ""
        lines.append(f"- T{item.get('turn')} {speaker}{suffix}{identity}: {item.get('text')}")
    lines.extend(["", "## World progression", ""])
    lines.extend(_world_progression_lines(trajectory))
    lines.extend(["", "## Doctor operations", ""])
    lines.extend(_doctor_operation_lines(trajectory))
    lines.extend(
        ["", quality_report_markdown(analyze_lite_trajectory(payload)).rstrip(), "", "## Evaluation brief", ""]
    )
    lines.extend(_evaluation_brief_lines(evaluation))
    lines.extend(["", "## Evaluation", "", json.dumps(evaluation, ensure_ascii=False, indent=2)])
    return "\n".join(lines) + "\n"


def _evaluation_brief_lines(evaluation: dict[str, Any]) -> list[str]:
    if not isinstance(evaluation, dict) or not evaluation:
        return ["- no evaluation available"]
    simulation_validity = (
        evaluation.get("simulation_validity") if isinstance(evaluation.get("simulation_validity"), dict) else {}
    )
    fragment_validity = evaluation.get("fragment_validity") if isinstance(evaluation.get("fragment_validity"), dict) else {}
    evaluation_validity = simulation_validity or fragment_validity
    validity_label = "simulation_validity" if simulation_validity else "fragment_validity"
    repair_metadata = evaluation.get("repair_metadata") if isinstance(evaluation.get("repair_metadata"), dict) else {}
    infrastructure_integrity = (
        evaluation.get("infrastructure_integrity") if isinstance(evaluation.get("infrastructure_integrity"), dict) else {}
    )
    return [
        f"- evaluation_mode: {evaluation.get('evaluation_mode') or 'n/a'}",
        f"- overall: {evaluation.get('overall') or 'n/a'}",
        f"- claim_strength: {evaluation.get('evaluation_claim_strength') or 'n/a'}",
        f"- repair: attempted={_yes_no(repair_metadata.get('repair_attempted'))}; "
        f"used={_yes_no(repair_metadata.get('repair_used'))}; "
        f"success={_yes_no(repair_metadata.get('repair_success'))}; "
        f"original={repair_metadata.get('original_evaluator_purpose') or 'n/a'} / "
        f"{repair_metadata.get('original_failure_type') or infrastructure_integrity.get('main_evaluator_failure_type') or infrastructure_integrity.get('failure_type') or 'n/a'}",
        f"- summary: {_brief_text(evaluation.get('summary'))}",
        f"- {validity_label}: status={evaluation_validity.get('status') or 'n/a'}; "
        f"limitations={_list_summary(evaluation_validity.get('limitations'))}; "
        f"rationale={_brief_text(evaluation_validity.get('rationale') or evaluation_validity.get('reason'))}",
        f"- closure_quality: {_brief_text(evaluation.get('closure_quality'))}",
        f"- care_loop_evidence_read: {_brief_text(evaluation.get('care_loop_evidence_read'))}",
        f"- doctor_performance_dimensions: {_evaluation_dimensions_summary(evaluation.get('doctor_performance_dimensions'))}",
        f"- prior_contract_weights: {_evaluation_weights_summary(evaluation.get('prior_contract_weights'))}",
        f"- trajectory_emergent_weights: {_evaluation_weights_summary(evaluation.get('trajectory_emergent_weights'))}",
        f"- final_trajectory_specific_weights: {_evaluation_weights_summary(evaluation.get('final_trajectory_specific_weights'))}",
        f"- trajectory_specific_weights: {_evaluation_weights_summary(evaluation.get('trajectory_specific_weights'))}",
        f"- dynamic_weight_notes: {_list_summary(evaluation.get('dynamic_weight_notes'))}",
        f"- missed_opportunities: {_list_summary(evaluation.get('missed_opportunities'))}",
        f"- critical_failures: {_list_summary(evaluation.get('critical_failures'))}",
    ]


def _evaluation_dimensions_summary(value: Any) -> str:
    if not isinstance(value, dict) or not value:
        return "none"
    bits = [f"{dimension}={_brief_text(read, limit=90)}" for dimension, read in sorted(value.items())]
    return "; ".join(bits)


def _evaluation_weights_summary(value: Any) -> str:
    if not isinstance(value, list) or not value:
        return "none"
    bits: list[str] = []
    for item in value[:8]:
        if not isinstance(item, dict):
            continue
        dimension = str(item.get("dimension") or "dimension").strip()
        weight = str(item.get("relative_weight") or item.get("relative_weight_delta") or "n/a").strip()
        reason = _brief_text(item.get("reason") or item.get("trigger"), limit=90)
        bits.append(f"{dimension}:{weight} ({reason})")
    return "; ".join(bits) if bits else "none"


def _brief_text(value: Any, *, limit: int = 160) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        return "n/a"
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 14)].rstrip() + " ...[truncated]"


def _trajectory_audit_brief_lines(payload: dict[str, Any], quality: dict[str, Any]) -> list[str]:
    """Human-readable, non-scoring audit of whether a trajectory exercised CareLoop_FCCT-1's target loop.

    The raw trajectory remains the source of truth.  This brief intentionally
    avoids judging the doctor; it exposes the evidence a human/LLM evaluator
    needs for the project question: did this run create a realistic, long-form
    medical-world interaction rather than a superficial chat or rigid handoff?
    """

    metrics = quality.get("metrics") if isinstance(quality.get("metrics"), dict) else {}
    trajectory = payload.get("trajectory") if isinstance(payload.get("trajectory"), dict) else {}
    memory_integrity = _memory_integrity_summary_from_trajectory(trajectory)
    care_loop = metrics.get("care_loop") if isinstance(metrics.get("care_loop"), dict) else {}
    care_process = (
        metrics.get("longitudinal_care_process")
        if isinstance(metrics.get("longitudinal_care_process"), dict)
        else {}
    )
    clinical_core = metrics.get("clinical_core_loop") if isinstance(metrics.get("clinical_core_loop"), dict) else {}
    friction = metrics.get("real_world_friction") if isinstance(metrics.get("real_world_friction"), dict) else {}
    progression = (
        metrics.get("trajectory_progression")
        if isinstance(metrics.get("trajectory_progression"), dict)
        else {}
    )
    flags = quality.get("flags") or []
    committed_titles = care_loop.get("committed_world_event_titles") or []
    operation_counts = care_loop.get("operation_counts") if isinstance(care_loop.get("operation_counts"), dict) else {}
    receipt_status_counts = (
        care_loop.get("receipt_status_counts") if isinstance(care_loop.get("receipt_status_counts"), dict) else {}
    )
    update_status_counts = (
        care_loop.get("care_system_update_status_counts")
        if isinstance(care_loop.get("care_system_update_status_counts"), dict)
        else {}
    )
    friction_categories = (
        friction.get("friction_category_counts")
        if isinstance(friction.get("friction_category_counts"), dict)
        else {}
    )
    actor_examples = friction.get("actor_friction_examples") or []
    adaptation_examples = friction.get("doctor_adaptation_examples") or []
    actor_cooperation = metrics.get("actor_cooperation_realism") if isinstance(metrics.get("actor_cooperation_realism"), dict) else {}
    external_dependency = metrics.get("external_progression_dependency") if isinstance(metrics.get("external_progression_dependency"), dict) else {}

    lines = [
        f"- closure/evaluation: closure={((payload.get('closure') or {}).get('status') or 'n/a')}; "
        f"evaluation={((payload.get('evaluation') or {}).get('overall') or 'n/a')}; "
        f"quality={quality.get('status') or 'n/a'}",
        f"- virtual time and progression: elapsed={metrics.get('total_elapsed_minutes', 0)} min; "
        f"last_sim_time={metrics.get('last_sim_time') or 'n/a'}; "
        f"committed_world_events={metrics.get('committed_world_event_count', 0)}; "
        f"living_state_updates={metrics.get('living_state_update_count', 0)}; "
        f"doctor_system_notifications={metrics.get('doctor_system_notification_count', 0)}",
        f"- trajectory progression audit: status={progression.get('status') or 'n/a'}; "
        f"reason={_brief_text(progression.get('status_reason'))}; "
        f"material_signals={progression.get('material_progress_signal_count', 0)}; "
        f"substantive_signals={progression.get('substantive_progress_signal_count', 0)}; "
        f"mechanical_only={_yes_no(progression.get('mechanical_progress_only'))}; "
        f"mechanical_loop={_yes_no(progression.get('mechanical_repetition_loop'))}; "
        f"material_turns={progression.get('material_progress_turn_count', 0)}; "
        f"last_material_turn=T{progression.get('last_material_progress_turn', 0)}; "
        f"open_at_max_but_progressing={_yes_no(progression.get('open_at_max_but_progressing'))}",
        f"- care-loop shape: {metrics.get('care_loop_shape') or 'unknown'}; "
        f"phase_count={metrics.get('care_loop_phase_count', 0)}; "
        f"phases={_phase_summary(care_loop.get('phases'))}",
        f"- care-process evidence: {_batch_care_process_summary(care_process)}; "
        f"understanding_or_feasibility={_yes_no((care_process.get('step_flags') or {}).get('understanding_or_feasibility_checked') if isinstance(care_process.get('step_flags'), dict) else False)}",
        f"- clinical-core loop: status={clinical_core.get('status') or 'n/a'}; "
        f"stage_count={clinical_core.get('stage_count', 0)}; "
        f"stages={_phase_summary(clinical_core.get('stage_flags'))}",
        f"- evidence acquisition: panels={_list_summary(care_loop.get('queried_panels'))}; "
        f"prior_records_or_timeline={_yes_no(care_loop.get('prior_records_or_timeline_queried'))}; "
        f"test_results_or_documents={_yes_no(care_loop.get('test_results_or_documents_queried'))}; "
        f"medications={_yes_no(care_loop.get('medication_context_queried'))}; "
        f"access_context={_yes_no(care_loop.get('care_access_context_queried'))}",
        f"- doctor-side operations: operations={_counts_summary(operation_counts)}; "
        f"receipt_status={_counts_summary(receipt_status_counts)}; "
        f"system_updates={_counts_summary(update_status_counts)}; "
        f"followup={_yes_no(care_loop.get('followup_scheduled'))}; "
        f"result_tracking={_yes_no(care_loop.get('result_tracking_registered'))}; "
        f"handoff_or_emergency={_yes_no(care_loop.get('handoff_or_emergency_registered'))}",
        f"- result/action chain: result_signals={_list_summary(care_loop.get('result_signal_turns'))}; "
        f"tracking_registered={_yes_no(care_loop.get('result_tracking_registered'))}; "
        f"reviewed_after_return={_yes_no(care_loop.get('result_reviewed_after_return'))}; "
        f"actioned_after_result={_yes_no(care_loop.get('doctor_actioned_after_result_return'))}; "
        f"patient_execution_after_result_action={_yes_no(care_loop.get('patient_execution_after_result_action'))}",
        f"- real-world friction: signals={metrics.get('real_world_friction_signal_count', 0)}; "
        f"categories={_counts_summary(friction_categories)}; "
        f"doctor_adaptations={metrics.get('doctor_adaptation_after_friction_count', 0)}",
        f"- actor cooperation realism: status={actor_cooperation.get('status') or 'n/a'}; "
        f"overstructured_messages={actor_cooperation.get('overstructured_message_count', 0)}; "
        f"markers={_counts_summary(actor_cooperation.get('marker_counts'))}",
        f"- external/world progression dependency: status={external_dependency.get('status') or 'n/a'}; "
        f"reasons={_list_summary(external_dependency.get('reasons') or [])}; "
        f"non_handoff_doctor_actions={external_dependency.get('non_handoff_doctor_action_count', 0)}; "
        f"world_or_system_progress_events={external_dependency.get('world_or_system_progress_event_count', 0)}",
        f"- probability audit: {_probability_audit_summary(trajectory)}",
        f"- memory integrity: {_batch_memory_integrity_summary(memory_integrity)}",
        f"- quality cautions: {', '.join(str(flag) for flag in flags) if flags else 'none'}",
    ]
    if committed_titles:
        lines.append(f"- committed event examples: {_list_summary(committed_titles, limit=5)}")
    if actor_examples:
        lines.append(f"- friction example: {_compact((actor_examples[0] or {}).get('text_preview') or '')}")
    if adaptation_examples:
        lines.append(f"- doctor adaptation example: {_compact((adaptation_examples[0] or {}).get('text_preview') or '')}")
    return lines


def _phase_summary(phases: Any) -> str:
    if not isinstance(phases, dict) or not phases:
        return "none"
    active = [str(name) for name, enabled in phases.items() if enabled]
    inactive = [str(name) for name, enabled in phases.items() if not enabled]
    bits = []
    if active:
        bits.append("active=" + ",".join(active))
    if inactive:
        bits.append("missing=" + ",".join(inactive))
    return "; ".join(bits) if bits else "none"


def _counts_summary(counts: Any) -> str:
    if not isinstance(counts, dict) or not counts:
        return "none"
    return ", ".join(f"{key}:{value}" for key, value in sorted(counts.items()))


def _probability_audit_summary(trajectory: dict[str, Any]) -> str:
    """Compactly expose the single-roll probability ledger for human review.

    The runtime stores probabilities under estimated_probability /
    sampled_probability and the deterministic occurrence roll under
    event_roll. Keeping these exact field names visible in the summary avoids
    stale analysis scripts accidentally reading legacy probability/roll keys
    and concluding that the ledger is empty.
    """

    ledger = trajectory.get("probability_ledger") if isinstance(trajectory, dict) else None
    if not isinstance(ledger, list) or not ledger:
        return "records=0"

    resolved = [record for record in ledger if isinstance(record, dict) and record.get("status") == "resolved"]
    creates_world_fact = [
        record for record in ledger if isinstance(record, dict) and bool(record.get("creates_world_fact"))
    ]
    occurred = [record for record in ledger if isinstance(record, dict) and bool(record.get("occurred"))]
    not_occurred = [
        record
        for record in ledger
        if isinstance(record, dict) and record.get("occurred") is False
    ]
    blank_event_ids = [
        record
        for record in ledger
        if isinstance(record, dict) and not str(record.get("event_id") or "").strip()
    ]
    recent_bits: list[str] = []
    for record in [item for item in ledger if isinstance(item, dict)][-5:]:
        event_id = str(record.get("event_id") or "<blank>").strip() or "<blank>"
        probability = record.get("estimated_probability")
        if probability is None:
            probability = record.get("sampled_probability")
        roll = record.get("event_roll")
        outcome = (
            "occurred"
            if record.get("occurred") is True
            else "not_occurred"
            if record.get("occurred") is False
            else "unknown"
        )
        recent_bits.append(f"{event_id}:p={_fmt_probability(probability)}/roll={_fmt_probability(roll)}/{outcome}")

    return (
        f"records={len(ledger)}; resolved={len(resolved)}; world_fact={len(creates_world_fact)}; "
        f"occurred={len(occurred)}; not_occurred={len(not_occurred)}; "
        f"blank_event_id={len(blank_event_ids)}; recent={_list_summary(recent_bits, limit=5)}"
    )


def _fmt_probability(value: Any) -> str:
    try:
        if value is None:
            return "n/a"
        return f"{float(value):.3f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return str(value)


def _list_summary(value: Any, *, limit: int = 8) -> str:
    if not isinstance(value, (list, tuple, set)) or not value:
        return "none"
    items = [str(item) for item in list(value)[:limit]]
    suffix = "" if len(value) <= limit else f", +{len(value) - limit} more"
    return ", ".join(items) + suffix


def _yes_no(value: Any) -> str:
    return "yes" if bool(value) else "no"


def _world_progression_lines(trajectory: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for event in trajectory.get("events") or []:
        if not isinstance(event, dict):
            continue
        event_type = event.get("event_type")
        turn = event.get("turn")
        content = event.get("content") if isinstance(event.get("content"), dict) else {}
        if event_type == "time_advance":
            lines.append(
                f"- T{turn} time: {content.get('visible_time_phrase') or ''} "
                f"({content.get('elapsed_minutes', '?')} min) → {content.get('sim_time_after') or event.get('sim_time')}; "
                f"{_compact(content.get('rationale') or content.get('scene_time_explanation') or '')}"
            )
        elif event_type == "world_event_resolution":
            committed = [item for item in content.get("committed_world_events") or [] if isinstance(item, dict)]
            non_occurred = [item for item in content.get("non_occurred_event_candidates") or [] if isinstance(item, dict)]
            if committed:
                for item in committed:
                    lines.append(f"- T{turn} committed: {_event_title(item)} — {_compact(item.get('description') or '')}")
            if non_occurred:
                for item in non_occurred:
                    reason = item.get("non_commit_reason") or item.get("status") or ""
                    lines.append(f"- T{turn} not occurred: {_event_title(item)} — {_compact(reason)}")
        elif event_type == "living_state_update":
            update = content.get("update") if isinstance(content.get("update"), (dict, list, str)) else content
            try:
                update_text = json.dumps(update, ensure_ascii=False) if not isinstance(update, str) else update
            except TypeError:
                update_text = str(update)
            lines.append(f"- T{turn} living memory: {_compact(update_text)}")
        elif event_type == "max_turns_reached":
            lines.append(
                f"- T{turn} max-turns reached: limit={content.get('max_turns')}; "
                f"closure_at_limit={content.get('closure_status_at_limit')}; "
                f"{_compact(content.get('if_continued_next_focus') or content.get('principle') or '')}"
            )
    return lines or ["- none recorded"]


def _doctor_operation_lines(trajectory: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for event in trajectory.get("events") or []:
        if not isinstance(event, dict):
            continue
        event_type = event.get("event_type")
        turn = event.get("turn")
        content = event.get("content") if isinstance(event.get("content"), dict) else {}
        if event_type == "doctor_workspace_result":
            lines.append(
                f"- T{turn} workspace query: panels={content.get('requested_panels') or []}; "
                f"{_compact(content.get('summary') or content.get('doctor_visible_text') or '')}"
            )
        elif event_type == "doctor_care_system_receipt":
            lines.append(
                f"- T{turn} receipt: {content.get('operation')} / {content.get('status')} — "
                f"{_compact(content.get('summary') or '')}"
            )
        elif event_type == "care_system_state_update":
            for update in content.get("updates") or []:
                if isinstance(update, dict):
                    lines.append(
                        f"- T{turn} receipt update: {update.get('operation')} / {update.get('previous_status')} → {update.get('new_status')} "
                        f"from {update.get('source_event_id')}"
                    )
        elif event_type == "doctor_system_notification":
            lines.append(
                f"- T{turn} doctor system notification: {content.get('title') or ''} — "
                f"{_compact(content.get('summary') or '')}"
            )
        elif event_type == "workspace_state_update":
            updates = content.get("updates") or []
            lines.append(f"- T{turn} workspace update: {len(updates)} update(s)")
    return lines or ["- none recorded"]


def _event_title(event: dict[str, Any]) -> str:
    return str(event.get("title") or event.get("event_id") or event.get("event_type") or "world_event")


def _compact(value: Any, limit: int = 180) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 13].rstrip() + " ...[truncated]"


def _safe_name(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value)
    return safe.strip("_") or "case"


if __name__ == "__main__":
    raise SystemExit(main())
