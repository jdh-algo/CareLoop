#!/usr/bin/env python3
"""Convenience entry points for the public CareLoop package.

This helper does not contain credentials. It wraps public scripts so users can
validate CL120 cases, rebuild judge packets from their own trajectories, and run
an optional judge with predictable default paths.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PY = sys.executable


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=ROOT, check=True)


def validate_cases(args: argparse.Namespace) -> None:
    run([PY, "-m", "careloop", "--case-glob", args.case_glob, "--validate-cases"])


def rebuild_packets(args: argparse.Namespace) -> None:
    run([PY, "scripts/build_minimal_judge_packets.py"])


def run_judge(args: argparse.Namespace) -> None:
    if not os.environ.get(args.api_key_env, "").strip():
        raise SystemExit(f"{args.api_key_env} missing; export your private API key before judge calls")
    base_url = args.base_url or os.environ.get("CARELOOP_LITE_BASE_URL", "")
    if not base_url.strip():
        raise SystemExit("--base-url or CARELOOP_LITE_BASE_URL missing; aborting before API calls")
    judge_model = args.judge_model or os.environ.get("CARELOOP_JUDGE_MODEL", "")
    if not judge_model:
        raise SystemExit("--judge-model or CARELOOP_JUDGE_MODEL missing")
    run([
        PY,
        "scripts/run_final_trajectory_judge.py",
        "--packet-dir", args.packet_dir,
        "--out-csv", args.out_csv,
        "--summary-json", args.summary_json,
        "--model", judge_model,
        "--base-url", base_url,
        "--api-key-env", args.api_key_env,
        "--parallelism", str(args.parallelism),
    ])


def main() -> int:
    ap = argparse.ArgumentParser(description="CareLoop public-package pipeline helper")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("validate-cases", help="Load released CL120 public cases without API calls")
    p.add_argument("--case-glob", default="cases/public_cl120_deidentified_120/*.json")
    p.set_defaults(func=validate_cases)

    p = sub.add_parser("rebuild-packets", help="Rebuild blinded minimal judge packets from public trajectories")
    p.set_defaults(func=rebuild_packets)

    p = sub.add_parser("judge", help="Run an API-backed CL120 ordinal 1-5 trajectory-quality judge over released packets")
    p.add_argument("--packet-dir", default="outputs/judge_packets/cl120_blinded_ordinal_1to5")
    p.add_argument("--out-csv", default="outputs/judge_results/cl120_ordinal_1to5.csv")
    p.add_argument("--summary-json", default="outputs/judge_results/cl120_ordinal_1to5_summary.json")
    p.add_argument("--judge-model", default="")
    p.add_argument("--base-url", default=os.environ.get("CARELOOP_LITE_BASE_URL", ""))
    p.add_argument("--api-key-env", default="CARELOOP_LITE_API_KEY")
    p.add_argument("--parallelism", type=int, default=5)
    p.set_defaults(func=run_judge)

    args = ap.parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
