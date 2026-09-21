#!/usr/bin/env python3
from __future__ import annotations
import argparse,csv,json,math,statistics
from collections import defaultdict
from pathlib import Path

D={'unsafe':0,'weak':.25,'partial':.5,'good':.75,'excellent':1}
L={'valid_closure':1,'valid_open':1,'questionable_open':.4,'premature_closure':0,'invalid_or_unclear':0}
CONFIGS={
 'primary_authored':({'ho':.55,'adapt':.20,'cont':.10,'closure':.15},'zero'),
 'ho_heavy_authored':({'ho':.70,'adapt':.15,'cont':.05,'closure':.10},'zero'),
 'balanced_authored':({'ho':.40,'adapt':.25,'cont':.15,'closure':.20},'zero'),
 'primary_neutral_not_triggered':({'ho':.55,'adapt':.20,'cont':.10,'closure':.15},'neutral'),
 'primary_triggered_only':({'ho':.55,'adapt':.20,'cont':.10,'closure':.15},'triggered'),
}
def read(path):
 with path.open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
def avg(x):return sum(x)/len(x)
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
 return sum((a-mx)*(b-my) for a,b in zip(x,y))/math.sqrt(dx*dy)
def hcredit(h,mode):
 c=h['completion']
 if c=='not_triggered':return .5 if mode=='neutral' else None if mode=='triggered' else 0
 if c=='not_completed':return 0
 if c=='partial':return .5
 return 1 if h['active_model_action'] and h['meaningful_trajectory_impact'] else .75
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--root',type=Path,required=True);ap.add_argument('--out',type=Path,required=True);a=ap.parse_args();a.out.mkdir(parents=True,exist_ok=True)
 meta={(r['packet_id'],r['judge_model']):r for r in read(a.root/'analysis/canonical_judge_scores_long.csv')}
 raw=[]
 for p in a.root.glob('judge_results/*/*/canonical.json'):
  x=json.load(p.open());m=meta[(x['packet_id'],x['judge_model'])];cl=x['closure_assessment'];closure=.4*L[cl['label']]+.2*sum(float(cl[k]) for k in ['safe','executable','traceable'])
  base={'packet_id':x['packet_id'],'case_id':m['case_id'],'doctor_model':m['doctor_model'],'judge_model':x['judge_model'],'ordinal':int(x['trajectory_grade'])}
  for name,(w,mode) in CONFIGS.items():
   hs=[hcredit(h,mode) for h in x['high_order_assessment']];valid=[v for v in hs if v is not None];H=avg(valid) if valid else 0
   base[name]=w['ho']*H+w['adapt']*D[x['dimension_ratings']['patient_family_realworld_adaptation']]+w['cont']*D[x['dimension_ratings']['continuity_memory_focus']]+w['closure']*closure
  raw.append(base)
 by=defaultdict(list)
 for r in raw:by[r['packet_id']].append(r)
 traj=[]
 for pid,rs in by.items():
  z={k:rs[0][k] for k in ['packet_id','case_id','doctor_model']};z['ordinal']=statistics.median(r['ordinal'] for r in rs)
  for name in CONFIGS:z[name+'_median4']=statistics.median(r[name] for r in rs);z[name+'_mean4']=avg([r[name] for r in rs])
  traj.append(z)
 mods=sorted({r['doctor_model'] for r in traj});primary=[];summary=[]
 for name in CONFIGS:
  for agg in ['median4','mean4']:
   f=name+'_'+agg;means={m:avg([r[f] for r in traj if r['doctor_model']==m]) for m in mods};ords={m:avg([r['ordinal'] for r in traj if r['doctor_model']==m]) for m in mods}
   summary.append({'configuration':name,'aggregation':agg,'trajectory_rho_ordinal':rho([r[f] for r in traj],[r['ordinal'] for r in traj]),'model_rank_rho_ordinal':rho([means[m] for m in mods],[ords[m] for m in mods])})
   for m in mods:primary.append({'configuration':name,'aggregation':agg,'doctor_model':m,'score_mean':means[m],'ordinal_mean':ords[m]})
 for fn,rows in [('friction_score_sensitivity_summary.csv',summary),('friction_score_sensitivity_models.csv',primary)]:
  with (a.out/fn).open('w',encoding='utf-8',newline='') as f:w=csv.DictWriter(f,fieldnames=rows[0]);w.writeheader();w.writerows(rows)
 print(json.dumps(summary,indent=2))
if __name__=='__main__':main()
