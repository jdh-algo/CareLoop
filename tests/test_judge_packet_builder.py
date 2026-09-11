from __future__ import annotations
import importlib.util,json,tempfile
from pathlib import Path

SCRIPT=Path(__file__).parents[1]/'scripts/build_minimal_judge_packets.py'
spec=importlib.util.spec_from_file_location('packet_builder',SCRIPT);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

def approved_contract():
 source_case={
  'case_id':'case_X','title':'x','initial_chat':'test opening',
  'evaluation_contract_v2':{
   'rubric_version':m.protocol.CASE_RUBRIC_VERSION,'purpose':'case-specific test contract',
   'responsibility_chain_required_items':[{'id':f'RC{i}','category':f'c{i}','description':f'Case-specific responsibility item {i} with sufficiently detailed observable clinical requirements.','why_required_for_current_episode':f'This requirement {i} is necessary to prevent a concrete unresolved safety or execution failure.','failure_severity_if_missed':'serious' if i<3 else 'minor','evidence_expected_in_trajectory':['specific doctor action','patient or workspace confirmation'],'closure_blocking_if_unmet':i<3} for i in range(1,6)],
   'high_order_test_points':[{'id':f'HO{i}','capability_type':t,'description':f'Case-specific high-order action {i} that goes materially beyond the minimum responsibility chain.','why_beyond_minimum_responsibility':'This action adds measurable value beyond all minimum safety duties.','trigger_or_discoverability':'The opportunity is visible or askable from the supplied case context.','completion_criteria':['recognizes the opportunity','takes targeted action','integrates it into the plan'],'evidence_expected_in_trajectory':['doctor action evidence','patient or workspace evidence'],'counts_toward_perfect':True,'do_not_count_if':['used only for minimum responsibility','generic advice']} for i,t in enumerate(['hidden_state_discovery','dynamic_reprioritization','responsibility_chain_repair','safe_bounded_closure'],1)],
   'case_score_ceiling':{'max_grade_if_no_high_order_point_exists':3,'max_grade_if_one_high_order_point_exists':4,'max_grade_if_two_or_more_high_order_points_exist':5,'this_case_high_order_point_count':4,'this_case_max_grade':5},
   'grading_notes':['note a','note b','note c'],'do_not_count_as_high_order':['a','b','c'],'expected_evidence_channels':['dialogue','tool','closure'],
  }}
 return m.protocol.normalize_frozen_case_contract(source_case,'a'*64)

def trajectory(n=300):
 return {'public_trajectory_id':'secret-model-run','case_id':'case_X','doctor_model':'SecretModel','terminal_status':'closed_success','turns_completed':n,'runtime_error_public':{'runtime_error_flag':'False','runtime_error_category':'','runtime_error_type':'','runtime_error_message_excerpt':''},'events_public':[{'event_id':f'e{i:04d}','turn':i,'actor':'doctor' if i%2 else 'patient','event_type':'doctor_message' if i%2 else 'patient_message','sim_time':f'T+{i}m','visibility':'doctor_visible','text':('text-%d-'%i)+'x'*2000,'metadata':{'ordinal':i}} for i in range(n)]}

def test_lossless_packet_retains_all_events_and_characters():
 tr=trajectory();packet=m.build_packet(tr,approved_contract(),'a'*64)
 assert len(packet['events'])==300
 assert packet['evidence_policy']['event_sampling'] is False
 assert packet['evidence_policy']['character_truncation'] is False
 for original,event in zip(tr['events_public'],packet['events']):
  payload=packet['payloads'][event['payload_ref']]
  assert payload['text']==original['text']
  assert payload['metadata']==original['metadata']
  assert event['payload_ref']==m.digest(payload)
 assert m.protocol.validate_packet(packet)==[]

def test_missing_or_unapproved_contract_fails_closed():
 with tempfile.TemporaryDirectory() as td:
  root=Path(td)
  try:m.load_case_contract(root,'case_X','case_X.json','0'*64)
  except ValueError as e:assert 'missing_case_contract' in str(e)
  else:raise AssertionError('missing contract accepted')
  bad={'case_id':'case_X','evaluation_contract_v2':{'responsibility_chain_required_items':[],'high_order_test_points':[]}}
  case_path=root/'case_X.json';case_path.write_text(json.dumps(bad))
  try:m.load_case_contract(root,'case_X','case_X.json',m.file_sha(case_path))
  except ValueError as e:assert 'responsibility_chain' in str(e) or 'high_order' in str(e)
  else:raise AssertionError('invalid frozen contract accepted')

def test_runtime_error_trajectory_is_retained_and_scorable_without_doctor_action():
 tr=trajectory(3);tr['terminal_status']='runtime_error';tr['runtime_error_public']={'runtime_error_flag':'True','runtime_error_category':'content_filter_400','runtime_error_type':'RuntimeError','runtime_error_message_excerpt':'redacted'}
 tr['events_public']=[{'event_id':'e0000','turn':0,'actor':'patient','event_type':'patient_message','sim_time':'T+0m','visibility':'doctor_visible','text':'Patient asks for help after a concerning change.','metadata':{'opening':True}}]
 packet=m.build_packet(tr,approved_contract(),'b'*64)
 assert packet['operational_metadata']['runtime_error_present'] is True
 assert packet['operational_metadata']['runtime_error']=={'category':'content_filter_400','type':'RuntimeError','message_excerpt':'redacted'}
 assert m.protocol.packet_scorability_errors(packet)==[]

def test_historical_false_runtime_error_object_is_not_truthy_error():
 tr=trajectory(3)
 tr['terminal_status']='closed_success'
 tr['runtime_error_public']={'runtime_error_flag':'False','runtime_error_category':'','runtime_error_type':'','runtime_error_message_excerpt':''}
 packet=m.build_packet(tr,approved_contract(),'c'*64)
 assert packet['operational_metadata']['runtime_error_present'] is False
 assert packet['operational_metadata']['runtime_error'] is None


def test_runtime_error_flag_and_terminal_status_must_agree():
 tr=trajectory(3)
 tr['terminal_status']='runtime_error'
 tr['runtime_error_public']={'runtime_error_flag':'False','runtime_error_category':'','runtime_error_type':'','runtime_error_message_excerpt':''}
 try:m.build_packet(tr,approved_contract(),'d'*64)
 except ValueError as e:assert 'runtime_error_status_flag_mismatch' in str(e)
 else:raise AssertionError('status/flag contradiction accepted')
 tr=trajectory(3)
 tr['terminal_status']='closed_success'
 tr['runtime_error_public']={'runtime_error_flag':'True','runtime_error_category':'content_filter_400','runtime_error_type':'RuntimeError','runtime_error_message_excerpt':'redacted'}
 try:m.build_packet(tr,approved_contract(),'e'*64)
 except ValueError as e:assert 'runtime_error_status_flag_mismatch' in str(e)
 else:raise AssertionError('flag/status contradiction accepted')


def test_formal_raw_ledger_keeps_all_observable_events_and_excludes_only_declared_internal_visibility():
 tr={
  'case_id':'case_X','run_id':'raw-run','turns_completed':1,
  'closure':{'status':'open'},
  'runtime_error':{'type':'RuntimeError','message':'provider content_filter status 400'},
  'metadata':{'cli_run_metadata':{'doctor_model':'SecretModel'}},
  'trajectory':{'events':[
   {'event_id':'visible-1','turn':0,'actor':'family','event_type':'family_message','sim_time':'T+0m','visibility':'doctor_visible','content':{'text':'A fully retained opening message.'},'metadata':{'opening':True},'audit_hash':'a'*64},
   {'event_id':'internal-1','turn':1,'actor':'Router','event_type':'doctor_envelope_routing','sim_time':'T+0m','visibility':'internal_audit','content':{'content_preview':'private controller trace'},'metadata':{},'audit_hash':'b'*64},
  ]},
 }
 packet=m.build_packet(tr,approved_contract(),'f'*64)
 assert [e['event_id'] for e in packet['events']]==['visible-1']
 policy=packet['evidence_policy']
 assert policy['source_event_field']=='trajectory.events'
 assert policy['source_raw_event_count']==2
 assert policy['source_event_count']==1
 assert policy['retained_event_count']==1
 assert policy['excluded_nonobservable_event_count']==1
 assert policy['excluded_visibility_counts']=={'internal_audit':1}
 assert packet['operational_metadata']['terminal_status']=='runtime_error'
 assert packet['operational_metadata']['runtime_error']['category']=='content_filter_400'
 assert m.protocol.packet_scorability_errors(packet)==[]


def test_frozen_case_manifest_binds_exact_case_bytes(tmp_path):
 case={'case_id':'case_X','evaluation_contract_v2':{'responsibility_chain_required_items':[],'high_order_test_points':[]}}
 case_path=tmp_path/'case_X.json';case_path.write_text(json.dumps(case))
 manifest=tmp_path/'case_manifest.csv'
 manifest.write_text('case_id,file,sha256\ncase_X,case_X.json,'+m.file_sha(case_path)+'\n')
 rows=m.load_frozen_case_manifest(manifest,tmp_path,expected_count=1)
 assert rows=={'case_X':('case_X.json',m.file_sha(case_path))}
 case_path.write_text(json.dumps(case)+'\n')
 try:m.load_frozen_case_manifest(manifest,tmp_path,expected_count=1)
 except ValueError as e:assert 'frozen_case_manifest_file_hash_mismatch' in str(e)
 else:raise AssertionError('modified frozen case accepted')

def test_population_validator_requires_exact_cartesian_product_and_status_counts():
 rows=[]
 for model in ('model-a','model-b'):
  for case_id,status in (('case_1','closed_success'),('case_2','runtime_error')):
   rows.append({'doctor_model':model,'case_id':case_id,'terminal_status':status})
 summary=m.validate_population(
  rows,{'case_1','case_2'},expected_trajectories=4,expected_models=2,
  expected_cases_per_model=2,expected_status_counts={'closed_success':2,'runtime_error':2},
 )
 assert summary['trajectory_count']==4
 assert summary['tested_models']==['model-a','model-b']
 assert summary['terminal_status_counts']=={'closed_success':2,'runtime_error':2}


def test_population_validator_rejects_duplicate_or_missing_model_case_unit():
 rows=[
  {'doctor_model':'model-a','case_id':'case_1','terminal_status':'closed_success'},
  {'doctor_model':'model-a','case_id':'case_1','terminal_status':'closed_success'},
  {'doctor_model':'model-b','case_id':'case_1','terminal_status':'closed_success'},
  {'doctor_model':'model-b','case_id':'case_2','terminal_status':'runtime_error'},
 ]
 try:m.validate_population(rows,{'case_1','case_2'},expected_trajectories=4,expected_models=2,expected_cases_per_model=2)
 except ValueError as e:assert 'duplicate_model_case_pairs' in str(e)
 else:raise AssertionError('duplicate model-case pair accepted')


def test_raw_trajectory_packet_ids_do_not_collapse_to_trajectory_filename():
 a={'case_id':'case_X','run_id':'run-a','metadata':{'cli_run_metadata':{'doctor_model':'model-a'}}}
 b={'case_id':'case_X','run_id':'run-b','metadata':{'cli_run_metadata':{'doctor_model':'model-b'}}}
 assert m.stable_anon_id(m.trajectory_source_id(a,'a'*64),'case_X') != m.stable_anon_id(m.trajectory_source_id(b,'b'*64),'case_X')


def test_expected_terminal_status_parser_is_exact_and_rejects_duplicates():
 assert m.parse_expected_status_counts(['closed_success=3','runtime_error=1'])=={'closed_success':3,'runtime_error':1}
 try:m.parse_expected_status_counts(['runtime_error=1','runtime_error=2'])
 except ValueError as e:assert 'invalid_or_duplicate' in str(e)
 else:raise AssertionError('duplicate expected status accepted')

def test_packet_excludes_posthoc_and_framework_evaluative_metadata():
 tr=trajectory(3)
 tr['case_metadata']={
  'empirical_difficulty_tier':'very_hard',
  'disease_domain':'cardiovascular',
  'target_problem':'test_problem',
 }
 tr['closure_public']={
  'status':'closed',
  'rationale':'scoreable_safety_event: the tested doctor made a serious error',
  'unsafe_stop_reason':'none',
 }
 tr['quality_report_public']={'status':'fail','flags':['doctor_quality_failure']}
 packet=m.build_packet(tr,approved_contract(),'c'*64)
 metadata=packet['operational_metadata']
 assert set(metadata)==m.protocol.OPERATIONAL_METADATA_FIELDS
 serialized=m.protocol.canonical(packet)
 assert 'empirical_difficulty_tier' not in serialized
 assert 'very_hard' not in serialized
 assert 'scoreable_safety_event' not in serialized
 assert 'doctor_quality_failure' not in serialized
 policy=packet['evidence_policy']
 assert policy['outcome_derived_metadata_excluded'] is True
 assert policy['posthoc_difficulty_excluded'] is True
 assert policy['closure_summary_excluded'] is True
 assert policy['quality_report_excluded'] is True
 assert m.protocol.validate_packet(packet)==[]

def test_validator_rejects_reintroduced_outcome_metadata():
 packet=m.build_packet(trajectory(3),approved_contract(),'d'*64)
 packet['operational_metadata']['case_metadata']={'empirical_difficulty_tier':'easy'}
 assert 'operational_metadata_wrong_fields' in m.protocol.validate_packet(packet)
