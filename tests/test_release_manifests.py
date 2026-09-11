from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "build_release_manifests.py"
SPEC = importlib.util.spec_from_file_location("release_manifest_builder", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


def test_manifest_roundtrip_and_tamper_detection(tmp_path: Path):
    (tmp_path / "a.txt").write_text("alpha", encoding="utf-8")
    (tmp_path / "b.txt").write_text("beta", encoding="utf-8")
    expected = m.rows([tmp_path / "a.txt", tmp_path / "b.txt"], tmp_path)
    manifest = tmp_path / "manifest.csv"
    m.write_manifest(manifest, expected)
    assert m.check_manifest(manifest, expected) == []
    (tmp_path / "a.txt").write_text("changed", encoding="utf-8")
    changed = m.rows([tmp_path / "a.txt", tmp_path / "b.txt"], tmp_path)
    assert "mismatch:a.txt" in m.check_manifest(manifest, changed)


def test_excluded_local_artifacts():
    assert m.excluded(Path(".venv/lib/file.py"))
    assert m.excluded(Path("careloop/__pycache__/x.pyc"))
    assert m.excluded(Path("outputs/result.json"))
    assert m.excluded(Path(".DS_Store"))
    assert m.excluded(Path("careloop/__pycache__/x.pyc"))
    assert not m.excluded(Path("careloop/evaluation/protocol_v7.py"))


def test_supplement_paths_accepts_configurable_release_root(tmp_path: Path):
    supplement = tmp_path / "public_supplement" / "cl120_test"
    supplement.mkdir(parents=True)
    (supplement / "a.json").write_text("{}", encoding="utf-8")
    (supplement / "PACKAGE_MANIFEST.csv").write_text("self", encoding="utf-8")
    paths = m.supplement_paths(tmp_path, Path("public_supplement/cl120_test"))
    assert [path.relative_to(supplement).as_posix() for path in paths] == ["a.json"]


def test_repository_paths_are_independent_of_git_metadata(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    released = tmp_path / "provenance" / "stdout.log"
    released.parent.mkdir()
    released.write_text("audit log", encoding="utf-8")
    ignored_cache = tmp_path / "careloop" / "__pycache__" / "x.pyc"
    ignored_cache.parent.mkdir(parents=True)
    ignored_cache.write_bytes(b"cache")
    paths = [path.relative_to(tmp_path).as_posix() for path in m.repository_paths(tmp_path)]
    assert "provenance/stdout.log" in paths
    assert "careloop/__pycache__/x.pyc" not in paths
