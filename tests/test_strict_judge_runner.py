from __future__ import annotations

import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from careloop.evaluation import protocol_v7 as protocol
from protocol_v7_fixtures import packet as packet_v7_fixture, result_for as result_for_v7_fixture


def result_for(grade: int) -> dict:
    return result_for_v7_fixture(grade)


def packet() -> dict:
    pkt = packet_v7_fixture()
    assert protocol.validate_packet(pkt) == []
    return pkt


SCRIPT = Path(__file__).parents[1] / "scripts" / "run_final_trajectory_judge.py"
SPEC = importlib.util.spec_from_file_location("strict_judge_runner", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def write_packet_and_manifest(root: Path) -> tuple[dict, dict, Path]:
    root.mkdir(parents=True, exist_ok=True)
    source_path = root / "source_trajectory.json"
    source_path.write_text('{"case_id":"case_X","source":"fixture"}\n', encoding="utf-8")
    case_path = root / "case_X.json"
    packet_path = root / "packet.judge_packet.json"
    pkt = packet()
    case_path.write_text(
        json.dumps(pkt["case_contract"], sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    pkt["operational_metadata"]["source_trajectory_sha256"] = runner.file_sha256(source_path)
    packet_path.write_text(
        json.dumps(pkt, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    policy = pkt["evidence_policy"]
    row = {
        "packet_id": "packet_1",
        "case_id": "case_X",
        "doctor_model": "tested-model-X",
        "packet_file": packet_path.name,
        "packet_sha256": runner.file_sha256(packet_path),
        "source_trajectory_file": source_path.name,
        "source_trajectory_sha256": runner.file_sha256(source_path),
        "case_file": case_path.name,
        "case_sha256": runner.file_sha256(case_path),
        "contract_payload_sha256": pkt["case_contract"]["provenance"]["normalized_contract_payload_sha256"],
        "source_event_count": policy["source_event_count"],
        "retained_event_count": policy["retained_event_count"],
        "source_event_field": policy["source_event_field"],
        "source_raw_event_count": policy["source_raw_event_count"],
        "excluded_nonobservable_event_count": policy["excluded_nonobservable_event_count"],
        "terminal_status": pkt["operational_metadata"]["terminal_status"],
    }
    manifest = {
        "protocol_version": protocol.VERSION,
        "protocol_sha256": runner.protocol_sha256(),
        "packet_count": 1,
        "population": {
            "trajectory_count": 1,
            "tested_model_count": 1,
            "tested_models": ["tested-model-X"],
            "cases_per_model": {"tested-model-X": 1},
            "terminal_status_counts": {"closed_success": 1},
        },
        "packets": [row],
    }
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return pkt, row, manifest_path


def make_direct_canonical(root: Path, pkt: dict, row: dict, model: str = "judge-X") -> Path:
    raw = result_for(4)
    messages = runner.direct_final_messages(pkt)
    meta = runner.request_meta(
        model,
        messages,
        "direct_final",
        row["packet_id"],
        "direct",
        True,
        4096,
        [],
    ) | {
        "attempt": 1,
        "attempts_in_this_invocation": 3,
        "elapsed_seconds": 0.1,
        "response_model_returned": model,
    }
    content = json.dumps(raw, ensure_ascii=False, separators=(",", ":"))
    envelope = {
        "model": model,
        "choices": [{"message": {"content": content}}],
    }
    stage = root / "direct"
    artifacts = runner.save_attempt(stage, 1, meta, envelope, content, [], None)
    run_meta = {
        "attempt": 1,
        "request_meta": meta,
        "artifacts": artifacts,
        "response_model_returned": model,
    }
    canonical = protocol.canonical_record(raw, pkt) | {
        "judge_reported_grade": raw["proposed_trajectory_grade"],
        "judge_reported_grade_label": raw["proposed_trajectory_grade_label"],
        "judge_reported_grade_matches_derived": True,
        "judge_output_quality_errors": [],
        "judge_output_quality_error_count": 0,
        "canonical_grade_source": "deterministic_frozen_rubric_from_unmodified_judge_findings",
        "result_policy_version": runner.RESULT_POLICY_VERSION,
        "judge_model": model,
        "judge_model_returned": model,
        "packet_id": row["packet_id"],
        "doctor_model_blinded_from_judge": True,
        "mode": "direct_complete_evidence",
        "chunk_count": 0,
        "chunk_body_chars": 60000,
        "chunk_review_manifest": [],
        "source_trajectory_sha256": row["source_trajectory_sha256"],
        "source_case_sha256": row["case_sha256"],
        "packet_file_sha256": row["packet_sha256"],
        "protocol_sha256": runner.protocol_sha256(),
        "runner_sha256": runner.runner_sha256(),
        "raw_result_sha256": protocol.digest(raw),
        "accepted_at_unix": 1.0,
        "final_attempt": run_meta,
    }
    canonical_path = root / "canonical.json"
    runner.atomic_json(canonical_path, canonical)
    return canonical_path


def rewrite_request_and_lineage(canonical_path: Path, mutate) -> None:
    canonical = json.loads(canonical_path.read_text(encoding="utf-8"))
    request_path = canonical_path.parent / "direct" / "attempts" / "attempt_0001" / "request_meta.json"
    request = json.loads(request_path.read_text(encoding="utf-8"))
    mutate(request)
    runner.atomic_json(request_path, request)
    canonical["final_attempt"]["request_meta"] = request
    canonical["final_attempt"]["artifacts"]["request_meta_sha256"] = runner.file_sha256(request_path)
    runner.atomic_json(canonical_path, canonical)


def rewrite_envelope_and_lineage(canonical_path: Path, mutate) -> None:
    canonical = json.loads(canonical_path.read_text(encoding="utf-8"))
    envelope_path = canonical_path.parent / "direct" / "attempts" / "attempt_0001" / "response_envelope.json"
    envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
    mutate(envelope)
    runner.atomic_json(envelope_path, envelope)
    canonical["final_attempt"]["artifacts"]["response_envelope_sha256"] = runner.file_sha256(envelope_path)
    runner.atomic_json(canonical_path, canonical)


def test_strict_json_parser_accepts_exact_fence_but_rejects_extra_text():
    assert runner.strict_json_object('{"x":1}') == {"x": 1}
    assert runner.strict_json_object('```json\n{"x":1}\n```') == {"x": 1}
    assert runner.strict_json_object('```\n{"x":1}\n```') == {"x": 1}
    for text in ['prefix {"x":1}', '{"x":1} suffix', '```json\n{"x":1}\n``` trailing', '```python\n{"x":1}\n```']:
        with pytest.raises(ValueError):
            runner.strict_json_object(text)


def test_gateway_observed_provider_alias_is_explicitly_allowed():
    assert runner.response_model_allowed("GPT-5.5", "gpt-5.5-2026-04-24")
    assert not runner.response_model_allowed("GPT-5.5", "gpt-5.5-unknown")


def test_manifest_and_existing_canonical_require_complete_raw_lineage(tmp_path: Path):
    pkt, row, manifest_path = write_packet_and_manifest(tmp_path)
    rows = runner.load_manifest(manifest_path, tmp_path)
    assert len(rows) == 1

    raw = result_for(4)
    no_lineage = protocol.canonical_record(raw, pkt) | {
        "judge_model": "judge-X",
        "packet_id": "packet_1",
    }
    no_lineage_path = tmp_path / "no_lineage.json"
    runner.atomic_json(no_lineage_path, no_lineage)
    assert not runner.canonical_is_valid(no_lineage_path, pkt, "judge-X", row)

    canonical_path = make_direct_canonical(tmp_path, pkt, row)
    assert runner.canonical_is_valid(canonical_path, pkt, "judge-X", row)


def test_canonical_tampering_fails_closed_even_when_local_hash_is_rewritten(tmp_path: Path):
    for name, tamper in [
        ("response", "response"),
        ("envelope", "envelope"),
        ("request", "request"),
        ("finding", "finding"),
        ("extra", "extra"),
    ]:
        root = tmp_path / name
        root.mkdir()
        pkt, row, _ = write_packet_and_manifest(root)
        canonical_path = make_direct_canonical(root, pkt, row)
        if tamper == "response":
            response_path = root / "direct" / "attempts" / "attempt_0001" / "response_content.txt"
            response_path.write_text("{}", encoding="utf-8")
            canonical = json.loads(canonical_path.read_text(encoding="utf-8"))
            canonical["final_attempt"]["artifacts"]["response_content_sha256"] = runner.file_sha256(response_path)
            runner.atomic_json(canonical_path, canonical)
        elif tamper == "envelope":
            rewrite_envelope_and_lineage(canonical_path, lambda value: value.__setitem__("model", "alias-X"))
        elif tamper == "request":
            rewrite_request_and_lineage(canonical_path, lambda value: value.__setitem__("message_sha256", "0" * 64))
        elif tamper == "finding":
            canonical = json.loads(canonical_path.read_text(encoding="utf-8"))
            canonical["responsibility_chain_assessment"][0].update(
                status="serious_error", issue_kind="omission"
            )
            raw = {key: canonical[key] for key in protocol.FINAL_SCHEMA}
            canonical["raw_result_sha256"] = protocol.digest(raw)
            runner.atomic_json(canonical_path, canonical)
        elif tamper == "extra":
            canonical = json.loads(canonical_path.read_text(encoding="utf-8"))
            canonical["unexpected"] = True
            runner.atomic_json(canonical_path, canonical)
        assert not runner.canonical_is_valid(canonical_path, pkt, "judge-X", row)


def test_attempt_directories_are_append_only(tmp_path: Path):
    meta = {
        "protocol_version": protocol.VERSION,
        "protocol_sha256": runner.protocol_sha256(),
    }
    runner.save_attempt(tmp_path, 1, meta, None, None, ["bad"], None)
    assert runner.next_attempt_index(tmp_path) == 2
    with pytest.raises(FileExistsError):
        runner.save_attempt(tmp_path, 1, meta, None, None, ["bad"], None)


def test_manifest_hash_mismatch_fails_closed(tmp_path: Path):
    _, _, manifest_path = write_packet_and_manifest(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["packets"][0]["packet_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="manifest_packet_hash_mismatch"):
        runner.load_manifest(manifest_path, tmp_path)


def test_manifest_rejects_protocol_hash_path_escape_and_source_mismatch(tmp_path: Path):
    _, _, manifest_path = write_packet_and_manifest(tmp_path)
    baseline = json.loads(manifest_path.read_text(encoding="utf-8"))

    bad = copy.deepcopy(baseline)
    bad["protocol_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError, match="manifest_protocol_hash_mismatch"):
        runner.load_manifest(manifest_path, tmp_path)

    bad = copy.deepcopy(baseline)
    bad["packets"][0]["packet_file"] = "../packet.judge_packet.json"
    manifest_path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError, match="manifest_path_escapes_root"):
        runner.load_manifest(manifest_path, tmp_path)

    bad = copy.deepcopy(baseline)
    bad["packets"][0]["source_trajectory_sha256"] = "c" * 64
    manifest_path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError, match="manifest_source_trajectory_hash_mismatch"):
        runner.load_manifest(manifest_path, tmp_path)


@pytest.mark.parametrize(
    "entrypoint",
    ["run_final_trajectory_judge.py", "run_crossjudge_completion.py"],
)
def test_cli_entrypoints_run_without_pythonpath_from_external_cwd(
    tmp_path: Path, entrypoint: str
):
    """The shipped CLI must work exactly as documented in a clean shell.

    Importing the module under pytest is insufficient evidence because pytest
    places the repository root on sys.path.  Run both public entry points in a
    subprocess from outside the repository with PYTHONPATH explicitly removed.
    """
    _, _, manifest_path = write_packet_and_manifest(tmp_path)
    out_root = tmp_path / f"out_{Path(entrypoint).stem}"
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT.with_name(entrypoint)),
            "--root",
            str(tmp_path),
            "--packet-manifest",
            str(manifest_path),
            "--out-root",
            str(out_root),
            "--judge-models",
            "judge-A",
            "judge-B",
        ],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr
    assert "DRY_RUN_ONLY: no API calls made" in completed.stdout
    plan = json.loads((out_root / "run_plan.json").read_text(encoding="utf-8"))
    assert plan["totals"]["judge_trajectory_jobs"] == 2
    assert plan["totals"]["unscorable_jobs"] == 0
    assert len(plan["packets"]) == 1


def test_manifest_binds_source_case_contract_counts_and_population(tmp_path: Path):
    _, _, manifest_path = write_packet_and_manifest(tmp_path)
    baseline = json.loads(manifest_path.read_text(encoding="utf-8"))

    source = tmp_path / baseline["packets"][0]["source_trajectory_file"]
    source.write_text('{"case_id":"case_X","source":"tampered"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="manifest_source_trajectory_file_hash_mismatch"):
        runner.load_manifest(manifest_path, tmp_path)

    # Restore a fresh complete fixture for each independent lineage mutation.
    _, _, manifest_path = write_packet_and_manifest(tmp_path / "case_tamper")
    baseline = json.loads(manifest_path.read_text(encoding="utf-8"))
    case = manifest_path.parent / baseline["packets"][0]["case_file"]
    case.write_text(case.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(ValueError, match="manifest_case_file_hash_mismatch"):
        runner.load_manifest(manifest_path, manifest_path.parent)

    _, _, manifest_path = write_packet_and_manifest(tmp_path / "contract_tamper")
    bad = json.loads(manifest_path.read_text(encoding="utf-8"))
    bad["packets"][0]["contract_payload_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError, match="manifest_contract_payload_hash_mismatch"):
        runner.load_manifest(manifest_path, manifest_path.parent)

    _, _, manifest_path = write_packet_and_manifest(tmp_path / "count_tamper")
    bad = json.loads(manifest_path.read_text(encoding="utf-8"))
    bad["packets"][0]["source_event_count"] = 999
    manifest_path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError, match="manifest_source_event_count_mismatch"):
        runner.load_manifest(manifest_path, manifest_path.parent)

    _, _, manifest_path = write_packet_and_manifest(tmp_path / "population_tamper")
    bad = json.loads(manifest_path.read_text(encoding="utf-8"))
    bad["population"]["trajectory_count"] = 2
    manifest_path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError, match="manifest_population_summary_mismatch"):
        runner.load_manifest(manifest_path, manifest_path.parent)


def test_retry_after_parsing_and_rate_limit_jitter_are_bounded_and_stable():
    assert runner.parse_retry_after("45") == 45.0
    assert runner.parse_retry_after("9999") == 600.0
    assert runner.parse_retry_after("not-a-date") is None
    exc = runner.ProviderHTTPError(429, "limited", retry_after_seconds=75.0)
    first = runner.retry_delay_seconds(20, exc, model="judge-X", packet_id="p1", part="direct", attempt=1)
    second = runner.retry_delay_seconds(20, exc, model="judge-X", packet_id="p1", part="direct", attempt=1)
    assert first == second
    assert 75.0 <= first < 80.0
    ordinary = runner.retry_delay_seconds(40, RuntimeError("x"), model="judge-X", packet_id="p1", part="direct", attempt=2)
    assert ordinary == 40.0


def test_api_request_omits_temperature_and_preserves_provider_error_body(monkeypatch):
    import io
    import urllib.error

    seen = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"model":"judge-X","choices":[{"message":{"content":"{}"}}]}'

    def ok(request, **kwargs):
        seen.update(json.loads(request.data.decode("utf-8")))
        return Response()

    monkeypatch.setattr(runner.urllib.request, "urlopen", ok)
    envelope, content, _ = runner.api_call(
        "https://example.invalid/v1", "secret", "judge-X",
        [{"role": "user", "content": "x"}], 100, 2, True,
    )
    assert envelope["model"] == "judge-X" and content == "{}"
    assert "temperature" not in seen

    body = b'{"error":{"message":"temperature only supports the default value"}}'
    http_error = urllib.error.HTTPError("https://example.invalid", 400, "Bad Request", {}, io.BytesIO(body))
    monkeypatch.setattr(runner.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(http_error))
    with pytest.raises(runner.ProviderHTTPError) as caught:
        runner.api_call(
            "https://example.invalid/v1", "secret", "judge-X",
            [{"role": "user", "content": "x"}], 100, 2, True,
        )
    assert caught.value.code == 400
    assert "temperature only supports" in caught.value.detail


def test_explicit_provider_snapshot_model_id_is_accepted(tmp_path: Path):
    pkt, row, manifest_path = write_packet_and_manifest(tmp_path)
    row = runner.load_manifest(manifest_path, tmp_path)[0]
    canonical_path = make_direct_canonical(tmp_path, pkt, row, model="gpt-5.6-sol")
    canonical = json.loads(canonical_path.read_text(encoding="utf-8"))
    request_path = tmp_path / "direct" / "attempts" / "attempt_0001" / "request_meta.json"
    request = json.loads(request_path.read_text(encoding="utf-8"))
    envelope_path = tmp_path / "direct" / "attempts" / "attempt_0001" / "response_envelope.json"
    envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
    returned = "gpt-5.6-sol-2026-07-09"
    request["response_model_returned"] = returned
    envelope["model"] = returned
    runner.atomic_json(request_path, request)
    runner.atomic_json(envelope_path, envelope)
    canonical["judge_model_returned"] = returned
    canonical["final_attempt"]["response_model_returned"] = returned
    canonical["final_attempt"]["request_meta"] = request
    canonical["final_attempt"]["artifacts"]["request_meta_sha256"] = runner.file_sha256(request_path)
    canonical["final_attempt"]["artifacts"]["response_envelope_sha256"] = runner.file_sha256(envelope_path)
    runner.atomic_json(canonical_path, canonical)
    assert runner.canonical_is_valid(canonical_path, pkt, "gpt-5.6-sol", row)

def test_default_operational_profile_preserves_legacy_formal_runner_settings():
    args = runner.parser().parse_args([])
    assert args.timeout == 180
    assert args.attempts == 6
    assert args.concurrency == 5
    assert args.json_mode_models == ["DeepSeek-V4-Pro", "GPT-5.5", "gpt-5.6-sol"]
    assert runner.LEGACY_RETRY_BACKOFF_SECONDS == (20, 40, 60, 80, 100)


def test_run_plan_records_complete_operational_profile(tmp_path: Path):
    _, _, manifest_path = write_packet_and_manifest(tmp_path)
    args = runner.parser().parse_args([
        "--root", str(tmp_path),
        "--packet-manifest", str(manifest_path),
        "--out-root", str(tmp_path / "out"),
        "--judge-models", "judge-X",
    ])
    args.root = args.root.resolve()
    args.packet_manifest = runner.resolve_under_root(args.root, str(args.packet_manifest))
    args.out_root = runner.resolve_under_root(args.root, str(args.out_root))
    args.json_mode_models = set(args.json_mode_models)
    rows = runner.load_manifest(args.packet_manifest, args.root)
    plan = runner.build_run_plan(args, rows)
    params = plan["parameters"]
    assert params["timeout_seconds"] == 180
    assert params["attempts"] == 6
    assert params["concurrency"] == 5
    assert params["temperature_field_omitted"] is True
    assert params["retry_backoff_seconds"] == [20, 40, 60, 80, 100]
    assert params["operational_profile"] == "legacy_formal_20260907_minimal_correctness_patch"



def test_user_authorized_per_judge_concurrency_is_explicit_and_auditable(tmp_path: Path):
    _, _, manifest_path = write_packet_and_manifest(tmp_path)
    args = runner.parser().parse_args([
        "--root", str(tmp_path),
        "--packet-manifest", str(manifest_path),
        "--out-root", str(tmp_path / "out-20"),
        "--judge-models", "judge-A", "judge-B", "judge-C", "judge-D",
        "--concurrency-per-judge", "20",
    ])
    args.root = args.root.resolve()
    args.packet_manifest = runner.resolve_under_root(args.root, str(args.packet_manifest))
    args.out_root = runner.resolve_under_root(args.root, str(args.out_root))
    args.json_mode_models = set(args.json_mode_models)
    rows = runner.load_manifest(args.packet_manifest, args.root)
    plan = runner.build_run_plan(args, rows)
    assert runner.effective_max_workers(args) == 80
    assert plan["parameters"]["concurrency_per_judge"] == 20
    assert plan["parameters"]["effective_max_workers"] == 80
    assert plan["parameters"]["operational_profile"].endswith("user_authorized_20_per_judge")


def test_full_context_only_disables_chunking_and_records_per_judge_token_budget(tmp_path: Path):
    _, _, manifest_path = write_packet_and_manifest(tmp_path)
    args = runner.parser().parse_args([
        "--root", str(tmp_path),
        "--packet-manifest", str(manifest_path),
        "--out-root", str(tmp_path / "out-full"),
        "--judge-models", "judge-X",
        "--max-direct-message-chars", "1000",
        "--full-context-only",
        "--final-max-tokens-by-judge", "judge-X=6000",
    ])
    args.root = args.root.resolve()
    args.packet_manifest = runner.resolve_under_root(args.root, str(args.packet_manifest))
    args.out_root = runner.resolve_under_root(args.root, str(args.out_root))
    args.json_mode_models = set(args.json_mode_models)
    args.final_max_tokens_by_judge = runner.parse_model_int_overrides(args.final_max_tokens_by_judge)
    rows = runner.load_manifest(args.packet_manifest, args.root)
    plan = runner.build_run_plan(args, rows)
    assert plan["totals"]["direct_jobs"] == 1
    assert plan["totals"]["chunked_jobs"] == 0
    assert plan["parameters"]["full_context_only"] is True
    assert plan["parameters"]["resolved_final_max_tokens_by_judge"] == {"judge-X": 6000}
    assert plan["parameters"]["operational_profile"].endswith("_full_context_only")


def test_model_token_override_parser_rejects_bad_or_duplicate_values():
    assert runner.parse_model_int_overrides(["GLM-5=6000"]) == {"GLM-5": 6000}
    for values in (["GLM-5"], ["GLM-5=0"], ["GLM-5=x"], ["GLM-5=6000", "GLM-5=7000"]):
        try:
            runner.parse_model_int_overrides(list(values))
        except ValueError:
            pass
        else:
            raise AssertionError(f"override should fail: {values}")


def test_runner_prompt_guidance_pins_exact_targets_and_citation_transport():
    pkt = packet()
    text = runner.direct_final_messages(pkt)[-1]["content"]
    assert "MACHINE_VALIDATION_GUIDANCE=" in text
    assert "valid_category_target_ids" in text
    assert "RC1" in text and "HO1" in text
    assert "medical_safety_risk_recognition" in text
    assert "character-for-character" in text
    assert "Never paraphrase" in text


def test_retry_prompt_explains_machine_errors_without_changing_findings():
    pkt = packet()
    text = runner.direct_final_messages(
        pkt, ["rc[0]:citation[0]:nonverbatim_quote", "rc[0]:citation:requires_doctor_action_citation"]
    )[-1]["content"]
    assert "REJECTION_REPAIR_GUIDANCE" in text
    assert "recopy a shorter exact substring" in text
    assert "source-verbatim doctor-event citation" in text
    assert "Never change a clinical finding merely to pass validation" in text


def test_no_doctor_runtime_packet_can_be_scored_from_observable_omissions():
    pkt = packet()
    raw = result_for(2)
    for event in pkt["events"]:
        event["actor"] = "family"
        event["event_type"] = "family_message"
    citation = {
        "event_id": "e1",
        "quote": "verifies the medication list",
        "explanation": "observable presenting evidence",
    }
    for item in raw["responsibility_chain_assessment"]:
        item.update(
            status="minor_or_moderate_error",
            issue_kind="omission",
            citations=[copy.deepcopy(citation)],
        )
    for dimension in protocol.DIMENSIONS:
        raw["dimension_ratings"][dimension] = "weak"
        raw["dimension_citations"][dimension] = [copy.deepcopy(citation)]
    raw["closure_assessment"]["citations"] = [copy.deepcopy(citation)]

    protocol_errors = protocol.validate_final_result(raw, pkt)
    assert set(protocol_errors) == {
        f"dimension:{dimension}:requires_doctor_action_citation"
        for dimension in protocol.DIMENSIONS
    }
    assert runner.validate_final_result(raw, pkt) == []
    assert protocol.derive_grade(raw)[0] == 2


def test_no_doctor_prompt_forbids_positive_completion_claims():
    pkt = packet()
    for event in pkt["events"]:
        event["actor"] = "family"
        event["event_type"] = "family_message"
    text = runner.direct_final_messages(pkt)[-1]["content"]
    assert '"doctor_event_ids":[]' in text
    assert "Do not mark an RC met" in text


def test_chunk_transport_map_preserves_exact_quotes_but_not_provisional_labels():
    pkt = packet()
    pair = ("e1", "verifies the medication list")
    reviews = [{
        "observations": [{
            "category": "responsibility_chain",
            "target_id": "RC1",
            "valence": "neutral",
            "provisional_severity": "none",
            "issue_kind": "none",
            "citations": [{
                "event_id": pair[0],
                "quote": pair[1],
                "explanation": "retrieved source evidence",
            }],
            "explanation": "retrieval-stage annotation is provisional",
        }],
        "review_summary": "Complete retrieval-stage review for the fixture chunk.",
    }]
    mapping = runner.reviewed_citation_transport_map(reviews, pkt)
    rc_ids, ho_ids = protocol.packet_contract_ids(pkt)
    expected_targets = (
        [("responsibility_chain", value) for value in rc_ids]
        + [("high_order", value) for value in ho_ids]
        + [("dimension", value) for value in protocol.DIMENSIONS]
        + [("closure", "closure"), ("other", "other")]
    )
    for target in expected_targets:
        assert pair in mapping[target]
        assert ("positive", "none", "none") in mapping[target][pair]
        assert ("negative", "serious", "omission") in mapping[target][pair]
        assert ("e1", "altered quote") not in mapping[target]


def test_previous_rejected_json_is_supplied_for_same_judge_targeted_repair():
    pkt = packet()
    previous = result_for(4)
    errors = ["rc[0]:citation[0]:nonverbatim_quote"]
    messages = runner._append_previous_rejected_output(
        runner.direct_final_messages(pkt, errors), previous, errors
    )
    text = messages[-1]["content"]
    assert "PREVIOUS_REJECTED_JSON=" in text
    assert protocol.canonical(previous) in text
    assert "Preserve its clinical findings" in text
    assert "repair only event_id/quote/explanation" in text


def test_same_judge_repair_attempt_lineage_is_reconstructable(tmp_path: Path, monkeypatch):
    pkt = packet()
    invalid = result_for(4)
    invalid["responsibility_chain_assessment"][0]["citations"][0]["quote"] = "not source text"
    valid = result_for(4)
    responses = [invalid, valid]

    def fake_api_call(base_url, api_key, model, messages, max_tokens, timeout, response_format):
        value = responses.pop(0)
        content = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        return {"model": model, "choices": [{"message": {"content": content}}]}, content, 0.01

    monkeypatch.setattr(runner, "api_call", fake_api_call)
    monkeypatch.setattr(runner.time, "sleep", lambda _: None)
    result, run_meta = runner.invoke_validated(
        base_url="https://example.invalid/v1/chat/completions",
        api_key="secret-not-logged",
        model="judge-X",
        messages_builder=lambda errors: runner.direct_final_messages(pkt, errors),
        validator=lambda value: runner.validate_final_result(value, pkt),
        folder=tmp_path / "direct",
        kind="direct_final",
        packet_id="packet_1",
        part="direct",
        attempts=2,
        max_tokens=4096,
        timeout=30,
        response_format=True,
    )
    assert result == valid
    assert run_meta["attempt"] == 2
    request = run_meta["request_meta"]
    assert request["repair_from_previous_structured_output"] is True
    assert request["previous_rejected_attempt"] == 1
    assert request["previous_rejected_result_sha256"] == protocol.digest(invalid)
    assert runner._validated_attempt_artifacts(
        tmp_path / "direct",
        run_meta,
        "judge-X",
        valid,
        expected_kind="direct_final",
        expected_packet_id="packet_1",
        expected_part="direct",
        messages_builder=lambda errors: runner.direct_final_messages(pkt, errors),
    )


def test_final_quality_disagreements_are_preserved_not_retried():
    pkt = packet()
    raw = result_for(4)
    raw["responsibility_chain_assessment"][0]["citations"][0]["quote"] = "not source text"
    raw["proposed_trajectory_grade"] = 5
    raw["proposed_trajectory_grade_label"] = "perfect"
    quality = runner.final_result_quality_errors(raw, pkt)
    assert "rc[0]:citation[0]:nonverbatim_quote" in quality
    assert "proposed_grade_inconsistent:expected_4" in quality
    assert runner.final_result_acceptance_errors(raw, pkt) == []


def test_final_structural_failures_still_block_acceptance():
    pkt = packet()
    raw = result_for(4)
    del raw["dimension_ratings"]
    errors = runner.final_result_acceptance_errors(raw, pkt)
    assert "final_wrong_top_level_fields" in errors
    assert "invalid_dimension_ratings" in errors


def test_chunk_judge_quality_errors_are_preserved_not_retried():
    pkt = packet()
    chunk = protocol.make_event_chunks(pkt)[0]
    review = {
        "observations": [{
            "category": "responsibility_chain",
            "target_id": "RC1",
            "valence": "positive",
            "provisional_severity": "none",
            "issue_kind": "none",
            "citations": [{
                "event_id": "e1",
                "quote": "not source text",
                "explanation": "The Judge supplied a parseable but non-verbatim citation.",
            }],
            "explanation": "The clinical observation is retained exactly as the Judge returned it.",
        }],
        "review_summary": "A complete typed chunk review whose citation quality is an observed Judge outcome.",
    }
    quality = runner.chunk_review_quality_errors(review, chunk, pkt)
    assert any(error.endswith(":nonverbatim_quote") for error in quality)
    assert runner.chunk_review_acceptance_errors(review, chunk, pkt) == []


def test_chunk_structural_errors_still_block_acceptance():
    pkt = packet()
    chunk = protocol.make_event_chunks(pkt)[0]
    review = {"observations": "not-a-list", "review_summary": "Invalid typed structure."}
    errors = runner.chunk_review_acceptance_errors(review, chunk, pkt)
    assert "observations_not_list" in errors


def test_canonical_uses_frozen_derived_grade_but_retains_judge_reported_grade(tmp_path: Path, monkeypatch):
    pkt, row, manifest_path = write_packet_and_manifest(tmp_path)
    row = runner.load_manifest(manifest_path, tmp_path)[0]
    raw = result_for(4)
    raw["proposed_trajectory_grade"] = 5
    raw["proposed_trajectory_grade_label"] = "perfect"

    def fake_api_call(base_url, api_key, model, messages, max_tokens, timeout, response_format):
        content = json.dumps(raw, ensure_ascii=False, separators=(",", ":"))
        return {"model": model, "choices": [{"message": {"content": content}}]}, content, 0.01

    monkeypatch.setattr(runner, "api_call", fake_api_call)
    args = type("Args", (), {
        "out_root": tmp_path / "out",
        "json_mode_models": {"judge-X"},
        "max_direct_message_chars": 90000,
        "chunk_body_chars": 45000,
        "max_final_synthesis_chars": 180000,
        "attempts": 2,
        "final_max_tokens": 4096,
        "chunk_max_tokens": 4096,
        "timeout": 30,
    })()
    result = runner.process_one(args, "https://example.invalid/v1", "secret", row, "judge-X")
    assert result["state"] == "accepted"
    canonical_path = args.out_root / "results" / "judge-X" / "packet_1" / "canonical.json"
    canonical = json.loads(canonical_path.read_text())
    assert canonical["judge_reported_grade"] == 5
    assert canonical["judge_reported_grade_matches_derived"] is False
    assert canonical["proposed_trajectory_grade"] == 5
    assert canonical["trajectory_grade"] == 4
    assert "proposed_grade_inconsistent:expected_4" in canonical["judge_output_quality_errors"]
    assert runner.canonical_is_valid(canonical_path, pkt, "judge-X", row)
