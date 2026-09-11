#!/usr/bin/env python3
"""Create a byte-accurate manifest for the actual frozen CL120 case files.

The historical in-folder CL120 CSV contains hashes from an earlier authoring
stage and is provenance only.  When supplied, the outer forensic archive
manifest is the authority for the extracted bytes.  This tool fails closed on
ambiguous rows, path traversal, malformed case JSON, case-ID disagreement,
duplicate archive members, invalid hashes, or any archive/member mismatch.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
from typing import Any

HEX = set("0123456789abcdef")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= HEX


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def safe_case_path(case_dir: Path, filename: str) -> Path:
    if not filename or Path(filename).is_absolute():
        raise ValueError("case_filename_missing_or_absolute")
    resolved_root = case_dir.resolve()
    resolved = (resolved_root / filename).resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(f"case_filename_escapes_case_dir:{filename}") from exc
    return resolved


def load_archive_hashes(archive_manifest: Path | None) -> tuple[dict[str, str], str | None, str | None]:
    if archive_manifest is None:
        return {}, None, None
    archive = json.loads(archive_manifest.read_text(encoding="utf-8"))
    if not isinstance(archive, dict):
        raise ValueError("archive_manifest_not_object")
    members = archive.get("members")
    if not isinstance(members, list):
        raise ValueError("archive_manifest_members_missing")
    hashes: dict[str, str] = {}
    for index, item in enumerate(members):
        if not isinstance(item, dict):
            raise ValueError(f"archive_member_not_object:{index}")
        path = str(item.get("path") or "")
        member_hash = str(item.get("sha256") or "")
        if not path or path in hashes:
            raise ValueError(f"archive_member_path_missing_or_duplicate:{index}:{path}")
        if not is_sha256(member_hash):
            raise ValueError(f"archive_member_hash_invalid:{path}")
        hashes[path] = member_hash
    archive_sha = archive.get("archive_sha256")
    if archive_sha is not None and not is_sha256(archive_sha):
        raise ValueError("source_archive_sha256_invalid")
    return hashes, sha256(archive_manifest), archive_sha


def write_csv_atomic(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)


def build_manifest(
    *,
    case_dir: Path,
    legacy_manifest: Path,
    output: Path,
    audit_json: Path,
    archive_manifest: Path | None = None,
    archive_member_prefix: str = "",
    expected_count: int = 120,
) -> dict[str, Any]:
    case_dir = case_dir.resolve()
    if not case_dir.is_dir():
        raise ValueError(f"case_dir_missing:{case_dir}")
    rows = list(csv.DictReader(legacy_manifest.open(encoding="utf-8-sig", newline="")))
    if len(rows) != expected_count:
        raise ValueError(f"expected_{expected_count}_manifest_rows_got_{len(rows)}")
    archive_hashes, archive_manifest_sha, archive_sha = load_archive_hashes(archive_manifest)

    out: list[dict[str, Any]] = []
    seen_case_ids: set[str] = set()
    seen_filenames: set[str] = set()
    mismatches: list[str] = []
    for index, row in enumerate(rows, 1):
        case_id = str(row.get("case_id") or "").strip()
        filename = str(row.get("file") or f"{case_id}.json").strip()
        legacy = str(row.get("sha256") or "").strip()
        if not case_id or case_id in seen_case_ids:
            raise ValueError(f"duplicate_or_missing_case_id:{index}:{case_id}")
        if filename in seen_filenames:
            raise ValueError(f"duplicate_case_filename:{filename}")
        if not is_sha256(legacy):
            raise ValueError(f"legacy_declared_hash_invalid:{case_id}")
        seen_case_ids.add(case_id)
        seen_filenames.add(filename)
        path = safe_case_path(case_dir, filename)
        if not path.is_file():
            raise ValueError(f"missing_case_file:{case_id}:{filename}")
        case = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(case, dict):
            raise ValueError(f"case_json_not_object:{case_id}")
        embedded_case_id = str(case.get("case_id") or "").strip()
        if embedded_case_id != case_id:
            raise ValueError(f"embedded_case_id_mismatch:{case_id}:{embedded_case_id}")
        actual = sha256(path)
        if actual != legacy:
            mismatches.append(case_id)
        archive_member = (archive_member_prefix.rstrip("/") + "/" + filename).lstrip("/")
        archive_member_sha = archive_hashes.get(archive_member) if archive_hashes else None
        if archive_hashes and archive_member_sha is None:
            raise ValueError(f"archive_member_missing:{case_id}:{archive_member}")
        if archive_hashes and archive_member_sha != actual:
            raise ValueError(f"archive_member_hash_mismatch:{case_id}:{archive_member}")
        out.append({
            "case_id": case_id,
            "file": filename,
            "sha256": actual,
            "legacy_declared_sha256": legacy,
            "archive_member_sha256": archive_member_sha or "",
        })

    write_csv_atomic(output, out)
    audit = {
        "schema_version": "careloop.cl120.frozen_case_file_manifest_audit.v7",
        "case_count": len(out),
        "legacy_manifest_sha256": sha256(legacy_manifest),
        "archive_manifest_sha256": archive_manifest_sha,
        "source_archive_sha256": archive_sha,
        "archive_member_prefix": archive_member_prefix,
        "archive_member_verification": "passed" if archive_hashes else "not_requested",
        "generated_manifest_sha256": sha256(output),
        "legacy_hash_mismatch_count": len(mismatches),
        "legacy_hash_mismatch_case_ids": mismatches,
        "interpretation": (
            "The generated sha256 column identifies the actual frozen case bytes. "
            "When archive_member_verification is passed, each value is byte-identical to the verified outer archive member; "
            "legacy_declared_sha256 is provenance only."
        ),
    }
    atomic_text(audit_json, json.dumps(audit, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    return audit


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Build the byte-accurate V7 frozen CL120 case manifest.")
    ap.add_argument("--case-dir", required=True, type=Path)
    ap.add_argument("--legacy-manifest", required=True, type=Path)
    ap.add_argument("--archive-manifest", type=Path)
    ap.add_argument("--archive-member-prefix", default="")
    ap.add_argument("--output", required=True, type=Path)
    ap.add_argument("--audit-json", required=True, type=Path)
    ap.add_argument("--expected-count", type=int, default=120)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    audit = build_manifest(
        case_dir=args.case_dir,
        legacy_manifest=args.legacy_manifest,
        archive_manifest=args.archive_manifest,
        archive_member_prefix=args.archive_member_prefix,
        output=args.output,
        audit_json=args.audit_json,
        expected_count=args.expected_count,
    )
    print(json.dumps(audit, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
