#!/usr/bin/env python3
"""Verify manuscript representative cases against released CL120 artifacts."""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
import pandas as pd

SELECTED = {
 "positive_delayed_result": "case_CL120_EHR_115__anon_5b883f4851415e8d",
 "same_case_high": "case_CL120_EHR_014__anon_95e3fe2578d4a64b",
 "same_case_low": "case_CL120_EHR_014__anon_609dfb9bc9f404a1",
 "high_fcc_safety_penalty": "case_CL120_EHR_061__anon_df81ff06b56e89c9",
 "verified_stronger_doctor_omission": "case_CL120_EHR_058__anon_bccc18593eaa2fc0",
}
JUDGES=("DeepSeek-V4-Pro","GLM-5","GPT-5.5","gpt-5.6-sol")
EXPECTED={
 "positive_delayed_result": {"fcc":1.0,"crwr":1.0,"serious":0,"minor":0},
 "same_case_high": {"fcc":0.895,"crwr":0.895,"serious":0,"minor":0},
 "same_case_low": {"fcc":0.394,"crwr":0.1379,"serious":3,"minor":3},
 "high_fcc_safety_penalty": {"fcc":0.9291666667,"crwr":0.336875,"serious":3,"minor":1},
 "verified_stronger_doctor_omission": {"fcc":0.9333333333,"crwr":0.675,"serious":1,"minor":1},
}
def sha(p):
 h=hashlib.sha256();h.update(p.read_bytes());return h.hexdigest()
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--root',type=Path,required=True);ap.add_argument('--out',type=Path,required=True);a=ap.parse_args();root=a.root.resolve();out=a.out.resolve();out.parent.mkdir(parents=True,exist_ok=True)
 d=pd.read_csv(root/'analysis/friction_v0_2/friction_domains_trajectory.csv').set_index('packet_id')
 records=[]
 for label,pid in SELECTED.items():
  assert pid in d.index
  r=d.loc[pid];serious=minor=0;judge_rows=[]
  for judge in JUDGES:
   p=root/'judge_results'/judge/pid/'canonical.json';q=json.loads(p.read_text())
   flags=q['derived_gate_flags'];serious+=int(flags['serious_error_present']);minor+=int(flags['minor_or_moderate_error_present'])
   judge_rows.append({'judge_model':judge,'trajectory_grade':q['trajectory_grade'],'serious_error_present':flags['serious_error_present'],'minor_or_moderate_error_present':flags['minor_or_moderate_error_present'],'canonical_sha256':sha(p)})
  exp=EXPECTED[label];fcc=float(r['friction_capability_composite_median4']);crwr=float(r['careloop_real_world_robustness_median4'])
  assert abs(fcc-exp['fcc'])<1e-8,(label,fcc);assert abs(crwr-exp['crwr'])<1e-8,(label,crwr);assert serious==exp['serious'];assert minor==exp['minor']
  packet=root/'judge_packets'/f'{pid}.judge_packet.json';assert packet.exists()
  records.append({'label':label,'packet_id':pid,'case_id':r['case_id'],'doctor_model':r['doctor_model'],'primary_friction':r['primary_friction'],'fcc':fcc,'crwr':crwr,'clinical_error_gate_median4':float(r['clinical_error_gate_median4']),'ordinal_comparator_median4':float(r['ordinal_grade_median4']),'serious_error_judge_count':serious,'minor_or_moderate_error_judge_count':minor,'packet_sha256':sha(packet),'judges':judge_rows})
 audit={'schema_version':'careloop.cl120.fcc_representative_case_audit.v1','selection_policy':'post-analysis explanatory selection; no scores or rankings modified','counts':{'selected_roles':len(records),'unique_packets':len(set(SELECTED.values()))},'records':records,'result':'PASS'}
 out.write_text(json.dumps(audit,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps({'result':'PASS','output':str(out),'selected_roles':len(records)},indent=2))
if __name__=='__main__':main()
