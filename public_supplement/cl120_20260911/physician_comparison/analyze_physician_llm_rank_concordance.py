#!/usr/bin/env python3
"""Reproduce the CL120 physician--LLM Judge comparison.

The study design assigns one fixed five-physician panel to all ten model
trajectories of a case. Primary analyses therefore rank the ten models within
case and summarize case-wise concordance over 120 cases.
"""
from __future__ import annotations
import csv, importlib.util, json, math
from collections import Counter, defaultdict
from pathlib import Path
import numpy as np

HERE=Path(__file__).resolve().parent
ROOT=Path(__file__).resolve().parents[3]
PHYS=HERE/'physician_title_group_reviews.jsonl'
PANEL=HERE/'physician_case_panel_assignments.tsv'
CASES=ROOT/'cases/public_cl120_deidentified_120'
JUDGE_LONG=HERE.parent/'analysis/friction_v0_2/friction_domains_judge_long.csv'
SCORER=ROOT/'careloop/evaluation/friction_score_v0_2.py'
OUT=HERE/'physician_llm_rank_concordance.json'
CASE_DETAIL=HERE/'case_level_10model_rank_concordance.tsv'
CASE_SUMMARY=HERE/'case_level_10model_rank_concordance_summary.tsv'
PAIRWISE=HERE/'evaluator_pairwise_case_rank_concordance.tsv'
CLASS_SUMMARY=HERE/'evaluator_class_case_rank_concordance.tsv'
WITHIN_TITLE=HERE/'within_title_case_rank_concordance.tsv'
AGGREGATE=HERE/'aggregate_10model_rank_concordance.tsv'
WORKLOAD=HERE/'reviewer_workload_summary.tsv'
DOMAIN_SUMMARY=HERE/'clinical_domain_panel_summary.tsv'
PANEL_CLUSTER_SENSITIVITY=HERE/'panel_cluster_bootstrap_sensitivity.tsv'
BOOTSTRAP_REPLICATES=5000
BOOTSTRAP_SEED=20260918
PANEL_BOOTSTRAP_SEED=2026091802
TITLES=['Resident physician','Attending physician','Associate-chief/chief physician']
JUDGES_RAW=['GPT-5.5','gpt-5.6-sol','DeepSeek-V4-Pro','GLM-5']
DISPLAY={'GPT-5.5':'GPT-5.5','gpt-5.6-sol':'GPT-5.6-Sol','DeepSeek-V4-Pro':'DeepSeek-V4-Pro','GLM-5':'GLM-5'}
METRICS=['FCC','C_RWR','information_repair','constraint_navigation','dynamic_reprioritization','execution_loop_repair','state_sensitive_communication','bounded_closure_continuity']
JCOL={'FCC':'friction_capability_composite','C_RWR':'careloop_real_world_robustness','information_repair':'domain_information_repair','constraint_navigation':'domain_constraint_navigation','dynamic_reprioritization':'domain_dynamic_reprioritization','execution_loop_repair':'domain_execution_loop_repair','state_sensitive_communication':'domain_state_sensitive_communication','bounded_closure_continuity':'domain_bounded_closure_continuity'}
RC={'完成':'met','轻或中度错误':'minor_or_moderate_error','严重错误':'serious_error'}
ISSUE={'不适用':'none','做错了（commission）':'commission','该做未做（omission）':'omission'}
HO={'未触发':(False,'not_triggered',False,False),'已触发但未完成':(True,'not_completed',False,False),'部分完成：有主动行动但未见实质影响':(True,'partial',True,False),'完整完成：有主动行动且产生实质影响':(True,'complete',True,True)}
CLOSURE={'有效闭环':'valid_closure','过早闭环':'premature_closure','有效保持开放':'valid_open','可疑开放':'questionable_open','无效或不清楚':'invalid_or_unclear'}
CAT={'不适用':'none','医疗安全':'medical_safety','临床推理':'clinical_reasoning','行动性':'actionability','沟通':'communication','连续性':'continuity','其他':'other'}
SEV={'不适用':'none','关键质量缺陷':'strong_blocker','轻或中度错误':'minor_or_moderate_error','严重错误':'serious_error'}
BOOL={'是':True,'否':False}

def load_module(path):
    spec=importlib.util.spec_from_file_location('careloop_friction',path); mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod
fr=load_module(SCORER); DOMAINS=list(fr.DOMAIN_WEIGHTS); assert METRICS[2:]==DOMAINS

def read_jsonl(path): return [json.loads(x) for x in path.open(encoding='utf-8') if x.strip()]
def canonical(record):
    review=record['structured_review']; rc=[]
    for item in review['责任链RC']:
        status=RC[item['判断']]; rc.append({'rc_id':item['RC编号'],'status':status,'issue_kind':'none' if status=='met' else ISSUE[item['错误类型']],'citations':[],'explanation':''})
    ho=[]
    for item in review['高阶目标HO']:
        triggered,completion,active,impact=HO[item['完成状态']]
        ho.append({'ho_id':item['HO编号'],'triggered':triggered,'completion':completion,'active_model_action':active,'meaningful_trajectory_impact':impact,'citations':[],'explanation':''})
    dimensions={item['维度代码']:item['等级'] for item in review['五维基础判断']}; closure=review['闭环与其他缺陷']; present=BOOL[closure['存在重要非链缺陷']]
    defect={'present':present,'category':CAT[closure['缺陷类别']] if present else 'none','severity':SEV[closure['严重度']] if present else 'none','issue_kind':ISSUE[closure['错误类型']] if present else 'none','citations':[],'explanation':''}
    serious=any(x['status']=='serious_error' for x in rc) or (present and defect['severity']=='serious_error')
    minor=any(x['status']=='minor_or_moderate_error' for x in rc) or (present and defect['severity']=='minor_or_moderate_error')
    return {'responsibility_chain_assessment':rc,'dimension_ratings':dimensions,'dimension_citations':{k:[] for k in dimensions},'high_order_assessment':ho,'closure_assessment':{'label':CLOSURE[closure['闭环判断']],'safe':BOOL[closure['安全']],'executable':BOOL[closure['可执行']],'traceable':BOOL[closure['可追踪']],'citations':[],'explanation':''},'important_non_chain_defect_assessment':defect,'derived_gate_flags':{'serious_error_present':serious,'minor_or_moderate_error_present':minor},'brief_rationale':''}
def score(result,caps):
    s=fr.score_canonical_assessment(result,caps)
    return {'FCC':float(s['friction_capability_composite']),'C_RWR':float(s['careloop_real_world_robustness']),**{d:float(s['domain_scores'].get(d,math.nan)) for d in DOMAINS}}
def ranks(values):
    values=np.asarray(values,float); order=np.argsort(values,kind='mergesort'); out=np.empty(len(values)); i=0
    while i<len(values):
        j=i+1
        while j<len(values) and values[order[j]]==values[order[i]]: j+=1
        out[order[i:j]]=(i+1+j)/2; i=j
    return out
def rho(left,right):
    left,right=np.asarray(left,float),np.asarray(right,float); keep=np.isfinite(left)&np.isfinite(right)
    if keep.sum()<3:return math.nan
    lr,rr=ranks(left[keep]),ranks(right[keep])
    if np.std(lr)==0 or np.std(rr)==0:return math.nan
    return float(np.corrcoef(lr,rr)[0,1])
def finite(values): return np.asarray([x for x in values if math.isfinite(x)],float)
def ci(values):
    x=finite(values); return [float(np.quantile(x,.025)),float(np.quantile(x,.975))] if len(x) else [math.nan,math.nan]
def summarize(values,rng):
    x=finite(values)
    if not len(x): return {'n_cases_total':len(values),'n_cases_defined':0}
    boot=[float(np.median(x[rng.integers(0,len(x),len(x))])) for _ in range(BOOTSTRAP_REPLICATES)]; low,high=ci(boot)
    return {'n_cases_total':len(values),'n_cases_defined':len(x),'mean_rho':float(x.mean()),'median_rho':float(np.median(x)),'median_case_bootstrap_ci_low':low,'median_case_bootstrap_ci_high':high,'q1_rho':float(np.quantile(x,.25)),'q3_rho':float(np.quantile(x,.75)),'rho_ge_0_5_rate':float(np.mean(x>=.5)),'rho_ge_0_7_rate':float(np.mean(x>=.7)),'negative_rho_rate':float(np.mean(x<0))}
def top_membership_weights(values,k):
    """Expected top-k membership under uniform random resolution of cutoff ties."""
    x=np.asarray(values,float); valid=np.flatnonzero(np.isfinite(x))
    if len(valid)<k:return None
    threshold=np.sort(x[valid])[::-1][k-1]
    above=valid[x[valid]>threshold]; tied=valid[x[valid]==threshold]
    remaining=k-len(above)
    weights=np.zeros(len(x),float); weights[above]=1.0
    if len(tied): weights[tied]=remaining/len(tied)
    return weights
def overlap(left,right,k):
    if k==1:
        a=np.asarray(left,float); b=np.asarray(right,float)
        if not np.isfinite(a).any() or not np.isfinite(b).any(): return math.nan
        aset=set(np.flatnonzero(a==np.nanmax(a)).tolist()); bset=set(np.flatnonzero(b==np.nanmax(b)).tolist())
        return float(bool(aset & bset))
    a,b=top_membership_weights(left,k),top_membership_weights(right,k)
    return float(np.dot(a,b)/k) if a is not None and b is not None else math.nan
def med(values):
    x=finite(values); return float(np.median(x)) if len(x) else math.nan
def sanitize(v):
    if isinstance(v,dict):return {k:sanitize(x) for k,x in v.items()}
    if isinstance(v,list):return [sanitize(x) for x in v]
    if isinstance(v,float) and not math.isfinite(v):return None
    if isinstance(v,np.generic):return sanitize(v.item())
    return v
records=read_jsonl(PHYS); assert len(records)==6000 and len({r['review_id'] for r in records})==6000
by_packet=defaultdict(list); reviewer_meta={}; workload=Counter()
for r in records:
    by_packet[r['packet_id']].append(r); m=(r['physician_title_group'],r['clinical_domain_code'])
    assert r['reviewer_id'] not in reviewer_meta or reviewer_meta[r['reviewer_id']]==m
    reviewer_meta[r['reviewer_id']]=m; workload[r['reviewer_id']]+=1
expected=Counter({'Resident physician':2,'Attending physician':2,'Associate-chief/chief physician':1})
for pid,rr in by_packet.items():
    assert len(rr)==5 and len({x['reviewer_id'] for x in rr})==5 and Counter(x['physician_title_group'] for x in rr)==expected
assert len(by_packet)==1200 and len(reviewer_meta)==139
assert Counter(t for t,d in reviewer_meta.values())==Counter({'Resident physician':64,'Attending physician':51,'Associate-chief/chief physician':24})
case_packets=defaultdict(list)
for pid,rr in by_packet.items():case_packets[rr[0]['case_id']].append(pid)
for cid,pids in case_packets.items():
    assert len(pids)==10
    panels={tuple(sorted((r['physician_title_group'],r['reviewer_id']) for r in by_packet[p])) for p in pids}
    assert len(panels)==1,cid
packet_meta={pid:{'case_id':rr[0]['case_id'],'tested_model':rr[0]['tested_model'],'clinical_domain_code':rr[0]['clinical_domain_code']} for pid,rr in by_packet.items()}
packet_ids=sorted(packet_meta); cases=sorted(case_packets); models=sorted({m['tested_model'] for m in packet_meta.values()})
assert len(cases)==120 and len(models)==10
case_panel_key={cid:tuple(sorted({r['reviewer_id'] for pid in case_packets[cid] for r in by_packet[pid]})) for cid in cases}
panel_cases=defaultdict(list)
for cid in cases: panel_cases[case_panel_key[cid]].append(cid)
assert len(panel_cases)==105
case_model_packet={(m['case_id'],m['tested_model']):pid for pid,m in packet_meta.items()}; assert len(case_model_packet)==1200
case_capabilities={}
for case_path in sorted(CASES.glob('case_CL120_EHR_*.json')):
    case=json.loads(case_path.read_text(encoding='utf-8'))
    points=case['evaluation_contract_v2']['high_order_test_points']
    case_capabilities[case['case_id']]={str(point['id']):str(point['source_item']['capability_type']) for point in points}
assert len(case_capabilities)==120
caps={pid:case_capabilities[packet_meta[pid]['case_id']] for pid in packet_ids}
phys_scores={}; by_packet_title=defaultdict(list); by_packet_reviewer={}
for r in records:
    s=score(canonical(r),caps[r['packet_id']]); phys_scores[r['review_id']]=s
    by_packet_title[r['packet_id'],r['physician_title_group']].append(s); by_packet_reviewer[r['packet_id'],r['reviewer_id']]=s
def title_value(pid,title,metric):return med([x[metric] for x in by_packet_title[pid,title]])
def consensus(pid,metric):return med([title_value(pid,t,metric) for t in TITLES])
def all_five_median(pid,metric):return med([phys_scores[r['review_id']][metric] for r in by_packet[pid]])
def all_five_mean(pid,metric):
    x=finite([phys_scores[r['review_id']][metric] for r in by_packet[pid]]); return float(x.mean()) if len(x) else math.nan
judge_scores={}
with JUDGE_LONG.open(encoding='utf-8',newline='') as f:
    for r in csv.DictReader(f):judge_scores[r['packet_id'],r['judge_model']]={m:(float(r[c]) if r[c] else math.nan) for m,c in JCOL.items()}
def judge_value(pid,judge,metric):return judge_scores[pid,judge][metric]
def judge_median(pid,metric):return med([judge_value(pid,j,metric) for j in JUDGES_RAW])
assert len(judge_scores)==4800

rng=np.random.default_rng(BOOTSTRAP_SEED)
panel_rng=np.random.default_rng(PANEL_BOOTSTRAP_SEED)
def panel_cluster_ci(values):
    value_by_case=dict(zip(cases,values)); clusters=list(panel_cases)
    boot=[]
    for _ in range(BOOTSTRAP_REPLICATES):
        sampled=panel_rng.integers(0,len(clusters),len(clusters)); vals=[]
        for idx in sampled:
            vals.extend(value_by_case[cid] for cid in panel_cases[clusters[idx]] if math.isfinite(value_by_case[cid]))
        if vals: boot.append(float(np.median(vals)))
    return ci(boot)
comparisons=[(DISPLAY[j],lambda pid,m,j=j:judge_value(pid,j,m)) for j in JUDGES_RAW]+[('Four-judge median',judge_median)]
case_detail=[]; case_summary=[]
for metric in METRICS:
    for label,right_fn in comparisons:
        rhos=[]; top1=[]; top3=[]
        for cid in cases:
            pids=[case_model_packet[cid,m] for m in models]; left=[consensus(p,metric) for p in pids]; right=[right_fn(p,metric) for p in pids]
            cr=rho(left,right); o1=overlap(left,right,1); o3=overlap(left,right,3)
            rhos.append(cr); top1.append(o1); top3.append(o3)
            case_detail.append({'metric':metric,'comparison':label,'case_id':cid,'spearman_rho':cr,'top1_overlap':o1,'tie_aware_top3_overlap_fraction':o3})
        s=summarize(rhos,rng); pcl,pch=panel_cluster_ci(rhos); s.update({'metric':metric,'comparison':label,'median_panel_cluster_bootstrap_ci_low':pcl,'median_panel_cluster_bootstrap_ci_high':pch,'top1_overlap_rate':float(np.nanmean(top1)),'mean_tie_aware_top3_overlap_fraction':float(np.nanmean(top3))}); case_summary.append(s)

evaluator_order=TITLES+[DISPLAY[j] for j in JUDGES_RAW]
def evaluator_value(pid,evaluator,metric):
    if evaluator in TITLES:return title_value(pid,evaluator,metric)
    raw=next(k for k,v in DISPLAY.items() if v==evaluator); return judge_value(pid,raw,metric)
pairwise_rows=[]; class_rows=[]
for metric in METRICS:
    pair_case={}
    for i,left in enumerate(evaluator_order):
        for right in evaluator_order[i+1:]:
            vals=[]
            for cid in cases:
                pids=[case_model_packet[cid,m] for m in models]
                vals.append(rho([evaluator_value(p,left,metric) for p in pids],[evaluator_value(p,right,metric) for p in pids]))
            pair_case[left,right]=vals; s=summarize(vals,rng)
            pair_class='physician_title_vs_physician_title' if left in TITLES and right in TITLES else ('physician_title_vs_llm_judge' if left in TITLES or right in TITLES else 'llm_judge_vs_llm_judge')
            pairwise_rows.append({'metric':metric,'evaluator_1':left,'evaluator_2':right,'pair_class':pair_class,**s})
    physician_pairs=[(TITLES[i],TITLES[j]) for i in range(3) for j in range(i+1,3)]
    groups={'Physician title strata':physician_pairs}
    for judge in [DISPLAY[j] for j in JUDGES_RAW]: groups[judge]=[(t,judge) for t in TITLES]
    per_group={}
    for name,pairs in groups.items():
        vals=[]
        for idx,cid in enumerate(cases):
            vals.append(med([pair_case[p][idx] if p in pair_case else pair_case[(p[1],p[0])][idx] for p in pairs]))
        per_group[name]=vals; row={'metric':metric,'comparison_group':name,'pair_count_per_case':3,**summarize(vals,rng)}
        if name!='Physician title strata':
            dif=[a-b for a,b in zip(vals,per_group['Physician title strata']) if math.isfinite(a) and math.isfinite(b)]
            boot=[float(np.median(np.asarray(dif)[rng.integers(0,len(dif),len(dif))])) for _ in range(BOOTSTRAP_REPLICATES)]; low,high=ci(boot)
            row.update({'median_difference_from_physician_internal':float(np.median(dif)),'difference_bootstrap_ci_low':low,'difference_bootstrap_ci_high':high,'n_cases_for_difference':len(dif)})
        class_rows.append(row)

within_title_rows=[]
for metric in METRICS:
    for title in TITLES[:2]:
        vals=[]
        for cid in cases:
            ids=sorted({r['reviewer_id'] for pid in case_packets[cid] for r in by_packet[pid] if r['physician_title_group']==title}); assert len(ids)==2
            pids=[case_model_packet[cid,m] for m in models]
            vals.append(rho([by_packet_reviewer[p,ids[0]][metric] for p in pids],[by_packet_reviewer[p,ids[1]][metric] for p in pids]))
        within_title_rows.append({'metric':metric,'physician_title_group':title,**summarize(vals,rng)})
aggregate_rows=[]
for metric in METRICS:
    pmean=[float(np.nanmean([consensus(case_model_packet[c,m],metric) for c in cases])) for m in models]
    for label,right_fn in comparisons:
        jmean=[float(np.nanmean([right_fn(case_model_packet[c,m],metric) for c in cases])) for m in models]
        aggregate_rows.append({'metric':metric,'comparison':label,'n_models':10,'spearman_rho':rho(pmean,jmean)})
def sensitivity_stat(metric,left_fn):
    vals=[]
    for cid in cases:
        pids=[case_model_packet[cid,m] for m in models]
        vals.append(rho([left_fn(p,metric) for p in pids],[judge_median(p,metric) for p in pids]))
    return summarize(vals,rng)
sensitivity={}
for metric in METRICS:
    sensitivity[metric]={'equal_title_median':sensitivity_stat(metric,consensus),'all_five_median':sensitivity_stat(metric,all_five_median),'all_five_mean':sensitivity_stat(metric,all_five_mean),'resident_attending_only':sensitivity_stat(metric,lambda p,m:med([title_value(p,TITLES[0],m),title_value(p,TITLES[1],m)]))}
with PANEL.open(encoding='utf-8',newline='') as f:panel_rows=list(csv.DictReader(f,delimiter='\t'))
assert len(panel_rows)==600
panel_work=Counter(); panel_meta={}
for r in panel_rows:
    panel_work[r['reviewer_id']]+=int(r['n_trajectory_assessments']); panel_meta[r['reviewer_id']]=(r['physician_title_group'],r['clinical_domain_code'])
assert panel_work==workload
workload_rows=[]
for title in ['All physicians']+TITLES:
    values=[n for rid,n in panel_work.items() if title=='All physicians' or panel_meta[rid][0]==title]
    workload_rows.append({'physician_title_group':title,'n_reviewers':len(values),'minimum':min(values),'median':float(np.median(values)),'mean':float(np.mean(values)),'maximum':max(values)})
domain_rows=[]
for domain in sorted({r['clinical_domain_code'] for r in panel_rows}):
    dr=[r for r in panel_rows if r['clinical_domain_code']==domain]; reviewers={r['reviewer_id']:r['physician_title_group'] for r in dr}
    domain_rows.append({'clinical_domain_code':domain,'n_cases':len({r['case_id'] for r in dr}),'n_physicians':len(reviewers),'resident_physicians':sum(v==TITLES[0] for v in reviewers.values()),'attending_physicians':sum(v==TITLES[1] for v in reviewers.values()),'associate_chief_or_chief_physicians':sum(v==TITLES[2] for v in reviewers.values()),'n_reviews':sum(int(r['n_trajectory_assessments']) for r in dr)})
report={'schema_version':'careloop.physician_case_panel_concordance.v5','analysis_date':'2026-09-19','metadata':{'primary_unit':'case; ten tested-model trajectories ranked within each case','assignment_design':'the same fixed panel of two residents, two attendings, and one associate-chief/chief physician reviewed all ten trajectories for a case','n_cases':120,'n_trajectories':1200,'n_physician_assessments':6000,'n_physicians':139,'n_unique_five_physician_combinations':len(panel_cases),'primary_statistics':'case-wise Spearman rank correlation, summarized across 120 cases; case bootstrap for primary confidence intervals with exact-panel cluster bootstrap as a dependence sensitivity analysis','aggregation':'median within resident and attending strata, single senior assessment, then equal-title median for physician consensus','interpretation':'descriptive concordance; physicians are an independent clinical comparison panel, not an error-free gold standard','bootstrap_replicates':BOOTSTRAP_REPLICATES,'bootstrap_seed':BOOTSTRAP_SEED},'assignment_integrity':{'fixed_case_panels':True,'case_count':120,'trajectory_count':1200,'review_count':6000,'physician_count':139,'unique_five_physician_combinations':len(panel_cases),'physicians_by_title':dict(Counter(t for t,d in reviewer_meta.values())),'workload_range':[min(workload.values()),max(workload.values())]},'physician_consensus_vs_judge_case_rank_summary':{m:{r['comparison']:{k:v for k,v in r.items() if k not in ('metric','comparison')} for r in case_summary if r['metric']==m} for m in METRICS},'evaluator_pairwise_case_rank_summary':pairwise_rows,'evaluator_class_case_rank_summary':class_rows,'within_title_case_rank_summary':within_title_rows,'aggregate_10model_rank_summary':aggregate_rows,'aggregation_sensitivity_case_rank':sensitivity}
OUT.write_text(json.dumps(sanitize(report),ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
def write_tsv(path,rows,fields):
    with path.open('w',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields,delimiter='\t',extrasaction='ignore'); w.writeheader()
        for row in rows:w.writerow({k:('' if isinstance(row.get(k),float) and not math.isfinite(row[k]) else row.get(k,'')) for k in fields})
write_tsv(CASE_DETAIL,case_detail,['metric','comparison','case_id','spearman_rho','top1_overlap','tie_aware_top3_overlap_fraction'])
write_tsv(CASE_SUMMARY,case_summary,['metric','comparison','n_cases_total','n_cases_defined','mean_rho','median_rho','median_case_bootstrap_ci_low','median_case_bootstrap_ci_high','median_panel_cluster_bootstrap_ci_low','median_panel_cluster_bootstrap_ci_high','q1_rho','q3_rho','rho_ge_0_5_rate','rho_ge_0_7_rate','negative_rho_rate','top1_overlap_rate','mean_tie_aware_top3_overlap_fraction'])
write_tsv(PAIRWISE,pairwise_rows,['metric','evaluator_1','evaluator_2','pair_class','n_cases_total','n_cases_defined','mean_rho','median_rho','median_case_bootstrap_ci_low','median_case_bootstrap_ci_high','q1_rho','q3_rho','rho_ge_0_5_rate','rho_ge_0_7_rate','negative_rho_rate'])
write_tsv(CLASS_SUMMARY,class_rows,['metric','comparison_group','pair_count_per_case','n_cases_total','n_cases_defined','mean_rho','median_rho','median_case_bootstrap_ci_low','median_case_bootstrap_ci_high','q1_rho','q3_rho','rho_ge_0_5_rate','rho_ge_0_7_rate','negative_rho_rate','median_difference_from_physician_internal','difference_bootstrap_ci_low','difference_bootstrap_ci_high','n_cases_for_difference'])
write_tsv(WITHIN_TITLE,within_title_rows,['metric','physician_title_group','n_cases_total','n_cases_defined','mean_rho','median_rho','median_case_bootstrap_ci_low','median_case_bootstrap_ci_high','q1_rho','q3_rho','rho_ge_0_5_rate','rho_ge_0_7_rate','negative_rho_rate'])
write_tsv(AGGREGATE,aggregate_rows,['metric','comparison','n_models','spearman_rho'])
write_tsv(WORKLOAD,workload_rows,['physician_title_group','n_reviewers','minimum','median','mean','maximum'])
write_tsv(DOMAIN_SUMMARY,domain_rows,list(domain_rows[0]))
panel_sensitivity=[{k:r[k] for k in ['metric','comparison','n_cases_defined','median_rho','median_case_bootstrap_ci_low','median_case_bootstrap_ci_high','median_panel_cluster_bootstrap_ci_low','median_panel_cluster_bootstrap_ci_high']} for r in case_summary if r['metric'] in ('FCC','C_RWR')]
write_tsv(PANEL_CLUSTER_SENSITIVITY,panel_sensitivity,list(panel_sensitivity[0]))
print(OUT)
