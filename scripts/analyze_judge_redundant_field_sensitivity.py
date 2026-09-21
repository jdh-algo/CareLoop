#!/usr/bin/env python3
"""Sensitivity analysis for redundant HO and closure fields in released Judge records."""
from __future__ import annotations
import argparse, copy, csv, json, math
from collections import defaultdict
from pathlib import Path
import numpy as np
from careloop.evaluation.friction_score_v0_2 import score_canonical_assessment

HO_STATE={
 'not_triggered':(False,False,False),
 'not_completed':(True,False,False),
 'partial':(True,True,False),
 'complete':(True,True,True),
}

def ranks(v):
 x=np.asarray(v,float); o=np.argsort(x,kind='mergesort'); r=np.empty(len(x)); i=0
 while i<len(x):
  j=i+1
  while j<len(x) and x[o[j]]==x[o[i]]:j+=1
  r[o[i:j]]=(i+1+j)/2;i=j
 return r

def rho(a,b):
 return float(np.corrcoef(ranks(a),ranks(b))[0,1])

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--root',type=Path,required=True);ap.add_argument('--out',type=Path,required=True);a=ap.parse_args();root=a.root.resolve()
 cases={}
 for p in sorted((root.parents[1]/'cases/public_cl120_deidentified_120').glob('case_CL120_EHR_*.json')):
  d=json.loads(p.read_text(encoding='utf-8'));cases[d['case_id']]={str(x['id']):str(x['source_item']['capability_type']) for x in d['evaluation_contract_v2']['high_order_test_points']}
 records=[json.loads(x) for x in (root/'judge_results/canonical_judge_results.jsonl').read_text(encoding='utf-8').splitlines() if x.strip()]
 variants=defaultdict(lambda:defaultdict(list));counts={'judge_cells':len(records),'ho_item_state_differences':0,'ho_cells_with_difference':0,'closure_cells_with_difference':0}
 ho_cells=set()
 for r in records:
  base=score_canonical_assessment(r,cases[r['case_id']]);
  for metric,key in [('FCC','friction_capability_composite'),('C_RWR','careloop_real_world_robustness')]:variants['reported'][r['doctor_model'],metric].append(base[key])
  n=copy.deepcopy(r)
  for h in n['high_order_assessment']:
   expected=HO_STATE[h['completion']];actual=(h.get('triggered'),h.get('active_model_action'),h.get('meaningful_trajectory_impact'))
   if actual!=expected:
    counts['ho_item_state_differences']+=1;ho_cells.add((r['packet_id'],r['judge_model']))
   h['triggered'],h['active_model_action'],h['meaningful_trajectory_impact']=expected
  cl=n['closure_assessment'];all_true=all(cl.get(k) is True for k in ('safe','executable','traceable'))
  label=cl.get('label');valid=label in ('valid_closure','valid_open')
  if valid!=all_true:counts['closure_cells_with_difference']+=1
  if all_true: cl['label']='valid_closure' if r.get('terminal_status')=='closed_success' else 'valid_open'
  elif label in ('valid_closure','valid_open'): cl['label']='premature_closure' if r.get('terminal_status')=='closed_success' else 'questionable_open'
  alt=score_canonical_assessment(n,cases[r['case_id']])
  for metric,key in [('FCC','friction_capability_composite'),('C_RWR','careloop_real_world_robustness')]:variants['normalized_redundant_fields'][r['doctor_model'],metric].append(alt[key])
 counts['ho_cells_with_difference']=len(ho_cells)
 models=sorted({m for v in variants.values() for m,_ in v})
 rows=[]
 for metric in ('FCC','C_RWR'):
  base={m:float(np.mean(variants['reported'][m,metric])) for m in models}; alt={m:float(np.mean(variants['normalized_redundant_fields'][m,metric])) for m in models}
  rr=rho([base[m] for m in models],[alt[m] for m in models])
  for m in models: rows.append({'metric':metric,'model':m,'reported_mean':base[m],'normalized_mean':alt[m],'difference':alt[m]-base[m],'model_rank_spearman':rr})
 a.out.parent.mkdir(parents=True,exist_ok=True)
 with a.out.open('w',encoding='utf-8',newline='') as f:
  w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
 summary=a.out.with_suffix('.json');summary.write_text(json.dumps({'schema_version':'careloop.judge_redundant_field_sensitivity.v1','counts':counts,'model_rank_spearman':{metric:rows[0 if metric=='FCC' else len(models)]['model_rank_spearman'] for metric in ('FCC','C_RWR')}},indent=2)+'\n')
 print(summary.read_text())
if __name__=='__main__':main()
