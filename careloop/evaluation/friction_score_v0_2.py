"""CareLoop formative six-domain friction capability scoring, version 0.2.

The score is computed from unmodified canonical Judge fields.  It is a
secondary post-hoc measurement framework and does not alter the frozen 1--5
rubric or its deterministic grade.
"""
from __future__ import annotations
from collections import defaultdict
from statistics import mean
from typing import Any, Mapping

DIMENSION_VALUE={"unsafe":0.0,"weak":0.25,"partial":0.5,"good":0.75,"excellent":1.0}
CLOSURE_LABEL={"valid_closure":1.0,"valid_open":1.0,"questionable_open":0.4,"premature_closure":0.0,"invalid_or_unclear":0.0}
DOMAIN_HO_TYPES={
 "information_repair":{"hidden_state_discovery","patient_misinformation_correction","false_positive_or_false_negative_verification"},
 "constraint_navigation":{"real_world_constraint_navigation"},
 "dynamic_reprioritization":{"dynamic_reprioritization"},
 "execution_loop_repair":{"responsibility_chain_repair"},
 "state_sensitive_communication":{"patient_state_sensitive_communication"},
 "bounded_closure_continuity":{"safe_bounded_closure"},
}
DOMAIN_WEIGHTS={"information_repair":.20,"constraint_navigation":.20,"dynamic_reprioritization":.15,"execution_loop_repair":.20,"state_sensitive_communication":.10,"bounded_closure_continuity":.15}

def ho_credit(h:Mapping[str,Any],not_triggered_credit:float|None=0.0)->float|None:
 c=h.get('completion')
 if c=='not_triggered':return not_triggered_credit
 if c=='not_completed':return 0.0
 if c=='partial':return .5
 if c=='complete':return 1.0 if h.get('active_model_action') is True and h.get('meaningful_trajectory_impact') is True else .75
 raise ValueError(f'unknown_high_order_completion:{c!r}')
def closure_score(c:Mapping[str,Any])->float:
 return .4*CLOSURE_LABEL[str(c.get('label'))]+.2*sum(float(c.get(k) is True) for k in ('safe','executable','traceable'))
def clinical_error_gate(x:Mapping[str,Any])->float:
 flags=x.get('derived_gate_flags') or {}
 if flags.get('serious_error_present') is True:return .35
 if flags.get('minor_or_moderate_error_present') is True:return .75
 return 1.0
def _mean(xs):return mean(xs) if xs else None
def _combine(parts:dict[str,float],weights:dict[str,float])->float:
 if set(parts)!=set(weights):raise ValueError(f'component_mismatch:{sorted(parts)}:{sorted(weights)}')
 return sum(parts[k]*weights[k] for k in weights)
def score_canonical_assessment(result:Mapping[str,Any],ho_capability_by_id:Mapping[str,str],*,not_triggered_credit:float|None=0.0,domain_weights:Mapping[str,float]|None=None)->dict[str,Any]:
 dims={k:DIMENSION_VALUE[str(v)] for k,v in result['dimension_ratings'].items()};cl=result['closure_assessment'];cs=closure_score(cl)
 by=defaultdict(list)
 for h in result['high_order_assessment']:
  cap=ho_capability_by_id.get(str(h.get('ho_id')))
  if not cap:raise ValueError(f"missing_capability:{h.get('ho_id')}")
  credit=ho_credit(h,not_triggered_credit)
  if credit is not None:by[cap].append(credit)
 domain={}
 h={d:_mean([v for cap in types for v in by.get(cap,[])]) for d,types in DOMAIN_HO_TYPES.items()}
 if h['information_repair'] is not None:domain['information_repair']=.8*h['information_repair']+.2*dims['clinical_reasoning_direction']
 if h['constraint_navigation'] is not None:domain['constraint_navigation']=.7*h['constraint_navigation']+.2*dims['patient_family_realworld_adaptation']+.1*float(cl['executable'] is True)
 if h['dynamic_reprioritization'] is not None:domain['dynamic_reprioritization']=.7*h['dynamic_reprioritization']+.15*dims['clinical_reasoning_direction']+.15*dims['medical_safety_risk_recognition']
 if h['execution_loop_repair'] is not None:domain['execution_loop_repair']=.6*h['execution_loop_repair']+.2*dims['actionability_responsibility_chain']+.1*float(cl['executable'] is True)+.1*float(cl['traceable'] is True)
 if h['state_sensitive_communication'] is not None:domain['state_sensitive_communication']=.7*h['state_sensitive_communication']+.3*dims['patient_family_realworld_adaptation']
 if h['bounded_closure_continuity'] is not None:domain['bounded_closure_continuity']=.55*h['bounded_closure_continuity']+.25*dims['continuity_memory_focus']+.2*cs
 weights=dict(DOMAIN_WEIGHTS if domain_weights is None else domain_weights)
 unknown=set(weights)-set(DOMAIN_WEIGHTS)
 if unknown:raise ValueError(f'unknown_domain_weights:{sorted(unknown)}')
 den=sum(weights[d] for d in domain)
 if not den:raise ValueError('no_applicable_friction_domain')
 composite=sum(weights[d]*v for d,v in domain.items())/den
 gate=clinical_error_gate(result);robust=composite*gate
 return {'domain_scores':dict(sorted(domain.items())),'applicable_domain_count':len(domain),'friction_capability_composite':composite,'clinical_error_gate':gate,'careloop_real_world_robustness':robust,'closure_process_score':cs}
