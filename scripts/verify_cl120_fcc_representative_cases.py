#!/usr/bin/env python3
"""Rebuild and verify the released FCC/C-RWR representative-record audit."""
from __future__ import annotations
import argparse, csv, json
from collections import defaultdict
from pathlib import Path

LABELS = {
    'positive_delayed_result': 'case_CL120_EHR_115__anon_5b883f4851415e8d',
    'same_case_high': 'case_CL120_EHR_014__anon_95e3fe2578d4a64b',
    'same_case_low': 'case_CL120_EHR_014__anon_609dfb9bc9f404a1',
    'high_fcc_safety_penalty': 'case_CL120_EHR_061__anon_df81ff06b56e89c9',
    'stronger_doctor_verified_omission': 'case_CL120_EHR_058__anon_bccc18593eaa2fc0',
}

def read_csv(path: Path):
    with path.open(encoding='utf-8', newline='') as f: return list(csv.DictReader(f))

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--root',type=Path,required=True)
    ap.add_argument('--out',type=Path,required=True)
    a=ap.parse_args(); root=a.root.resolve()
    traj={r['packet_id']:r for r in read_csv(root/'analysis/friction_v0_2/friction_domains_trajectory.csv')}
    judge=defaultdict(list)
    with (root/'judge_results/canonical_judge_results.jsonl').open(encoding='utf-8') as f:
        for line in f:
            if line.strip():
                r=json.loads(line); judge[r['packet_id']].append(r)
    records=[]
    for label,pid in LABELS.items():
        if pid not in traj or len(judge[pid])!=4: raise RuntimeError(f'missing_representative_record:{label}:{pid}')
        t=traj[pid]; js=sorted(judge[pid],key=lambda r:r['judge_model'])
        records.append({
            'label':label,'packet_id':pid,'case_id':t['case_id'],'doctor_model':t['doctor_model'],
            'primary_friction':t['primary_friction'],
            'fcc':float(t['friction_capability_composite_median4']),
            'crwr':float(t['careloop_real_world_robustness_median4']),
            'clinical_error_gate_median4':float(t['clinical_error_gate_median4']),
            'ordinal_comparator_median4':float(t['ordinal_grade_median4']),
            'serious_error_judge_count':sum(bool(r['derived_gate_flags']['serious_error_present']) for r in js),
            'minor_or_moderate_error_judge_count':sum(bool(r['derived_gate_flags']['minor_or_moderate_error_present']) for r in js),
            'judges':[{'judge_model':r['judge_model'],'trajectory_grade':int(r['trajectory_grade']),
                       'serious_error_present':bool(r['derived_gate_flags']['serious_error_present']),
                       'minor_or_moderate_error_present':bool(r['derived_gate_flags']['minor_or_moderate_error_present'])} for r in js],
        })
    result={'result':'PASS','schema_version':'careloop.cl120.fcc_representative_case_audit.v1',
            'selection_policy':'explanatory selection from the completed FCC/C-RWR analysis; no scores or rankings modified',
            'counts':{'selected_roles':len(records),'unique_packets':len({r['packet_id'] for r in records})},
            'records':records}
    a.out.parent.mkdir(parents=True,exist_ok=True)
    a.out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'result':'PASS','records':len(records)},indent=2))
if __name__=='__main__': main()
