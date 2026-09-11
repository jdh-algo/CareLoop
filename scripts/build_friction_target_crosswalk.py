#!/usr/bin/env python3
"""Build auditable draft HO exposure and RC capability crosswalks from frozen contracts.

The output is a deterministic proposal for human/clinical review.  It never
reads model scores or Judge outcomes.
"""
from __future__ import annotations
import argparse,csv,hashlib,json,re
from collections import Counter,defaultdict
from pathlib import Path
from typing import Any

DOMAINS={
 'information_repair':[
  'information gap','information_gap','record','document','result','pathology','imaging','image','verify','verification','reconcil','uncertain','uncertainty','non-overreach','non_overreach','misinformation','missing information','history clarification','资料','记录','文档','报告','结果','病理','影像','核实','核对','信息缺','不确定','误解','纠正','不能确认'],
 'constraint_navigation':[
  'barrier','constraint','cost','transport','work barrier','access','alternative','refusal','privacy','stigma','caregiver conflict','fatigue','现实','障碍','费用','交通','工作','拒绝','隐私','污名','家属冲突','照护疲劳','替代方案'],
 'dynamic_response':[
  'repriorit','priority','triage','urgent','emergency','red flag','red_flag','escalat','deteriorat','new symptom','acute','when new','if new','风险升级','优先级','分诊','急诊','紧急','红旗','恶化','新症状','升级'],
 'execution_adherence':[
  'adherence','execution','medication','medicine','drug','dose','monitoring','home care','self-management','wound care','diet','exercise','device','instruction','compliance','服药','用药','药物','剂量','依从','执行','居家','监测','伤口护理','饮食','运动','器械','医嘱'],
 'communication':[
  'communication','patient-facing','patient_facing','caregiver role','sensitive','health literacy','cognitive','emotion','teach-back','teachback','understanding','shared decision','沟通','患者语言','家属角色','敏感','认知','情绪','复述','理解确认','共同决策'],
 'closure_continuity':[
  'closure','handoff','follow-up','followup','follow up','receipt','owner','timing','transition','continuity','pending result','next step','action plan','closed loop','闭环','交接','随访','回执','责任人','时间','过渡','连续','待回结果','下一步','行动计划'],
 'core_safety':[
  'safety','safe','risk','danger','contraindicat','bleeding','infection','airway','serious','avoid','boundary','current status','安全','风险','危险','禁忌','出血','感染','气道','严重','避免','边界','当前状态'],
}
PRIORITY=['closure_continuity','execution_adherence','information_repair','constraint_navigation','communication','dynamic_response','core_safety']

def canonical(x:Any)->str:return json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def digest(x:Any)->str:return hashlib.sha256(canonical(x).encode()).hexdigest()
def flatten_text(x:Any)->str:
 if isinstance(x,str):return x
 if isinstance(x,list):return ' '.join(flatten_text(v) for v in x)
 if isinstance(x,dict):return ' '.join(flatten_text(v) for v in x.values())
 return str(x) if x is not None else ''
def classify_rc(src:dict[str,Any])->tuple[str,dict[str,int],str]:
 text=flatten_text(src).lower();scores={d:sum(text.count(k.lower()) for k in keys) for d,keys in DOMAINS.items()}
 top=max(scores.values())
 if top==0:return 'core_safety',scores,'low:no_keyword_default'
 winners=[d for d,v in scores.items() if v==top]
 primary=next(d for d in PRIORITY if d in winners)
 total=sum(v>0 for v in scores.values());confidence='high' if len(winners)==1 and top>=2 else 'medium' if len(winners)==1 else 'low:tie'
 if total>=4 and top<3:confidence='low:diffuse'
 return primary,scores,confidence

def classify_ho_exposure(src:dict[str,Any])->tuple[str,str]:
 text=flatten_text({'trigger':src.get('trigger_or_discoverability'),'criteria':src.get('completion_criteria'),'point':src.get('point'),'description':src.get('description')}).lower()
 if any(k in text for k in ['near closure','before closure','at closure','closure stage','teach-back','teachback','临近闭环','闭环前','结束前','复述']):return 'closure_contingent','closure_or_receipt_language'
 if any(k in text for k in ['when new','if new','new symptom','new result','new barrier','after the','if the patient','if the family','when the patient','when the family','当对话中','当出现','若出现','如果出现','新信息','突然','恶化']):return 'event_contingent','conditional_event_language'
 if any(k in text for k in ['askable','discoverable','elicitable','actively ask','clarif','probe','visible','inferable','追问','核实','询问','主动发现','可询问','可发现']):return 'model_elicitable','discoverability_language'
 return 'always_observable','no_contingency_detected'

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--root',type=Path,required=True);ap.add_argument('--out',type=Path,required=True);a=ap.parse_args();a.out.mkdir(parents=True,exist_ok=True)
 cases={}
 for p in (a.root/'judge_packets').glob('*.judge_packet.json'):
  c=json.loads(p.read_text())['case_contract'];cases.setdefault(c['case_id'],c)
 rc_rows=[];ho_rows=[];seen_rc=set();seen_ho=set()
 for cid,c in sorted(cases.items()):
  ec=c['evaluation_contract_v2']
  for item in ec['responsibility_chain_required_items']:
   src=item['source_item'];sh=item.get('source_item_sha256') or digest(src);key=(cid,item['id'],sh)
   if key in seen_rc:continue
   seen_rc.add(key);primary,scores,conf=classify_rc(src)
   rc_rows.append({'case_id':cid,'rc_id':item['id'],'source_item_sha256':sh,'primary_domain':primary,'classification_confidence':conf,
    'closure_blocking_if_unmet':src.get('closure_blocking_if_unmet',''),'failure_severity_if_missed':src.get('failure_severity_if_missed',''),
    **{f'match_{d}':scores[d] for d in DOMAINS},'source_label':src.get('category') or src.get('name') or '',
    'source_text':flatten_text(src).replace('\n',' ')})
  for item in ec['high_order_test_points']:
   src=item['source_item'];sh=item.get('source_item_sha256') or digest(src);key=(cid,item['id'],sh)
   if key in seen_ho:continue
   seen_ho.add(key);ex,reason=classify_ho_exposure(src)
   ho_rows.append({'case_id':cid,'ho_id':item['id'],'source_item_sha256':sh,'capability_type':src.get('capability_type',''),
    'exposure_class_draft':ex,'classification_reason':reason,'trigger_or_discoverability':flatten_text(src.get('trigger_or_discoverability')).replace('\n',' '),
    'source_text':flatten_text(src).replace('\n',' ')})
 for fn,rows in [('rc_capability_crosswalk_draft.csv',rc_rows),('ho_exposure_crosswalk_draft.csv',ho_rows)]:
  with (a.out/fn).open('w',encoding='utf-8',newline='') as f:w=csv.DictWriter(f,fieldnames=rows[0]);w.writeheader();w.writerows(rows)
 audit={'schema':'careloop.friction_target_crosswalk.v0_1_draft','case_count':len(cases),'rc_count':len(rc_rows),'ho_count':len(ho_rows),
  'rc_domain_counts':Counter(r['primary_domain'] for r in rc_rows),'rc_confidence_counts':Counter(r['classification_confidence'] for r in rc_rows),
  'ho_exposure_counts':Counter(r['exposure_class_draft'] for r in ho_rows)}
 (a.out/'crosswalk_draft_audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2,default=dict)+'\n')
 print(json.dumps(audit,ensure_ascii=False,indent=2,default=dict))
if __name__=='__main__':main()
