#!/usr/bin/env python3
"""Build or verify deterministic SHA-256 manifests for the CareLoop release.

The repository manifest excludes itself to avoid a circular hash. The CL120
supplement manifest likewise excludes its own CSV. Local environments, caches,
Git metadata, and generated runtime outputs are never release inputs.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import os
from pathlib import Path
from typing import Iterable

REPOSITORY_MANIFEST = Path("manifests/repository_file_manifest.sha256.csv")
DEFAULT_SUPPLEMENT_ROOT = Path("public_supplement/cl120_20260911")
EXCLUDED_PARTS = {
    ".git", ".venv", ".pytest_cache", ".ruff_cache", ".mypy_cache",
    "__pycache__", "build", "dist", "outputs", "runs", "logs",
}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def excluded(path: Path) -> bool:
    return (
        any(part in EXCLUDED_PARTS or part.endswith(".egg-info") for part in path.parts)
        or path.name == ".DS_Store"
        or path.suffix in {".pyc", ".pyo"}
    )


def repository_paths(root: Path) -> list[Path]:
    # Always enumerate the release tree itself so verification is identical in a
    # source checkout and after archive extraction. Local caches, logs, and Git metadata
    # are excluded explicitly by ``excluded``.
    candidates = [path for path in root.rglob("*") if path.is_file()]
    manifest = (root / REPOSITORY_MANIFEST).resolve()
    return sorted(
        (path for path in candidates if path.resolve() != manifest and not excluded(path.relative_to(root))),
        key=lambda path: path.relative_to(root).as_posix(),
    )


def supplement_paths(root: Path, supplement_root: Path = DEFAULT_SUPPLEMENT_ROOT) -> list[Path]:
    supplement = root / supplement_root
    manifest = (supplement / "PACKAGE_MANIFEST.csv").resolve()
    return sorted(
        (path for path in supplement.rglob("*") if path.is_file() and path.resolve() != manifest and not excluded(path.relative_to(root))),
        key=lambda path: path.relative_to(supplement).as_posix(),
    )


def rows(paths: Iterable[Path], base: Path) -> list[dict[str, str | int]]:
    return [
        {
            "relative_path": path.relative_to(base).as_posix(),
            "size_bytes": path.stat().st_size,
            "sha256": file_sha256(path),
        }
        for path in paths
    ]


def write_manifest(path: Path, manifest_rows: list[dict[str, str | int]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["relative_path", "size_bytes", "sha256"], lineterminator="\n")
        writer.writeheader()
        writer.writerows(manifest_rows)
    os.replace(temp, path)


def read_manifest(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        result = list(csv.DictReader(handle))
    if any(set(row) != {"relative_path", "size_bytes", "sha256"} for row in result):
        raise ValueError("manifest_wrong_columns")
    return result


def check_manifest(path: Path, expected: list[dict[str, str | int]]) -> list[str]:
    if not path.is_file():
        return ["manifest_missing"]
    actual = read_manifest(path)
    expected_text = [
        {"relative_path": str(row["relative_path"]), "size_bytes": str(row["size_bytes"]), "sha256": str(row["sha256"])}
        for row in expected
    ]
    errors: list[str] = []
    if len(actual) != len(expected_text):
        errors.append(f"row_count:{len(actual)}!={len(expected_text)}")
    actual_by_path = {row["relative_path"]: row for row in actual}
    expected_by_path = {row["relative_path"]: row for row in expected_text}
    for missing in sorted(set(expected_by_path) - set(actual_by_path)):
        errors.append("missing:" + missing)
    for extra in sorted(set(actual_by_path) - set(expected_by_path)):
        errors.append("extra:" + extra)
    for relative_path in sorted(set(actual_by_path) & set(expected_by_path)):
        if actual_by_path[relative_path] != expected_by_path[relative_path]:
            errors.append("mismatch:" + relative_path)
    if [row["relative_path"] for row in actual] != sorted(row["relative_path"] for row in actual):
        errors.append("paths_not_sorted")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build or verify CareLoop release SHA-256 manifests.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--supplement-root", type=Path, default=DEFAULT_SUPPLEMENT_ROOT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    supplement_root = args.supplement_root
    if supplement_root.is_absolute():
        try:
            supplement_root = supplement_root.resolve().relative_to(root)
        except ValueError as exc:
            raise SystemExit("--supplement-root must be inside --root") from exc
    supplement_dir = root / supplement_root
    supplement_manifest = supplement_dir / "PACKAGE_MANIFEST.csv"
    if not supplement_dir.is_dir():
        raise SystemExit(f"supplement root missing: {supplement_root}")
    supplement_expected = rows(supplement_paths(root, supplement_root), supplement_dir)
    if not args.check:
        write_manifest(supplement_manifest, supplement_expected)
    repository_expected = rows(repository_paths(root), root)
    if not args.check:
        write_manifest(root / REPOSITORY_MANIFEST, repository_expected)
        # Recalculate after writing the supplement manifest; repository_paths includes it.
        repository_expected = rows(repository_paths(root), root)
        write_manifest(root / REPOSITORY_MANIFEST, repository_expected)
    checks = {
        "supplement_manifest": check_manifest(supplement_manifest, supplement_expected),
        "repository_manifest": check_manifest(root / REPOSITORY_MANIFEST, repository_expected),
    }
    print({name: ("PASS" if not errors else errors) for name, errors in checks.items()})
    return 1 if any(checks.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
