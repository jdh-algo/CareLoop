#!/usr/bin/env python3
from __future__ import annotations
import argparse,csv,hashlib,json,math,random,statistics
from collections import defaultdict
from pathlib import Path
from typing import Any
from careloop.evaluation.friction_score_v0_2 import score_canonical_assessment,DOMAIN_WEIGHTS

SCHEMA='careloop.cl120.friction_domains.v0_2_formative'
DOMAINS=tuple(DOMAIN_WEIGHTS)
FRICTIONS=("caregiver_conflict_or_fatigue","cost_transport_or_work_barrier","delayed_or_staged_result_return","document_or_image_quality_problem","external_system_gap","incomplete_patient_information","low_adherence_or_refusal","medication_confusion_or_execution_failure","misstated_family_or_patient_information","privacy_stigma_or_sensitive_history")
def read_csv(p):
 with p.open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
def write_csv(p,rows):
 if not rows:raise ValueError(p)
 p.parent.mkdir(parents=True,exist_ok=True)
 with p.open('w',encoding='utf-8',newline='') as f:w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
def avg(x):x=list(x);return sum(x)/len(x) if x else float('nan')
def pct(v,p):
 v=sorted(v);x=(len(v)-1)*p;i=int(x);f=x-i;return v[i]*(1-f)+v[min(i+1,len(v)-1)]*f
def rank(v):
 o=sorted(range(len(v)),key=lambda i:v[i]);r=[0.]*len(v);i=0
 while i<len(o):
  j=i+1
  while j<len(o) and v[o[j]]==v[o[i]]:j+=1
  q=(i+j-1)/2+1
  for k in range(i,j):r[o[k]]=q
  i=j
 return r
def rho(x,y):
 x=rank(x);y=rank(y);mx=avg(x);my=avg(y);dx=sum((v-mx)**2 for v in x);dy=sum((v-my)**2 for v in y)
 return sum((a-mx)*(b-my) for a,b in zip(x,y))/math.sqrt(dx*dy) if dx and dy else float('nan')
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1<<20),b''):h.update(b)
 return h.hexdigest()
def bootstrap_matrix(rows,field,reps,seed,subset=None):
 z=[r for r in rows if (subset(r) if subset else True) and r[field] != ''];mods=sorted({r['doctor_model'] for r in z});cases=sorted({r['case_id'] for r in z});lookup={(r['doctor_model'],r['case_id']):float(r[field]) for r in z}
 # applicable cases must exist for every model
 cases=[c for c in cases if all((m,c) in lookup for m in mods)]
 rng=random.Random(seed);draws={m:[] for m in mods};ranks={m:[] for m in mods}
 for _ in range(reps):
  sample=[cases[rng.randrange(len(cases))] for _ in cases];vals={m:avg(lookup[(m,c)] for c in sample) for m in mods};rr=rank([-vals[m] for m in mods])
  for m,q in zip(mods,rr):draws[m].append(vals[m]);ranks[m].append(q)
 return {m:{'mean':avg(lookup[(m,c)] for c in cases),'ci95_low':pct(draws[m],.025),'ci95_high':pct(draws[m],.975),'rank':rank([-avg(lookup[(x,c)] for c in cases) for x in mods])[mods.index(m)],'rank_ci_low':pct(ranks[m],.025),'rank_ci_high':pct(ranks[m],.975),'n_cases':len(cases)} for m in mods}
def main():
 a=argparse.ArgumentParser();a.add_argument('--root',type=Path,required=True);a.add_argument('--out',type=Path,required=True);a.add_argument('--bootstrap-replicates',type=int,default=10000);a.add_argument('--seed',type=int,default=20260911);x=a.parse_args();root=x.root.resolve();out=x.out.resolve();out.mkdir(parents=True,exist_ok=True)
 long=read_csv(root/'analysis/canonical_judge_scores_long.csv');meta={(r['packet_id'],r['judge_model']):r for r in long};contracts={}
 for p in (root/'judge_packets').glob('*.judge_packet.json'):
  q=json.load(p.open());contracts[p.name[:-len('.judge_packet.json')]]=q['case_contract']
 raw=[]
 for p in sorted((root/'judge_results').glob('*/*/canonical.json')):
  q=json.load(p.open());m=meta[(q['packet_id'],q['judge_model'])];c=contracts[q['packet_id']];ho={i['id']:i['source_item']['capability_type'] for i in c['evaluation_contract_v2']['high_order_test_points']};s=score_canonical_assessment(q,ho);cov=c['closure_contract_v2']['coverage_tags']
  r={'packet_id':q['packet_id'],'case_id':m['case_id'],'doctor_model':m['doctor_model'],'judge_model':q['judge_model'],'primary_friction':cov['primary_friction'],'friction_intensity':c['real_world_friction_design']['friction_intensity'],'ordinal_grade':int(q['trajectory_grade']),'applicable_domain_count':s['applicable_domain_count'],'friction_capability_composite':s['friction_capability_composite'],'clinical_error_gate':s['clinical_error_gate'],'careloop_real_world_robustness':s['careloop_real_world_robustness'],'closure_process_score':s['closure_process_score']}
  for d in DOMAINS:r['domain_'+d]=s['domain_scores'].get(d,'')
  raw.append(r)
 assert len(raw)==4800
 write_csv(out/'friction_domains_judge_long.csv',raw)
 by=defaultdict(list)
 for r in raw:by[r['packet_id']].append(r)
 traj=[]
 for pid,rs in sorted(by.items()):
  assert len(rs)==4
  r={k:rs[0][k] for k in ['packet_id','case_id','doctor_model','primary_friction','friction_intensity']}
  for f in ['ordinal_grade','applicable_domain_count','friction_capability_composite','clinical_error_gate','careloop_real_world_robustness','closure_process_score']+['domain_'+d for d in DOMAINS]:
   vals=[float(z[f]) for z in rs if z[f] != ''];r[f+'_median4']=statistics.median(vals) if vals else '';r[f+'_mean4']=avg(vals) if vals else ''
  traj.append(r)
 assert len(traj)==1200
 write_csv(out/'friction_domains_trajectory.csv',traj)
 mods=sorted({r['doctor_model'] for r in traj});main_boot={f:bootstrap_matrix(traj,f, x.bootstrap_replicates,x.seed+i) for i,f in enumerate(['friction_capability_composite_median4','careloop_real_world_robustness_median4','ordinal_grade_median4'])}
 domain_boot={d:bootstrap_matrix(traj,'domain_'+d+'_median4',x.bootstrap_replicates,x.seed+20+i) for i,d in enumerate(DOMAINS)}
 model=[]
 for m in mods:
  z={'doctor_model':m,'n_cases':120}
  for f,b in main_boot.items():
   for k,v in b[m].items():z[f+'_'+k]=v
  for d,b in domain_boot.items():
   for k in ['mean','ci95_low','ci95_high','n_cases']:z['domain_'+d+'_'+k]=b[m][k]
  model.append(z)
 model.sort(key=lambda r:-r['friction_capability_composite_median4_mean']);write_csv(out/'friction_domains_model_summary.csv',model)
 # category model table
 cat=[]
 for i,fr in enumerate(FRICTIONS):
  b=bootstrap_matrix(traj,'friction_capability_composite_median4',x.bootstrap_replicates,x.seed+100+i,lambda r,fr=fr:r['primary_friction']==fr)
  br=bootstrap_matrix(traj,'careloop_real_world_robustness_median4',x.bootstrap_replicates,x.seed+120+i,lambda r,fr=fr:r['primary_friction']==fr)
  for m in mods:cat.append({'doctor_model':m,'primary_friction':fr,'n_cases':b[m]['n_cases'],'friction_capability_mean':b[m]['mean'],'friction_capability_ci95_low':b[m]['ci95_low'],'friction_capability_ci95_high':b[m]['ci95_high'],'robustness_mean':br[m]['mean'],'robustness_ci95_low':br[m]['ci95_low'],'robustness_ci95_high':br[m]['ci95_high']})
 write_csv(out/'friction_domains_by_primary_friction.csv',cat)
 # Equal-model summaries for each authored primary-friction stratum.  Resample
 # case IDs so the ten tested models remain paired within each case.
 rng=random.Random(x.seed+500);fr_summary=[]
 for fr in FRICTIONS:
  z=[r for r in traj if r['primary_friction']==fr];cases=sorted({r['case_id'] for r in z});lookup={(r['doctor_model'],r['case_id']):float(r['friction_capability_composite_median4']) for r in z};rlookup={(r['doctor_model'],r['case_id']):float(r['careloop_real_world_robustness_median4']) for r in z}
  draws=[];rdraws=[]
  for _ in range(x.bootstrap_replicates):
   sample=[cases[rng.randrange(len(cases))] for _ in cases];draws.append(avg(lookup[(m,c)] for c in sample for m in mods));rdraws.append(avg(rlookup[(m,c)] for c in sample for m in mods))
  fr_summary.append({'primary_friction':fr,'n_cases':len(cases),'n_model_case_trajectories':len(z),'equal_model_friction_capability_mean':avg(float(r['friction_capability_composite_median4']) for r in z),'ci95_low':pct(draws,.025),'ci95_high':pct(draws,.975),'equal_model_robustness_mean':avg(float(r['careloop_real_world_robustness_median4']) for r in z),'robustness_ci95_low':pct(rdraws,.025),'robustness_ci95_high':pct(rdraws,.975)})
 write_csv(out/'friction_primary_friction_summary.csv',fr_summary)
 overall=[]
 for d in DOMAINS:
  z=[r for r in traj if r['domain_'+d+'_median4']!=''];overall.append({'domain':d,'n_trajectories':len(z),'n_unique_cases':len({r['case_id'] for r in z}),'equal_trajectory_mean':avg(float(r['domain_'+d+'_median4']) for r in z)})
 write_csv(out/'friction_domain_overall_summary.csv',overall)
 # judge severity
 judges=[]
 for j in sorted({r['judge_model'] for r in raw}):
  z=[r for r in raw if r['judge_model']==j];q={'judge_model':j,'n':len(z),'friction_capability_composite_mean':avg(float(r['friction_capability_composite']) for r in z),'careloop_real_world_robustness_mean':avg(float(r['careloop_real_world_robustness']) for r in z),'ordinal_grade_mean':avg(float(r['ordinal_grade']) for r in z)}
  for d in DOMAINS:q['domain_'+d+'_mean']=avg(float(r['domain_'+d]) for r in z if r['domain_'+d]!='')
  judges.append(q)
 write_csv(out/'friction_domains_judge_summary.csv',judges)
 jf=[]
 for j in sorted({r['judge_model'] for r in raw}):
  for fr in FRICTIONS:
   z=[r for r in raw if r['judge_model']==j and r['primary_friction']==fr];jf.append({'judge_model':j,'primary_friction':fr,'n':len(z),'friction_capability_composite_mean':avg(float(r['friction_capability_composite']) for r in z),'careloop_real_world_robustness_mean':avg(float(r['careloop_real_world_robustness']) for r in z)})
 write_csv(out/'friction_primary_friction_by_judge.csv',jf)
 # within ordinal median band
 within=[]
 for g in sorted({float(r['ordinal_grade_median4']) for r in traj}):
  z=[r for r in traj if float(r['ordinal_grade_median4'])==g];within.append({'ordinal_grade_median4':g,'n':len(z),'friction_capability_mean':avg(float(r['friction_capability_composite_median4']) for r in z),'friction_capability_sd':statistics.pstdev(float(r['friction_capability_composite_median4']) for r in z),'friction_capability_min':min(float(r['friction_capability_composite_median4']) for r in z),'friction_capability_max':max(float(r['friction_capability_composite_median4']) for r in z)})
 write_csv(out/'friction_domains_within_ordinal.csv',within)
 mf=[r['friction_capability_composite_median4_mean'] for r in model];mo=[r['ordinal_grade_median4_mean'] for r in model]
 summary={'schema_version':SCHEMA,'status':'posthoc_formative_secondary_endpoint','counts':{'judge_assessments':len(raw),'trajectories':len(traj),'cases':120,'models':10,'judges':4},'bootstrap_replicates':x.bootstrap_replicates,'seed':x.seed,'model_rank_spearman_new_vs_ordinal':rho(mf,mo),'trajectory_spearman_new_vs_ordinal':rho([float(r['friction_capability_composite_median4']) for r in traj],[float(r['ordinal_grade_median4']) for r in traj]),'model_results':[{k:r[k] for k in ('doctor_model','friction_capability_composite_median4_mean','friction_capability_composite_median4_ci95_low','friction_capability_composite_median4_ci95_high','careloop_real_world_robustness_median4_mean','ordinal_grade_median4_mean')} for r in model],'primary_friction_results':fr_summary,'judge_results':judges,'config_sha256':sha(Path(__file__).resolve().parents[1]/'configs/careloop_friction_score_v0_2.json'),'outputs':[]}
 for p in sorted(out.glob('*.csv')):summary['outputs'].append({'file':p.name,'sha256':sha(p),'bytes':p.stat().st_size})
 (out/'friction_domains_analysis_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n');print(json.dumps(summary,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
