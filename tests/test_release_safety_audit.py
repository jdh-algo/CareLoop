from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "audit_release_safety.py"
SPEC = importlib.util.spec_from_file_location("release_safety_audit", SCRIPT)
assert SPEC and SPEC.loader
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


def test_detects_secrets_and_private_paths_without_echoing_value(tmp_path: Path):
    secret = "sk-" + "A" * 32
    provider_secret = "pk-" + "B" * 36
    path = tmp_path / "bad.txt"
    private_remote = "/mnt/" + "workspace/private/run"
    private_local = "/" + "Users/example/private"
    path.write_text(f"{secret}\n{provider_secret}\n{private_remote}\n{private_local}\n", encoding="utf-8")
    findings = m.scan([path], tmp_path)
    assert {row["pattern"] for row in findings} == {
        "openai_style_secret", "provider_style_pk_secret", "private_remote_path", "private_local_path"
    }
    assert all(secret not in str(row) and provider_secret not in str(row) for row in findings)
    assert all("match_sha256_prefix" in row for row in findings)


def test_allows_documented_placeholder(tmp_path: Path):
    path = tmp_path / "example.env"
    path.write_text('CARELOOP_API_KEY="replace-with-your-private-key"\n', encoding="utf-8")
    assert m.scan([path], tmp_path) == []

def test_repository_audit_does_not_flag_its_own_pattern_fixtures():
    paths = m.manifest_builder.repository_paths(Path(__file__).parents[1])
    assert m.scan(paths, Path(__file__).parents[1]) == []

