#!/usr/bin/env python3
"""Validate and aggregate a complete CareLoop V7 judge result matrix.

This script never reads legacy score tables.  It reconstructs the expected
packet-by-judge Cartesian product from the lossless packet manifest, requires
exactly one fully lineage-valid ``canonical.json`` for every cell, and writes
new derived tables only after all cells pass the V7 runner's validator.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any, Iterable

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))
_SCRIPTS_ROOT = Path(__file__).resolve().parent
if str(_SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_ROOT))

import run_final_trajectory_judge as runner
from careloop.evaluation import protocol_v7 as protocol

SCHEMA_VERSION = "careloop.canonical_judge_aggregation.v8_observation_preserving"
DEFAULT_JUDGES = tuple(runner.DEFAULT_JUDGES)


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def atomic_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)


def canonical_candidates(result_roots: Iterable[Path], judge: str, packet_id: str) -> list[Path]:
    return [
        root / "results" / judge / packet_id / "canonical.json"
        for root in result_roots
        if (root / "results" / judge / packet_id / "canonical.json").is_file()
    ]


def ensure_fresh_output(output_dir: Path) -> None:
    if output_dir.exists():
        if output_dir.is_dir() and not any(output_dir.iterdir()):
            return
        raise FileExistsError(f"output_dir_not_fresh:{output_dir}")
    output_dir.mkdir(parents=True)


def aggregate(
    *,
    root: Path,
    packet_manifest: Path,
    result_roots: list[Path],
    output_dir: Path,
    judges: list[str],
    expected_packets: int,
    expected_judges: int,
) -> dict[str, Any]:
    if not judges or len(judges) != len(set(judges)) or any(not judge.strip() for judge in judges):
        raise ValueError("judges_must_be_nonempty_and_unique")
    if len(judges) != expected_judges:
        raise ValueError(f"judge_count_mismatch:expected_{expected_judges}:got_{len(judges)}")
    if not result_roots or len(result_roots) != len(set(result_roots)):
        raise ValueError("result_roots_must_be_nonempty_and_unique")
    for result_root in result_roots:
        if not result_root.is_dir():
            raise ValueError(f"result_root_missing:{result_root}")

    rows = runner.load_manifest(packet_manifest, root)
    if len(rows) != expected_packets:
        raise ValueError(f"packet_count_mismatch:expected_{expected_packets}:got_{len(rows)}")
    expected_cells = expected_packets * expected_judges
    if expected_cells != len(rows) * len(judges):
        raise AssertionError("expected_cell_arithmetic_mismatch")

    failures: list[str] = []
    score_rows: list[dict[str, Any]] = []
    record_rows: list[dict[str, Any]] = []
    file_rows: list[dict[str, Any]] = []
    grade_by_judge: dict[str, Counter[int]] = defaultdict(Counter)
    mode_by_judge: dict[str, Counter[str]] = defaultdict(Counter)
    status_by_judge: dict[str, Counter[str]] = defaultdict(Counter)
    quality_by_judge: dict[str, Counter[str]] = defaultdict(Counter)
    reported_grade_mismatch_by_judge: Counter[str] = Counter()

    for manifest_row in rows:
        packet_id = str(manifest_row["packet_id"])
        packet_path = Path(manifest_row["_packet_path"])
        packet = json.loads(packet_path.read_text(encoding="utf-8"))
        for judge in judges:
            candidates = canonical_candidates(result_roots, judge, packet_id)
            if len(candidates) != 1:
                failures.append(f"canonical_candidate_count:{judge}:{packet_id}:{len(candidates)}")
                continue
            canonical_path = candidates[0]
            if not runner.canonical_is_valid(canonical_path, packet, judge, manifest_row):
                failures.append(f"canonical_validation_failed:{judge}:{packet_id}")
                continue
            record = json.loads(canonical_path.read_text(encoding="utf-8"))
            grade = record["trajectory_grade"]
            flags = record["derived_gate_flags"]
            relative_source = canonical_path.relative_to(root).as_posix()
            canonical_sha = file_sha256(canonical_path)
            identity = {
                "packet_id": packet_id,
                "case_id": str(manifest_row.get("case_id") or ""),
                "doctor_model": str(manifest_row.get("doctor_model") or ""),
                "judge_model": judge,
                "terminal_status": str(manifest_row.get("terminal_status") or ""),
            }
            score_rows.append(identity | {
                "trajectory_grade": grade,
                "trajectory_grade_label": record["trajectory_grade_label"],
                "judge_reported_grade": record["judge_reported_grade"],
                "judge_reported_grade_label": record["judge_reported_grade_label"],
                "judge_reported_grade_matches_derived": record["judge_reported_grade_matches_derived"],
                "judge_output_quality_error_count": record["judge_output_quality_error_count"],
                "judge_output_quality_errors_json": json.dumps(record["judge_output_quality_errors"], ensure_ascii=False, separators=(",", ":")),
                "serious_error_present": flags["serious_error_present"],
                "minor_or_moderate_error_present": flags["minor_or_moderate_error_present"],
                "important_non_chain_defect_present": flags["important_non_chain_defect_present"],
                "completed_high_order_count": flags["completed_high_order_count"],
                "all_dimensions_at_least_good": flags["all_dimensions_at_least_good"],
                "closure_valid": flags["closure_valid"],
                "strong_gate": flags["strong_gate"],
                "perfect_gate": flags["perfect_gate"],
                "mode": record["mode"],
                "chunk_count": record["chunk_count"],
                "packet_sha256": record["packet_sha256"],
                "source_trajectory_sha256": record["source_trajectory_sha256"],
                "source_case_sha256": record["source_case_sha256"],
                "canonical_json_sha256": canonical_sha,
            })
            record_rows.append({
                "identity": identity,
                "canonical_source_path": relative_source,
                "canonical_source_sha256": canonical_sha,
                "canonical": record,
            })
            file_rows.append(identity | {
                "canonical_source_path": relative_source,
                "size_bytes": canonical_path.stat().st_size,
                "sha256": canonical_sha,
            })
            grade_by_judge[judge][grade] += 1
            mode_by_judge[judge][record["mode"]] += 1
            status_by_judge[judge][identity["terminal_status"]] += 1
            if not record["judge_reported_grade_matches_derived"]:
                reported_grade_mismatch_by_judge[judge] += 1
            for error in record["judge_output_quality_errors"]:
                quality_by_judge[judge][error] += 1

    if failures:
        preview = ";".join(failures[:20])
        raise ValueError(f"canonical_matrix_invalid:{len(failures)}:{preview}")
    if len(score_rows) != expected_cells or len({(r["packet_id"], r["judge_model"]) for r in score_rows}) != expected_cells:
        raise AssertionError("canonical_matrix_cardinality_or_uniqueness_failure")

    ensure_fresh_output(output_dir)
    score_rows.sort(key=lambda row: (row["judge_model"], row["doctor_model"], row["case_id"], row["packet_id"]))
    record_rows.sort(key=lambda row: (row["identity"]["judge_model"], row["identity"]["packet_id"]))
    file_rows.sort(key=lambda row: (row["judge_model"], row["packet_id"]))

    scores_path = output_dir / "canonical_scores.csv"
    records_path = output_dir / "canonical_records.jsonl"
    files_path = output_dir / "canonical_source_files.csv"
    audit_path = output_dir / "aggregation_audit.json"
    score_fields = list(score_rows[0])
    atomic_csv(scores_path, score_rows, score_fields)
    atomic_text(records_path, "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in record_rows))
    atomic_csv(files_path, file_rows, list(file_rows[0]))

    audit = {
        "schema_version": SCHEMA_VERSION,
        "protocol_version": protocol.VERSION,
        "protocol_sha256": runner.protocol_sha256(),
        "runner_sha256": runner.runner_sha256(),
        "packet_manifest_sha256": file_sha256(packet_manifest),
        "expected_packet_count": expected_packets,
        "validated_packet_count": len(rows),
        "expected_judge_count": expected_judges,
        "judge_models": judges,
        "expected_cell_count": expected_cells,
        "validated_unique_cell_count": len(score_rows),
        "missing_cell_count": 0,
        "duplicate_cell_count": 0,
        "invalid_cell_count": 0,
        "grade_counts_by_judge": {judge: {str(k): v for k, v in sorted(grade_by_judge[judge].items())} for judge in judges},
        "mode_counts_by_judge": {judge: dict(sorted(mode_by_judge[judge].items())) for judge in judges},
        "terminal_status_counts_by_judge": {judge: dict(sorted(status_by_judge[judge].items())) for judge in judges},
        "judge_reported_grade_mismatch_by_judge": {
            judge: reported_grade_mismatch_by_judge[judge] for judge in judges
        },
        "judge_output_quality_error_counts_by_judge": {
            judge: dict(quality_by_judge[judge].most_common()) for judge in judges
        },
        "canonical_grade_policy": "deterministic_frozen_rubric_from_unmodified_judge_findings",
        "outputs": {
            scores_path.name: {"rows": len(score_rows), "sha256": file_sha256(scores_path)},
            records_path.name: {"rows": len(record_rows), "sha256": file_sha256(records_path)},
            files_path.name: {"rows": len(file_rows), "sha256": file_sha256(files_path)},
        },
        "result": "PASS",
    }
    atomic_text(audit_path, json.dumps(audit, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    return audit


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Validate and aggregate the complete CareLoop V7 judge matrix.")
    ap.add_argument("--root", type=Path, default=Path.cwd())
    ap.add_argument("--packet-manifest", type=Path, required=True)
    ap.add_argument("--result-roots", nargs="+", type=Path, required=True)
    ap.add_argument("--output-dir", type=Path, required=True)
    ap.add_argument("--judge-models", nargs="+", default=list(DEFAULT_JUDGES))
    ap.add_argument("--expected-packets", type=int, default=1200)
    ap.add_argument("--expected-judges", type=int, default=4)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    root = args.root.resolve()
    packet_manifest = runner.resolve_under_root(root, str(args.packet_manifest))
    result_roots = [runner.resolve_under_root(root, str(path)) for path in args.result_roots]
    output_dir = runner.resolve_under_root(root, str(args.output_dir))
    try:
        audit = aggregate(
            root=root,
            packet_manifest=packet_manifest,
            result_roots=result_roots,
            output_dir=output_dir,
            judges=args.judge_models,
            expected_packets=args.expected_packets,
            expected_judges=args.expected_judges,
        )
    except Exception:
        if output_dir.is_dir() and not any(output_dir.iterdir()):
            shutil.rmtree(output_dir)
        raise
    print(json.dumps(audit, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
