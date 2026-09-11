from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "analyze_cl120_canonical_results.py"
SPEC = importlib.util.spec_from_file_location("canonical_analysis", SCRIPT)
analysis = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(analysis)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def json_sha(value) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    packet_rows = []
    score_rows = []
    case_metadata_rows = []
    judges = ["Judge-A", "Judge-B"]
    statuses = {
        ("Model-A", "case_001"): "closed_success",
        ("Model-A", "case_002"): "closed_success",
        ("Model-A", "case_003"): "open_at_100",
        ("Model-A", "case_004"): "runtime_error",
        ("Model-B", "case_001"): "closed_success",
        ("Model-B", "case_002"): "closed_success",
        ("Model-B", "case_003"): "open_at_100",
        ("Model-B", "case_004"): "closed_success",
    }
    grades = {
        ("Model-A", "case_001"): [5, 5],
        ("Model-A", "case_002"): [4, 4],
        ("Model-A", "case_003"): [2, 2],
        ("Model-A", "case_004"): [1, 1],
        ("Model-B", "case_001"): [4, 4],
        ("Model-B", "case_002"): [3, 3],
        ("Model-B", "case_003"): [2, 2],
        ("Model-B", "case_004"): [2, 2],
    }
    packet_dir = tmp_path / "packets"
    packet_dir.mkdir(parents=True)
    for case_number in range(1, 5):
        case_id = f"case_{case_number:03d}"
        case_metadata_rows.append({
            "case_id": case_id,
            "empirical_difficulty_tier": "legacy_should_be_replaced",
            "empirical_difficulty_index": "999",
            "disease_domain": f"domain_{case_number}",
        })
        for model_number, model in enumerate(("Model-A", "Model-B"), start=1):
            packet_id = f"packet_{model_number}_{case_number}"
            status = statuses[(model, case_id)]
            turns_completed = case_number * 10 + model_number
            public_trajectory_id = f"{case_id}__{model}"
            trajectory_path = tmp_path / "trajectories" / "by_model" / model / f"{case_id}.json"
            trajectory_path.parent.mkdir(parents=True, exist_ok=True)
            events = [
                {"turn": 0, "event_type": "patient_message"},
                {"turn": 1, "event_type": "doctor_workspace_message"},
                {"turn": 1, "event_type": "doctor_workspace_result"},
                {"turn": 1, "event_type": "clinical_workspace_response"},
                {"turn": 1, "event_type": "doctor_message"},
            ]
            trajectory = {
                "public_trajectory_id": public_trajectory_id, "case_id": case_id,
                "doctor_model": model, "terminal_status": status,
                "turns_completed": turns_completed, "events_public": events,
                # Deliberately bogus withdrawn fields: analysis must not read them.
                "scores": {"aggregate_median": 999},
                "case_metadata": {"empirical_difficulty_index": 999},
                "runtime_error_public": {
                    "runtime_error_category": "provider" if status == "runtime_error" else "",
                    "runtime_error_type": "fixture_error" if status == "runtime_error" else "",
                },
                "closure_public": {"status": "closed" if status == "closed_success" else status, "closure_kind": "fixture"},
            }
            trajectory_path.write_text(json.dumps(trajectory), encoding="utf-8")
            source_sha256 = sha(trajectory_path)
            packet = {
                "protocol_version": "careloop_calibrated_v1_1_legacy_preserving_rejudging_v7_1_outcome_blind",
                "case_id": case_id,
                "case_contract": {
                    "evaluation_contract_v2": {
                        "responsibility_chain_required_items": [{"id": f"RC{i}"} for i in range(1, 6 + case_number)],
                        "high_order_test_points": [{"id": f"HO{i}"} for i in range(1, 5)],
                        "supporting_evaluation_context": {
                            "expected_evidence_channels": [f"channel_{i}" for i in range(1, 6 + min(case_number - 1, 3))],
                        },
                    },
                    "closure_contract_v2": {
                        "expected_turn_budget": {
                            "category": ("short", "medium", "long", "extra_long")[case_number - 1],
                            "expected_closure_within_100": ("yes", "yes", "maybe", "no")[case_number - 1],
                        },
                        "handoff_policy": {
                            "handoff_is_transition_only": case_number >= 3,
                            "required_receipts": ["owner", "timing"] + (["confirmation"] if case_number == 4 else []),
                        },
                        "blocking_residual_risks": [f"risk_{i}" for i in range(5 + (case_number == 4))],
                        "premature_closure_traps": [f"trap_{i}" for i in range(5 + (case_number == 4))],
                    },
                    "real_world_friction_design": {
                        "friction_intensity": ("light", "moderate", "heavy", "extreme")[case_number - 1],
                    },
                }, "events": [], "payloads": {}, "evidence_policy": {},
                "operational_metadata": {
                    "source_trajectory_sha256": source_sha256,
                    "source_trajectory_id_sha256": "b" * 64,
                    "terminal_status": status,
                    "turns_completed": turns_completed,
                    "runtime_error_present": status == "runtime_error",
                    "runtime_error": {"category": "provider"} if status == "runtime_error" else None,
                },
            }
            packet_path = packet_dir / f"{packet_id}.json"
            packet_path.write_text(json.dumps(packet), encoding="utf-8")
            packet_rows.append({
                "packet_id": packet_id, "case_id": case_id, "doctor_model": model,
                "terminal_status": status, "packet_file": str(packet_path.relative_to(tmp_path)),
                "packet_sha256": sha(packet_path), "source_trajectory_sha256": source_sha256,
                "source_trajectory_file": str(trajectory_path.relative_to(tmp_path)),
            })
            for judge, grade in zip(judges, grades[(model, case_id)]):
                score_rows.append({
                    "packet_id": packet_id, "case_id": case_id, "doctor_model": model,
                    "judge_model": judge, "terminal_status": status, "trajectory_grade": grade,
                    "trajectory_grade_label": {1: "unsafe", 2: "borderline", 3: "acceptable", 4: "strong", 5: "perfect"}[grade],
                    "serious_error_present": grade == 1,
                    "minor_or_moderate_error_present": grade == 2,
                    "important_non_chain_defect_present": False,
                    "completed_high_order_count": 4 if grade >= 4 else 1,
                    "all_dimensions_at_least_good": grade >= 4,
                    "closure_valid": status == "closed_success",
                    "strong_gate": grade >= 4, "perfect_gate": grade == 5,
                    "judge_reported_grade": grade,
                    "judge_reported_grade_label": {1: "unsafe", 2: "borderline", 3: "acceptable", 4: "strong", 5: "perfect"}[grade],
                    "judge_reported_grade_matches_derived": True,
                    "judge_output_quality_error_count": 0,
                    "judge_output_quality_errors_json": "[]",
                    "mode": "direct_complete_evidence", "chunk_count": 0,
                    "packet_sha256": json_sha(packet), "source_trajectory_sha256": source_sha256,
                })
    manifest_path = tmp_path / "packet_manifest.json"
    manifest_path.write_text(json.dumps({"packets": packet_rows}), encoding="utf-8")
    scores_path = tmp_path / "canonical_scores.csv"
    write_csv(scores_path, score_rows)
    metadata_path = tmp_path / "case_metadata.csv"
    write_csv(metadata_path, case_metadata_rows)
    return scores_path, manifest_path, metadata_path


def run_analysis(tmp_path: Path, out_name: str = "analysis"):
    scores, manifest, metadata = fixture(tmp_path)
    output = tmp_path / out_name
    audit = analysis.analyze(
        root=tmp_path, canonical_scores=scores, packet_manifest=manifest,
        case_metadata=metadata, output_dir=output,
        expected_packets=8, expected_judges=2, expected_models=2, expected_cases=4,
        bootstrap_replicates=100, bootstrap_seed=7,
    )
    return audit, output, scores


def test_analysis_reconstructs_primary_outputs_without_outcome_derived_difficulty(tmp_path):
    audit, output, _ = run_analysis(tmp_path)
    assert audit["result"] == "PASS"
    assert audit["population"]["judge_cells"] == 16
    assert "difficulty_tier_counts" not in audit
    assert "difficulty_index" not in audit["definitions"]
    assert audit["definitions"]["case_stratification_policy"].startswith("no outcome-derived")
    models = {row["doctor_model"]: row for row in analysis.read_csv(output / "model_level_summary.csv")}
    assert float(models["Model-A"]["primary_score_mean"]) == pytest.approx(3.0)
    assert float(models["Model-B"]["primary_score_mean"]) == pytest.approx(2.75)
    assert float(models["Model-A"]["primary_rank"]) == 1
    metadata = analysis.read_csv(output / "case_metadata_deidentified_recomputed.csv")
    assert all("empirical_difficulty_tier" not in row for row in metadata)
    assert all("empirical_difficulty_index" not in row for row in metadata)
    assert not (output / "model_by_difficulty_summary.csv").exists()
    assert len(analysis.read_csv(output / "canonical_judge_scores_long.csv")) == 16
    assert len(analysis.read_csv(output / "trajectory_level_results.csv")) == 8
    assert len(analysis.read_csv(output / "judge_output_quality_summary.csv")) == 2
    assert analysis.read_csv(output / "judge_output_quality_error_types.csv") == []
    identities = analysis.read_csv(output / "judge_model_identity.csv")
    assert {row["judge_model_id"] for row in identities} == {"Judge-A", "Judge-B"}
    assert len(analysis.read_csv(output / "judge_specific_model_rankings.csv")) == 4
    pairwise = analysis.read_csv(output / "pairwise_judge_model_ranking_consistency.csv")
    assert len(pairwise) == 1
    assert pairwise[0]["n_models"] == "2"
    assert pairwise[0]["top3_overlap_count"] == "2"
    assert analysis.read_csv(output / "judge_self_association_summary.csv") == []
    assert (output / "analysis_audit.json").is_file()


def test_analysis_is_deterministic(tmp_path):
    audit_a, _, _ = run_analysis(tmp_path / "a", "out")
    audit_b, _, _ = run_analysis(tmp_path / "b", "out")
    comparable_a = dict(audit_a); comparable_b = dict(audit_b)
    comparable_a.pop("source_files"); comparable_b.pop("source_files")
    assert comparable_a == comparable_b


def test_analysis_rejects_score_gate_violation(tmp_path):
    scores, manifest, metadata = fixture(tmp_path)
    rows = analysis.read_csv(scores)
    rows[0]["serious_error_present"] = "true"
    rows[0]["trajectory_grade"] = "5"
    write_csv(scores, rows)
    with pytest.raises(ValueError, match="serious_gate_violation"):
        analysis.analyze(
            root=tmp_path, canonical_scores=scores, packet_manifest=manifest,
            case_metadata=metadata, output_dir=tmp_path / "out",
            expected_packets=8, expected_judges=2, expected_models=2, expected_cases=4,
            bootstrap_replicates=10, bootstrap_seed=7,
        )


def test_analysis_accepts_overlapping_serious_and_minor_flags_at_grade_one(tmp_path):
    scores, manifest, metadata = fixture(tmp_path)
    rows = analysis.read_csv(scores)
    target = next(row for row in rows if row["trajectory_grade"] == "1")
    target["serious_error_present"] = "true"
    target["minor_or_moderate_error_present"] = "true"
    write_csv(scores, rows)
    audit = analysis.analyze(
        root=tmp_path, canonical_scores=scores, packet_manifest=manifest,
        case_metadata=metadata, output_dir=tmp_path / "out",
        expected_packets=8, expected_judges=2, expected_models=2, expected_cases=4,
        bootstrap_replicates=10, bootstrap_seed=7,
    )
    assert audit["result"] == "PASS"


def test_analysis_rejects_minor_only_above_grade_two(tmp_path):
    scores, manifest, metadata = fixture(tmp_path)
    rows = analysis.read_csv(scores)
    target = next(row for row in rows if row["trajectory_grade"] == "4")
    target["serious_error_present"] = "false"
    target["minor_or_moderate_error_present"] = "true"
    write_csv(scores, rows)
    with pytest.raises(ValueError, match="minor_moderate_gate_violation"):
        analysis.analyze(
            root=tmp_path, canonical_scores=scores, packet_manifest=manifest,
            case_metadata=metadata, output_dir=tmp_path / "out",
            expected_packets=8, expected_judges=2, expected_models=2, expected_cases=4,
            bootstrap_replicates=10, bootstrap_seed=7,
        )


def test_analysis_rejects_grade_five_when_perfect_gate_is_false(tmp_path):
    scores, manifest, metadata = fixture(tmp_path)
    rows = analysis.read_csv(scores)
    target = next(row for row in rows if row["trajectory_grade"] == "5")
    target["perfect_gate"] = "false"
    write_csv(scores, rows)
    with pytest.raises(ValueError, match="canonical_grade_gate_violation"):
        analysis.analyze(
            root=tmp_path, canonical_scores=scores, packet_manifest=manifest,
            case_metadata=metadata, output_dir=tmp_path / "out",
            expected_packets=8, expected_judges=2, expected_models=2, expected_cases=4,
            bootstrap_replicates=10, bootstrap_seed=7,
        )


def test_analysis_rejects_internally_inconsistent_strong_gate_flag(tmp_path):
    scores, manifest, metadata = fixture(tmp_path)
    rows = analysis.read_csv(scores)
    target = next(row for row in rows if row["trajectory_grade"] == "4")
    target["all_dimensions_at_least_good"] = "false"
    write_csv(scores, rows)
    with pytest.raises(ValueError, match="strong_gate_flag_inconsistent"):
        analysis.analyze(
            root=tmp_path, canonical_scores=scores, packet_manifest=manifest,
            case_metadata=metadata, output_dir=tmp_path / "out",
            expected_packets=8, expected_judges=2, expected_models=2, expected_cases=4,
            bootstrap_replicates=10, bootstrap_seed=7,
        )


def test_analysis_preserves_reported_grade_mismatch_and_quality_errors(tmp_path):
    scores, manifest, metadata = fixture(tmp_path)
    rows = analysis.read_csv(scores)
    rows[0]["judge_reported_grade"] = "4"
    rows[0]["judge_reported_grade_label"] = "strong"
    rows[0]["judge_reported_grade_matches_derived"] = "false"
    rows[0]["judge_output_quality_error_count"] = "2"
    rows[0]["judge_output_quality_errors_json"] = '["proposed_grade_inconsistent:expected_5","citation:nonverbatim_quote"]'
    write_csv(scores, rows)
    audit = analysis.analyze(
        root=tmp_path, canonical_scores=scores, packet_manifest=manifest,
        case_metadata=metadata, output_dir=tmp_path / "out",
        expected_packets=8, expected_judges=2, expected_models=2, expected_cases=4,
        bootstrap_replicates=10, bootstrap_seed=7,
    )
    assert audit["result"] == "PASS"
    long_rows = analysis.read_csv(tmp_path / "out" / "canonical_judge_scores_long.csv")
    first = next(row for row in long_rows if row["packet_id"] == rows[0]["packet_id"] and row["judge_model"] == rows[0]["judge_model"])
    assert first["canonical_grade"] == "5"
    assert first["judge_reported_grade"] == "4"
    summary = {row["judge_model"]: row for row in analysis.read_csv(tmp_path / "out" / "judge_output_quality_summary.csv")}
    assert summary[rows[0]["judge_model"]]["reported_grade_mismatch_count"] == "1"
    error_rows = analysis.read_csv(tmp_path / "out" / "judge_output_quality_error_types.csv")
    assert {row["quality_error"] for row in error_rows} == {"proposed_grade_inconsistent:expected_5", "citation:nonverbatim_quote"}


def test_analysis_rejects_source_trajectory_hash_mismatch(tmp_path):
    scores, manifest, metadata = fixture(tmp_path)
    manifest_doc = json.loads(manifest.read_text(encoding="utf-8"))
    source = tmp_path / manifest_doc["packets"][0]["source_trajectory_file"]
    source.write_text(source.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="source_trajectory_hash_mismatch"):
        analysis.analyze(
            root=tmp_path, canonical_scores=scores, packet_manifest=manifest,
            case_metadata=metadata, output_dir=tmp_path / "out",
            expected_packets=8, expected_judges=2, expected_models=2, expected_cases=4,
            bootstrap_replicates=10, bootstrap_seed=7,
        )


def test_analysis_rebuilds_workspace_and_failure_tables_without_legacy_scores(tmp_path):
    audit, output, _ = run_analysis(tmp_path)
    assert audit["result"] == "PASS"
    trajectory_rows = analysis.read_csv(output / "trajectory_level_results.csv")
    assert {row["workspace_turn_count"] for row in trajectory_rows} == {"1"}
    assert all(row["aggregate_score_median"] != "999" for row in trajectory_rows)
    failure_rows = analysis.read_csv(output / "trajectory_failure_flags.csv")
    assert len(failure_rows) == 8
    assert {row["primary_failure_mode"] for row in failure_rows}.issubset({
        "runtime_error", "open_at_100", "very_low_score", "long_or_tool_intensive_low_score",
        "workspace_underuse_candidate", "closed_but_low_score", "high_judge_disagreement",
        "no_major_failure_flag",
    })
    assert len(analysis.read_csv(output / "workspace_behavior_by_model.csv")) == 2
    assert len(analysis.read_csv(output / "case_challenge_map.csv")) == 4
    dimensions = analysis.read_csv(output / "model_by_clinical_dimension_summary.csv")
    dimension_types = {row["dimension_type"] for row in dimensions}
    assert "disease_domain" in dimension_types
    assert "exploratory_contract_complexity" in dimension_types
    profiles = analysis.read_csv(output / "case_contract_burden_profile.csv")
    assert len(profiles) == 4
    assert {row["contract_complexity_level"] for row in profiles} == {"low", "moderate", "high"}
    assert len(analysis.read_csv(output / "contract_burden_outcome_associations.csv")) == 24


def test_contract_burden_profile_is_outcome_independent(tmp_path):
    scores, manifest, metadata = fixture(tmp_path)
    output_a = tmp_path / "out_a"
    analysis.analyze(
        root=tmp_path, canonical_scores=scores, packet_manifest=manifest,
        case_metadata=metadata, output_dir=output_a,
        expected_packets=8, expected_judges=2, expected_models=2, expected_cases=4,
        bootstrap_replicates=20, bootstrap_seed=7,
    )
    profiles_a = analysis.read_csv(output_a / "case_contract_burden_profile.csv")

    rows = analysis.read_csv(scores)
    labels = {1: "unsafe", 2: "borderline", 3: "acceptable", 4: "strong", 5: "perfect"}
    for row in rows:
        old = int(row["trajectory_grade"])
        # Change outcomes while preserving the fixture's deterministic gate algebra.
        new = 4 if old < 4 else 3
        row["trajectory_grade"] = str(new)
        row["trajectory_grade_label"] = labels[new]
        row["judge_reported_grade"] = str(new)
        row["judge_reported_grade_label"] = labels[new]
        row["judge_reported_grade_matches_derived"] = "true"
        row["serious_error_present"] = "false"
        row["minor_or_moderate_error_present"] = "false"
        row["all_dimensions_at_least_good"] = "true" if new == 4 else "false"
        row["completed_high_order_count"] = "1" if new == 4 else "0"
        row["important_non_chain_defect_present"] = "false"
        row["closure_valid"] = "true" if new == 4 else "false"
        row["strong_gate"] = "true" if new == 4 else "false"
        row["perfect_gate"] = "false"
    write_csv(scores, rows)
    output_b = tmp_path / "out_b"
    analysis.analyze(
        root=tmp_path, canonical_scores=scores, packet_manifest=manifest,
        case_metadata=metadata, output_dir=output_b,
        expected_packets=8, expected_judges=2, expected_models=2, expected_cases=4,
        bootstrap_replicates=20, bootstrap_seed=7,
    )
    profiles_b = analysis.read_csv(output_b / "case_contract_burden_profile.csv")
    assert profiles_a == profiles_b


def test_contract_burden_profile_rejects_case_inconsistency(tmp_path):
    scores, manifest_path, metadata = fixture(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    target = next(row for row in manifest["packets"] if row["case_id"] == "case_001")
    packet_path = tmp_path / target["packet_file"]
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    packet["case_contract"]["real_world_friction_design"]["friction_intensity"] = "extreme"
    packet_path.write_text(json.dumps(packet), encoding="utf-8")
    target["packet_sha256"] = sha(packet_path)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="case_contract_burden_profile_mismatch"):
        analysis.analyze(
            root=tmp_path, canonical_scores=scores, packet_manifest=manifest_path,
            case_metadata=metadata, output_dir=tmp_path / "out",
            expected_packets=8, expected_judges=2, expected_models=2, expected_cases=4,
            bootstrap_replicates=10, bootstrap_seed=7,
        )
