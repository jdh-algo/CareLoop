#!/usr/bin/env python3
"""Sensitivity and heterogeneity analyses for the CL120 friction score v0.2.

All variants operate on the same unmodified canonical Judge findings.  They do
not alter the frozen 1--5 rubric or overwrite canonical Judge files.
"""
from __future__ import annotations
import argparse,csv,json,math,statistics,hashlib
from collections import defaultdict
from pathlib import Path
from careloop.evaluation.friction_score_v0_2 import score_canonical_assessment,DOMAIN_WEIGHTS

DOMAINS=tuple(DOMAIN_WEIGHTS)
POLICIES={"authored_opportunity_zero":0.0,"neutral_not_triggered":0.5,"triggered_only":None}

def read_csv(p):
 with p.open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
def write_csv(p,rows):
 if not rows:raise ValueError(f'no rows: {p}')
 p.parent.mkdir(parents=True,exist_ok=True)
 with p.open('w',encoding='utf-8',newline='') as f:
  w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
def avg(xs):
 xs=list(xs);return sum(xs)/len(xs) if xs else float('nan')
def rank(values,descending=True):
 order=sorted(range(len(values)),key=lambda i:values[i],reverse=descending);out=[0.]*len(values);i=0
 while i<len(order):
  j=i+1
  while j<len(order) and values[order[j]]==values[order[i]]:j+=1
  q=(i+j-1)/2+1
  for k in range(i,j):out[order[k]]=q
  i=j
 return out
def rho(x,y):
 rx=rank(x,False);ry=rank(y,False);mx=avg(rx);my=avg(ry);dx=sum((v-mx)**2 for v in rx);dy=sum((v-my)**2 for v in ry)
 return sum((a-mx)*(b-my) for a,b in zip(rx,ry))/math.sqrt(dx*dy) if dx and dy else float('nan')
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1<<20),b''):h.update(b)
 return h.hexdigest()
def aggregate(rows,field,judges=None,method='median'):
 by=defaultdict(list)
 for r in rows:
  if judges is None or r['judge_model'] in judges:
   v=r[field]
   if v!='':by[r['packet_id']].append(float(v))
 fn=statistics.median if method=='median' else avg
 return {k:fn(v) for k,v in by.items() if v}
def model_table(packet_values,packet_meta):
 by=defaultdict(list)
 for pid,v in packet_values.items():by[packet_meta[pid]['doctor_model']].append(v)
 mods=sorted(by);means=[avg(by[m]) for m in mods];ranks=rank(means)
 return {m:{'mean':means[i],'rank':ranks[i],'n':len(by[m])} for i,m in enumerate(mods)}
def composite_from_domains(r,weights):
 vals={d:float(r['domain_'+d+'_median4']) for d in DOMAINS if r['domain_'+d+'_median4']!=''}
 den=sum(weights[d] for d in vals)
 return sum(weights[d]*vals[d] for d in vals)/den

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--root',type=Path,required=True);ap.add_argument('--analysis-dir',type=Path,required=True);ap.add_argument('--out',type=Path,required=True);a=ap.parse_args()
 root=a.root.resolve();src=a.analysis_dir.resolve();out=a.out.resolve();out.mkdir(parents=True,exist_ok=True)
 meta_rows=read_csv(root/'analysis/canonical_judge_scores_long.csv');meta={(r['packet_id'],r['judge_model']):r for r in meta_rows};packet_meta={r['packet_id']:r for r in meta_rows}
 contracts={}
 for p in (root/'judge_packets').glob('*.judge_packet.json'):
  q=json.loads(p.read_text());contracts[p.name[:-len('.judge_packet.json')]]=q['case_contract']
 canonical=[]
 for p in sorted((root/'judge_results').glob('*/*/canonical.json')):
  q=json.loads(p.read_text());m=meta[(q['packet_id'],q['judge_model'])];q['_meta']=m;canonical.append(q)
 assert len(canonical)==4800
 judges=sorted({q['judge_model'] for q in canonical});mods=sorted({q['_meta']['doctor_model'] for q in canonical})
 # Primary raw table plus three not-triggered policies.
 variant_raw=[]
 for q in canonical:
  c=contracts[q['packet_id']];ho={i['id']:i['source_item']['capability_type'] for i in c['evaluation_contract_v2']['high_order_test_points']}
  z={'packet_id':q['packet_id'],'case_id':q['_meta']['case_id'],'doctor_model':q['_meta']['doctor_model'],'judge_model':q['judge_model'],'primary_friction':c['closure_contract_v2']['coverage_tags']['primary_friction']}
  for name,credit in POLICIES.items():
   try:
    s=score_canonical_assessment(q,ho,not_triggered_credit=credit)
    z[name]=s['friction_capability_composite'];z[name+'_robustness']=s['careloop_real_world_robustness'];z[name+'_applicable_domains']=s['applicable_domain_count']
   except ValueError as e:
    if name!='triggered_only' or str(e)!='no_applicable_friction_domain':raise
    z[name]='';z[name+'_robustness']='';z[name+'_applicable_domains']=0
  variant_raw.append(z)
 # Aggregation and not-triggered strategy sensitivity.
 primary_scores=aggregate(variant_raw,'authored_opportunity_zero',method='median');primary_mt=model_table(primary_scores,packet_meta)
 sensitivity=[]
 for policy in POLICIES:
  for method in ('median','mean'):
   vals=aggregate(variant_raw,policy,method=method);mt=model_table(vals,packet_meta)
   ordered=[m for m in mods]
   for m in mods:
    sensitivity.append({'analysis_family':'not_triggered_and_aggregation','configuration':policy,'aggregation':method,'doctor_model':m,'n_trajectories':mt[m]['n'],'score_mean':mt[m]['mean'],'rank':mt[m]['rank'],'rank_spearman_vs_primary':rho([mt[x]['mean'] for x in ordered],[primary_mt[x]['mean'] for x in ordered])})
 write_csv(out/'friction_sensitivity_not_triggered_and_aggregation.csv',sensitivity)
 # Leave one Judge out using median of remaining three.
 loo=[]
 for omitted in judges:
  keep=set(judges)-{omitted};vals=aggregate(variant_raw,'authored_opportunity_zero',judges=keep,method='median');mt=model_table(vals,packet_meta)
  rr=rho([mt[m]['mean'] for m in mods],[primary_mt[m]['mean'] for m in mods])
  for m in mods:loo.append({'omitted_judge':omitted,'aggregation':'median_of_remaining_three','doctor_model':m,'n_trajectories':mt[m]['n'],'score_mean':mt[m]['mean'],'rank':mt[m]['rank'],'rank_spearman_vs_all_four_primary':rr})
 write_csv(out/'friction_leave_one_judge_out.csv',loo)
 # Judge-specific model summaries and all six pairwise ranking correlations.
 js=[];j_tables={}
 for j in judges:
  vals=aggregate(variant_raw,'authored_opportunity_zero',judges={j},method='mean');mt=model_table(vals,packet_meta);j_tables[j]=mt
  for m in mods:js.append({'judge_model':j,'doctor_model':m,'n_trajectories':mt[m]['n'],'score_mean':mt[m]['mean'],'rank':mt[m]['rank']})
 write_csv(out/'friction_judge_specific_model_rankings.csv',js)
 pairs=[]
 for i,j1 in enumerate(judges):
  for j2 in judges[i+1:]:
   pairs.append({'judge_1':j1,'judge_2':j2,'model_rank_spearman':rho([j_tables[j1][m]['mean'] for m in mods],[j_tables[j2][m]['mean'] for m in mods]),'mean_absolute_rank_difference':avg(abs(j_tables[j1][m]['rank']-j_tables[j2][m]['rank']) for m in mods),'top3_overlap':len(set(sorted(mods,key=lambda m:j_tables[j1][m]['rank'])[:3])&set(sorted(mods,key=lambda m:j_tables[j2][m]['rank'])[:3]))})
 write_csv(out/'friction_pairwise_judge_rank_consistency.csv',pairs)
 # Composite-weight perturbations. Domain scores stay frozen; only summary weights vary.
 traj=read_csv(src/'friction_domains_trajectory.csv')
 weight_sets={'primary':dict(DOMAIN_WEIGHTS),'equal':{d:1/len(DOMAINS) for d in DOMAINS}}
 for d in DOMAINS:
  for factor in (.8,1.2):
   w=dict(DOMAIN_WEIGHTS);w[d]*=factor;s=sum(w.values());w={k:v/s for k,v in w.items()};weight_sets[f'{d}_{factor:.1f}x']=w
 weight_rows=[]
 for name,w in weight_sets.items():
  vals={r['packet_id']:composite_from_domains(r,w) for r in traj};mt=model_table(vals,packet_meta);rr=rho([mt[m]['mean'] for m in mods],[primary_mt[m]['mean'] for m in mods])
  for m in mods:weight_rows.append({'weight_configuration':name,'doctor_model':m,'score_mean':mt[m]['mean'],'rank':mt[m]['rank'],'rank_spearman_vs_primary':rr,**{'weight_'+d:w[d] for d in DOMAINS}})
 write_csv(out/'friction_composite_weight_sensitivity.csv',weight_rows)
 # Leave one friction category out to detect a result driven by one category.
 lfo=[]
 frictions=sorted({r['primary_friction'] for r in traj})
 primary_by_pid={r['packet_id']:float(r['friction_capability_composite_median4']) for r in traj}
 for omitted in frictions:
  vals={pid:v for pid,v in primary_by_pid.items() if packet_meta[pid].get('primary_friction',next(r['primary_friction'] for r in traj if r['packet_id']==pid))!=omitted}
  # canonical metadata lacks primary_friction in some releases; use trajectory lookup explicitly.
  omitted_ids={r['packet_id'] for r in traj if r['primary_friction']==omitted};vals={pid:v for pid,v in primary_by_pid.items() if pid not in omitted_ids};mt=model_table(vals,packet_meta);rr=rho([mt[m]['mean'] for m in mods],[primary_mt[m]['mean'] for m in mods])
  for m in mods:lfo.append({'omitted_primary_friction':omitted,'doctor_model':m,'n_trajectories':mt[m]['n'],'score_mean':mt[m]['mean'],'rank':mt[m]['rank'],'rank_spearman_vs_all_friction_primary':rr})
 write_csv(out/'friction_leave_one_category_out.csv',lfo)
 # Availability by model/domain supports transparent denominators.
 avail=[]
 for m in mods:
  z=[r for r in traj if r['doctor_model']==m]
  for d in DOMAINS:
   q=[r for r in z if r['domain_'+d+'_median4']!=''];avail.append({'doctor_model':m,'domain':d,'n_applicable_cases':len(q),'proportion_of_120':len(q)/120,'mean_if_applicable':avg(float(r['domain_'+d+'_median4']) for r in q)})
 write_csv(out/'friction_domain_denominators_by_model.csv',avail)
 outputs=[]
 for p in sorted(out.glob('*.csv')):outputs.append({'file':p.name,'sha256':sha(p),'bytes':p.stat().st_size})
 summary={'schema_version':'careloop.cl120.friction_domains_robustness.v0_2','status':'posthoc_formative_secondary_endpoint','counts':{'canonical_judge_assessments':len(canonical),'trajectories':len(primary_scores),'models':len(mods),'judges':len(judges),'primary_friction_categories':len(frictions)},'primary_policy':'authored_opportunity_zero','primary_aggregation':'median_of_four','judge_pair_rank_spearman_range':[min(r['model_rank_spearman'] for r in pairs),max(r['model_rank_spearman'] for r in pairs)],'leave_one_judge_rank_spearman_range':[min(r['rank_spearman_vs_all_four_primary'] for r in loo),max(r['rank_spearman_vs_all_four_primary'] for r in loo)],'weight_perturbation_rank_spearman_range':[min(r['rank_spearman_vs_primary'] for r in weight_rows),max(r['rank_spearman_vs_primary'] for r in weight_rows)],'leave_one_friction_rank_spearman_range':[min(r['rank_spearman_vs_all_friction_primary'] for r in lfo),max(r['rank_spearman_vs_all_friction_primary'] for r in lfo)],'outputs':outputs}
 (out/'friction_robustness_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps(summary,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
