from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from test_canonical_analysis import fixture

REPO = Path(__file__).parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


analysis = load_module("canonical_analysis_for_release", REPO / "scripts" / "analyze_cl120_canonical_results.py")
builder = load_module("release_builder", REPO / "scripts" / "build_cl120_release_supplement.py")


def test_release_builder_replaces_withdrawn_fields_and_preserves_lineage(tmp_path):
    scores, manifest, metadata = fixture(tmp_path)
    analysis_dir = tmp_path / "analysis"
    analysis.analyze(
        root=tmp_path, canonical_scores=scores, packet_manifest=manifest,
        case_metadata=metadata, output_dir=analysis_dir,
        expected_packets=8, expected_judges=2, expected_models=2, expected_cases=4,
        bootstrap_replicates=10, bootstrap_seed=7,
    )
    aggregation = tmp_path / "aggregation"
    aggregation.mkdir()
    (aggregation / "canonical_scores.csv").write_bytes(scores.read_bytes())
    (aggregation / "aggregation_audit.json").write_text(json.dumps({
        "result": "PASS", "expected_packet_count": 8, "expected_judge_count": 2,
        "validated_unique_cell_count": 16, "missing_cell_count": 0,
        "duplicate_cell_count": 0, "invalid_cell_count": 0,
    }), encoding="utf-8")
    manifest_doc = json.loads(manifest.read_text(encoding="utf-8"))
    result_root = tmp_path / "results_root"
    for packet in manifest_doc["packets"]:
        packet["case_sha256"] = "c" * 64
        for judge in ("Judge-A", "Judge-B"):
            cell = result_root / "results" / judge / packet["packet_id"]
            attempt = cell / "direct" / "attempts" / "attempt_0001"
            attempt.mkdir(parents=True)
            (cell / "canonical.json").write_text("{}", encoding="utf-8")
            (attempt / "request_meta.json").write_text("{}", encoding="utf-8")
            (attempt / "validation.json").write_text('{"accepted":true}', encoding="utf-8")
    manifest.write_text(json.dumps(manifest_doc), encoding="utf-8")

    output = tmp_path / "release"
    audit = builder.build(
        root=tmp_path, packet_manifest=manifest, result_root=result_root,
        aggregation_dir=aggregation, analysis_dir=analysis_dir, output_dir=output,
        expected_packets=8, expected_judges=2, expected_judge_models=("Judge-A", "Judge-B"),
    )
    assert audit["result"] == "PASS"
    assert audit["judge_attempt_count"] == 16
    lineage = builder.read_csv(output / "metadata" / "trajectory_lineage.csv")
    assert len(lineage) == 8
    released = json.loads((output / lineage[0]["public_trajectory_file"]).read_text(encoding="utf-8"))
    assert released["scores"]["aggregate_median"] != 999
    assert released["canonical_release_provenance"]["legacy_embedded_scores_removed"] is True
    assert "quality_report_public" not in released


def test_release_builder_emits_rebased_self_contained_analysis_inputs(tmp_path):
    scores, manifest, metadata = fixture(tmp_path)
    analysis_dir = tmp_path / "analysis"
    analysis.analyze(
        root=tmp_path, canonical_scores=scores, packet_manifest=manifest,
        case_metadata=metadata, output_dir=analysis_dir,
        expected_packets=8, expected_judges=2, expected_models=2, expected_cases=4,
        bootstrap_replicates=10, bootstrap_seed=7,
    )
    aggregation = tmp_path / "aggregation"
    aggregation.mkdir()
    (aggregation / "canonical_scores.csv").write_bytes(scores.read_bytes())
    (aggregation / "aggregation_audit.json").write_text(json.dumps({
        "result": "PASS", "expected_packet_count": 8, "expected_judge_count": 2,
        "validated_unique_cell_count": 16, "missing_cell_count": 0,
        "duplicate_cell_count": 0, "invalid_cell_count": 0,
    }), encoding="utf-8")
    manifest_doc = json.loads(manifest.read_text(encoding="utf-8"))
    result_root = tmp_path / "results_root"
    for packet in manifest_doc["packets"]:
        packet["case_sha256"] = "c" * 64
        for judge in ("Judge-A", "Judge-B"):
            cell = result_root / "results" / judge / packet["packet_id"]
            attempt = cell / "direct" / "attempts" / "attempt_0001"
            attempt.mkdir(parents=True)
            (cell / "canonical.json").write_text("{}", encoding="utf-8")
            (attempt / "request_meta.json").write_text("{}", encoding="utf-8")
            (attempt / "validation.json").write_text('{"accepted":true}', encoding="utf-8")
    manifest.write_text(json.dumps(manifest_doc), encoding="utf-8")

    output = tmp_path / "release"
    builder.build(
        root=tmp_path, packet_manifest=manifest, result_root=result_root,
        aggregation_dir=aggregation, analysis_dir=analysis_dir, output_dir=output,
        expected_packets=8, expected_judges=2, expected_judge_models=("Judge-A", "Judge-B"),
    )
    public_manifest_path = output / "metadata" / "packet_manifest_public.json"
    public_manifest = json.loads(public_manifest_path.read_text(encoding="utf-8"))
    assert public_manifest["paths_rebased_for_public_release"] is True
    assert len(public_manifest["packets"]) == 8
    serialized = public_manifest_path.read_text(encoding="utf-8")
    assert str(tmp_path) not in serialized
    assert '"work/' not in serialized
    assert all("public_trajectory_sha256" in row for row in public_manifest["packets"])

    copied_audit = json.loads((output / "analysis" / "analysis_audit.json").read_text(encoding="utf-8"))
    assert copied_audit["public_release_rebased_inputs"] is True
    assert all(not Path(item["path"]).is_absolute() for item in copied_audit["source_files"].values())

    reproduced = tmp_path / "reproduced_analysis"
    rerun = analysis.analyze(
        root=output,
        canonical_scores=Path("analysis/canonical_scores.csv"),
        packet_manifest=Path("metadata/packet_manifest_public.json"),
        case_metadata=Path("metadata/case_metadata_deidentified.csv"),
        output_dir=reproduced,
        expected_packets=8, expected_judges=2, expected_models=2, expected_cases=4,
        bootstrap_replicates=10, bootstrap_seed=7,
    )
    assert rerun["result"] == "PASS"
