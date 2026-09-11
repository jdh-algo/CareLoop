#!/usr/bin/env python3
"""Judge agreement for CL120 friction-centered v0.2 measurements.

Reports two-way random-effects absolute-agreement ICC(A,1)/(A,k), all six
pairwise trajectory-level Spearman correlations, and all six pairwise
10-model rank correlations for FCC, C-RWR, and the six capability domains.
Case-paired bootstrap intervals preserve the ten tested trajectories within
an authored case.
"""
from __future__ import annotations
import argparse,csv,json,math,random,statistics,hashlib
from collections import defaultdict
from pathlib import Path

METRICS=[
 ('friction_capability_composite','Friction Capability Composite'),
 ('careloop_real_world_robustness','Safety-gated C-RWR'),
 ('domain_information_repair','Information repair'),
 ('domain_constraint_navigation','Constraint navigation'),
 ('domain_dynamic_reprioritization','Dynamic reprioritization'),
 ('domain_execution_loop_repair','Execution-loop repair'),
 ('domain_state_sensitive_communication','State-sensitive communication'),
 ('domain_bounded_closure_continuity','Bounded closure/continuity'),
]

def read(p):
 with p.open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
def write(p,rows):
 if not rows:raise ValueError(p)
 with p.open('w',encoding='utf-8',newline='') as f:w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
def avg(xs):xs=list(xs);return sum(xs)/len(xs) if xs else float('nan')
def pct(xs,p):
 xs=sorted(xs);q=(len(xs)-1)*p;i=int(q);r=q-i;return xs[i]*(1-r)+xs[min(i+1,len(xs)-1)]*r
def ranks(xs):
 o=sorted(range(len(xs)),key=lambda i:xs[i]);r=[0.]*len(xs);i=0
 while i<len(o):
  j=i+1
  while j<len(o) and xs[o[j]]==xs[o[i]]:j+=1
  v=(i+j-1)/2+1
  for k in range(i,j):r[o[k]]=v
  i=j
 return r
def spearman(x,y):
 rx,ry=ranks(x),ranks(y);mx,my=avg(rx),avg(ry);dx=sum((v-mx)**2 for v in rx);dy=sum((v-my)**2 for v in ry)
 return sum((a-mx)*(b-my) for a,b in zip(rx,ry))/math.sqrt(dx*dy) if dx and dy else float('nan')
def icc_absolute(matrix):
 """McGraw-Wong/Shrout-Fleiss two-way random absolute ICC(A,1)/(A,k)."""
 n=len(matrix);k=len(matrix[0])
 grand=avg(v for row in matrix for v in row);rowm=[avg(row) for row in matrix];colm=[avg(row[j] for row in matrix) for j in range(k)]
 ssr=k*sum((v-grand)**2 for v in rowm);ssc=n*sum((v-grand)**2 for v in colm)
 sse=sum((matrix[i][j]-rowm[i]-colm[j]+grand)**2 for i in range(n) for j in range(k))
 msr=ssr/(n-1);msc=ssc/(k-1);mse=sse/((n-1)*(k-1))
 one=(msr-mse)/(msr+(k-1)*mse+k*(msc-mse)/n)
 mean=(msr-mse)/(msr+(msc-mse)/n)
 return one,mean,msr,msc,mse

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--analysis-dir',type=Path,required=True);ap.add_argument('--identity-csv',type=Path,required=True);ap.add_argument('--out',type=Path,required=True);ap.add_argument('--bootstrap-replicates',type=int,default=2000);ap.add_argument('--seed',type=int,default=20260911);a=ap.parse_args();a.out.mkdir(parents=True,exist_ok=True)
 rows=read(a.analysis_dir/'friction_domains_judge_long.csv');identity={r['judge_model_id']:r['display_name'] for r in read(a.identity_csv)}
 judges=sorted({r['judge_model'] for r in rows});display={j:identity.get(j,j) for j in judges};mods=sorted({r['doctor_model'] for r in rows});case_by_packet={r['packet_id']:r['case_id'] for r in rows};model_by_packet={r['packet_id']:r['doctor_model'] for r in rows}
 metric_summary=[];pair_traj=[];pair_rank=[]
 rng=random.Random(a.seed)
 for mi,(field,label) in enumerate(METRICS):
  by=defaultdict(dict)
  for r in rows:
   if r[field]!='':by[r['packet_id']][r['judge_model']]=float(r[field])
  packets=sorted(p for p,z in by.items() if len(z)==len(judges));matrix=[[by[p][j] for j in judges] for p in packets];one,mean,msr,msc,mse=icc_absolute(matrix)
  cases=sorted({case_by_packet[p] for p in packets});packets_by_case={c:[p for p in packets if case_by_packet[p]==c] for c in cases}
  b1=[];bk=[]
  for _ in range(a.bootstrap_replicates):
   sampled=[cases[rng.randrange(len(cases))] for __ in cases];bm=[]
   for c in sampled:
    bm.extend([[by[p][j] for j in judges] for p in packets_by_case[c]])
   q1,qk,_,_,_=icc_absolute(bm);b1.append(q1);bk.append(qk)
  traj_pair_vals=[];rank_pair_vals=[]
  for i,j1 in enumerate(judges):
   for j2 in judges[i+1:]:
    x=[by[p][j1] for p in packets];y=[by[p][j2] for p in packets];rt=spearman(x,y);traj_pair_vals.append(rt)
    m1={m:avg(by[p][j1] for p in packets if model_by_packet[p]==m) for m in mods};m2={m:avg(by[p][j2] for p in packets if model_by_packet[p]==m) for m in mods};rr=spearman([m1[m] for m in mods],[m2[m] for m in mods]);rank_pair_vals.append(rr)
    pair_traj.append({'metric':field,'metric_label':label,'judge_1':display[j1],'judge_2':display[j2],'n_trajectories':len(packets),'spearman_rho':rt})
    pair_rank.append({'metric':field,'metric_label':label,'judge_1':display[j1],'judge_2':display[j2],'n_models':len(mods),'spearman_rho':rr})
  metric_summary.append({'metric':field,'metric_label':label,'n_trajectories':len(packets),'n_cases':len(cases),'n_judges':len(judges),'icc_a1':one,'icc_a1_ci95_low':pct(b1,.025),'icc_a1_ci95_high':pct(b1,.975),'icc_a4':mean,'icc_a4_ci95_low':pct(bk,.025),'icc_a4_ci95_high':pct(bk,.975),'pairwise_trajectory_spearman_min':min(traj_pair_vals),'pairwise_trajectory_spearman_max':max(traj_pair_vals),'pairwise_trajectory_spearman_mean':avg(traj_pair_vals),'pairwise_model_rank_spearman_min':min(rank_pair_vals),'pairwise_model_rank_spearman_max':max(rank_pair_vals),'pairwise_model_rank_spearman_mean':avg(rank_pair_vals),'judge_mean_min':min(avg(by[p][j] for p in packets) for j in judges),'judge_mean_max':max(avg(by[p][j] for p in packets) for j in judges)})
 write(a.out/'friction_judge_agreement_by_metric.csv',metric_summary);write(a.out/'friction_judge_pairwise_trajectory_correlations.csv',pair_traj);write(a.out/'friction_judge_pairwise_model_rank_correlations.csv',pair_rank)
 report={'schema_version':'careloop.friction_judge_agreement.v0_3','status':'posthoc_formative_secondary_endpoint','bootstrap_unit':'case; all available model trajectories within a sampled case are retained','bootstrap_replicates':a.bootstrap_replicates,'seed':a.seed,'judges':[display[j] for j in judges],'metrics':metric_summary,'interpretation':'ICC(A,1) measures absolute agreement for one Judge; ICC(A,4) measures absolute agreement of the mean of four. Pairwise Spearman metrics describe association/ranking and do not imply absolute calibration agreement.'}
 (a.out/'friction_judge_agreement_summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
