#!/usr/bin/env python3
"""Fail closed on credentials and private workstation paths in release files."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import re
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_SCRIPT = ROOT / "scripts" / "build_release_manifests.py"
SPEC = importlib.util.spec_from_file_location("release_manifest_builder", MANIFEST_SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot_load_release_manifest_builder")
manifest_builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(manifest_builder)

PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private_local_path", re.compile(r"/Users/[A-Za-z0-9._-]+(?:/[^\s\"'<>]*)?")),
    ("private_remote_path", re.compile(r"/mnt/workspace(?:/[^\s\"'<>]*)?")),
    ("private_jupyter_identifier", re.compile(r"(?:queue|nb)-[a-z0-9]{12,}", re.I)),
    ("private_gateway_host", re.compile(r"maas-batch(?:-inner)?\.jdcloud\.com", re.I)),
    ("openai_style_secret", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("provider_style_pk_secret", re.compile(r"\bpk-[A-Za-z0-9_-]{20,}\b")),
    ("bearer_secret", re.compile(r"\bBearer\s+[A-Za-z0-9._~-]{20,}")),
    ("private_key_block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("assigned_secret", re.compile(r"(?i)(?:api[_-]?key|secret|token)\s*[:=]\s*[\"']([A-Za-z0-9._~-]{20,})[\"']")),
)
SAFE_ASSIGNED_VALUES = {"replace-with-your-private-key"}
# This source line is a redaction regular expression, not a literal local path.
SAFE_LITERAL_OCCURRENCES = {
    ("scripts/build_minimal_judge_packets.py", "private_local_path", "/Users/"),
    # Pattern-definition and unit-test fixtures below are deliberate literals,
    # not workstation paths embedded in released data or documentation.
    ("scripts/audit_release_safety.py", "private_remote_path", "/mnt/workspace"),
    ("scripts/audit_release_safety.py", "private_gateway_host", "maas-batch"),
    ("tests/test_release_safety_audit.py", "private_local_path", "/" + "Users/example/private"),
    ("tests/test_release_safety_audit.py", "private_remote_path", "/mnt/" + "workspace/private/run"),
}


def scan(paths: Iterable[Path], root: Path) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    for path in paths:
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        relative = path.relative_to(root).as_posix()
        for pattern_name, pattern in PATTERNS:
            for match in pattern.finditer(text):
                matched = match.group(0)
                if pattern_name == "assigned_secret" and match.group(1) in SAFE_ASSIGNED_VALUES:
                    continue
                if any(
                    relative == safe_path and pattern_name == safe_pattern and matched.startswith(safe_prefix)
                    for safe_path, safe_pattern, safe_prefix in SAFE_LITERAL_OCCURRENCES
                ):
                    continue
                findings.append({
                    "path": relative,
                    "line": text.count("\n", 0, match.start()) + 1,
                    "pattern": pattern_name,
                    # Never echo a candidate secret into logs.
                    "match_sha256_prefix": __import__("hashlib").sha256(matched.encode("utf-8")).hexdigest()[:16],
                    "match_length": len(matched),
                })
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit CareLoop release candidates for credentials and private paths.")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    paths = manifest_builder.repository_paths(root)
    findings = scan(paths, root)
    report = {
        "schema_version": "careloop.release_safety_audit.v1",
        "scanned_release_files": len(paths),
        "finding_count": len(findings),
        "findings": findings,
        "result": "PASS" if not findings else "FAIL",
    }
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
