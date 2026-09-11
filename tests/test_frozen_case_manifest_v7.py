from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "build_frozen_case_manifest_v7.py"
SPEC = importlib.util.spec_from_file_location("frozen_manifest_v7", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


def write_inputs(root: Path):
    cases = root / "cases"
    cases.mkdir()
    case_path = cases / "case_X.json"
    case_path.write_text(json.dumps({"case_id": "case_X", "value": 1}) + "\n", encoding="utf-8")
    old_hash = "0" * 64
    legacy = root / "legacy.csv"
    legacy.write_text(f"case_id,file,sha256\ncase_X,case_X.json,{old_hash}\n", encoding="utf-8")
    prefix = "bundle/cases"
    archive = root / "archive.manifest.json"
    archive.write_text(json.dumps({
        "archive_sha256": "a" * 64,
        "members": [{"path": f"{prefix}/case_X.json", "sha256": m.sha256(case_path)}],
    }), encoding="utf-8")
    return cases, legacy, archive, prefix, case_path


def test_build_manifest_binds_actual_case_bytes_to_outer_archive(tmp_path: Path):
    cases, legacy, archive, prefix, case_path = write_inputs(tmp_path)
    output = tmp_path / "out.csv"
    audit_path = tmp_path / "audit.json"
    audit = m.build_manifest(
        case_dir=cases,
        legacy_manifest=legacy,
        archive_manifest=archive,
        archive_member_prefix=prefix,
        output=output,
        audit_json=audit_path,
        expected_count=1,
    )
    rows = list(csv.DictReader(output.open(encoding="utf-8")))
    assert rows[0]["sha256"] == m.sha256(case_path)
    assert rows[0]["archive_member_sha256"] == m.sha256(case_path)
    assert rows[0]["legacy_declared_sha256"] == "0" * 64
    assert audit["legacy_hash_mismatch_count"] == 1
    assert audit["archive_member_verification"] == "passed"
    assert audit["generated_manifest_sha256"] == m.sha256(output)


def test_build_manifest_rejects_archive_mismatch_and_path_escape(tmp_path: Path):
    cases, legacy, archive, prefix, _ = write_inputs(tmp_path)
    bad = json.loads(archive.read_text())
    bad["members"][0]["sha256"] = "f" * 64
    archive.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError, match="archive_member_hash_mismatch"):
        m.build_manifest(
            case_dir=cases, legacy_manifest=legacy, archive_manifest=archive,
            archive_member_prefix=prefix, output=tmp_path / "o.csv",
            audit_json=tmp_path / "a.json", expected_count=1,
        )

    legacy.write_text(f"case_id,file,sha256\ncase_X,../outside.json,{'0' * 64}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="escapes_case_dir"):
        m.build_manifest(
            case_dir=cases, legacy_manifest=legacy, output=tmp_path / "o2.csv",
            audit_json=tmp_path / "a2.json", expected_count=1,
        )


def test_build_manifest_rejects_embedded_case_id_disagreement(tmp_path: Path):
    cases, legacy, _, _, case_path = write_inputs(tmp_path)
    case_path.write_text(json.dumps({"case_id": "case_Y"}), encoding="utf-8")
    actual = m.sha256(case_path)
    legacy.write_text(f"case_id,file,sha256\ncase_X,case_X.json,{actual}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="embedded_case_id_mismatch"):
        m.build_manifest(
            case_dir=cases, legacy_manifest=legacy, output=tmp_path / "o.csv",
            audit_json=tmp_path / "a.json", expected_count=1,
        )
