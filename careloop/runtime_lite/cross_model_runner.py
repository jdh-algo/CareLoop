
from __future__ import annotations

"""Cross-model formal runner for FCCT-2 runtime_lite experiments.

This module orchestrates multiple *tested doctor* models while keeping the
CareLoop internal simulator/evaluator model fixed. It intentionally shells out
to ``python -m careloop.runtime_lite`` for each doctor model group so the normal
runner, checkpointing, and resume behavior remain the source of truth.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter, defaultdict
import csv
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Optional

from careloop.runtime_lite.interface_observation import (
    INTERFACE_SIGNAL_KEYS,
    interface_observation_from_case_dir,
    sum_interface_signals,
)

from careloop.runtime_lite.hardening import (
    FORMAL_CARELOOP_INTERNAL_MODEL,
    FORMAL_DOCTOR_MODELS,
    build_model_role_contract,
    load_formal_manifest,
    manifest_hash,
    safe_model_name,
    validate_formal_model_contract,
)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run formal FCCT-2 cross-model CareLoop benchmark.")
    parser.add_argument("--manifest", required=True, help="Formal manifest JSON.")
    parser.add_argument("--output-root", required=True, help="Root directory for all model-group outputs.")
    parser.add_argument("--client", choices=["scripted", "openai-compatible"], default="openai-compatible")
    parser.add_argument("--base-url", default=os.environ.get("CARELOOP_LITE_BASE_URL", ""))
    parser.add_argument("--api-key-env", default="CARELOOP_LITE_API_KEY")
    parser.add_argument("--careloop-internal-model", default=FORMAL_CARELOOP_INTERNAL_MODEL)
    parser.add_argument("--doctor-models", nargs="+", default=list(FORMAL_DOCTOR_MODELS))
    parser.add_argument("--doctor-model-api-id-map-json", default=os.environ.get("CARELOOP_FCCT2_DOCTOR_MODEL_API_ID_MAP_JSON", ""), help="JSON object mapping formal doctor model labels to provider-specific API model ids. Labels stay unchanged in benchmark metadata.")
    parser.add_argument("--case-labels", nargs="*", default=[], help="Optional case labels to select from the formal manifest, e.g. L07/H01/LM01 for smoke runs.")
    parser.add_argument("--case-ids", nargs="*", default=[], help="Optional case ids to select from the formal manifest.")
    parser.add_argument("--parallelism-per-model", type=int, default=10)
    parser.add_argument("--run-model-groups", choices=["sequential", "parallel"], default=os.environ.get("CARELOOP_FCCT2_RUN_MODEL_GROUPS", "parallel"))
    parser.add_argument("--max-turns", type=int, default=2000)
    parser.add_argument("--run-id-prefix", default="")
    parser.add_argument("--timeout-seconds", type=float, default=float(os.environ.get("CARELOOP_LITE_TIMEOUT_SECONDS", "120")))
    parser.add_argument("--max-output-tokens", type=int, default=int(os.environ.get("CARELOOP_LITE_MAX_OUTPUT_TOKENS", "0")))
    parser.add_argument("--llm-stream", action="store_true")
    parser.add_argument("--llm-omit-temperature", action="store_true")
    parser.add_argument("--llm-extra-body-json", default=os.environ.get("CARELOOP_LITE_EXTRA_BODY_JSON", ""))
    parser.add_argument("--llm-purpose-timeouts-json", default=os.environ.get("CARELOOP_LITE_PURPOSE_TIMEOUTS_JSON", ""))
    parser.add_argument("--llm-max-retries", type=int, default=int(os.environ.get("CARELOOP_LITE_MAX_RETRIES", "2")))
    parser.add_argument("--llm-bounded-retries", action="store_true")
    parser.add_argument("--llm-retry-backoff-seconds", type=float, default=float(os.environ.get("CARELOOP_LITE_RETRY_BACKOFF_SECONDS", "30")))
    parser.add_argument("--llm-retry-max-delay-seconds", type=float, default=float(os.environ.get("CARELOOP_LITE_RETRY_MAX_DELAY_SECONDS", "60")))
    parser.add_argument("--resume-existing", action="store_true")
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--stop-on-first-model-failure", action="store_true")
    parser.add_argument("--no-eval", action="store_true")
    parser.add_argument("--hardening-llm-audits", choices=["off", "final"], default="final", help="Run LLM-led hardening audits in each model-group final output.")
    parser.add_argument("--hardening-llm-audit-purposes", default="visibility,benchmark,receipt,closure")
    parser.add_argument("--formal-runtime-advisory", action="store_true", help="Enable all optional LLM-led runtime advisory nodes in each model group.")
    parser.add_argument("--enable-actor-realism-controller-v2", action="store_true")
    parser.add_argument("--enable-friction-coverage-planner", action="store_true")
    parser.add_argument("--enable-closure-thread-advisory", action="store_true")
    parser.add_argument("--enable-receipt-lifecycle-advisory", action="store_true")
    parser.add_argument("--enable-episode-governance-closure-advisory", action="store_true")
    parser.add_argument("--actor-realism-v2-interval-turns", type=int, default=4)
    parser.add_argument("--friction-coverage-interval-turns", type=int, default=6)
    parser.add_argument("--closure-thread-advisory-interval-turns", type=int, default=4)
    parser.add_argument("--receipt-lifecycle-advisory-interval-turns", type=int, default=4)
    parser.add_argument("--clinical-memory-mode", choices=["every_turn", "eventful", "off"], default=os.environ.get("CARELOOP_LITE_CLINICAL_MEMORY_MODE", "eventful"), help="ClinicalMemorySteward cadence passed to each runtime_lite model group. Formal 1000+ default is eventful.")
    parser.add_argument("--clinical-memory-max-interval-turns", type=int, default=int(os.environ.get("CARELOOP_LITE_CLINICAL_MEMORY_MAX_INTERVAL_TURNS", "3")))
    parser.add_argument("--clinical-memory-eventful-min-interval-turns", type=int, default=int(os.environ.get("CARELOOP_LITE_CLINICAL_MEMORY_EVENTFUL_MIN_INTERVAL_TURNS", "3")), help="Minimum routine-turn spacing for eventful ClinicalMemorySteward refreshes; raw ledgers still persist every turn.")
    parser.add_argument("--disable-long-context-1000-plus", action="store_true", help="Do not pass the 1000+ long-context sidecar mode into runtime_lite. Formal long runs should leave it enabled.")
    parser.add_argument("--episode-turn-span", type=int, default=20)
    parser.add_argument("--chapter-episode-span", type=int, default=5)
    parser.add_argument("--memory-integrity-audit-interval-turns", type=int, default=50)
    parser.add_argument("--fragment-eval-interval-turns", type=int, default=100)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--validate-cases", action="store_true")
    parser.add_argument("--aggregate-only", action="store_true")
    args = parser.parse_args(argv)

    manifest_path = Path(args.manifest)
    manifest = load_formal_manifest(manifest_path)
    exp_id = str(manifest.get("experiment_id") or manifest_path.stem)
    mh = str(manifest.get("manifest_hash") or manifest_hash(manifest_path))
    all_cases = _resolve_manifest_cases(manifest_path, manifest)
    cases = _filter_manifest_cases(all_cases, labels=args.case_labels, case_ids=args.case_ids)
    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    plan = _run_matrix(args, manifest_path, exp_id, mh, cases)
    (output_root / "run_matrix.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.dry_run:
        (output_root / "cross_model_dry_run.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        (output_root / "cross_model_report.md").write_text(_cross_model_report(plan, []), encoding="utf-8")
        print(json.dumps({"dry_run": True, "plan": plan}, ensure_ascii=False, indent=2))
        return 0

    if args.aggregate_only:
        summaries = aggregate_cross_model_outputs(output_root, args.doctor_models)
        _write_cross_model_outputs(output_root, plan, summaries)
        print(json.dumps({"aggregate_only": True, "output_root": str(output_root), "models": summaries}, ensure_ascii=False, indent=2))
        return 0

    for model in args.doctor_models:
        validate_formal_model_contract(
            sim_model=args.careloop_internal_model,
            doctor_model=model,
            internal_model=args.careloop_internal_model,
            allowed_doctor_models=args.doctor_models,
        )
    runtime_case_list = _runtime_case_list_path(output_root, manifest_path, cases)

    failures = []
    if args.run_model_groups == "parallel":
        model_group_workers = max(1, len(args.doctor_models))
        print(json.dumps({
            "event": "model_groups_parallel_start",
            "model_group_workers": model_group_workers,
            "parallelism_per_model": args.parallelism_per_model,
            "effective_total_case_parallelism": model_group_workers * max(1, int(args.parallelism_per_model or 1)),
            "isolation": "one subprocess and one doctor_<model> output directory per tested doctor model; CareLoop internal model remains fixed",
        }, ensure_ascii=False), flush=True)
        with ThreadPoolExecutor(max_workers=model_group_workers) as executor:
            future_to_model = {
                executor.submit(_run_model_group, args, manifest_path, runtime_case_list, exp_id, mh, model, output_root, True): model
                for model in args.doctor_models
            }
            for future in as_completed(future_to_model):
                status = future.result()
                print(json.dumps({
                    "event": "model_group_finished",
                    "doctor_model": status.get("doctor_model"),
                    "returncode": status.get("returncode"),
                    "status": status.get("status"),
                    "output_dir": status.get("output_dir"),
                    "log_path": status.get("log_path"),
                }, ensure_ascii=False), flush=True)
                if int(status.get("returncode") or 0) != 0:
                    failures.append(status)
    else:
        for model in args.doctor_models:
            status = _run_model_group(args, manifest_path, runtime_case_list, exp_id, mh, model, output_root, False)
            if int(status.get("returncode") or 0) != 0:
                failures.append(status)
                if args.stop_on_first_model_failure:
                    break
    summaries = aggregate_cross_model_outputs(output_root, args.doctor_models)
    _write_cross_model_outputs(output_root, plan, summaries)
    print(json.dumps({"output_root": str(output_root), "failures": failures, "models": summaries}, ensure_ascii=False, indent=2))
    return 1 if failures else 0


def _resolve_manifest_cases(manifest_path: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    cases = []
    repo_root = Path(__file__).resolve().parents[2]
    for entry in manifest.get("cases") or []:
        item = dict(entry)
        raw_path = Path(str(item.get("path") or item.get("case_path") or ""))
        if not raw_path.is_absolute():
            manifest_candidate = manifest_path.parent / raw_path
            repo_candidate = repo_root / raw_path
            if manifest_candidate.exists():
                raw_path = manifest_candidate
            elif repo_candidate.exists():
                raw_path = repo_candidate
        item["resolved_path"] = str(raw_path)
        cases.append(item)
    return cases


def _filter_values(values: list[Any]) -> set[str]:
    out: set[str] = set()
    for value in values or []:
        for part in str(value or "").replace(",", " ").split():
            part = part.strip()
            if part:
                out.add(part)
    return out


def _doctor_model_api_id_map(args: argparse.Namespace) -> dict[str, str]:
    raw = str(getattr(args, "doctor_model_api_id_map_json", "") or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"--doctor-model-api-id-map-json must be a JSON object: {exc}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("--doctor-model-api-id-map-json must decode to a JSON object")
    mapping: dict[str, str] = {}
    for label, api_id in parsed.items():
        label_s = str(label or "").strip()
        api_id_s = str(api_id or "").strip()
        if label_s and api_id_s:
            mapping[label_s] = api_id_s
    return mapping


def _doctor_model_api_id(args: argparse.Namespace, doctor_model: str) -> str:
    return _doctor_model_api_id_map(args).get(str(doctor_model or "").strip(), "")


def _filter_manifest_cases(cases: list[dict[str, Any]], *, labels: list[Any], case_ids: list[Any]) -> list[dict[str, Any]]:
    label_set = _filter_values(labels)
    id_set = _filter_values(case_ids)
    if not label_set and not id_set:
        return cases
    selected = []
    for item in cases:
        label = str(item.get("case_label") or item.get("label") or "")
        case_id = str(item.get("case_id") or "")
        if (label_set and label in label_set) or (id_set and case_id in id_set):
            selected.append(item)
    if not selected:
        raise RuntimeError(
            "case filter selected no formal manifest cases; "
            f"labels={sorted(label_set)} case_ids={sorted(id_set)}"
        )
    return selected


def _runtime_case_list_path(output_root: Path, manifest_path: Path, cases: list[dict[str, Any]]) -> Path:
    # Runtime CLI accepts arbitrary case-list JSON.  Use absolute resolved paths
    # so a filtered manifest under output_root still points at the authored case files.
    selected_path = output_root / "selected_runtime_cases.json"
    payload = {
        "protocol": "careloop.runtime_lite.selected_case_list.v1",
        "source_manifest": str(manifest_path),
        "case_count": len(cases),
        "cases": [
            {
                "case_label": item.get("case_label"),
                "case_id": item.get("case_id"),
                "path": item.get("resolved_path") or item.get("path") or item.get("case_path"),
                "source_path": item.get("path") or item.get("case_path"),
            }
            for item in cases
        ],
    }
    selected_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return selected_path


def _run_matrix(args: argparse.Namespace, manifest_path: Path, exp_id: str, mh: str, cases: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "protocol": "careloop.fcct2.cross_model_run_matrix.v1",
        "experiment_id": exp_id,
        "manifest_path": str(manifest_path),
        "manifest_hash": mh,
        "careloop_internal_model": args.careloop_internal_model,
        "doctor_models": list(args.doctor_models),
        "doctor_model_api_id_map": _doctor_model_api_id_map(args),
        "parallelism_per_model": args.parallelism_per_model,
        "run_model_groups": args.run_model_groups,
        "model_group_parallelism": len(args.doctor_models) if args.run_model_groups == "parallel" else 1,
        "effective_total_case_parallelism": max(1, int(args.parallelism_per_model or 1)) * (len(args.doctor_models) if args.run_model_groups == "parallel" else 1),
        "model_group_isolation_policy": {
            "one_subprocess_per_doctor_model": True,
            "one_output_directory_per_doctor_model": True,
            "doctor_model_label_preserved_in_metadata": True,
            "provider_api_id_only_routes_tested_doctor_calls": True,
            "careloop_internal_model_fixed_for_all_groups": args.careloop_internal_model,
        },
        "max_turns": args.max_turns,
        "hardening_llm_audits": getattr(args, "hardening_llm_audits", "final"),
        "hardening_llm_audit_purposes": getattr(args, "hardening_llm_audit_purposes", "visibility,benchmark,receipt,closure"),
        "case_filter": {
            "case_labels": sorted(_filter_values(getattr(args, "case_labels", []) or [])),
            "case_ids": sorted(_filter_values(getattr(args, "case_ids", []) or [])),
        },
        "clinical_memory": {
            "mode": str(getattr(args, "clinical_memory_mode", "eventful") or "eventful"),
            "max_interval_turns": max(1, int(getattr(args, "clinical_memory_max_interval_turns", 3) or 3)),
            "eventful_min_interval_turns": max(1, int(getattr(args, "clinical_memory_eventful_min_interval_turns", 3) or 3)),
            "principle": "Raw ledgers persist every turn; this only spaces routine ClinicalMemorySteward LLM refreshes.",
        },
        "long_context": {
            "long_context_1000_plus": not bool(getattr(args, "disable_long_context_1000_plus", False)),
            "episode_turn_span": max(1, int(getattr(args, "episode_turn_span", 20) or 20)),
            "chapter_episode_span": max(1, int(getattr(args, "chapter_episode_span", 5) or 5)),
            "memory_integrity_audit_interval_turns": max(1, int(getattr(args, "memory_integrity_audit_interval_turns", 50) or 50)),
            "fragment_eval_interval_turns": max(1, int(getattr(args, "fragment_eval_interval_turns", 100) or 100)),
            "doctor_visibility_boundary": "Backstage long-context sidecars are not tested-doctor context.",
        },
        "runtime_advisory": {
            "formal_runtime_advisory": bool(getattr(args, "formal_runtime_advisory", False)),
            "actor_realism_controller_v2": bool(getattr(args, "enable_actor_realism_controller_v2", False)) or bool(getattr(args, "formal_runtime_advisory", False)),
            "friction_coverage_planner": bool(getattr(args, "enable_friction_coverage_planner", False)) or bool(getattr(args, "formal_runtime_advisory", False)),
            "closure_thread_advisory": bool(getattr(args, "enable_closure_thread_advisory", False)) or bool(getattr(args, "formal_runtime_advisory", False)),
            "receipt_lifecycle_advisory": bool(getattr(args, "enable_receipt_lifecycle_advisory", False)) or bool(getattr(args, "formal_runtime_advisory", False)),
            "episode_governance_closure_advisory": bool(getattr(args, "enable_episode_governance_closure_advisory", False)) or bool(getattr(args, "formal_runtime_advisory", False)),
            "interval_turns": {
                "actor_realism_v2": max(1, int(getattr(args, "actor_realism_v2_interval_turns", 4) or 4)),
                "friction_coverage": max(1, int(getattr(args, "friction_coverage_interval_turns", 6) or 6)),
                "closure_thread_advisory": max(1, int(getattr(args, "closure_thread_advisory_interval_turns", 4) or 4)),
                "receipt_lifecycle_advisory": max(1, int(getattr(args, "receipt_lifecycle_advisory_interval_turns", 4) or 4)),
            },
        },
        "case_count": len(cases),
        "cases": cases,
        "model_role_contracts": {
            model: build_model_role_contract(args.careloop_internal_model, model, formal=True)
            for model in args.doctor_models
        },
    }


def _run_model_group(
    args: argparse.Namespace,
    manifest_path: Path,
    runtime_case_list_path: Path,
    exp_id: str,
    mh: str,
    doctor_model: str,
    output_root: Path,
    capture_log: bool,
) -> dict[str, Any]:
    """Run one tested-doctor model group in an isolated subprocess.

    Isolation contract for cross-model runs:
    - each doctor model gets its own subprocess, run id prefix and
      ``doctor_<safe_model_name>`` output directory;
    - the formal doctor model label is passed unchanged for benchmark metadata;
    - provider-specific model ids are passed only through ``--doctor-model-api-id``;
    - the CareLoop internal simulator/evaluator model is passed separately and
      remains fixed for every subprocess.

    In parallel mode stdout/stderr are captured per model-group log file so
    four concurrent tested doctors do not interleave their runtime traces.
    """

    model_dir = output_root / ("doctor_" + safe_model_name(doctor_model))
    model_dir.mkdir(parents=True, exist_ok=True)
    cmd = _runtime_command(args, manifest_path, runtime_case_list_path, exp_id, mh, doctor_model, output_root)
    (model_dir / "runtime_command.json").write_text(json.dumps({"cmd": _redacted_cmd(cmd)}, ensure_ascii=False, indent=2), encoding="utf-8")
    log_path = model_dir / "model_group_run.log"
    started = time.time()
    running_status = {
        "doctor_model": doctor_model,
        "status": "running",
        "returncode": None,
        "started_at_epoch": started,
        "finished_at_epoch": None,
        "output_dir": str(model_dir),
        "log_path": str(log_path),
        "execution_mode": "parallel_model_group" if capture_log else "sequential_model_group",
        "isolation_contract": {
            "doctor_model_label": doctor_model,
            "doctor_model_api_id": _doctor_model_api_id(args, doctor_model) or doctor_model,
            "careloop_internal_model": args.careloop_internal_model,
            "output_dir": str(model_dir),
        },
    }
    (model_dir / "model_group_status.json").write_text(json.dumps(running_status, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        if capture_log:
            with log_path.open("ab") as log:
                log.write((
                    f"\n=== model_group_start doctor_model={doctor_model} "
                    f"started_at_epoch={started:.3f} ===\n"
                ).encode("utf-8"))
                proc = subprocess.run(
                    cmd,
                    cwd=str(Path(__file__).resolve().parents[2]),
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
                log.write((
                    f"\n=== model_group_finish doctor_model={doctor_model} "
                    f"returncode={proc.returncode} finished_at_epoch={time.time():.3f} ===\n"
                ).encode("utf-8"))
        else:
            proc = subprocess.run(cmd, cwd=str(Path(__file__).resolve().parents[2]))
        status = {
            **running_status,
            "status": "completed" if proc.returncode == 0 else "failed",
            "returncode": proc.returncode,
            "finished_at_epoch": time.time(),
        }
    except Exception as exc:  # noqa: BLE001 - preserve cross-model audit status
        status = {
            **running_status,
            "status": "launcher_exception",
            "returncode": 98,
            "finished_at_epoch": time.time(),
            "error_type": type(exc).__name__,
            "error_message": str(exc),
        }
    (model_dir / "model_group_status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    return status

def _runtime_command(args: argparse.Namespace, manifest_path: Path, runtime_case_list_path: Path, exp_id: str, mh: str, doctor_model: str, output_root: Path) -> list[str]:
    output_dir = output_root / ("doctor_" + safe_model_name(doctor_model))
    run_id = args.run_id_prefix or (exp_id + "_" + safe_model_name(doctor_model))
    doctor_model_api_id = _doctor_model_api_id(args, doctor_model)
    cmd = [
        sys.executable,
        "-m",
        "careloop.runtime_lite",
        "--case-list",
        str(runtime_case_list_path),
        "--client",
        args.client,
        "--sim-model",
        args.careloop_internal_model,
        "--doctor-model",
        doctor_model,
        "--formal-fcct2-cross-model",
        "--careloop-internal-model",
        args.careloop_internal_model,
        "--formal-doctor-models",
        ",".join(str(item) for item in (getattr(args, "doctor_models", None) or [doctor_model])),
        "--experiment-id",
        exp_id,
        "--manifest-path",
        str(manifest_path),
        "--manifest-hash",
        mh,
        "--hardening-llm-audits",
        args.hardening_llm_audits,
        "--hardening-llm-audit-purposes",
        args.hardening_llm_audit_purposes,
        "--parallelism",
        str(args.parallelism_per_model),
        "--max-turns",
        str(args.max_turns),
        "--run-id",
        run_id,
        "--output-dir",
        str(output_dir),
        "--timeout-seconds",
        str(args.timeout_seconds),
        "--llm-max-retries",
        str(args.llm_max_retries),
        "--llm-retry-backoff-seconds",
        str(args.llm_retry_backoff_seconds),
        "--llm-retry-max-delay-seconds",
        str(args.llm_retry_max_delay_seconds),
        "--clinical-memory-mode",
        str(getattr(args, "clinical_memory_mode", "eventful") or "eventful"),
        "--clinical-memory-max-interval-turns",
        str(max(1, int(getattr(args, "clinical_memory_max_interval_turns", 3) or 3))),
        "--clinical-memory-eventful-min-interval-turns",
        str(max(1, int(getattr(args, "clinical_memory_eventful_min_interval_turns", 3) or 3))),
    ]
    if args.client == "openai-compatible":
        cmd.extend(["--base-url", args.base_url, "--api-key-env", args.api_key_env])
    if doctor_model_api_id:
        cmd.extend(["--doctor-model-api-id", doctor_model_api_id])
    if args.max_output_tokens > 0:
        cmd.extend(["--max-output-tokens", str(args.max_output_tokens)])
    if args.llm_stream:
        cmd.append("--llm-stream")
    if args.llm_omit_temperature:
        cmd.append("--llm-omit-temperature")
    if args.llm_extra_body_json:
        cmd.extend(["--llm-extra-body-json", args.llm_extra_body_json])
    if args.llm_purpose_timeouts_json:
        cmd.extend(["--llm-purpose-timeouts-json", args.llm_purpose_timeouts_json])
    if args.llm_bounded_retries:
        cmd.append("--llm-bounded-retries")
    if bool(getattr(args, "formal_runtime_advisory", False)):
        cmd.append("--formal-runtime-advisory")
    for flag_attr, flag_name in [
        ("enable_actor_realism_controller_v2", "--enable-actor-realism-controller-v2"),
        ("enable_friction_coverage_planner", "--enable-friction-coverage-planner"),
        ("enable_closure_thread_advisory", "--enable-closure-thread-advisory"),
        ("enable_receipt_lifecycle_advisory", "--enable-receipt-lifecycle-advisory"),
        ("enable_episode_governance_closure_advisory", "--enable-episode-governance-closure-advisory"),
    ]:
        if bool(getattr(args, flag_attr, False)):
            cmd.append(flag_name)
    cmd.extend([
        "--actor-realism-v2-interval-turns",
        str(max(1, int(getattr(args, "actor_realism_v2_interval_turns", 4) or 4))),
        "--friction-coverage-interval-turns",
        str(max(1, int(getattr(args, "friction_coverage_interval_turns", 6) or 6))),
        "--closure-thread-advisory-interval-turns",
        str(max(1, int(getattr(args, "closure_thread_advisory_interval_turns", 4) or 4))),
        "--receipt-lifecycle-advisory-interval-turns",
        str(max(1, int(getattr(args, "receipt_lifecycle_advisory_interval_turns", 4) or 4))),
    ])
    if not bool(getattr(args, "disable_long_context_1000_plus", False)):
        cmd.extend([
            "--long-context-1000-plus",
            "--episode-turn-span",
            str(max(1, int(getattr(args, "episode_turn_span", 20) or 20))),
            "--chapter-episode-span",
            str(max(1, int(getattr(args, "chapter_episode_span", 5) or 5))),
            "--memory-integrity-audit-interval-turns",
            str(max(1, int(getattr(args, "memory_integrity_audit_interval_turns", 50) or 50))),
            "--fragment-eval-interval-turns",
            str(max(1, int(getattr(args, "fragment_eval_interval_turns", 100) or 100))),
        ])
    if args.resume_existing:
        cmd.append("--resume-existing")
    if args.continue_on_error:
        cmd.append("--continue-on-error")
    if args.no_eval:
        cmd.append("--no-eval")
    if args.validate_cases:
        cmd.append("--validate-cases")
    return cmd


REQUIRED_CASE_FINAL_FILES = ["trajectory.json", "summary.md"]

REQUIRED_CASE_SIDECARS = [
    "status.json",
    "quality_report.json",
    "benchmark_validity.json",
    "visibility_boundary_audit.json",
    "receipt_lifecycle_summary.json",
    "closure_thread_summary.json",
    "api_runtime_safety.json",
    "resume_manifest.json",
    "latest_closure.json",
    "latest_visible_exchange.json",
    "memory/memory_store_manifest.json",
    "memory/long_context_status.json",
    "memory/memory_integrity_audit.json",
    "memory/final_long_context_evidence_pack.json",
]


def _read_json_file(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - aggregation must be best-effort and auditable
        return {"read_error": f"{type(exc).__name__}: {exc}"}
    return data if isinstance(data, dict) else {"read_error": "json_root_not_object"}


def _expected_case_ids_from_plan(output_root: Path) -> list[str]:
    plan_path = output_root / "run_matrix.json"
    if not plan_path.exists():
        return []
    plan = _read_json_file(plan_path)
    ids: list[str] = []
    for item in plan.get("cases") or []:
        if not isinstance(item, dict):
            continue
        cid = str(item.get("case_id") or item.get("case_label") or "").strip()
        if cid:
            ids.append(cid)
    return ids


def _status_row_from_case_dir(model: str, case_dir: Path) -> dict[str, Any]:
    status = _read_json_file(case_dir / "status.json") if (case_dir / "status.json").exists() else {}
    trajectory = _read_json_file(case_dir / "trajectory.json") if (case_dir / "trajectory.json").exists() else {}
    checkpoint = _read_json_file(case_dir / "trajectory.checkpoint.json") if (case_dir / "trajectory.checkpoint.json").exists() else {}
    resume = _read_json_file(case_dir / "resume_manifest.json") if (case_dir / "resume_manifest.json").exists() else {}
    api = _read_json_file(case_dir / "api_runtime_safety.json") if (case_dir / "api_runtime_safety.json").exists() else {}
    validity = _read_json_file(case_dir / "benchmark_validity.json") if (case_dir / "benchmark_validity.json").exists() else {}
    closure_threads = _read_json_file(case_dir / "closure_thread_summary.json") if (case_dir / "closure_thread_summary.json").exists() else {}
    receipt = _read_json_file(case_dir / "receipt_lifecycle_summary.json") if (case_dir / "receipt_lifecycle_summary.json").exists() else {}
    visibility = _read_json_file(case_dir / "visibility_boundary_audit.json") if (case_dir / "visibility_boundary_audit.json").exists() else {}
    payload = trajectory if trajectory else checkpoint
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    runtime_advisory = metadata.get("runtime_advisory") if isinstance(metadata.get("runtime_advisory"), dict) else {}
    advisory_counts = runtime_advisory.get("ledger_counts") if isinstance(runtime_advisory.get("ledger_counts"), dict) else {}
    required_artifacts = REQUIRED_CASE_FINAL_FILES + REQUIRED_CASE_SIDECARS
    files_present = [name for name in required_artifacts if (case_dir / name).exists()]
    files_missing = [name for name in required_artifacts if not (case_dir / name).exists()]
    long_context_status = _read_json_file(case_dir / "memory" / "long_context_status.json") if (case_dir / "memory" / "long_context_status.json").exists() else {}
    long_context_audit = _read_json_file(case_dir / "memory" / "memory_integrity_audit.json") if (case_dir / "memory" / "memory_integrity_audit.json").exists() else {}
    interface_observation = interface_observation_from_case_dir(case_dir)
    interface_signals = interface_observation.get("signals") if isinstance(interface_observation.get("signals"), dict) else {}
    case_id = str(
        status.get("case_id")
        or payload.get("case_id")
        or resume.get("case_id")
        or case_dir.name
    )
    closure = payload.get("closure") if isinstance(payload.get("closure"), dict) else {}
    evaluation = payload.get("evaluation") if isinstance(payload.get("evaluation"), dict) else {}
    row = {
        "doctor_model": model,
        "case_id": case_id,
        "case_dir": str(case_dir),
        "run_id": status.get("run_id") or payload.get("run_id") or resume.get("run_id") or "",
        "turns_completed": status.get("turns_completed") or payload.get("turns_completed") or resume.get("last_complete_turn") or 0,
        "closure_status": status.get("closure_status") or closure.get("status") or resume.get("closure_status") or "",
        "evaluation_overall": status.get("evaluation_overall") or evaluation.get("overall") or "",
        "evaluation_mode": status.get("evaluation_mode") or evaluation.get("evaluation_mode") or "",
        "quality_status": status.get("quality_status") or "",
        "benchmark_validity_status": status.get("benchmark_validity_status") or validity.get("status") or "",
        "benchmark_primary": status.get("benchmark_primary") if "benchmark_primary" in status else validity.get("should_count_in_primary_benchmark"),
        "doctor_performance_usable": validity.get("doctor_performance_usable"),
        "visibility_boundary_status": status.get("visibility_boundary_status") or visibility.get("status") or "",
        "visibility_boundary_max_severity": status.get("visibility_boundary_max_severity") or visibility.get("max_severity") or "",
        "receipt_lifecycle_status": status.get("receipt_lifecycle_status") or receipt.get("status") or "",
        "closure_thread_readiness": status.get("closure_thread_readiness") or closure_threads.get("overall_closure_readiness") or "",
        "api_runtime_error_class": api.get("runtime_error_class") or status.get("runtime_error_type") or "",
        "api_recommended_action": api.get("recommended_action") or "",
        "resume_safety": resume.get("resume_safety") or "",
        "safe_to_resume_from_checkpoint": resume.get("safe_to_resume_from_checkpoint"),
        "status_present": bool(status),
        "trajectory_present": (case_dir / "trajectory.json").exists(),
        "checkpoint_present": (case_dir / "trajectory.checkpoint.json").exists(),
        "required_artifact_present_count": len(files_present),
        "required_artifact_missing_count": len(files_missing),
        "missing_required_artifacts": files_missing,
        "long_context_enabled": bool(long_context_status.get("enabled")),
        "long_context_turn": long_context_status.get("turn", 0),
        "long_context_integrity_status": long_context_status.get("integrity_status") or long_context_audit.get("status", ""),
        "long_context_ledger_counts": long_context_status.get("ledger_counts") or {},
        "required_sidecar_present_count": sum(1 for name in REQUIRED_CASE_SIDECARS if (case_dir / name).exists()),
        "required_sidecar_missing_count": sum(1 for name in REQUIRED_CASE_SIDECARS if not (case_dir / name).exists()),
        "missing_sidecars": [name for name in REQUIRED_CASE_SIDECARS if not (case_dir / name).exists()],
        "runtime_advisory_ledger_counts": advisory_counts,
        "non_weighted_interface_observations": interface_observation,
        "interface_observation_level": interface_observation.get("observation_level") or "",
        "interface_observation_summary": interface_observation.get("summary") or "",
        "interface_signal_counts": interface_signals,
    }
    return row


def _case_rows_by_output_dir(model: str, model_dir: Path) -> list[dict[str, Any]]:
    rows = []
    for case_dir in sorted(model_dir.glob("case_*")):
        if case_dir.is_dir():
            rows.append(_status_row_from_case_dir(model, case_dir))
    # When the selected runtime case-list contains a single case, runtime_lite
    # writes directly into the model output dir rather than a case_* child dir.
    # Formal route-smoke aggregation must count that direct output shape too.
    if not rows and ((model_dir / "status.json").exists() or (model_dir / "trajectory.json").exists()):
        rows.append(_status_row_from_case_dir(model, model_dir))
    return rows


def _counter_from_rows(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    return dict(Counter(str(row.get(key) or "") for row in rows))


def _advisory_ledger_totals(rows: list[dict[str, Any]]) -> dict[str, int]:
    totals: Counter[str] = Counter()
    for row in rows:
        counts = row.get("runtime_advisory_ledger_counts") if isinstance(row.get("runtime_advisory_ledger_counts"), dict) else {}
        for key, value in counts.items():
            try:
                totals[str(key)] += int(value or 0)
            except (TypeError, ValueError):
                continue
    return dict(totals)


def _interface_observations_from_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for row in rows:
        obs = row.get("non_weighted_interface_observations")
        if isinstance(obs, dict):
            observations.append(obs)
    return observations


def aggregate_cross_model_outputs(output_root: Path, doctor_models: list[str]) -> list[dict[str, Any]]:
    summaries = []
    expected_case_ids = _expected_case_ids_from_plan(output_root)
    expected_case_set = set(expected_case_ids)
    for model in doctor_models:
        model_dir = output_root / ("doctor_" + safe_model_name(model))
        batch_summary = model_dir / "batch_summary.json"
        data = _read_json_file(batch_summary) if batch_summary.exists() else {}
        runs = data.get("runs") if isinstance(data.get("runs"), list) else []
        aggregate = data.get("aggregate") if isinstance(data.get("aggregate"), dict) else {}
        case_rows = _case_rows_by_output_dir(model, model_dir)
        present_case_ids = {str(row.get("case_id") or "") for row in case_rows if str(row.get("case_id") or "")}
        missing_expected_cases = sorted(expected_case_set - present_case_ids) if expected_case_set else []
        sidecar_complete_rows = [row for row in case_rows if int(row.get("required_sidecar_missing_count") or 0) == 0]
        artifact_complete_rows = [row for row in case_rows if int(row.get("required_artifact_missing_count") or 0) == 0]
        model_status_path = model_dir / "model_group_status.json"
        model_status = _read_json_file(model_status_path) if model_status_path.exists() else {}
        summaries.append(
            {
                "doctor_model": model,
                "output_dir": str(model_dir),
                "model_group_status": model_status,
                "batch_summary_present": batch_summary.exists(),
                "case_count": len(runs) or len(case_rows),
                "expected_case_count": len(expected_case_ids),
                "present_case_count": len(case_rows),
                "missing_expected_cases": missing_expected_cases,
                "required_artifact_complete_count": len(artifact_complete_rows),
                "required_artifact_incomplete_count": len(case_rows) - len(artifact_complete_rows),
                "required_sidecar_complete_count": len(sidecar_complete_rows),
                "required_sidecar_incomplete_count": len(case_rows) - len(sidecar_complete_rows),
                "aggregate": aggregate,
                "sidecar_benchmark_validity_counts": _counter_from_rows(case_rows, "benchmark_validity_status"),
                "sidecar_visibility_boundary_counts": _counter_from_rows(case_rows, "visibility_boundary_status"),
                "sidecar_closure_thread_readiness_counts": _counter_from_rows(case_rows, "closure_thread_readiness"),
                "sidecar_receipt_lifecycle_status_counts": _counter_from_rows(case_rows, "receipt_lifecycle_status"),
                "api_runtime_error_class_counts": _counter_from_rows(case_rows, "api_runtime_error_class"),
                "resume_safety_counts": _counter_from_rows(case_rows, "resume_safety"),
                "closure_status_counts_from_sidecars": _counter_from_rows(case_rows, "closure_status"),
                "quality_status_counts_from_sidecars": _counter_from_rows(case_rows, "quality_status"),
                "runtime_advisory_ledger_totals": _advisory_ledger_totals(case_rows),
                "interface_discipline_observation_counts": _counter_from_rows(case_rows, "interface_observation_level"),
                "interface_discipline_signal_totals": sum_interface_signals(_interface_observations_from_rows(case_rows)),
                "cases": case_rows,
            }
        )
    return summaries


def _write_cross_model_outputs(output_root: Path, plan: dict[str, Any], summaries: list[dict[str, Any]]) -> None:
    payload = {"plan": plan, "models": summaries, "aggregate": _cross_model_aggregate(summaries)}
    (output_root / "cross_model_summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_root / "cross_model_report.md").write_text(_cross_model_report(plan, summaries), encoding="utf-8")
    _write_case_matrix(output_root / "cross_model_case_matrix.csv", summaries)


def _cross_model_aggregate(summaries: list[dict[str, Any]]) -> dict[str, Any]:
    validity = Counter()
    visibility = Counter()
    closure_threads = Counter()
    receipt = Counter()
    api_errors = Counter()
    resume = Counter()
    interface_levels = Counter()
    interface_signal_totals: Counter[str] = Counter()
    for item in summaries:
        validity.update(item.get("sidecar_benchmark_validity_counts") or {})
        visibility.update(item.get("sidecar_visibility_boundary_counts") or {})
        closure_threads.update(item.get("sidecar_closure_thread_readiness_counts") or {})
        receipt.update(item.get("sidecar_receipt_lifecycle_status_counts") or {})
        api_errors.update(item.get("api_runtime_error_class_counts") or {})
        resume.update(item.get("resume_safety_counts") or {})
        interface_levels.update(item.get("interface_discipline_observation_counts") or {})
        for key, value in (item.get("interface_discipline_signal_totals") or {}).items():
            if key == "max_workspace_round_observed":
                try:
                    interface_signal_totals[key] = max(interface_signal_totals.get(key, 0), int(value or 0))
                except (TypeError, ValueError):
                    pass
            else:
                try:
                    interface_signal_totals[str(key)] += int(value or 0)
                except (TypeError, ValueError):
                    pass
    interface_signal_payload = {key: int(interface_signal_totals.get(key, 0)) for key in INTERFACE_SIGNAL_KEYS}
    return {
        "model_count": len(summaries),
        "total_case_rows": sum(int(item.get("case_count") or 0) for item in summaries),
        "total_present_case_dirs": sum(int(item.get("present_case_count") or 0) for item in summaries),
        "total_expected_cases": sum(int(item.get("expected_case_count") or 0) for item in summaries),
        "total_missing_expected_cases": sum(len(item.get("missing_expected_cases") or []) for item in summaries),
        "models_with_batch_summary": sum(1 for item in summaries if item.get("batch_summary_present")),
        "required_artifact_complete_count": sum(int(item.get("required_artifact_complete_count") or 0) for item in summaries),
        "required_artifact_incomplete_count": sum(int(item.get("required_artifact_incomplete_count") or 0) for item in summaries),
        "required_sidecar_complete_count": sum(int(item.get("required_sidecar_complete_count") or 0) for item in summaries),
        "required_sidecar_incomplete_count": sum(int(item.get("required_sidecar_incomplete_count") or 0) for item in summaries),
        "benchmark_validity_counts": dict(validity),
        "visibility_boundary_counts": dict(visibility),
        "closure_thread_readiness_counts": dict(closure_threads),
        "receipt_lifecycle_status_counts": dict(receipt),
        "api_runtime_error_class_counts": dict(api_errors),
        "resume_safety_counts": dict(resume),
        "interface_discipline_observation_counts": dict(interface_levels),
        "interface_discipline_signal_totals": interface_signal_payload,
    }


def _cross_model_report(plan: dict[str, Any], summaries: list[dict[str, Any]]) -> str:
    aggregate = _cross_model_aggregate(summaries)
    lines = [
        "# CareLoop FCCT-2 cross-model benchmark report",
        "",
        "## Plan",
        "",
        f"- experiment_id: {plan.get('experiment_id')}",
        f"- manifest_hash: {plan.get('manifest_hash')}",
        f"- careloop_internal_model: {plan.get('careloop_internal_model')}",
        f"- doctor_models: {', '.join(plan.get('doctor_models') or [])}",
        f"- case_count: {plan.get('case_count')}",
        f"- parallelism_per_model: {plan.get('parallelism_per_model')}",
        f"- runtime_advisory: `{json.dumps(plan.get('runtime_advisory') or {}, ensure_ascii=False)}`",
        f"- hardening_llm_audits: {plan.get('hardening_llm_audits')}",
        "",
        "## Cross-model aggregate",
        "",
        f"- model_count: {aggregate.get('model_count')}",
        f"- total_expected_cases: {aggregate.get('total_expected_cases')}",
        f"- total_present_case_dirs: {aggregate.get('total_present_case_dirs')}",
        f"- total_missing_expected_cases: {aggregate.get('total_missing_expected_cases')}",
        f"- required_artifact_complete_count: {aggregate.get('required_artifact_complete_count')}",
        f"- required_artifact_incomplete_count: {aggregate.get('required_artifact_incomplete_count')}",
        f"- required_sidecar_complete_count: {aggregate.get('required_sidecar_complete_count')}",
        f"- required_sidecar_incomplete_count: {aggregate.get('required_sidecar_incomplete_count')}",
        f"- benchmark_validity_counts: {json.dumps(aggregate.get('benchmark_validity_counts') or {}, ensure_ascii=False)}",
        f"- visibility_boundary_counts: {json.dumps(aggregate.get('visibility_boundary_counts') or {}, ensure_ascii=False)}",
        f"- closure_thread_readiness_counts: {json.dumps(aggregate.get('closure_thread_readiness_counts') or {}, ensure_ascii=False)}",
        f"- receipt_lifecycle_status_counts: {json.dumps(aggregate.get('receipt_lifecycle_status_counts') or {}, ensure_ascii=False)}",
        f"- api_runtime_error_class_counts: {json.dumps(aggregate.get('api_runtime_error_class_counts') or {}, ensure_ascii=False)}",
        f"- resume_safety_counts: {json.dumps(aggregate.get('resume_safety_counts') or {}, ensure_ascii=False)}",
        "",
        "## Non-weighted interface discipline observations",
        "",
        "These observations are deployment/interface reminders only; they are not used for clinical grading, closure status, quality status, or weighted evaluation.",
        "",
        f"- aggregate_observation_level_counts: {json.dumps(aggregate.get('interface_discipline_observation_counts') or {}, ensure_ascii=False)}",
        f"- aggregate_signal_totals: {json.dumps(aggregate.get('interface_discipline_signal_totals') or {}, ensure_ascii=False)}",
        "",
        "## Model summaries",
        "",
    ]
    if not summaries:
        lines.append("_No model outputs aggregated yet._")
    for item in summaries:
        agg = item.get("aggregate") or {}
        status = item.get("model_group_status") if isinstance(item.get("model_group_status"), dict) else {}
        lines.extend(
            [
                f"### {item.get('doctor_model')}",
                "",
                f"- output_dir: `{item.get('output_dir')}`",
                f"- model_group_returncode: {status.get('returncode', 'n/a')}",
                f"- batch_summary_present: {item.get('batch_summary_present')}",
                f"- case_count: {item.get('case_count')}",
                f"- expected_case_count: {item.get('expected_case_count')}",
                f"- present_case_count: {item.get('present_case_count')}",
                f"- missing_expected_cases: {json.dumps(item.get('missing_expected_cases') or [], ensure_ascii=False)}",
                f"- required_artifact_complete_count: {item.get('required_artifact_complete_count')}",
                f"- required_artifact_incomplete_count: {item.get('required_artifact_incomplete_count')}",
                f"- required_sidecar_complete_count: {item.get('required_sidecar_complete_count')}",
                f"- required_sidecar_incomplete_count: {item.get('required_sidecar_incomplete_count')}",
                f"- closure_status_counts: {json.dumps(item.get('closure_status_counts_from_sidecars') or agg.get('closure_status_counts') or {}, ensure_ascii=False)}",
                f"- quality_status_counts: {json.dumps(item.get('quality_status_counts_from_sidecars') or {}, ensure_ascii=False)}",
                f"- evaluation_overall_counts: {json.dumps(agg.get('evaluation_overall_counts') or {}, ensure_ascii=False)}",
                f"- benchmark_validity_counts: {json.dumps(item.get('sidecar_benchmark_validity_counts') or agg.get('benchmark_validity_counts') or {}, ensure_ascii=False)}",
                f"- visibility_boundary_counts: {json.dumps(item.get('sidecar_visibility_boundary_counts') or agg.get('visibility_boundary_status_counts') or {}, ensure_ascii=False)}",
                f"- closure_thread_readiness_counts: {json.dumps(item.get('sidecar_closure_thread_readiness_counts') or {}, ensure_ascii=False)}",
                f"- receipt_lifecycle_status_counts: {json.dumps(item.get('sidecar_receipt_lifecycle_status_counts') or {}, ensure_ascii=False)}",
                f"- api_runtime_error_class_counts: {json.dumps(item.get('api_runtime_error_class_counts') or {}, ensure_ascii=False)}",
                f"- resume_safety_counts: {json.dumps(item.get('resume_safety_counts') or {}, ensure_ascii=False)}",
                f"- runtime_advisory_ledger_totals: {json.dumps(item.get('runtime_advisory_ledger_totals') or {}, ensure_ascii=False)}",
                f"- non_weighted_interface_observation_counts: {json.dumps(item.get('interface_discipline_observation_counts') or {}, ensure_ascii=False)}",
                f"- non_weighted_interface_signal_totals: {json.dumps(item.get('interface_discipline_signal_totals') or {}, ensure_ascii=False)}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _write_case_matrix(path: Path, summaries: list[dict[str, Any]]) -> None:
    rows = []
    for model_summary in summaries:
        model = model_summary.get("doctor_model")
        for case in model_summary.get("cases") or []:
            rows.append(
                {
                    "doctor_model": model,
                    "case_id": case.get("case_id"),
                    "run_id": case.get("run_id"),
                    "turns_completed": case.get("turns_completed"),
                    "closure_status": case.get("closure_status"),
                    "evaluation_mode": case.get("evaluation_mode"),
                    "evaluation_overall": case.get("evaluation_overall"),
                    "quality_status": case.get("quality_status"),
                    "benchmark_validity_status": case.get("benchmark_validity_status"),
                    "benchmark_primary": case.get("benchmark_primary"),
                    "doctor_performance_usable": case.get("doctor_performance_usable"),
                    "visibility_boundary_status": case.get("visibility_boundary_status"),
                    "visibility_boundary_max_severity": case.get("visibility_boundary_max_severity"),
                    "receipt_lifecycle_status": case.get("receipt_lifecycle_status"),
                    "closure_thread_readiness": case.get("closure_thread_readiness"),
                    "api_runtime_error_class": case.get("api_runtime_error_class"),
                    "api_recommended_action": case.get("api_recommended_action"),
                    "resume_safety": case.get("resume_safety"),
                    "safe_to_resume_from_checkpoint": case.get("safe_to_resume_from_checkpoint"),
                    "required_artifact_present_count": case.get("required_artifact_present_count"),
                    "required_artifact_missing_count": case.get("required_artifact_missing_count"),
                    "missing_required_artifacts": ";".join(case.get("missing_required_artifacts") or []),
                    "required_sidecar_present_count": case.get("required_sidecar_present_count"),
                    "required_sidecar_missing_count": case.get("required_sidecar_missing_count"),
                    "missing_sidecars": ";".join(case.get("missing_sidecars") or []),
                    "interface_observation_level": case.get("interface_observation_level"),
                    "interface_doctor_envelope_repaired_count": (case.get("interface_signal_counts") or {}).get("doctor_envelope_repaired_count", 0),
                    "interface_invalid_fallback_count": (case.get("interface_signal_counts") or {}).get("doctor_envelope_invalid_fallback_count", 0),
                    "interface_workspace_cap_enforced_count": (case.get("interface_signal_counts") or {}).get("workspace_round_cap_enforced_count", 0),
                    "interface_boundary_rewrite_count": (case.get("interface_signal_counts") or {}).get("patient_facing_boundary_rewrite_count", 0),
                    "interface_raw_envelope_patient_visible_count": (case.get("interface_signal_counts") or {}).get("raw_envelope_patient_visible_count", 0),
                    "interface_workspace_artifact_patient_visible_count": (case.get("interface_signal_counts") or {}).get("workspace_artifact_patient_visible_count", 0),
                }
            )
    with path.open("w", encoding="utf-8", newline="") as f:
        fieldnames = [
            "doctor_model",
            "case_id",
            "run_id",
            "turns_completed",
            "closure_status",
            "evaluation_mode",
            "evaluation_overall",
            "quality_status",
            "benchmark_validity_status",
            "benchmark_primary",
            "doctor_performance_usable",
            "visibility_boundary_status",
            "visibility_boundary_max_severity",
            "receipt_lifecycle_status",
            "closure_thread_readiness",
            "api_runtime_error_class",
            "api_recommended_action",
            "resume_safety",
            "safe_to_resume_from_checkpoint",
            "required_artifact_present_count",
            "required_artifact_missing_count",
            "missing_required_artifacts",
            "required_sidecar_present_count",
            "required_sidecar_missing_count",
            "missing_sidecars",
            "interface_observation_level",
            "interface_doctor_envelope_repaired_count",
            "interface_invalid_fallback_count",
            "interface_workspace_cap_enforced_count",
            "interface_boundary_rewrite_count",
            "interface_raw_envelope_patient_visible_count",
            "interface_workspace_artifact_patient_visible_count",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _redacted_cmd(cmd: list[str]) -> list[str]:
    redacted = []
    skip_next = False
    for i, part in enumerate(cmd):
        if skip_next:
            redacted.append("<redacted>")
            skip_next = False
            continue
        redacted.append(part)
        if part in {"--base-url"}:
            skip_next = True
    return redacted


if __name__ == "__main__":
    raise SystemExit(main())
