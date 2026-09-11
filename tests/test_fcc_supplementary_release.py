from __future__ import annotations
import csv, json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'public_supplement/cl120_20260911/analysis/friction_v0_2/supplementary'
FIG=ROOT/'public_supplement/cl120_20260911/analysis/friction_v0_2/figures'

def rows(name):
 with (BASE/name).open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))

def test_fcc_supplementary_population_and_audits():
 audit=json.loads((BASE/'FCC_SUPPLEMENTARY_AUDIT.json').read_text())
 rep=json.loads((BASE/'FCC_REPRESENTATIVE_CASE_AUDIT.json').read_text())
 assert audit['result']=='PASS'
 assert audit['source_counts']=={'trajectories':1200,'judge_cells':4800,'cases':120,'tested_models':10,'judges':4}
 assert rep['result']=='PASS'
 assert len(rows('fcc_case_level_summary.csv'))==120
 assert len(rows('fcc_trajectory_review_map.csv'))==1200
 assert len(rows('fcc_model_diagnostic_summary.csv'))==10
 assert len(rows('fcc_judge_self_association_summary.csv'))==6

def test_retained_supplementary_figures_exist_and_obsolete_set_is_absent():
 retained={
  'cl120_sfig_fcc_task_burden_associations.pdf',
  'cl120_sfig_fcc_matched_model_judge_association.pdf',
  'cl120_sfig_fcc_model_diagnostic_map.pdf',
  'cl120_sfig_friction_ho_coverage.pdf',
  'cl120_sfig_judge_specific_capability.pdf',
 }
 assert retained <= {p.name for p in FIG.glob('*.pdf')}
 obsolete={
  'cl120_sfig1_exploratory_complexity_validation.pdf',
  'cl120_sfig2_task_burden_associations.pdf',
  'cl120_sfig3_model_by_task_burden_heatmap.pdf',
  'cl120_sfig4_screening_descriptors.pdf',
  'cl120_sfig5_workspace_behavior.pdf',
  'cl120_sfig6_matched_model_judge_association.pdf',
  'cl120_sfig7_case_review_map.pdf',
  'cl120_sfig8_model_behavior_map.pdf',
 }
 assert not (obsolete & {p.name for p in ROOT.rglob('*.pdf')})

def test_representative_values_use_fcc_crwr_primary():
 by={r['packet_id']:r for r in rows('fcc_trajectory_review_map.csv')}
 expected={
  'case_CL120_EHR_115__anon_5b883f4851415e8d':(1.000,1.000),
  'case_CL120_EHR_014__anon_95e3fe2578d4a64b':(0.895,0.895),
  'case_CL120_EHR_014__anon_609dfb9bc9f404a1':(0.394,0.1379),
  'case_CL120_EHR_061__anon_df81ff06b56e89c9':(0.9291666667,0.336875),
  'case_CL120_EHR_058__anon_bccc18593eaa2fc0':(0.9333333333,0.675),
 }
 for packet,(fcc,crwr) in expected.items():
  assert abs(float(by[packet]['friction_capability_composite_median4'])-fcc)<1e-8
  assert abs(float(by[packet]['careloop_real_world_robustness_median4'])-crwr)<1e-8
