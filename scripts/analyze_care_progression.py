#!/usr/bin/env python3
"""Reproducible descriptive analyses for CareLoop care progression.

This script re-expresses frozen structured Judge fields. It does not modify
FCC/C-RWR, original trajectories, cases, or canonical Judge records.
"""
from __future__ import annotations
import argparse, json, math
from pathlib import Path
import numpy as np
import pandas as pd

SEED = 20260919
DOMAINS = [
    "information_repair", "constraint_navigation", "dynamic_reprioritization",
    "execution_loop_repair", "state_sensitive_communication",
    "bounded_closure_continuity",
]
DOMAIN_LABEL = {
    "information_repair":"Information repair",
    "constraint_navigation":"Constraint navigation",
    "dynamic_reprioritization":"Dynamic reprioritization",
    "execution_loop_repair":"Execution-loop repair",
    "state_sensitive_communication":"State-sensitive communication",
    "bounded_closure_continuity":"Bounded closure / continuity",
}

def rank_corr(a, b):
    a=pd.Series(a,dtype=float); b=pd.Series(b,dtype=float)
    m=a.notna() & b.notna()
    if m.sum()<3: return np.nan
    return a[m].rank(method="average").corr(b[m].rank(method="average"))

def qci(x):
    x=np.asarray(x,float)
    return float(np.nanquantile(x,.025)), float(np.nanquantile(x,.975))

def load_cases(case_dir):
    meta={}; ho_type={}
    for p in sorted(case_dir.glob("case_*.json")):
        x=json.loads(p.read_text())
        cid=x["case_id"]
        meta[cid]={
            "primary_friction":x["real_world_friction_design"]["primary_friction"],
            "friction_intensity":x["real_world_friction_design"]["friction_intensity"],
            "clinical_domain":x["case_taxonomy"]["disease_domain"],
        }
        for h in x["evaluation_contract_v2"]["high_order_test_points"]:
            ho_type[(cid,h["id"])]=h["source_item"]["capability_type"]
    assert len(meta)==120
    assert len(ho_type)==480
    return meta,ho_type

def bootstrap_case_mean(df, value, n=10000):
    rng=np.random.default_rng(SEED)
    cases=np.array(sorted(df.case_id.unique()))
    vals=[]
    for _ in range(n):
        take=rng.choice(cases,len(cases),replace=True)
        # preserve duplicated sampled clusters
        vals.append(np.mean([df.loc[df.case_id==c,value].mean() for c in take]))
    return qci(vals)

def main(root:Path,out:Path):
    base=root/"public_supplement/cl120_20260911"
    out.mkdir(parents=True,exist_ok=True)
    meta, ho_map=load_cases(root/"cases/public_cl120_deidentified_120")
    traj=pd.read_csv(base/"analysis/friction_v0_2/friction_domains_trajectory.csv")
    jrows=[]; hrows=[]; crows=[]
    with (base/"judge_results/canonical_judge_results.jsonl").open() as f:
        for line in f:
            if not line.strip(): continue
            r=json.loads(line); jrows.append(r)
            for h in r["high_order_assessment"]:
                completion=h["completion"]
                hrows.append({
                    "packet_id":r["packet_id"],"case_id":r["case_id"],
                    "doctor_model":r["doctor_model"],"judge_model":r["judge_model"],
                    "primary_friction":meta[r["case_id"]]["primary_friction"],
                    "ho_type":ho_map[(r["case_id"],h["ho_id"])],
                    "triggered":bool(h["triggered"]),
                    "active_model_action":bool(h["active_model_action"]),
                    "meaningful_trajectory_impact":bool(h["meaningful_trajectory_impact"]),
                    "complete":completion=="complete",
                    "effective_completion":completion=="complete" and bool(h["active_model_action"]) and bool(h["meaningful_trajectory_impact"]),
                    "completion":completion,
                })
            c=r["closure_assessment"]
            crows.append({"packet_id":r["packet_id"],"case_id":r["case_id"],"doctor_model":r["doctor_model"],
                "judge_model":r["judge_model"],"terminal_status":r["terminal_status"],"closure_label":c["label"],
                "safe":bool(c["safe"]),"executable":bool(c["executable"]),"traceable":bool(c["traceable"])})
    ho=pd.DataFrame(hrows); clos=pd.DataFrame(crows)
    assert len(jrows)==4800 and len(ho)==19200 and len(clos)==4800 and len(traj)==1200

    # Opportunity-to-impact stages, among realized/triggered opportunities.
    tr=ho[ho.triggered].copy()
    def stage_summary(g):
        return pd.Series({
            "judge_ho_assessments":len(g),
            "active_action_rate":g.active_model_action.mean(),
            "meaningful_impact_rate":g.meaningful_trajectory_impact.mean(),
            "effective_completion_rate":g.effective_completion.mean(),
            "action_to_impact_gap":g.active_model_action.mean()-g.meaningful_trajectory_impact.mean(),
        })
    by_model=tr.groupby("doctor_model").apply(stage_summary,include_groups=False).reset_index()
    by_type=tr.groupby("ho_type").apply(stage_summary,include_groups=False).reset_index()
    overall=stage_summary(tr).to_frame().T
    for col in ["active_action_rate","meaningful_impact_rate","effective_completion_rate","action_to_impact_gap"]:
        lo,hi=bootstrap_case_mean(tr,col if col in tr else "active_model_action") if False else (np.nan,np.nan)
    # bootstrap derived functions
    rng=np.random.default_rng(SEED)
    case_agg=tr.groupby("case_id").agg(n=("triggered","size"),active=("active_model_action","sum"),impact=("meaningful_trajectory_impact","sum"),effective=("effective_completion","sum")).sort_index()
    arr=case_agg[["n","active","impact","effective"]].to_numpy(float)
    idx=rng.integers(0,len(arr),size=(10000,len(arr)))
    sampled=arr[idx].sum(axis=1)
    active=sampled[:,1]/sampled[:,0]; impact=sampled[:,2]/sampled[:,0]; effective=sampled[:,3]/sampled[:,0]
    boot=np.column_stack([active,impact,effective,active-impact])
    for i,col in enumerate(["active_action_rate","meaningful_impact_rate","effective_completion_rate","action_to_impact_gap"]):
        overall[col+"_ci_low"],overall[col+"_ci_high"]=qci(boot[:,i])
    overall.to_csv(out/"ho_opportunity_stage_overall.csv",index=False)
    by_model.sort_values("effective_completion_rate",ascending=False).to_csv(out/"ho_opportunity_stage_by_model.csv",index=False)
    by_type.sort_values("effective_completion_rate").to_csv(out/"ho_opportunity_stage_by_type.csv",index=False)

    # Terminal status vs semantic closure labels.
    ca=clos.groupby(["packet_id","case_id","doctor_model","terminal_status"]).agg(
        premature_votes=("closure_label",lambda s:int((s=="premature_closure").sum())),
        valid_open_votes=("closure_label",lambda s:int((s=="valid_open").sum())),
        safe_votes=("safe","sum"),executable_votes=("executable","sum"),traceable_votes=("traceable","sum")
    ).reset_index()
    ca=ca.merge(traj[["packet_id","friction_capability_composite_median4","careloop_real_world_robustness_median4","closure_process_score_median4"]],on="packet_id",how="left")
    ca.to_csv(out/"terminal_status_closure_quality_trajectory.csv",index=False)
    terminal_summary=pd.DataFrame([
        {"quantity":"closed_success","count":int((ca.terminal_status=="closed_success").sum())},
        {"quantity":"open_at_100","count":int((ca.terminal_status=="open_at_100").sum())},
        {"quantity":"runtime_error","count":int((ca.terminal_status=="runtime_error").sum())},
        {"quantity":"open_at_100_valid_open_3plus_votes","count":int(((ca.terminal_status=="open_at_100")&(ca.valid_open_votes>=3)).sum())},
        {"quantity":"open_at_100_valid_open_4_votes","count":int(((ca.terminal_status=="open_at_100")&(ca.valid_open_votes==4)).sum())},
        {"quantity":"closed_success_premature_1plus_votes","count":int(((ca.terminal_status=="closed_success")&(ca.premature_votes>=1)).sum())},
        {"quantity":"closed_success_premature_2plus_votes","count":int(((ca.terminal_status=="closed_success")&(ca.premature_votes>=2)).sum())},
        {"quantity":"closed_success_premature_3plus_votes","count":int(((ca.terminal_status=="closed_success")&(ca.premature_votes>=3)).sum())},
    ])
    terminal_summary.to_csv(out/"terminal_status_closure_quality_summary.csv",index=False)

    # Deterministic FCC contribution decomposition at the Judge-cell level.
    # FCC is additive within a Judge cell. The public primary result uses a
    # median across Judges per trajectory, so this diagnostic uses the parallel
    # mean-of-Judge representation and never replaces the primary ranking.
    cfg=json.loads((root/"configs/careloop_friction_score_v0_2.json").read_text())
    weights=cfg["domain_composite_weights"]
    jlong=pd.read_csv(base/"analysis/friction_v0_2/friction_domains_judge_long.csv")
    contrib=[]
    for _,r in jlong.iterrows():
        present=[d for d in DOMAINS if pd.notna(r[f"domain_{d}"])]
        denom=sum(weights[d] for d in present)
        row={"packet_id":r.packet_id,"case_id":r.case_id,"doctor_model":r.doctor_model,"judge_model":r.judge_model}
        for d in DOMAINS:
            row[d+"_contribution"]=(weights[d]/denom*r[f"domain_{d}"]) if d in present else 0.0
        row["contribution_sum"]=sum(row[d+"_contribution"] for d in DOMAINS)
        row["fcc_judge_cell"]=r.friction_capability_composite
        contrib.append(row)
    contrib=pd.DataFrame(contrib)
    assert np.max(np.abs(contrib.contribution_sum-contrib.fcc_judge_cell))<2e-6
    model_contrib=contrib.groupby("doctor_model")[[d+"_contribution" for d in DOMAINS]+["fcc_judge_cell"]].mean().reset_index()
    model_contrib=model_contrib.rename(columns={"fcc_judge_cell":"fcc_mean_of_judge_cells"})
    primary=traj.groupby("doctor_model").friction_capability_composite_median4.mean().rename("fcc_primary_median4").reset_index()
    model_contrib=model_contrib.merge(primary,on="doctor_model")
    grand=model_contrib[[d+"_contribution" for d in DOMAINS]].mean()
    for d in DOMAINS: model_contrib[d+"_vs_grand"]=model_contrib[d+"_contribution"]-grand[d+"_contribution"]
    model_contrib.sort_values("fcc_primary_median4",ascending=False).to_csv(out/"fcc_domain_contribution_by_model.csv",index=False)

    domain_means=traj.groupby("doctor_model")[[f"domain_{d}_median4" for d in DOMAINS]].mean()
    discrimination=[]
    for d in DOMAINS:
        ss=domain_means[f"domain_{d}_median4"]
        discrimination.append({"domain":d,"label":DOMAIN_LABEL[d],"model_mean":ss.mean(),"between_model_sd":ss.std(ddof=1),"between_model_range":ss.max()-ss.min(),"best_model":ss.idxmax(),"worst_model":ss.idxmin()})
    pd.DataFrame(discrimination).sort_values("between_model_range",ascending=False).to_csv(out/"capability_domain_discrimination.csv",index=False)

    safety=traj.groupby("doctor_model").agg(FCC=("friction_capability_composite_median4","mean"),C_RWR=("careloop_real_world_robustness_median4","mean"),clinical_error_gate=("clinical_error_gate_median4","mean")).reset_index()
    safety["safety_gate_penalty"]=safety.FCC-safety.C_RWR
    safety.sort_values("FCC",ascending=False).to_csv(out/"fcc_crwr_safety_penalty_by_model.csv",index=False)

    # Case-level discriminatory yield.
    rows=[]
    for cid,g in traj.groupby("case_id"):
        row={"case_id":cid,"primary_friction":g.primary_friction.iloc[0],"fcc_range":g.friction_capability_composite_median4.max()-g.friction_capability_composite_median4.min(),"fcc_sd":g.friction_capability_composite_median4.std(ddof=1),"crwr_range":g.careloop_real_world_robustness_median4.max()-g.careloop_real_world_robustness_median4.min()}
        ds=[]
        for d in DOMAINS:
            x=g[f"domain_{d}_median4"].dropna()
            row[d+"_range"]=(x.max()-x.min()) if len(x) else np.nan
            if len(x): ds.append(row[d+"_range"])
        row["mean_applicable_domain_range"]=np.mean(ds)
        rows.append(row)
    case_yield=pd.DataFrame(rows).sort_values("fcc_range",ascending=False)
    case_yield.to_csv(out/"case_level_discriminatory_yield.csv",index=False)

    # Pairwise domain correlations.
    dcols=[f"domain_{d}_median4" for d in DOMAINS]
    traj[dcols].corr(method="spearman",min_periods=30).to_csv(out/"domain_pairwise_spearman.csv")

    # Friction-specific model ranking heterogeneity.
    fr=traj.groupby(["primary_friction","doctor_model"])["friction_capability_composite_median4"].mean().unstack()
    fr.T.corr(method="spearman").to_csv(out/"friction_rank_correlation.csv")
    ranks=fr.rank(axis=1,ascending=False,method="average")
    pd.DataFrame({"doctor_model":ranks.columns,"best_friction_rank":ranks.min(axis=0).values,"worst_friction_rank":ranks.max(axis=0).values,"rank_range":(ranks.max(axis=0)-ranks.min(axis=0)).values}).sort_values("rank_range",ascending=False).to_csv(out/"friction_rank_range_by_model.csv",index=False)

    # Ranking stability by number of cases and disjoint 60/60 split halves.
    rng=np.random.default_rng(SEED)
    stability=[]
    for metric,col in [("FCC","friction_capability_composite_median4"),("C-RWR","careloop_real_world_robustness_median4")]:
        wide=traj.pivot(index="case_id",columns="doctor_model",values=col).sort_index()
        full=wide.mean(); top=full.idxmax()
        for n in [5,10,20,30,60,90,120]:
            reps=1 if n==120 else 3000; rr=[]; top1=[]
            for _ in range(reps):
                ids=wide.index if n==120 else rng.choice(wide.index,n,replace=False)
                cur=wide.loc[ids].mean()
                rr.append(rank_corr(cur.values,full.values)); top1.append(cur.idxmax()==top)
            stability.append({"metric":metric,"n_cases":n,"median_spearman":np.median(rr),"ci_low":np.quantile(rr,.025),"ci_high":np.quantile(rr,.975),"top1_recovery":np.mean(top1),"resamples":reps})
    pd.DataFrame(stability).to_csv(out/"ranking_stability_by_case_count.csv",index=False)

    split=[]
    for metric,col in [("FCC","friction_capability_composite_median4"),("C-RWR","careloop_real_world_robustness_median4")]:
        wide=traj.pivot(index="case_id",columns="doctor_model",values=col)
        fric=traj.drop_duplicates("case_id").set_index("case_id").primary_friction
        rr=[]; top=[]
        for _ in range(5000):
            a=[]; b=[]
            for _,ids in fric.groupby(fric).groups.items():
                ids=np.array(list(ids)); rng.shuffle(ids); a.extend(ids[:len(ids)//2]); b.extend(ids[len(ids)//2:])
            ra=wide.loc[a].mean(); rb=wide.loc[b].mean()
            rr.append(rank_corr(ra.values,rb.values)); top.append(ra.idxmax()==rb.idxmax())
        lo,hi=qci(rr)
        split.append({"metric":metric,"median_spearman":np.median(rr),"ci_low":lo,"ci_high":hi,"top1_agreement":np.mean(top),"resamples":5000})
    pd.DataFrame(split).to_csv(out/"stratified_split_half_rank_stability.csv",index=False)

    summary={
      "schema":"careloop.care_progression.analysis.v1",
      "seed":SEED,"cases":120,"trajectories":1200,"judge_records":4800,"trajectory_ho_units":4800,"judge_ho_assessments":19200,
      "interpretation":"Descriptive analysis of opportunity-to-impact conversion, model separation, closure quality, diagnostic yield, and ranking stability.",
      "overall_opportunity_to_impact":overall.iloc[0].to_dict(),
      "terminal_counts":dict(zip(terminal_summary.quantity,terminal_summary["count"])),
    }
    (out/"analysis_summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__=="__main__":
    ap=argparse.ArgumentParser(); ap.add_argument("--repo-root",type=Path,default=Path(__file__).resolve().parents[1]); ap.add_argument("--out",type=Path,default=None)
    a=ap.parse_args(); root=a.repo_root.resolve(); out=a.out or root/"public_supplement/cl120_20260911/analysis/care_progression"
    main(root,out)
