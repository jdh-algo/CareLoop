from careloop.evaluation.friction_score_v0_2 import score_canonical_assessment

def result():
 return {'dimension_ratings':{'medical_safety_risk_recognition':'good','clinical_reasoning_direction':'good','actionability_responsibility_chain':'good','patient_family_realworld_adaptation':'excellent','continuity_memory_focus':'good'},
 'high_order_assessment':[{'ho_id':'HO1','triggered':True,'completion':'complete','active_model_action':True,'meaningful_trajectory_impact':True},{'ho_id':'HO2','triggered':False,'completion':'not_triggered','active_model_action':False,'meaningful_trajectory_impact':False}],
 'closure_assessment':{'label':'valid_closure','safe':True,'executable':True,'traceable':True},'derived_gate_flags':{'serious_error_present':False,'minor_or_moderate_error_present':False}}
def test_domains_and_gate():
 x=score_canonical_assessment(result(),{'HO1':'real_world_constraint_navigation','HO2':'safe_bounded_closure'})
 assert set(x['domain_scores'])=={'constraint_navigation','bounded_closure_continuity'}
 assert x['clinical_error_gate']==1
 assert x['careloop_real_world_robustness']==x['friction_capability_composite']
def test_serious_gate():
 r=result();r['derived_gate_flags']['serious_error_present']=True
 x=score_canonical_assessment(r,{'HO1':'real_world_constraint_navigation','HO2':'safe_bounded_closure'})
 assert abs(x['careloop_real_world_robustness']-.35*x['friction_capability_composite'])<1e-12

def test_not_triggered_sensitivity_policies():
 r=result()
 zero=score_canonical_assessment(r,{'HO1':'real_world_constraint_navigation','HO2':'safe_bounded_closure'})
 neutral=score_canonical_assessment(r,{'HO1':'real_world_constraint_navigation','HO2':'safe_bounded_closure'},not_triggered_credit=.5)
 triggered=score_canonical_assessment(r,{'HO1':'real_world_constraint_navigation','HO2':'safe_bounded_closure'},not_triggered_credit=None)
 assert neutral['domain_scores']['bounded_closure_continuity']>zero['domain_scores']['bounded_closure_continuity']
 assert 'bounded_closure_continuity' not in triggered['domain_scores']
