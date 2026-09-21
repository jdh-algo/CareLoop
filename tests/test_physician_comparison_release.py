from __future__ import annotations
import csv,json
from collections import Counter,defaultdict
from pathlib import Path
from scripts.audit_physician_comparison_release import audit
ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'public_supplement/cl120_20260911/physician_comparison'

def load_rows():return [json.loads(x) for x in (P/'physician_title_group_reviews.jsonl').open(encoding='utf-8') if x.strip()]

def test_physician_release_audit_passes():
    result=audit(P/'physician_title_group_reviews.jsonl',ROOT,P/'physician_case_panel_assignments.tsv')
    assert result['result']=='PASS'; assert result['fixed_case_panel_count']==120; assert result['unique_five_physician_combinations']==105; assert result['workload_range']==[10,100]

def test_each_case_has_one_fixed_five_physician_panel():
    rows=load_rows(); by_packet=defaultdict(list)
    for r in rows:by_packet[r['packet_id']].append(r)
    by_case=defaultdict(list)
    for pid,rr in by_packet.items():by_case[rr[0]['case_id']].append(pid)
    assert len(by_case)==120
    for cid,pids in by_case.items():
        assert len(pids)==10
        panels={tuple(sorted((r['physician_title_group'],r['reviewer_id']) for r in by_packet[p])) for p in pids}
        assert len(panels)==1
        assert Counter(t for t,rid in next(iter(panels)))==Counter({'Resident physician':2,'Attending physician':2,'Associate-chief/chief physician':1})

def test_case_panel_table_matches_review_records():
    rows=load_rows(); by_case=defaultdict(set)
    for r in rows:by_case[r['case_id']].add((r['physician_title_group'],r['reviewer_id']))
    with (P/'physician_case_panel_assignments.tsv').open(encoding='utf-8',newline='') as f:panel=list(csv.DictReader(f,delimiter='\t'))
    assert len(panel)==600
    tab=defaultdict(set)
    for r in panel:
        tab[r['case_id']].add((r['physician_title_group'],r['reviewer_id'])); assert r['n_tested_models']=='10'; assert r['n_trajectory_assessments']=='10'
    assert by_case==tab

def test_analysis_outputs_are_case_rank_centered():
    report=json.loads((P/'physician_llm_rank_concordance.json').read_text(encoding='utf-8'))
    assert report['schema_version']=='careloop.physician_case_panel_concordance.v5'
    assert report['metadata']['primary_unit'].startswith('case;')
    assert report['assignment_integrity']['fixed_case_panels'] is True
    for name in ['case_level_10model_rank_concordance.tsv','case_level_10model_rank_concordance_summary.tsv','evaluator_pairwise_case_rank_concordance.tsv','evaluator_class_case_rank_concordance.tsv','within_title_case_rank_concordance.tsv','aggregate_10model_rank_concordance.tsv','panel_cluster_bootstrap_sensitivity.tsv']:
        assert (P/name).is_file()
    assert not (P/'evaluator_pairwise_trajectory_concordance.tsv').exists()
    assert not (P/'evaluator_class_concordance_summary.tsv').exists()
