#!/usr/bin/env python3
"""Build FCC/C-RWR-primary supplementary tables from frozen CL120 artifacts.

This analysis does not modify trajectories, canonical Judge records, or the frozen
1--5 ordinal rubric. It joins the released friction-domain tables to operational
metadata and pre-execution case-contract burden fields. Ordinal grades are retained
only as comparator columns where useful for audit.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import random
from typing import Iterable

import numpy as np
import pandas as pd

DOMAINS = (
    "information_repair",
    "constraint_navigation",
    "dynamic_reprioritization",
    "execution_loop_repair",
    "state_sensitive_communication",
    "bounded_closure_continuity",
)
BURDENS = (
    ("responsibility_chain_burden_score", "Responsibility-chain breadth"),
    ("evidence_integration_burden_score", "Evidence integration"),
    ("friction_burden_score", "Friction exposure"),
    ("temporal_burden_score", "Temporal demand"),
    ("closure_coordination_burden_score", "Closure coordination"),
    ("contract_complexity_index", "Exploratory aggregate index"),
)
SELF_MODEL_MATCH = {
    "DeepSeek-V4-Pro": "DeepSeek-V4-Pro",
    "GLM-5": "GLM-5",
    "gpt-5.6-sol": "GPT-5.6 Sol",
}
JUDGE_DISPLAY = {
    "DeepSeek-V4-Pro": "DeepSeek-V4-Pro",
    "GLM-5": "GLM-5",
    "GPT-5.5": "GPT-5.5",
    "gpt-5.6-sol": "GPT-5.6 Sol",
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def write_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, lineterminator="\n", float_format="%.10g")


def spearman(x: Iterable[float], y: Iterable[float]) -> float:
    a = pd.Series(list(x), dtype=float)
    b = pd.Series(list(y), dtype=float)
    keep = a.notna() & b.notna()
    if keep.sum() < 3:
        return float("nan")
    ar = a[keep].rank(method="average").to_numpy()
    br = b[keep].rank(method="average").to_numpy()
    if np.std(ar) == 0 or np.std(br) == 0:
        return float("nan")
    return float(np.corrcoef(ar, br)[0, 1])


def bootstrap_spearman(x: np.ndarray, y: np.ndarray, reps: int, seed: int) -> tuple[float, float, float]:
    point = spearman(x, y)
    rng = np.random.default_rng(seed)
    n = len(x)
    vals = np.empty(reps, dtype=float)
    for i in range(reps):
        idx = rng.integers(0, n, size=n)
        vals[i] = spearman(x[idx], y[idx])
    vals = vals[np.isfinite(vals)]
    return point, float(np.quantile(vals, 0.025)), float(np.quantile(vals, 0.975))


def bootstrap_mean(values: np.ndarray, reps: int, seed: int) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    n = len(values)
    draws = np.empty(reps, dtype=float)
    for i in range(reps):
        draws[i] = float(np.mean(values[rng.integers(0, n, size=n)]))
    return float(np.quantile(draws, 0.025)), float(np.quantile(draws, 0.975))


def self_association(long: pd.DataFrame, metric: str, reps: int, seed: int) -> pd.DataFrame:
    judges = sorted(long.judge_model.unique())
    models = sorted(long.doctor_model.unique())
    cases = sorted(long.case_id.unique())
    lookup = long.set_index(["case_id", "doctor_model", "judge_model"])[metric].to_dict()
    rows = []
    for k, (judge, own_model) in enumerate(SELF_MODEL_MATCH.items()):
        case_effects = []
        own = []
        peers = []
        nonself_residuals = []
        case_vectors = []
        for case in cases:
            residuals = {}
            for model in models:
                focal = float(lookup[(case, model, judge)])
                peer = float(np.mean([lookup[(case, model, other)] for other in judges if other != judge]))
                residuals[model] = focal - peer
                if model == own_model:
                    own.append(focal)
                    peers.append(peer)
            other = [residuals[m] for m in models if m != own_model]
            nonself_residuals.extend(other)
            case_effects.append(residuals[own_model] - float(np.mean(other)))
            case_vectors.append([residuals[m] for m in models])
        effects = np.asarray(case_effects, dtype=float)
        point = float(np.mean(effects))
        lo, hi = bootstrap_mean(effects, reps, seed + 1009 * (k + 1))
        # Case-stratified pseudo-self randomization: within each case, assign the
        # pseudo-self slot uniformly among the ten tested models.
        rng = np.random.default_rng(seed + 2003 * (k + 1))
        mat = np.asarray(case_vectors, dtype=float)
        perm = np.empty(reps, dtype=float)
        for i in range(reps):
            choices = rng.integers(0, len(models), size=len(cases))
            selected = mat[np.arange(len(cases)), choices]
            other_mean = (mat.sum(axis=1) - selected) / (len(models) - 1)
            perm[i] = float(np.mean(selected - other_mean))
        p = float((1 + np.sum(np.abs(perm) >= abs(point))) / (reps + 1))
        rows.append({
            "metric": metric,
            "judge_model_id": judge,
            "judge_display_name": JUDGE_DISPLAY[judge],
            "matched_doctor_model": own_model,
            "n_cases": len(cases),
            "n_self_trajectories": len(own),
            "n_nonself_trajectories": len(nonself_residuals),
            "judge_mean_on_own_model": float(np.mean(own)),
            "other_three_judges_mean_on_same_trajectories": float(np.mean(peers)),
            "raw_self_minus_peer_judges_same_trajectories": float(np.mean(np.asarray(own)-np.asarray(peers))),
            "judge_mean_peer_residual_on_other_models": float(np.mean(nonself_residuals)),
            "calibration_adjusted_case_stratified_self_association_effect": point,
            "bootstrap_95ci_low": lo,
            "bootstrap_95ci_high": hi,
            "case_stratified_randomization_two_sided_p": p,
            "packet_model_identity_blinded": True,
        })
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--bootstrap-replicates", type=int, default=10000)
    ap.add_argument("--self-association-replicates", type=int, default=100000)
    ap.add_argument("--seed", type=int, default=20260911)
    args = ap.parse_args()
    root = args.root.resolve()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    analysis = root / "analysis"
    friction_dir = analysis / "friction_v0_2"

    traj = pd.read_csv(friction_dir / "friction_domains_trajectory.csv")
    judge = pd.read_csv(friction_dir / "friction_domains_judge_long.csv")
    operational = pd.read_csv(analysis / "trajectory_level_results.csv").drop(
        columns=["contract_complexity_index", "contract_complexity_level"], errors="ignore"
    )
    burden = pd.read_csv(analysis / "case_contract_burden_profile.csv").drop(columns=["friction_intensity"], errors="ignore")
    metadata = pd.read_csv(analysis / "case_metadata_deidentified_recomputed.csv")

    assert len(traj) == 1200 and traj.packet_id.nunique() == 1200
    assert len(judge) == 4800 and judge.groupby("packet_id").size().eq(4).all()
    assert traj.doctor_model.nunique() == 10 and traj.case_id.nunique() == 120
    assert traj.groupby("doctor_model").size().eq(120).all()

    df = traj.merge(operational, on=["packet_id", "case_id", "doctor_model"], validate="one_to_one")
    df = df.merge(burden, on="case_id", validate="many_to_one")
    meta_cols = ["case_id", "disease_domain", "target_problem", "starting_phase", "secondary_frictions", "tool_dependency", "high_order_capability_domains"]
    df = df.merge(metadata[meta_cols], on="case_id", validate="many_to_one")
    df["fcc_minus_crwr"] = df.friction_capability_composite_median4 - df.careloop_real_world_robustness_median4

    # Judge dispersion is computed on the new measurement layer, not the ordinal comparator.
    jdisp = judge.groupby("packet_id").agg(
        fcc_judge_mean=("friction_capability_composite", "mean"),
        fcc_judge_sd=("friction_capability_composite", lambda x: float(np.std(x, ddof=0))),
        fcc_judge_range=("friction_capability_composite", lambda x: float(np.max(x)-np.min(x))),
        crwr_judge_mean=("careloop_real_world_robustness", "mean"),
        crwr_judge_sd=("careloop_real_world_robustness", lambda x: float(np.std(x, ddof=0))),
        crwr_judge_range=("careloop_real_world_robustness", lambda x: float(np.max(x)-np.min(x))),
    ).reset_index()
    df = df.merge(jdisp, on="packet_id", validate="one_to_one")

    case = df.groupby("case_id", as_index=False).agg(
        n_models=("doctor_model", "nunique"),
        mean_fcc=("friction_capability_composite_median4", "mean"),
        median_fcc=("friction_capability_composite_median4", "median"),
        cross_model_fcc_sd=("friction_capability_composite_median4", lambda x: float(np.std(x, ddof=0))),
        cross_model_fcc_range=("friction_capability_composite_median4", lambda x: float(np.max(x)-np.min(x))),
        mean_crwr=("careloop_real_world_robustness_median4", "mean"),
        median_crwr=("careloop_real_world_robustness_median4", "median"),
        cross_model_crwr_sd=("careloop_real_world_robustness_median4", lambda x: float(np.std(x, ddof=0))),
        cross_model_crwr_range=("careloop_real_world_robustness_median4", lambda x: float(np.max(x)-np.min(x))),
        mean_fcc_minus_crwr=("fcc_minus_crwr", "mean"),
        mean_fcc_judge_sd=("fcc_judge_sd", "mean"),
        mean_crwr_judge_sd=("crwr_judge_sd", "mean"),
        mean_turns=("turns_completed", "mean"),
        max_turns=("turns_completed", "max"),
        mean_workspace_turns=("workspace_turn_count", "mean"),
        closed_success_n=("closed_success_flag", "sum"),
        open_at_100_n=("open_at_100_flag", "sum"),
        runtime_error_n=("runtime_error_flag", "sum"),
    ).merge(burden, on="case_id", validate="one_to_one").merge(metadata[meta_cols], on="case_id", validate="one_to_one")
    write_csv(case, out / "fcc_case_level_summary.csv")

    assoc_rows = []
    outcomes = (
        ("mean_fcc", "case_mean_FCC"),
        ("mean_crwr", "case_mean_C_RWR"),
        ("mean_turns", "case_mean_turns"),
        ("cross_model_fcc_sd", "cross_model_FCC_SD"),
    )
    for i, (pred, pred_label) in enumerate(BURDENS):
        for j, (outcome, outcome_label) in enumerate(outcomes):
            point, lo, hi = bootstrap_spearman(
                case[pred].to_numpy(float), case[outcome].to_numpy(float),
                args.bootstrap_replicates, args.seed + i * 101 + j * 17,
            )
            assoc_rows.append({
                "predictor": pred,
                "predictor_label": pred_label,
                "outcome": outcome,
                "outcome_label": outcome_label,
                "n_cases": len(case),
                "spearman_rho": point,
                "bootstrap_95ci_low": lo,
                "bootstrap_95ci_high": hi,
                "bootstrap_unit": "case",
                "bootstrap_replicates": args.bootstrap_replicates,
            })
    assoc = pd.DataFrame(assoc_rows)
    write_csv(assoc, out / "fcc_contract_burden_associations.csv")

    # Model-level diagnostic map: continuous FCC/C-RWR values plus operational status.
    agg = {
        "n_trajectories": ("packet_id", "size"),
        "mean_fcc": ("friction_capability_composite_median4", "mean"),
        "median_fcc": ("friction_capability_composite_median4", "median"),
        "mean_crwr": ("careloop_real_world_robustness_median4", "mean"),
        "median_crwr": ("careloop_real_world_robustness_median4", "median"),
        "mean_fcc_minus_crwr": ("fcc_minus_crwr", "mean"),
        "mean_fcc_judge_sd": ("fcc_judge_sd", "mean"),
        "mean_crwr_judge_sd": ("crwr_judge_sd", "mean"),
        "closed_success_rate": ("closed_success_flag", "mean"),
        "open_at_100_rate": ("open_at_100_flag", "mean"),
        "runtime_error_rate": ("runtime_error_flag", "mean"),
        "mean_turns": ("turns_completed", "mean"),
        "mean_workspace_turns": ("workspace_turn_count", "mean"),
    }
    model = df.groupby("doctor_model").agg(**agg).reset_index()
    for d in DOMAINS:
        col = f"domain_{d}_median4"
        z = df.groupby("doctor_model")[col].agg(["mean", "count"]).reset_index()
        model = model.merge(z.rename(columns={"mean": f"mean_domain_{d}", "count": f"n_domain_{d}"}), on="doctor_model", validate="one_to_one")
    model["fcc_rank"] = model.mean_fcc.rank(method="min", ascending=False).astype(int)
    model["crwr_rank"] = model.mean_crwr.rank(method="min", ascending=False).astype(int)
    model = model.sort_values(["fcc_rank", "doctor_model"])
    write_csv(model, out / "fcc_model_diagnostic_summary.csv")

    # Workspace use remains descriptive; adjusted residual associations remove stable model and case means.
    residual = df.copy()
    for col in ["friction_capability_composite_median4", "careloop_real_world_robustness_median4", "workspace_turn_count"]:
        residual[col + "_two_way_residual"] = residual[col] - residual.groupby("doctor_model")[col].transform("mean") - residual.groupby("case_id")[col].transform("mean") + residual[col].mean()
    work_rows = []
    for outcome in ["friction_capability_composite_median4", "careloop_real_world_robustness_median4"]:
        work_rows.append({"analysis":"unadjusted_trajectory_level", "outcome":outcome, "n":len(df), "spearman_rho":spearman(df.workspace_turn_count, df[outcome])})
        work_rows.append({"analysis":"two_way_model_and_case_residual", "outcome":outcome, "n":len(df), "spearman_rho":spearman(residual.workspace_turn_count_two_way_residual, residual[outcome+"_two_way_residual"])})
    write_csv(pd.DataFrame(work_rows), out / "fcc_workspace_associations.csv")

    self_rows = []
    for metric in ["friction_capability_composite", "careloop_real_world_robustness"]:
        self_rows.append(self_association(judge, metric, args.self_association_replicates, args.seed))
    write_csv(pd.concat(self_rows, ignore_index=True), out / "fcc_judge_self_association_summary.csv")

    # Continuous review map: retrieval aid only; no analyst-defined binary failure thresholds.
    review_cols = [
        "packet_id", "case_id", "doctor_model", "terminal_status", "turns_completed", "workspace_turn_count",
        "primary_friction", "friction_intensity", "friction_capability_composite_median4",
        "careloop_real_world_robustness_median4", "fcc_minus_crwr", "clinical_error_gate_median4",
        "fcc_judge_sd", "fcc_judge_range", "crwr_judge_sd", "crwr_judge_range", "ordinal_grade_median4",
    ] + [f"domain_{d}_median4" for d in DOMAINS]
    review = df[review_cols].copy()
    review["review_order_low_crwr"] = review.careloop_real_world_robustness_median4.rank(method="first", ascending=True).astype(int)
    review["review_order_large_safety_gap"] = review.fcc_minus_crwr.rank(method="first", ascending=False).astype(int)
    review["review_order_high_judge_dispersion"] = review.fcc_judge_sd.rank(method="first", ascending=False).astype(int)
    write_csv(review.sort_values("review_order_low_crwr"), out / "fcc_trajectory_review_map.csv")

    # Candidate inventory for evidence review. Categories are deliberately redundant;
    # inclusion in this table is not an endorsement for manuscript use.
    candidates = []
    def add(category: str, rows: pd.DataFrame, reason: str) -> None:
        for _, r in rows.iterrows():
            candidates.append({
                "candidate_category": category,
                "candidate_reason": reason,
                "case_id": r.case_id,
                "doctor_model": r.doctor_model,
                "packet_id": r.packet_id,
                "terminal_status": r.terminal_status,
                "primary_friction": r.primary_friction,
                "fcc": r.friction_capability_composite_median4,
                "crwr": r.careloop_real_world_robustness_median4,
                "fcc_minus_crwr": r.fcc_minus_crwr,
                "clinical_error_gate": r.clinical_error_gate_median4,
                "fcc_judge_sd": r.fcc_judge_sd,
                "ordinal_comparator": r.ordinal_grade_median4,
            })
    eligible = df[(df.terminal_status == "closed_success") & (df.applicable_domain_count_median4 >= 3)]
    add("high_fcc_high_crwr", eligible.sort_values(["careloop_real_world_robustness_median4", "friction_capability_composite_median4"], ascending=False).head(15), "Positive friction-response exemplar with broad applicable-domain coverage")
    add("high_fcc_low_crwr", eligible[eligible.friction_capability_composite_median4 >= eligible.friction_capability_composite_median4.quantile(.75)].sort_values("fcc_minus_crwr", ascending=False).head(20), "Strong ungated friction handling with a large clinical-safety penalty")
    add("high_judge_dispersion", df.sort_values("fcc_judge_sd", ascending=False).head(20), "Large FCC dispersion across the four experimental Judges")
    for case_id, z in df.groupby("case_id"):
        if z.doctor_model.nunique() != 10:
            continue
        hi = z.loc[z.careloop_real_world_robustness_median4.idxmax()]
        lo = z.loc[z.careloop_real_world_robustness_median4.idxmin()]
        candidates.append({
            "candidate_category":"same_case_crwr_contrast",
            "candidate_reason":f"Same-case C-RWR spread {hi.careloop_real_world_robustness_median4-lo.careloop_real_world_robustness_median4:.3f}; high model {hi.doctor_model}; low model {lo.doctor_model}",
            "case_id":case_id,
            "doctor_model":f"{hi.doctor_model} vs {lo.doctor_model}",
            "packet_id":f"{hi.packet_id} | {lo.packet_id}",
            "terminal_status":f"{hi.terminal_status} | {lo.terminal_status}",
            "primary_friction":hi.primary_friction,
            "fcc":hi.friction_capability_composite_median4-lo.friction_capability_composite_median4,
            "crwr":hi.careloop_real_world_robustness_median4-lo.careloop_real_world_robustness_median4,
            "fcc_minus_crwr":np.nan,
            "clinical_error_gate":np.nan,
            "fcc_judge_sd":np.nan,
            "ordinal_comparator":np.nan,
        })
    cand = pd.DataFrame(candidates).drop_duplicates(["candidate_category", "case_id", "doctor_model", "packet_id"])
    contrasts = cand[cand.candidate_category=="same_case_crwr_contrast"].sort_values("crwr", ascending=False).head(25)
    cand = pd.concat([cand[cand.candidate_category!="same_case_crwr_contrast"], contrasts], ignore_index=True)
    write_csv(cand, out / "fcc_representative_case_candidates.csv")

    outputs = []
    for path in sorted(out.glob("*.csv")):
        outputs.append({"file": path.name, "rows": int(len(pd.read_csv(path))), "sha256": sha256(path), "bytes": path.stat().st_size})
    audit = {
        "schema_version": "careloop.cl120.fcc_primary_supplementary.v1",
        "status": "posthoc_formative_supplementary_analysis",
        "source_counts": {"trajectories": len(traj), "judge_cells": len(judge), "cases": traj.case_id.nunique(), "tested_models": traj.doctor_model.nunique(), "judges": judge.judge_model.nunique()},
        "invariants": {
            "frozen_ordinal_rubric_modified": False,
            "canonical_judge_records_modified": False,
            "trajectory_records_modified": False,
            "primary_trajectory_aggregation": "median_of_four_judges",
            "domain_missingness": "not imputed; fixed FCC weights renormalized over applicable domains",
            "burden_predictor_timing": "derived exclusively from frozen pre-execution case contracts",
            "representative_candidate_policy": "candidate generation only; manuscript examples require direct trajectory and Judge-evidence verification",
        },
        "outputs": outputs,
        "result": "PASS",
    }
    (out / "FCC_SUPPLEMENTARY_AUDIT.json").write_text(json.dumps(audit, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(audit, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
