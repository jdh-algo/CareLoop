#!/usr/bin/env python3
"""Cross-check friction-centered manuscript claims against released outputs."""
from __future__ import annotations
import argparse,csv,json,re,hashlib
from pathlib import Path

def read(p):
 with p.open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--analysis-dir',type=Path,required=True);ap.add_argument('--manuscript-dir',type=Path,required=True);ap.add_argument('--out',type=Path,required=True);a=ap.parse_args()
 src=a.analysis_dir.resolve();ms=a.manuscript_dir.resolve();text='\n'.join(p.read_text() for p in sorted((ms/'sections').glob('*.tex')))
 model=read(src/'friction_domains_model_summary.csv');by={r['doctor_model']:r for r in model};summary=json.loads((src/'friction_domains_analysis_summary.json').read_text());rob=json.loads((src/'robustness/friction_robustness_summary.json').read_text());agreement=read(src/'agreement/friction_judge_agreement_by_metric.csv');ag={r['metric']:r for r in agreement}
 supp=src/'supplementary';burden=read(supp/'fcc_contract_burden_associations.csv');ba={(r['predictor'],r['outcome']):r for r in burden};workspace=read(supp/'fcc_workspace_associations.csv');wa={(r['analysis'],r['outcome']):r for r in workspace};selfa=read(supp/'fcc_judge_self_association_summary.csv');sa={(r['metric'],r['judge_model_id']):r for r in selfa};review=read(supp/'fcc_trajectory_review_map.csv');rv={r['packet_id']:r for r in review}
 checks=[]
 def ck(name,expected,needle):checks.append({'check':name,'expected':expected,'needle':needle,'passed':needle in text})
 ck('judge_count',4,'Four model-blinded LLM Judges');ck('assessment_count',4800,'4,800');ck('trajectory_count',1200,'1,200 trajectories')
 ck('gpt56_fcc',float(by['GPT-5.6 Sol']['friction_capability_composite_median4_mean']),'0.824');ck('gpt56_fcc_ci_low',float(by['GPT-5.6 Sol']['friction_capability_composite_median4_ci95_low']),'0.799');ck('gpt56_fcc_ci_high',float(by['GPT-5.6 Sol']['friction_capability_composite_median4_ci95_high']),'0.849')
 ck('trajectory_rho',summary['trajectory_spearman_new_vs_ordinal'],'0.572');ck('model_rank_rho',summary['model_rank_spearman_new_vs_ordinal'],'0.924');ck('loo_low',rob['leave_one_judge_rank_spearman_range'][0],'0.939');ck('loo_high',rob['leave_one_judge_rank_spearman_range'][1],'0.988');ck('category_low',rob['leave_one_friction_rank_spearman_range'][0],'0.964');ck('weight_exact',rob['weight_perturbation_rank_spearman_range'],'1.000')
 ck('fcc_icc_a1',float(ag['friction_capability_composite']['icc_a1']),'0.257');ck('fcc_icc_a4',float(ag['friction_capability_composite']['icc_a4']),'0.581');ck('crwr_icc_a1',float(ag['careloop_real_world_robustness']['icc_a1']),'0.211');ck('crwr_icc_a4',float(ag['careloop_real_world_robustness']['icc_a4']),'0.517');ck('fcc_rank_rho_low',float(ag['friction_capability_composite']['pairwise_model_rank_spearman_min']),'0.697');ck('fcc_rank_rho_high',float(ag['friction_capability_composite']['pairwise_model_rank_spearman_max']),'0.879')
 # Revised Supplementary claims.
 for pred,out,needle in [
  ('friction_burden_score','mean_turns','0.288'),('temporal_burden_score','mean_turns','0.337'),('contract_complexity_index','mean_fcc','0.099'),('contract_complexity_index','mean_crwr','-0.005'),('contract_complexity_index','mean_turns','0.243')]:
  ck('burden:'+pred+':'+out,float(ba[(pred,out)]['spearman_rho']),needle)
 for analysis,out,needle in [
  ('unadjusted_trajectory_level','friction_capability_composite_median4','0.063'),('unadjusted_trajectory_level','careloop_real_world_robustness_median4','-0.022'),('two_way_model_and_case_residual','friction_capability_composite_median4','0.110'),('two_way_model_and_case_residual','careloop_real_world_robustness_median4','0.048')]:
  ck('workspace:'+analysis+':'+out,float(wa[(analysis,out)]['spearman_rho']),needle)
 for metric,judge,needle in [
  ('friction_capability_composite','DeepSeek-V4-Pro','+0.007'),('friction_capability_composite','GLM-5','$-0.022$'),('friction_capability_composite','gpt-5.6-sol','+0.085'),('careloop_real_world_robustness','DeepSeek-V4-Pro','+0.021'),('careloop_real_world_robustness','GLM-5','$-0.010$'),('careloop_real_world_robustness','gpt-5.6-sol','+0.215')]:
  ck('self_association:'+metric+':'+judge,float(sa[(metric,judge)]['calibration_adjusted_case_stratified_self_association_effect']),needle)
 for pid,fcc,crwr,needle in [
  ('case_CL120_EHR_115__anon_5b883f4851415e8d','1.000','1.000','FCC=1.000 and C-RWR=1.000'),
  ('case_CL120_EHR_014__anon_95e3fe2578d4a64b','0.895','0.895','FCC/C-RWR=0.895/0.895'),
  ('case_CL120_EHR_014__anon_609dfb9bc9f404a1','0.394','0.138','0.394/0.138'),
  ('case_CL120_EHR_061__anon_df81ff06b56e89c9','0.929','0.337','FCC=0.929, C-RWR=0.337'),
  ('case_CL120_EHR_058__anon_bccc18593eaa2fc0','0.933','0.675','FCC=0.933, C-RWR=0.675')]:
  assert pid in rv
  checks.append({'check':'representative_values:'+pid,'expected':[float(rv[pid]['friction_capability_composite_median4']),float(rv[pid]['careloop_real_world_robustness_median4'])],'needle':needle,'passed':needle in text})
 for fig in re.findall(r'\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}',text):checks.append({'check':'figure_exists:'+fig,'expected':True,'needle':fig,'passed':(ms/fig).exists()})
 forbidden=['primary friction endpoint','validated friction score','clinician-validated friction','friction gold standard','fig:supp-complexity-validation','cl120_sfig1_exploratory_complexity_validation','cl120_sfig4_screening_descriptors','cl120_sfig7_case_review_map','cl120_sfig8_model_behavior_map']
 for term in forbidden:checks.append({'check':'forbidden_claim_or_reference_absent:'+term,'expected':False,'needle':term,'passed':term.lower() not in text.lower()})
 report={'schema_version':'careloop.manuscript_friction_value_audit.v0_4_fcc_supplementary','passed':all(x['passed'] for x in checks),'checks':checks,'sources':{'model_summary_sha256':sha(src/'friction_domains_model_summary.csv'),'analysis_summary_sha256':sha(src/'friction_domains_analysis_summary.json'),'robustness_summary_sha256':sha(src/'robustness/friction_robustness_summary.json'),'agreement_summary_sha256':sha(src/'agreement/friction_judge_agreement_by_metric.csv'),'supplementary_audit_sha256':sha(supp/'FCC_SUPPLEMENTARY_AUDIT.json'),'representative_case_audit_sha256':sha(supp/'FCC_REPRESENTATIVE_CASE_AUDIT.json')},'manuscript_tex_files':len(list((ms/'sections').glob('*.tex')))}
 a.out.parent.mkdir(parents=True,exist_ok=True);a.out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'passed':report['passed'],'n_checks':len(checks),'failed':[x['check'] for x in checks if not x['passed']]},indent=2))
 if not report['passed']:raise SystemExit(1)
if __name__=='__main__':main()
