#!/usr/bin/env python3
"""Fail-closed audit for the released CL120 physician case-panel data."""
from __future__ import annotations
import argparse,csv,json,re
from collections import Counter,defaultdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
DEFAULT_CASES=Path('cases/public_cl120_deidentified_120')
DEFAULT_TRAJECTORIES=Path('public_supplement/cl120_20260911/trajectories/by_model')
DEFAULT_DATA=Path('public_supplement/cl120_20260911/physician_comparison/physician_title_group_reviews.jsonl')
DEFAULT_PANEL=Path('public_supplement/cl120_20260911/physician_comparison/physician_case_panel_assignments.tsv')
DEFAULT_CASE_SUMMARY=Path('public_supplement/cl120_20260911/physician_comparison/case_level_10model_rank_concordance_summary.tsv')
DEFAULT_PANEL_SENSITIVITY=Path('public_supplement/cl120_20260911/physician_comparison/panel_cluster_bootstrap_sensitivity.tsv')
TITLE_COUNTS=Counter({'Resident physician':2,'Attending physician':2,'Associate-chief/chief physician':1})
PHYSICIANS_BY_TITLE=Counter({'Resident physician':64,'Attending physician':51,'Associate-chief/chief physician':24})
ALLOWED_DEFECT_SEVERITY={'不适用','关键质量缺陷','轻或中度错误','严重错误'}
ID_RE=re.compile(r'^PHY-[0-9A-F]{10}$')
FORBIDDEN={'姓名','医生姓名','身份证号','手机号','联系电话','邮箱','医院名称','机构名称','physician_name','institution_name','email','phone','grade','score_1_5','physician_grade'}

def jsonl(path):return [json.loads(x) for x in path.open(encoding='utf-8') if x.strip()]
def domains(root):
    out={}
    for p in sorted((root/DEFAULT_CASES).glob('case_CL120_EHR_*.json')):
        x=json.loads(p.read_text(encoding='utf-8')); ds={x['hidden_simulation_state']['root_truth']['disease_domain'],x['closure_contract_v2']['coverage_tags']['disease_domain'],x['case_taxonomy']['disease_domain']}
        if len(ds)!=1:raise ValueError(f'case_domain_disagreement:{p.name}')
        out[x['case_id']]=next(iter(ds))
    return out

def audit(path,root=ROOT,panel_path=None):
    panel_path=panel_path or root/DEFAULT_PANEL; rows=jsonl(path); errors=[]; by_packet=defaultdict(list); reviewer_meta={}; workload=Counter(); case_domains=domains(root); logic=[]; closure_bad=[]
    if len(rows)!=6000:errors.append(f'record_count:{len(rows)}!=6000')
    if len({r.get('review_id') for r in rows})!=len(rows):errors.append('review_id_not_unique')
    for i,r in enumerate(rows,1):
        if FORBIDDEN & set(r):errors.append(f'row_{i}_forbidden_keys')
        rid=str(r.get('reviewer_id',''))
        if not ID_RE.fullmatch(rid):errors.append(f'row_{i}_invalid_reviewer_id')
        pid=r.get('packet_id'); cid=r.get('case_id')
        if not pid:errors.append(f'row_{i}_missing_packet_id');continue
        by_packet[pid].append(r); workload[rid]+=1
        if cid not in case_domains:errors.append(f'row_{i}_unknown_case_id:{cid}')
        elif r.get('clinical_domain_code')!=case_domains[cid]:errors.append(f'row_{i}_clinical_domain_mismatch:{cid}')
        meta=(r.get('physician_title_group'),r.get('clinical_domain_code'))
        if rid in reviewer_meta and reviewer_meta[rid]!=meta:errors.append(f'reviewer_metadata_changed:{rid}')
        reviewer_meta[rid]=meta
        review=r.get('structured_review',{}); cl=review.get('闭环与其他缺陷',{})
        valid=cl.get('闭环判断') in {'有效闭环','有效保持开放'}; fully=all(cl.get(k)=='是' for k in ('安全','可执行','可追踪'))
        if valid!=fully:closure_bad.append(r.get('review_id'))
        for item in review.get('责任链RC',[]):
            if (item.get('判断')=='完成')!=(item.get('错误类型')=='不适用'):logic.append(f"{r.get('review_id')}:RC:{item.get('RC编号')}")
        present=cl.get('存在重要非链缺陷')=='是'; none=all(cl.get(k)=='不适用' for k in ('缺陷类别','严重度','错误类型'))
        if cl.get('严重度') not in ALLOWED_DEFECT_SEVERITY: errors.append(f"row_{i}_invalid_defect_severity")
        if present==none:logic.append(f"{r.get('review_id')}:defect")
    if len(by_packet)!=1200:errors.append(f'trajectory_count:{len(by_packet)}!=1200')
    case_packets=defaultdict(list)
    for pid,rr in by_packet.items():
        if len(rr)!=5:errors.append(f'{pid}:review_count:{len(rr)}!=5')
        if len({r['reviewer_id'] for r in rr})!=5:errors.append(f'{pid}:reviewers_not_distinct')
        if Counter(r['physician_title_group'] for r in rr)!=TITLE_COUNTS:errors.append(f'{pid}:title_composition')
        case_packets[rr[0]['case_id']].append(pid)
    fixed_panel_fail=[]
    for cid,pids in case_packets.items():
        if len(pids)!=10:errors.append(f'{cid}:trajectory_count:{len(pids)}!=10')
        panels={tuple(sorted((r['physician_title_group'],r['reviewer_id']) for r in by_packet[p])) for p in pids}
        if len(panels)!=1:fixed_panel_fail.append(cid)
    if fixed_panel_fail:errors.extend(f'case_panel_not_fixed:{c}' for c in fixed_panel_fail)
    unique_panels={tuple(sorted((r['physician_title_group'],r['reviewer_id']) for r in by_packet[pids[0]])) for cid,pids in case_packets.items()}
    if len(unique_panels)!=105:errors.append(f'unique_five_physician_combinations:{len(unique_panels)}!=105')
    if not panel_path.exists():errors.append('case_panel_assignment_file_missing');panel=[]
    else:
        with panel_path.open(encoding='utf-8',newline='') as f:panel=list(csv.DictReader(f,delimiter='\t'))
        if len(panel)!=600:errors.append(f'panel_row_count:{len(panel)}!=600')
        panel_by_case=defaultdict(list); panel_work=Counter()
        for r in panel:
            panel_by_case[r['case_id']].append((r['physician_title_group'],r['reviewer_id']));panel_work[r['reviewer_id']]+=int(r['n_trajectory_assessments'])
            if r['clinical_domain_code']!=case_domains.get(r['case_id']):errors.append(f"panel_domain_mismatch:{r['case_id']}")
        for cid,pids in case_packets.items():
            observed=tuple(sorted((r['physician_title_group'],r['reviewer_id']) for r in by_packet[pids[0]])); expected=tuple(sorted(panel_by_case[cid]))
            if observed!=expected:errors.append(f'panel_file_mismatch:{cid}')
        if panel_work!=workload:errors.append('panel_workload_mismatch')
    title_counts=Counter(t for t,d in reviewer_meta.values())
    if title_counts!=PHYSICIANS_BY_TITLE:errors.append(f'physicians_by_title:{dict(title_counts)}')
    if len(reviewer_meta)!=139:errors.append(f'physician_count:{len(reviewer_meta)}!=139')
    if len({d for t,d in reviewer_meta.values()})!=13:errors.append('clinical_domain_count_not_13')
    if (min(workload.values()),max(workload.values()))!=(10,100):errors.append(f'workload_range:{min(workload.values())}-{max(workload.values())}')
    summary_path=root/DEFAULT_CASE_SUMMARY; sensitivity_path=root/DEFAULT_PANEL_SENSITIVITY
    if not summary_path.exists(): errors.append('case_rank_summary_missing')
    else:
        with summary_path.open(encoding='utf-8',newline='') as f: sr=list(csv.DictReader(f,delimiter='\t'))
        required={'mean_tie_aware_top3_overlap_fraction','median_panel_cluster_bootstrap_ci_low','median_panel_cluster_bootstrap_ci_high'}
        if len(sr)!=40 or not required.issubset(sr[0] if sr else {}): errors.append('case_rank_summary_schema_or_count')
    if not sensitivity_path.exists(): errors.append('panel_cluster_sensitivity_missing')
    else:
        with sensitivity_path.open(encoding='utf-8',newline='') as f: ps=list(csv.DictReader(f,delimiter='\t'))
        if len(ps)!=10: errors.append(f'panel_cluster_sensitivity_rows:{len(ps)}!=10')
    if closure_bad:errors.extend('closure_semantic_inconsistency:'+str(x) for x in closure_bad)
    if logic:errors.extend('structured_logic_inconsistency:'+x for x in logic)
    trajectory_bad=[]
    trajectory_files=sorted((root/DEFAULT_TRAJECTORIES).glob('*/case_CL120_EHR_*.json'))
    if len(trajectory_files)!=1200:errors.append(f'trajectory_file_count:{len(trajectory_files)}!=1200')
    for p in trajectory_files:
        x=json.loads(p.read_text(encoding='utf-8')); cid=x.get('case_id')
        if cid not in case_domains:trajectory_bad.append(p.relative_to(root).as_posix())
    errors.extend('trajectory_unknown_case:'+x for x in trajectory_bad)
    return {'schema_version':'careloop.physician_comparison_release_audit.v6','data_file':path.relative_to(root).as_posix(),'panel_assignment_file':panel_path.relative_to(root).as_posix(),'record_count':len(rows),'trajectory_count':len(by_packet),'case_count':len(case_packets),'fixed_case_panel_count':len(case_packets)-len(fixed_panel_fail),'unique_five_physician_combinations':len(unique_panels),'physician_count':len(reviewer_meta),'physicians_by_title':dict(title_counts),'clinical_domain_count':len({d for t,d in reviewer_meta.values()}),'workload_range':[min(workload.values()),max(workload.values())],'trajectory_case_reference_mismatch_count':len(trajectory_bad),'closure_semantic_inconsistency_count':len(closure_bad),'structured_logic_inconsistency_count':len(logic),'finding_count':len(errors),'findings':errors,'result':'PASS' if not errors else 'FAIL'}
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--root',type=Path,default=ROOT);ap.add_argument('--data',type=Path,default=DEFAULT_DATA);ap.add_argument('--panel',type=Path,default=DEFAULT_PANEL);a=ap.parse_args();root=a.root.resolve();data=a.data if a.data.is_absolute() else root/a.data;panel=a.panel if a.panel.is_absolute() else root/a.panel
    r=audit(data,root,panel);print(json.dumps(r,ensure_ascii=False,indent=2,sort_keys=True));return 0 if r['result']=='PASS' else 1
if __name__=='__main__':raise SystemExit(main())
