#!/usr/bin/env python3
"""Build a self-contained CL120 public supplement from validated final artifacts.

The builder refuses partial matrices and stale analysis. It preserves every Judge
attempt, but rewrites the public trajectory score block from the canonical matrix,
removes withdrawn empirical-difficulty fields and attaches the canonical contract-only burden profile.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import statistics
from typing import Any

EXPECTED_JUDGES = ("GPT-5.5", "gpt-5.6-sol", "DeepSeek-V4-Pro", "GLM-5")
SCHEMA_VERSION = "careloop.cl120.public_release.v3_task_burden"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"cannot_write_empty_csv:{path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), extrasaction="raise", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def require_pass(path: Path, required: dict[str, Any]) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("result") != "PASS":
        raise ValueError(f"audit_not_pass:{path}")
    for key, expected in required.items():
        if document.get(key) != expected:
            raise ValueError(f"audit_requirement_failed:{path}:{key}:{document.get(key)!r}!={expected!r}")
    return document


def build(
    *, root: Path, packet_manifest: Path, result_root: Path,
    aggregation_dir: Path, analysis_dir: Path, output_dir: Path,
    expected_packets: int = 1200, expected_judges: int = 4,
    expected_judge_models: tuple[str, ...] = EXPECTED_JUDGES,
) -> dict[str, Any]:
    root = root.resolve()
    packet_manifest = resolve(root, packet_manifest)
    result_root = resolve(root, result_root)
    aggregation_dir = resolve(root, aggregation_dir)
    analysis_dir = resolve(root, analysis_dir)
    output_dir = resolve(root, output_dir)
    if output_dir.exists():
        if not output_dir.is_dir() or any(output_dir.iterdir()):
            raise FileExistsError(f"output_dir_not_fresh:{output_dir}")
    else:
        output_dir.mkdir(parents=True)

    aggregation_audit = require_pass(
        aggregation_dir / "aggregation_audit.json",
        {"expected_packet_count": expected_packets,
         "expected_judge_count": expected_judges,
         "validated_unique_cell_count": expected_packets * expected_judges,
         "missing_cell_count": 0, "duplicate_cell_count": 0, "invalid_cell_count": 0},
    )
    analysis_audit = require_pass(analysis_dir / "analysis_audit.json", {})
    if analysis_audit.get("population", {}).get("judge_cells") != expected_packets * expected_judges:
        raise ValueError("analysis_population_not_complete")

    manifest_doc = json.loads(packet_manifest.read_text(encoding="utf-8"))
    packet_rows = manifest_doc.get("packets")
    if not isinstance(packet_rows, list) or len(packet_rows) != expected_packets:
        raise ValueError("packet_manifest_population_mismatch")
    scores = read_csv(aggregation_dir / "canonical_scores.csv")
    if len(scores) != expected_packets * expected_judges:
        raise ValueError("canonical_score_population_mismatch")
    score_by_cell: dict[tuple[str, str], dict[str, str]] = {}
    for row in scores:
        key = (row["packet_id"], row["judge_model"])
        if key in score_by_cell:
            raise ValueError(f"duplicate_canonical_score:{key}")
        score_by_cell[key] = row
    judges = sorted({row["judge_model"] for row in scores})
    if set(judges) != set(expected_judge_models):
        raise ValueError(f"unexpected_judges:{judges}")
    attempt_file_count = 0
    for row in scores:
        cell_root = result_root / "results" / row["judge_model"] / row["packet_id"]
        canonical_path = cell_root / "canonical.json"
        if not canonical_path.is_file():
            raise ValueError(f"canonical_result_missing:{row['judge_model']}:{row['packet_id']}")
        expected_canonical_sha = row.get("canonical_json_sha256", "")
        if expected_canonical_sha and sha256(canonical_path) != expected_canonical_sha:
            raise ValueError(f"canonical_result_hash_mismatch:{row['judge_model']}:{row['packet_id']}")
        attempts = cell_root / "direct" / "attempts"
        request_files = list(attempts.glob("attempt_*/request_meta.json")) if attempts.is_dir() else []
        validation_files = list(attempts.glob("attempt_*/validation.json")) if attempts.is_dir() else []
        if not request_files or len(request_files) != len(validation_files):
            raise ValueError(f"judge_attempt_lineage_incomplete:{row['judge_model']}:{row['packet_id']}")
        attempt_file_count += len(request_files)

    case_metadata_rows = read_csv(analysis_dir / "case_metadata_deidentified_recomputed.csv")
    case_metadata = {row["case_id"]: row for row in case_metadata_rows}
    trajectory_results = {row["packet_id"]: row for row in read_csv(analysis_dir / "trajectory_level_results.csv")}
    if len(trajectory_results) != expected_packets:
        raise ValueError("trajectory_analysis_population_mismatch")

    lineage_rows: list[dict[str, Any]] = []
    public_packet_rows: list[dict[str, Any]] = []
    public_manifest_packets: list[dict[str, Any]] = []
    for manifest_row in sorted(packet_rows, key=lambda row: (row["doctor_model"], row["case_id"])):
        packet_id = str(manifest_row["packet_id"])
        source_path = resolve(root, manifest_row["source_trajectory_file"])
        if sha256(source_path) != manifest_row["source_trajectory_sha256"]:
            raise ValueError(f"source_trajectory_hash_mismatch:{packet_id}")
        source = json.loads(source_path.read_text(encoding="utf-8"))
        rows = [score_by_cell[(packet_id, judge)] for judge in judges]
        grades = [int(row["trajectory_grade"]) for row in rows]
        reported = [int(row["judge_reported_grade"]) for row in rows]
        analysis_row = trajectory_results[packet_id]
        public_model_slug = str(analysis_row["public_model_slug"])
        relative_trajectory = Path("trajectories") / "by_model" / public_model_slug / f"{manifest_row['case_id']}.json"
        source["schema_version"] = "careloop.cl120.public_trajectory.v3_contract_burden"
        source["scores"] = {
            "canonical_by_judge": {row["judge_model"]: int(row["trajectory_grade"]) for row in rows},
            "judge_reported_by_judge": {row["judge_model"]: int(row["judge_reported_grade"]) for row in rows},
            "judge_reported_matches_canonical": {
                row["judge_model"]: str(row["judge_reported_grade_matches_derived"]).lower() == "true" for row in rows
            },
            "aggregate_median": statistics.median(grades),
            "aggregate_mean": statistics.mean(grades),
            "judge_score_range": max(grades) - min(grades),
            "judge_score_population_sd": statistics.pstdev(grades),
            "policy": "canonical grades deterministically derived from each Judge's unmodified typed findings under the frozen rubric",
        }
        authored = {k: v for k, v in case_metadata[manifest_row["case_id"]].items() if k != "case_id"}
        source["case_metadata"] = authored
        source.pop("quality_report_public", None)
        source["canonical_release_provenance"] = {
            "packet_id": packet_id,
            "packet_sha256": manifest_row["packet_sha256"],
            "frozen_source_trajectory_sha256": manifest_row["source_trajectory_sha256"],
            "canonical_score_matrix_sha256": sha256(aggregation_dir / "canonical_scores.csv"),
            "legacy_embedded_scores_removed": True,
            "legacy_empirical_difficulty_removed": True,
            "contract_burden_profile_added": True,
        }
        destination = output_dir / relative_trajectory
        write_json(destination, source)
        public_sha = sha256(destination)

        packet_source = resolve(root, manifest_row["packet_file"])
        if sha256(packet_source) != manifest_row["packet_sha256"]:
            raise ValueError(f"packet_hash_mismatch:{packet_id}")
        relative_packet = Path("judge_packets") / f"{packet_id}.judge_packet.json"
        packet_destination = output_dir / relative_packet
        packet_destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(packet_source, packet_destination)

        lineage_rows.append({
            "packet_id": packet_id, "case_id": manifest_row["case_id"],
            "doctor_model": manifest_row["doctor_model"],
            "frozen_source_trajectory_sha256": manifest_row["source_trajectory_sha256"],
            "public_trajectory_file": relative_trajectory.as_posix(),
            "public_trajectory_sha256": public_sha,
            "judge_packet_file": relative_packet.as_posix(),
            "judge_packet_sha256": manifest_row["packet_sha256"],
        })
        public_packet_rows.append({
            "packet_id": packet_id, "case_id": manifest_row["case_id"],
            "doctor_model": manifest_row["doctor_model"], "terminal_status": manifest_row["terminal_status"],
            "judge_packet_file": relative_packet.as_posix(), "judge_packet_sha256": manifest_row["packet_sha256"],
            "public_trajectory_file": relative_trajectory.as_posix(), "public_trajectory_sha256": public_sha,
            "frozen_source_trajectory_sha256": manifest_row["source_trajectory_sha256"],
            "source_case_sha256": manifest_row["case_sha256"],
        })
        # Rebased JSON manifest for self-contained public reanalysis. The
        # frozen source hash remains the canonical lineage identity; the public
        # hash validates the rewritten trajectory file actually distributed.
        public_manifest_packets.append({
            **{key: value for key, value in manifest_row.items() if key not in {
                "case_file", "packet_file", "source_trajectory_file"
            }},
            "packet_file": relative_packet.as_posix(),
            "source_trajectory_file": relative_trajectory.as_posix(),
            "public_trajectory_sha256": public_sha,
        })

    # Preserve every accepted and failed attempt exactly as recorded. No result
    # content is rewritten by the release builder.
    shutil.copytree(result_root / "results", output_dir / "judge_results")
    shutil.copytree(analysis_dir, output_dir / "analysis")
    # Include the exact canonical matrix used by the analysis; unlike the
    # internal aggregation lineage table, it contains no workstation paths.
    shutil.copy2(aggregation_dir / "canonical_scores.csv", output_dir / "analysis" / "canonical_scores.csv")
    provenance = output_dir / "provenance"
    provenance.mkdir(parents=True, exist_ok=True)
    for name in ("run_plan.json", "run_metadata.json", "progress.json", "supervisor_status.json", "supplemental_retry_plan.json", "supplemental_retry_results.json"):
        source_path = result_root / name
        if source_path.is_file():
            shutil.copy2(source_path, provenance / name)
    retries = result_root / "supplemental_retries"
    if retries.is_dir():
        shutil.copytree(retries, provenance / "supplemental_retries")
    shutil.copy2(aggregation_dir / "aggregation_audit.json", provenance / "aggregation_audit.json")
    write_csv(output_dir / "metadata" / "trajectory_lineage.csv", lineage_rows)
    write_csv(output_dir / "metadata" / "packet_manifest_public.csv", public_packet_rows)
    public_manifest = {
        "schema_version": "careloop.judge_packet_manifest.public.v2_contract_burden",
        "protocol_version": manifest_doc.get("protocol_version"),
        "protocol_sha256": manifest_doc.get("protocol_sha256"),
        "source_frozen_packet_manifest_sha256": sha256(packet_manifest),
        "evidence_policy": manifest_doc.get("evidence_policy", {}),
        "packet_count": len(public_manifest_packets),
        "population": manifest_doc.get("population", {}),
        "paths_rebased_for_public_release": True,
        "packets": public_manifest_packets,
    }
    public_manifest_path = output_dir / "metadata" / "packet_manifest_public.json"
    write_json(public_manifest_path, public_manifest)
    public_metadata_path = output_dir / "metadata" / "case_metadata_deidentified.csv"
    shutil.copy2(analysis_dir / "case_metadata_deidentified_recomputed.csv", public_metadata_path)

    # The analysis itself was run against frozen workspace inputs. Rebase only
    # its input locators for publication, preserving the original audit hash in
    # RELEASE_BUILD_AUDIT and leaving all analytical output hashes untouched.
    public_analysis_audit_path = output_dir / "analysis" / "analysis_audit.json"
    public_analysis_audit = json.loads(public_analysis_audit_path.read_text(encoding="utf-8"))
    public_analysis_audit["source_files"] = {
        "canonical_scores": {
            "path": "canonical_scores.csv",
            "sha256": sha256(output_dir / "analysis" / "canonical_scores.csv"),
        },
        "packet_manifest": {
            "path": "../metadata/packet_manifest_public.json",
            "sha256": sha256(public_manifest_path),
        },
        "case_metadata": {
            "path": "../metadata/case_metadata_deidentified.csv",
            "sha256": sha256(public_metadata_path),
        },
    }
    public_analysis_audit["public_release_rebased_inputs"] = True
    write_json(public_analysis_audit_path, public_analysis_audit)
    shutil.copy2(public_analysis_audit_path, provenance / "analysis_audit.json")

    readme = f"""# CareLoop CL120 final canonical supplement

This release contains {expected_packets:,} frozen trajectories and {expected_packets * expected_judges:,} Judge cells.
All four canonical grades for every trajectory were derived deterministically from each
Judge's unmodified structured findings under the frozen 1--5 rubric. Judge-proposed
grades and output-quality diagnostics remain available separately.

- `trajectories/`: public trajectories with canonical scores and contract-only burden profiles; withdrawn legacy difficulty fields are removed.
- `judge_packets/`: exact complete-context, outcome-blind packets sent to each Judge.
- `judge_results/`: canonical records plus every raw attempt and validation record.
- `analysis/`: reproducible primary tables, canonical score matrix, ranking sensitivity, Judge agreement, multidimensional task-burden outputs, and explicitly supplementary exploratory analyses.
- The aggregate contract index is a supplementary negative construct-validation result, not a validated case-difficulty scale.
- `analysis/judge_model_identity.csv`: official display names linked to exact internal run identifiers.
- `metadata/trajectory_lineage.csv`: frozen-source to final-public trajectory hashes.
- `metadata/packet_manifest_public.json`: release-rebased manifest for self-contained reanalysis.
- `provenance/`: original manifest hash, run metadata, retry lineage, and validation audits.

No trajectory was clipped, sampled, chunked, summarized, or repaired by another model.
Runtime errors in the original 1,200-trajectory experiment are retained as observed outcomes.

The manuscript's evidence-audited disagreement example is `case_CL120_EHR_058` /
GPT-5.6 Sol, packet `case_CL120_EHR_058__anon_bccc18593eaa2fc0`; the complete packet
and all four canonical Judge records are included here.
"""
    (output_dir / "README.md").write_text(readme, encoding="utf-8")
    release_audit = {
        "schema_version": SCHEMA_VERSION,
        "packet_count": len(public_packet_rows), "judge_cell_count": len(scores),
        "trajectory_count": len(lineage_rows), "judge_models": judges,
        "judge_attempt_count": attempt_file_count,
        "aggregation_audit_sha256": sha256(aggregation_dir / "aggregation_audit.json"),
        "source_analysis_audit_sha256": sha256(analysis_dir / "analysis_audit.json"),
        "source_frozen_packet_manifest_sha256": sha256(packet_manifest),
        "public_packet_manifest_sha256": sha256(public_manifest_path),
        "public_analysis_audit_sha256": sha256(public_analysis_audit_path),
        "public_paths_rebased": True,
        "legacy_embedded_scores_removed": True,
        "legacy_empirical_difficulty_removed": True,
        "contract_burden_profile_added": True,
        "all_judge_attempts_preserved": True,
        "result": "PASS",
    }
    write_json(output_dir / "RELEASE_BUILD_AUDIT.json", release_audit)
    return release_audit


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=Path.cwd())
    ap.add_argument("--packet-manifest", type=Path, required=True)
    ap.add_argument("--result-root", type=Path, required=True)
    ap.add_argument("--aggregation-dir", type=Path, required=True)
    ap.add_argument("--analysis-dir", type=Path, required=True)
    ap.add_argument("--output-dir", type=Path, required=True)
    ap.add_argument("--expected-packets", type=int, default=1200)
    ap.add_argument("--expected-judges", type=int, default=4)
    ap.add_argument("--judge-models", nargs="+", default=list(EXPECTED_JUDGES))
    args = ap.parse_args()
    audit = build(
        root=args.root, packet_manifest=args.packet_manifest, result_root=args.result_root,
        aggregation_dir=args.aggregation_dir, analysis_dir=args.analysis_dir, output_dir=args.output_dir,
        expected_packets=args.expected_packets, expected_judges=args.expected_judges,
        expected_judge_models=tuple(args.judge_models),
    )
    print(json.dumps(audit, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
