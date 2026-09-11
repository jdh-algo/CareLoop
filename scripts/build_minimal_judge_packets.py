#!/usr/bin/env python3
"""Build lossless, blinded CL120 packets for the frozen ordinal judge protocol.

The historical filename is retained for compatibility. This implementation is
not "minimal": it preserves every public observable event and every character
of each scrubbed event payload. Authentic runtime-error trajectories remain in
the prespecified population and retain their typed terminal metadata. The
builder refuses missing or non-reproducible frozen case contracts, duplicate event IDs, and any
packet that fails the versioned protocol validator.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

ROOT_DEFAULT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DEFAULT))
from careloop.evaluation import protocol_v7 as protocol

SECRET_PATTERNS = [
    (re.compile(r"(?i)(api[_-]?key|authorization|bearer)\s*[:=]\s*[^\s,;]+"), r"\1=[REDACTED]"),
    (re.compile(r"https?://notebook-[^\s\"']+"), "[REDACTED_NOTEBOOK_URL]"),
    (re.compile(r"/Users/[^\s\"']+"), "[REDACTED_LOCAL_PATH]"),
]
MODEL_KEYS = {"doctor_model", "candidate_model", "model"}


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def scrub_text(text: str) -> str:
    result = text
    for pattern, replacement in SECRET_PATTERNS:
        result = pattern.sub(replacement, result)
    for model_name in sorted(protocol.MODEL_IDS, key=len, reverse=True):
        result = re.sub(re.escape(model_name), "[REDACTED_TESTED_MODEL]", result, flags=re.I)
    return result


def scrub(value: Any) -> Any:
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, list):
        return [scrub(child) for child in value]
    if isinstance(value, dict):
        return {str(key): scrub(child) for key, child in value.items() if str(key) not in MODEL_KEYS}
    return value


def stable_anon_id(source_id: str, case_id: str) -> str:
    return f"{case_id}__anon_{hashlib.sha256(source_id.encode('utf-8')).hexdigest()[:16]}"


def load_frozen_case_manifest(path: Path, case_dir: Path, expected_count: int = 120) -> dict[str, tuple[str, str]]:
    if not path.is_file():
        raise ValueError(f"missing_frozen_case_manifest:{path}")
    rows = list(csv.DictReader(path.open(encoding="utf-8-sig", newline="")))
    if expected_count and len(rows) != expected_count:
        raise ValueError(f"frozen_case_manifest_count_mismatch:expected_{expected_count}:got_{len(rows)}")
    result: dict[str, tuple[str, str]] = {}
    for index, row in enumerate(rows, 1):
        case_id = str(row.get("case_id") or "").strip()
        filename = str(row.get("file") or f"{case_id}.json").strip()
        source_sha = str(row.get("sha256") or row.get("approved_case_sha256") or "").strip()
        if not case_id or case_id in result:
            raise ValueError(f"frozen_case_manifest_duplicate_or_missing_case:{index}")
        if re.fullmatch(r"[0-9a-f]{64}", source_sha) is None:
            raise ValueError(f"frozen_case_manifest_bad_hash:{case_id}")
        case_path = case_dir / filename
        if not case_path.is_file() or file_sha(case_path) != source_sha:
            raise ValueError(f"frozen_case_manifest_file_hash_mismatch:{case_id}")
        result[case_id] = (filename, source_sha)
    return result

def load_case_contract(case_dir: Path, case_id: str, filename: str, expected_sha256: str) -> tuple[dict[str, Any], Path]:
    path = case_dir / filename
    if not path.is_file():
        raise ValueError(f"missing_case_contract:{case_id}")
    if file_sha(path) != expected_sha256:
        raise ValueError(f"frozen_case_hash_mismatch:{case_id}")
    case = load_json(path)
    contract = protocol.normalize_frozen_case_contract(scrub(case), expected_sha256)
    errors = protocol.validate_case_contract(contract, case_id)
    if errors:
        raise ValueError(f"invalid_frozen_case_contract:{case_id}:" + ",".join(errors))
    return contract, path

def source_evidence(trajectory: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return the complete observable ledger and a proof of its source scope.

    Public release JSONs already expose ``events_public``.  Canonical formal
    runtime artifacts instead store the append-only ledger at
    ``trajectory.events``; only the four declared tested-doctor-observable
    visibility classes are admissible evidence.  Internal audit/controller
    events are counted and hashed by the source trajectory but never shown to
    the judge.
    """
    if isinstance(trajectory.get("events_public"), list):
        events = trajectory["events_public"]
        return events, {
            "source_event_field": "events_public",
            "source_raw_event_count": len(events),
            "excluded_nonobservable_event_count": 0,
            "excluded_visibility_counts": {},
        }
    nested = trajectory.get("trajectory")
    raw_events = nested.get("events") if isinstance(nested, dict) else None
    if not isinstance(raw_events, list):
        raise ValueError("missing_observable_event_ledger")
    excluded: dict[str, int] = {}
    events: list[dict[str, Any]] = []
    for event in raw_events:
        if not isinstance(event, dict):
            raise ValueError("raw_event_not_object")
        visibility = str(event.get("visibility") or "")
        if visibility in protocol.VISIBLE:
            events.append(event)
        else:
            excluded[visibility or "<missing>"] = excluded.get(visibility or "<missing>", 0) + 1
    return events, {
        "source_event_field": "trajectory.events",
        "source_raw_event_count": len(raw_events),
        "excluded_nonobservable_event_count": len(raw_events) - len(events),
        "excluded_visibility_counts": excluded,
    }


def source_events(trajectory: dict[str, Any]) -> list[dict[str, Any]]:
    return source_evidence(trajectory)[0]


def reconstruct_scrubbed_events(
    event_rows: list[dict[str, Any]], payloads: dict[str, Any]
) -> list[dict[str, Any]]:
    """Reconstruct the exact ordered scrubbed event objects from packet parts."""
    result: list[dict[str, Any]] = []
    for event in event_rows:
        ref = event["payload_ref"]
        result.append({
            "event_id": event["event_id"],
            "turn": event["turn"],
            "actor": event["actor"],
            "event_type": event["event_type"],
            "sim_time": event["sim_time"],
            "visibility": event["visibility"],
            **payloads[ref],
        })
    return result


def build_lossless_events(events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    event_rows: list[dict[str, Any]] = []
    payloads: dict[str, Any] = {}
    ids: list[str] = []
    for index, original in enumerate(events, 1):
        if not isinstance(original, dict):
            raise ValueError(f"event_{index}_not_object")
        clean = scrub(original)
        event_id = str(clean.get("event_id") or "")
        if not event_id:
            raise ValueError(f"event_{index}_missing_event_id")
        ids.append(event_id)
        payload = {key: value for key, value in clean.items() if key not in {"event_id", "turn", "actor", "event_type", "sim_time", "visibility"}}
        payload_ref = digest(payload)
        payloads[payload_ref] = payload
        event_rows.append({
            "event_id": event_id,
            "turn": clean.get("turn"),
            "actor": clean.get("actor"),
            "event_type": clean.get("event_type"),
            "sim_time": clean.get("sim_time"),
            "visibility": clean.get("visibility"),
            "payload_ref": payload_ref,
        })
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate_event_ids")
    return event_rows, payloads


def trajectory_runtime_error_present(trajectory: dict[str, Any]) -> bool:
    """Parse the public runtime-error sidecar without relying on dict truthiness.

    Historical valid trajectories store a non-empty object whose
    ``runtime_error_flag`` is the string ``"False"``. Treating the whole object
    as a boolean incorrectly rejects every valid trajectory. This parser also
    fails closed on inconsistent status/flag combinations.
    """
    if "runtime_error_public" in trajectory:
        value = trajectory.get("runtime_error_public")
        terminal_status = str(trajectory.get("terminal_status") or "").strip()
    elif "runtime_error" in trajectory:
        value = trajectory.get("runtime_error")
        flag = isinstance(value, dict) and bool(str(value.get("type") or "").strip() or str(value.get("message") or "").strip())
        if value is not None and not isinstance(value, dict):
            raise ValueError("runtime_error_invalid_type")
        if isinstance(value, dict) and not flag:
            raise ValueError("runtime_error_empty_object")
        terminal_status = trajectory_terminal_status(trajectory)
        if (terminal_status == "runtime_error") != flag:
            raise ValueError("runtime_error_status_flag_mismatch")
        return flag
    else:
        raise ValueError("runtime_error_metadata_missing")
    if value is None:
        flag = False
    elif isinstance(value, bool):
        flag = value
    elif isinstance(value, dict):
        raw_flag = value.get("runtime_error_flag")
        if raw_flag is True or raw_flag == "True":
            flag = True
        elif raw_flag is False or raw_flag == "False":
            flag = False
        else:
            raise ValueError("runtime_error_flag_invalid_or_missing")
        detail_present = any(str(value.get(key) or "").strip() for key in (
            "runtime_error_category", "runtime_error_type", "runtime_error_message_excerpt"
        ))
        if flag is False and detail_present:
            raise ValueError("runtime_error_false_with_nonempty_details")
        if flag is True and not str(value.get("runtime_error_category") or "").strip():
            raise ValueError("runtime_error_true_without_category")
    else:
        raise ValueError("runtime_error_public_invalid_type")
    status_is_error = terminal_status == "runtime_error"
    if status_is_error != flag:
        raise ValueError("runtime_error_status_flag_mismatch")
    return flag


def trajectory_terminal_status(trajectory: dict[str, Any]) -> str:
    explicit = str(trajectory.get("terminal_status") or "").strip()
    if explicit:
        if explicit not in {"closed_success", "open_at_100", "runtime_error"}:
            raise ValueError("terminal_status_invalid")
        return explicit
    runtime_error = trajectory.get("runtime_error")
    if isinstance(runtime_error, dict) and runtime_error:
        return "runtime_error"
    closure = trajectory.get("closure")
    closure_status = str(closure.get("status") or "").strip() if isinstance(closure, dict) else ""
    if closure_status == "closed":
        return "closed_success"
    if closure_status == "open" and trajectory.get("turns_completed") == 100:
        return "open_at_100"
    raise ValueError("cannot_derive_terminal_status")


def runtime_error_metadata(trajectory: dict[str, Any], present: bool) -> dict[str, str] | None:
    if not present:
        return None
    public = trajectory.get("runtime_error_public")
    if isinstance(public, dict):
        return scrub({
            "category": public.get("runtime_error_category"),
            "type": public.get("runtime_error_type"),
            "message_excerpt": public.get("runtime_error_message_excerpt"),
        })
    raw = trajectory.get("runtime_error")
    if not isinstance(raw, dict):
        raise ValueError("runtime_error_metadata_not_object")
    message = str(raw.get("message") or "")
    lower = message.lower()
    if "content_filter" in lower or "responsibleaipolicyviolation" in lower:
        category = "content_filter_400"
    elif "timeout" in lower:
        category = "timeout"
    else:
        category = "provider_or_runtime_error"
    # Exact provider bodies and paths are not judge evidence.  Retain a typed,
    # bounded, scrubbed operational description without silently altering the
    # observable event ledger.
    return scrub({
        "category": category,
        "type": str(raw.get("type") or "RuntimeError"),
        "message_excerpt": "Provider/runtime error detail withheld from judge evidence; typed category retained.",
    })


def trajectory_doctor_model(trajectory: dict[str, Any]) -> str:
    direct = str(trajectory.get("doctor_model") or "").strip()
    if direct:
        return direct
    metadata = trajectory.get("metadata")
    cli = metadata.get("cli_run_metadata") if isinstance(metadata, dict) else None
    return str(cli.get("doctor_model") or "").strip() if isinstance(cli, dict) else ""


def trajectory_source_id(trajectory: dict[str, Any], source_sha256: str) -> str:
    """Return a stable private source identifier before packet anonymization."""
    declared = str(
        trajectory.get("public_trajectory_id")
        or trajectory.get("anonymous_trajectory_id")
        or trajectory.get("run_id")
        or ""
    ).strip()
    doctor_model = trajectory_doctor_model(trajectory)
    case_id = str(trajectory.get("case_id") or trajectory.get("case_label") or "").strip()
    return declared or f"{doctor_model}|{case_id}|{source_sha256}"


def validate_population(
    rows: list[dict[str, Any]],
    approved_case_ids: set[str],
    *,
    expected_trajectories: int,
    expected_models: int,
    expected_cases_per_model: int,
    expected_status_counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Prove the complete model × case population before writing a manifest."""
    if len(rows) != expected_trajectories:
        raise ValueError(f"trajectory_count_mismatch:expected_{expected_trajectories}:got_{len(rows)}")
    pair_counts = Counter((str(row["doctor_model"]), str(row["case_id"])) for row in rows)
    duplicates = sorted(pair for pair, count in pair_counts.items() if count != 1)
    if duplicates:
        raise ValueError("duplicate_model_case_pairs:" + canonical(duplicates[:20]))
    cases_by_model: dict[str, set[str]] = defaultdict(set)
    for model, case_id in pair_counts:
        cases_by_model[model].add(case_id)
    if len(cases_by_model) != expected_models:
        raise ValueError(f"tested_model_count_mismatch:expected_{expected_models}:got_{len(cases_by_model)}")
    for model, cases in sorted(cases_by_model.items()):
        if len(cases) != expected_cases_per_model:
            raise ValueError(
                f"cases_per_model_mismatch:{model}:expected_{expected_cases_per_model}:got_{len(cases)}"
            )
        if cases != approved_case_ids:
            missing = sorted(approved_case_ids - cases)
            extra = sorted(cases - approved_case_ids)
            raise ValueError(f"model_case_coverage_mismatch:{model}:missing={missing}:extra={extra}")
    statuses = Counter(str(row["terminal_status"]) for row in rows)
    if expected_status_counts is not None and dict(sorted(statuses.items())) != dict(sorted(expected_status_counts.items())):
        raise ValueError(
            "terminal_status_count_mismatch:expected="
            + canonical(expected_status_counts)
            + ":got="
            + canonical(dict(statuses))
        )
    return {
        "trajectory_count": len(rows),
        "tested_model_count": len(cases_by_model),
        "tested_models": sorted(cases_by_model),
        "cases_per_model": {model: len(cases) for model, cases in sorted(cases_by_model.items())},
        "terminal_status_counts": dict(sorted(statuses.items())),
    }


def parse_expected_status_counts(values: list[str]) -> dict[str, int] | None:
    if not values:
        return None
    result: dict[str, int] = {}
    for value in values:
        if "=" not in value:
            raise ValueError("expected_terminal_status_count_requires_STATUS=COUNT")
        status, raw_count = value.split("=", 1)
        status = status.strip()
        if status not in {"closed_success", "open_at_100", "runtime_error"} or status in result:
            raise ValueError("invalid_or_duplicate_expected_terminal_status:" + status)
        count = int(raw_count)
        if count < 0:
            raise ValueError("negative_expected_terminal_status_count:" + status)
        result[status] = count
    return result


def build_packet(trajectory: dict[str, Any], case_contract: dict[str, Any], source_sha256: str) -> dict[str, Any]:
    case_id = str(trajectory.get("case_id") or trajectory.get("case_label") or "")
    if not case_id:
        raise ValueError("missing_case_id")
    if case_contract.get("case_id") != case_id:
        raise ValueError("case_contract_case_id_mismatch")
    runtime_error_present = trajectory_runtime_error_present(trajectory)
    runtime_error = runtime_error_metadata(trajectory, runtime_error_present)
    terminal_status = trajectory_terminal_status(trajectory)
    events_source, source_scope = source_evidence(trajectory)
    events, payloads = build_lossless_events(events_source)
    expected_scrubbed_events = scrub(events_source)
    reconstructed_events = reconstruct_scrubbed_events(events, payloads)
    if reconstructed_events != expected_scrubbed_events:
        raise AssertionError("scrubbed_event_reconstruction_mismatch")
    source_id = trajectory_source_id(trajectory, source_sha256)
    packet = {
        "protocol_version": protocol.VERSION,
        "case_id": case_id,
        "case_contract": case_contract,
        # Deliberately expose only neutral terminal facts. Post-run empirical
        # difficulty, framework-authored closure rationales, and quality reports
        # can contain outcome-derived or evaluative language and therefore must
        # never enter a blinded judge prompt. They remain available to downstream
        # analysis from the immutable source trajectory, outside the judge packet.
        "operational_metadata": {
            "source_trajectory_sha256": source_sha256,
            "source_trajectory_id_sha256": hashlib.sha256(source_id.encode("utf-8")).hexdigest(),
            "terminal_status": terminal_status,
            "turns_completed": trajectory.get("turns_completed"),
            "runtime_error_present": runtime_error_present,
            "runtime_error": runtime_error,
        },
        "events": events,
        "payloads": payloads,
        "evidence_policy": {
            "all_observable_events_retained": True,
            "tested_model_names_redacted": True,
            "character_truncation": False,
            "event_sampling": False,
            "source_event_count": len(events_source),
            "retained_event_count": len(events),
            **source_scope,
            "redaction_policy_version": "careloop_judge_packet_redaction_v2_outcome_blind",
            "redaction_only_transformation": True,
            "outcome_derived_metadata_excluded": True,
            "posthoc_difficulty_excluded": True,
            "closure_summary_excluded": True,
            "quality_report_excluded": True,
            "source_events_sha256": digest(events_source),
            "scrubbed_events_sha256": digest(expected_scrubbed_events),
            "event_order_sha256": digest([event["event_id"] for event in events]),
        },
    }
    errors = protocol.validate_packet(packet)
    if errors:
        raise ValueError("invalid_packet:" + ",".join(errors))
    return packet


def display(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(text, encoding="utf-8")
    os.replace(temp, path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build lossless blinded CL120 ordinal judge packets.")
    parser.add_argument("--root", default=str(ROOT_DEFAULT))
    parser.add_argument("--trajectory-dir", default="public_supplement/cl120_20260911/trajectories/by_model")
    parser.add_argument("--trajectory-glob", default="**/*.json", help="Glob below --trajectory-dir; use **/trajectory.json for formal runtime artifacts.")
    parser.add_argument("--case-dir", default="DATA/WenFuYi_EHR/CareLoop_CL120_EHR_seeded_v1_0_freeze_20260904/cases")
    parser.add_argument("--case-manifest", default="DATA/WenFuYi_EHR/CareLoop_CL120_EHR_seeded_v1_0_freeze_20260904/manifests/cl120_v7_case_file_manifest.csv")
    parser.add_argument("--out-dir", default="outputs/judge_packets/cl120_blinded_ordinal_1to5")
    parser.add_argument("--manifest-out", default="outputs/judge_packets/cl120_blinded_ordinal_1to5_manifest.json")
    parser.add_argument("--expected-trajectories", type=int, default=1200)
    parser.add_argument("--expected-models", type=int, default=10)
    parser.add_argument("--expected-cases-per-model", type=int, default=120)
    parser.add_argument(
        "--expected-terminal-status-count", action="append", default=[], metavar="STATUS=COUNT",
        help="Optional repeatable exact status-count assertion, e.g. closed_success=1106.",
    )
    args = parser.parse_args()
    root = Path(args.root).resolve()
    trajectory_dir = (root / args.trajectory_dir).resolve()
    case_dir = (root / args.case_dir).resolve()
    case_manifest_path = (root / args.case_manifest).resolve()
    frozen_cases = load_frozen_case_manifest(case_manifest_path, case_dir)
    out_dir = (root / args.out_dir).resolve()
    if out_dir.exists() and any(path.is_file() for path in out_dir.rglob("*")):
        raise SystemExit(f"output_directory_not_fresh:{display(out_dir, root)}")
    expected_status_counts = parse_expected_status_counts(args.expected_terminal_status_count)
    paths = sorted(path for path in trajectory_dir.glob(args.trajectory_glob) if path.is_file())
    if not paths:
        raise SystemExit(f"No trajectory JSON files found under {display(trajectory_dir, root)}")
    rows=[]; seen=set()
    for path in paths:
        trajectory=load_json(path);case_id=str(trajectory.get("case_id") or trajectory.get("case_label") or "")
        if case_id not in frozen_cases:
            raise SystemExit(f"case_missing_from_frozen_manifest:{case_id}")
        case_filename,case_sha=frozen_cases[case_id]
        contract,case_path=load_case_contract(case_dir,case_id,case_filename,case_sha)
        source_sha256=file_sha(path)
        doctor_model=trajectory_doctor_model(trajectory)
        if not doctor_model:
            raise SystemExit(f"missing_doctor_model:{path}")
        packet=build_packet(trajectory,contract,source_sha256)
        packet_id=stable_anon_id(trajectory_source_id(trajectory,source_sha256),case_id)
        if packet_id in seen:raise SystemExit(f"duplicate_packet_id:{packet_id}")
        seen.add(packet_id)
        out_path=out_dir/(packet_id+'.judge_packet.json')
        atomic_write(out_path,json.dumps(packet,ensure_ascii=False,sort_keys=True,separators=(",", ":"))+"\n")
        rows.append({
            "packet_id":packet_id,"case_id":case_id,"doctor_model":doctor_model,
            "packet_file":display(out_path,root),"packet_chars":len(out_path.read_text()),
            "packet_sha256":file_sha(out_path),"source_trajectory_file":display(path,root),
            "source_trajectory_sha256":source_sha256,"case_file":display(case_path,root),
            "case_sha256":file_sha(case_path),
            "contract_payload_sha256":contract["provenance"]["normalized_contract_payload_sha256"],
            "contract_normalization_version":contract["provenance"]["normalization_version"],
            "source_event_count":len(source_events(trajectory)),
            "retained_event_count":len(packet['events']),
            "source_event_field":packet["evidence_policy"]["source_event_field"],
            "source_raw_event_count":packet["evidence_policy"]["source_raw_event_count"],
            "excluded_nonobservable_event_count":packet["evidence_policy"]["excluded_nonobservable_event_count"],
            "terminal_status":packet["operational_metadata"]["terminal_status"],
        })
    used_case_ids = {row["case_id"] for row in rows}
    if used_case_ids != set(frozen_cases):
        missing = sorted(set(frozen_cases) - used_case_ids)
        extra = sorted(used_case_ids - set(frozen_cases))
        raise SystemExit(f"trajectory_case_coverage_mismatch:missing={missing}:extra={extra}")
    population = validate_population(
        rows, set(frozen_cases),
        expected_trajectories=args.expected_trajectories,
        expected_models=args.expected_models,
        expected_cases_per_model=args.expected_cases_per_model,
        expected_status_counts=expected_status_counts,
    )
    manifest={
        "schema_version":"careloop.cl120.lossless_judge_packet_manifest.v7",
        "protocol_version":protocol.VERSION,"protocol_sha256":file_sha(Path(protocol.__file__)),
        "packet_count":len(rows),"trajectory_dir":args.trajectory_dir,"trajectory_glob":args.trajectory_glob,"case_dir":args.case_dir,
        "frozen_case_manifest":args.case_manifest,"frozen_case_manifest_sha256":file_sha(case_manifest_path),
        "population":population,
        "evidence_policy":{"all_observable_events_retained":True,"character_truncation":False,"event_sampling":False},
        "packets":rows,
    }
    manifest_path=(root/args.manifest_out).resolve();atomic_write(manifest_path,json.dumps(manifest,ensure_ascii=False,sort_keys=True,separators=(",", ":"))+"\n")
    print(f"Wrote {len(rows)} lossless CL120 judge packets to {display(out_dir,root)}")
    print(f"Manifest: {display(manifest_path,root)}")
    return 0

if __name__=="__main__":raise SystemExit(main())
