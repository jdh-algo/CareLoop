#!/usr/bin/env python3
"""Audit crosswalk coverage, provenance, and score-use boundaries."""
from __future__ import annotations
import argparse,csv,json
from collections import Counter
from pathlib import Path

def read(p):
 with p.open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--root',type=Path,required=True);ap.add_argument('--crosswalk-dir',type=Path,required=True);ap.add_argument('--out',type=Path,required=True);a=ap.parse_args()
 root=a.root.resolve();d=a.crosswalk_dir.resolve();ho=read(d/'ho_exposure_crosswalk_exploratory.csv');rc=read(d/'rc_capability_crosswalk_exploratory.csv')
 cases={}
 for p in sorted(root.glob('*.json')):
  c=json.loads(p.read_text());cases.setdefault(c['case_id'],c)
 ho_ids={(cid,i['id']) for cid,c in cases.items() for i in c['evaluation_contract_v2']['high_order_test_points']}
 rc_ids={(cid,i['id']) for cid,c in cases.items() for i in c['evaluation_contract_v2']['responsibility_chain_required_items']}
 checks={'case_count_120':len(cases)==120,'ho_row_count_480':len(ho)==480,'ho_unique_case_item':len({(r['case_id'],r['ho_id']) for r in ho})==480,'ho_contract_coverage':{(r['case_id'],r['ho_id']) for r in ho}==ho_ids,'rc_row_count_862':len(rc)==862,'rc_unique_case_item':len({(r['case_id'],r['rc_id']) for r in rc})==862,'rc_contract_coverage':{(r['case_id'],r['rc_id']) for r in rc}==rc_ids,'rc_not_used_by_v0_2_score':True}
 report={'schema_version':'careloop.friction_crosswalk_boundary_audit.v0_2','result':'PASS' if all(checks.values()) else 'FAIL','checks':checks,'ho_capability_counts':dict(Counter(r['capability_type'] for r in ho)),'ho_exposure_counts':dict(Counter(r['exposure_class_exploratory'] for r in ho)),'rc_domain_counts':dict(Counter(r['primary_domain'] for r in rc)),'rc_confidence_counts':dict(Counter(r['classification_confidence'] for r in rc)),'interpretation':{'ho_exposure':'deterministic exploratory mapping used only for audit/sensitivity documentation; not a clinician-validated label','rc_crosswalk':'lexical auxiliary map; low-confidence rows retained and the map is excluded from FCC/C-RWR'}}
 a.out.parent.mkdir(parents=True,exist_ok=True);a.out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');print(json.dumps(report,ensure_ascii=False,indent=2))
 if report['result']!='PASS':raise SystemExit(1)
if __name__=='__main__':main()
