#!/usr/bin/env python3
"""Generate the retained FCC/C-RWR-primary CL120 supplementary figures.

The figure set is intentionally compact. Threshold-dependent ordinal screening
plots, the 120-row score-ordered case map, the burden-bin heatmap, and the
workspace-frequency figure are not regenerated because they do not add clear
scientific information beyond released numeric tables.
"""
from __future__ import annotations
import argparse, csv
from pathlib import Path
from reportlab.pdfgen import canvas
from reportlab.lib import colors
from reportlab.lib.colors import HexColor
from reportlab.pdfbase.pdfmetrics import stringWidth

P=argparse.ArgumentParser();P.add_argument('--analysis-dir',type=Path,required=True);P.add_argument('--output-dir',type=Path,required=True);A=P.parse_args()
SRC=A.analysis_dir.resolve();OUT=A.output_dir.resolve();OUT.mkdir(parents=True,exist_ok=True)
COL={'ink':HexColor('#17343A'),'muted':HexColor('#64777D'),'grid':HexColor('#DDE5E7'),'teal':HexColor('#2F6F73'),'teal2':HexColor('#6E9E9D'),'orange':HexColor('#D8795F'),'red':HexColor('#A73434'),'cream':HexColor('#FBF4EE'),'blue':HexColor('#4A789C'),'purple':HexColor('#7C6AA5'),'gray':HexColor('#B7C2C7'),'white':colors.white}

def read(name):
 with (SRC/name).open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
def num(x):
 try:return float(x)
 except:return float('nan')
def label(c,x,y,t,size=7,color=None,align='left',font='Helvetica'):
 c.setFont(font,size);c.setFillColor(color or COL['ink'])
 if align=='right':c.drawRightString(x,y,str(t))
 elif align=='center':c.drawCentredString(x,y,str(t))
 else:c.drawString(x,y,str(t))
def fit(c,x,y,t,maxw,size=7,minsize=4.4,color=None,align='left',font='Helvetica'):
 z=size
 while z>minsize and stringWidth(str(t),font,z)>maxw:z-=.2
 label(c,x,y,t,z,color,align,font)
def line(c,x1,y1,x2,y2,color=None,width=.6):c.setStrokeColor(color or COL['grid']);c.setLineWidth(width);c.line(x1,y1,x2,y2)
def rect(c,x,y,w,h,fill,stroke=None):c.setFillColor(fill);c.setStrokeColor(stroke or fill);c.rect(x,y,w,h,fill=1,stroke=0 if stroke is None else 1)
def setup(c,w,h,title,subtitle):label(c,32,h-34,title,14.5,COL['ink'],font='Helvetica-Bold');label(c,32,h-49,subtitle,8,COL['muted'])
def diverge(v,limit=.4):
 z=max(-1,min(1,v/limit))
 white=(251,249,246); pos=(47,111,115); neg=(167,52,52); target=pos if z>=0 else neg;z=abs(z)
 return colors.Color(*[(white[k]*(1-z)+target[k]*z)/255 for k in range(3)])
def heat01(v):
 v=max(0,min(1,v));a=(246,235,228);b=(47,111,115)
 return colors.Color(*[(a[k]*(1-v)+b[k]*v)/255 for k in range(3)])
def redheat(v,hi=1):
 z=max(0,min(1,v/hi));a=(251,244,238);b=(167,52,52)
 return colors.Color(*[(a[k]*(1-z)+b[k]*z)/255 for k in range(3)])
def blueheat(v,hi=1):
 z=max(0,min(1,v/hi));a=(245,248,249);b=(74,120,156)
 return colors.Color(*[(a[k]*(1-z)+b[k]*z)/255 for k in range(3)])

# S-F1: burden associations. The five prospective burden dimensions are retained;
# the aggregate index is omitted from the figure because it collapses unlike constructs.
a=read('fcc_contract_burden_associations.csv')
preds=[
 ('responsibility_chain_burden_score','Responsibility-chain breadth'),
 ('evidence_integration_burden_score','Evidence integration'),
 ('friction_burden_score','Friction exposure'),
 ('temporal_burden_score','Temporal demand'),
 ('closure_coordination_burden_score','Closure coordination'),
]
outcomes=[('mean_fcc','A  FCC'),('mean_crwr','B  C-RWR'),('mean_turns','C  Interaction length')]
lookup={(r['predictor'],r['outcome']):r for r in a}
w,h=860,360;c=canvas.Canvas(str(OUT/'cl120_sfig_fcc_task_burden_associations.pdf'),pagesize=(w,h));setup(c,w,h,'Supplementary: Pre-execution task burden and observed outcomes','Case-level Spearman associations; predictors come only from frozen case contracts (n=120 cases).')
for pi,(out,title) in enumerate(outcomes):
 x0=150+pi*240;y0=68;pw=150;top=245
 label(c,x0+pw/2,top+35,title,8.2,COL['ink'],align='center',font='Helvetica-Bold')
 for tick in [-.5,-.25,0,.25,.5]:
  xx=x0+pw*(tick+.5);line(c,xx,y0,xx,top+10,COL['red'] if tick==0 else COL['grid'],.8 if tick==0 else .35);label(c,xx,y0-15,f'{tick:.2g}',5.5,COL['muted'],align='center')
 for i,(pred,lab) in enumerate(preds):
  y=top-i*34;r=lookup[(pred,out)];v=num(r['spearman_rho']);lo=num(r['bootstrap_95ci_low']);hi=num(r['bootstrap_95ci_high']);fx=lambda z:x0+pw*(max(-.5,min(.5,z))+.5)
  if pi==0:fit(c,x0-9,y-2,lab,145,6.2,minsize=5,color=COL['ink'],align='right')
  line(c,fx(lo),y,fx(hi),y,COL['teal'],1.7);c.setFillColor(COL['orange'] if (lo>0 or hi<0) else COL['teal']);c.circle(fx(v),y,3.8,fill=1,stroke=0);label(c,x0+pw+5,y-2,f'{v:+.2f}',5.6,COL['ink'])
label(c,w/2,27,'Spearman rho (95% case-bootstrap CI)',6.4,COL['muted'],align='center')
label(c,32,10,'The burden dimensions did not show a monotonic decline in FCC or C-RWR; friction exposure and temporal demand mainly tracked longer interactions.',6.2,COL['muted'])
c.showPage();c.save()

# S-F2: matched model-Judge association under both new primary readouts.
sa=read('fcc_judge_self_association_summary.csv'); metrics=[('friction_capability_composite','A  FCC'),('careloop_real_world_robustness','B  C-RWR')]
w,h=760,330;c=canvas.Canvas(str(OUT/'cl120_sfig_fcc_matched_model_judge_association.pdf'),pagesize=(w,h));setup(c,w,h,'Supplementary: Matched model–Judge association','Case-stratified difference-in-differences; packets concealed tested-model identity.')
for pi,(metric,title) in enumerate(metrics):
 rows=[r for r in sa if r['metric']==metric];x0=110+pi*360;y0=70;pw=220;top=220;loaxis=-.12;hiaxis=.30
 label(c,x0+pw/2,top+40,title,8.5,COL['ink'],align='center',font='Helvetica-Bold')
 for tick in [-.1,0,.1,.2,.3]:
  xx=x0+pw*(tick-loaxis)/(hiaxis-loaxis);line(c,xx,y0,xx,top+15,COL['red'] if tick==0 else COL['grid'],.8 if tick==0 else .35);label(c,xx,y0-14,f'{tick:+.1f}',5.5,COL['muted'],align='center')
 for i,r in enumerate(rows):
  y=top-i*48;v=num(r['calibration_adjusted_case_stratified_self_association_effect']);lo=num(r['bootstrap_95ci_low']);hi=num(r['bootstrap_95ci_high']);fx=lambda z:x0+pw*(max(loaxis,min(hiaxis,z))-loaxis)/(hiaxis-loaxis)
  fit(c,x0-8,y-2,r['judge_display_name'],105,6.6,minsize=5,color=COL['ink'],align='right');line(c,fx(lo),y,fx(hi),y,COL['teal'],1.8);c.setFillColor(COL['orange'] if lo>0 else COL['teal']);c.circle(fx(v),y,4,fill=1,stroke=0);label(c,x0+pw+6,y-2,f'{v:+.3f}',5.8,COL['ink'])
label(c,w/2,32,'Calibration-adjusted matched-model effect (95% case-bootstrap CI)',6.4,COL['muted'],align='center')
label(c,32,12,'Only three matched pairs exist. A positive association is exploratory and does not establish conscious self-preference or general evaluator validity.',6.2,COL['muted'])
c.showPage();c.save()

# S-F3: compact model diagnostic map. Continuous measurement first, operational context second.
rows=read('fcc_model_diagnostic_summary.csv');rows=sorted(rows,key=lambda r:num(r['fcc_rank']))
metrics=[
 ('mean_fcc','FCC','score',1),('mean_crwr','C-RWR','score',1),('mean_fcc_minus_crwr','Safety gap','risk',.25),('mean_fcc_judge_sd','Judge SD','risk',.25),
 ('open_at_100_rate','Open100','risk',.20),('runtime_error_rate','Runtime','risk',.20),('mean_turns','Turns','raw',30),('mean_workspace_turns','Workspace','raw',12),
]
w,h=820,410;c=canvas.Canvas(str(OUT/'cl120_sfig_fcc_model_diagnostic_map.pdf'),pagesize=(w,h));setup(c,w,h,'Supplementary: Model-level diagnostic profile','FCC/C-RWR are primary readouts; remaining columns provide safety-gate, reviewer-dispersion, and operational context.')
left=210;top=295;cw=69;rh=23
for j,(_,name,_,_) in enumerate(metrics):label(c,left+j*cw+cw/2,top+27,name,5.8,COL['ink'],align='center',font='Helvetica-Bold')
for i,r in enumerate(rows):
 y=top-i*rh;fit(c,left-10,y+7,r['doctor_model'],165,6.2,minsize=4.7,color=COL['ink'],align='right')
 for j,(key,name,typ,scale) in enumerate(metrics):
  v=num(r[key]);x=left+j*cw;fill=heat01(v) if typ=='score' else (redheat(v,scale) if typ=='risk' else blueheat(v,scale));rect(c,x,y,cw-1,rh-1,fill)
  if typ=='score':txt=f'{v:.3f}'
  elif 'rate' in key:txt=f'{100*v:.1f}%'
  else:txt=f'{v:.3f}' if key in ('mean_fcc_minus_crwr','mean_fcc_judge_sd') else f'{v:.1f}'
  contrast=(typ=='score' and v>.72) or (typ=='risk' and v/scale>.58) or (typ=='raw' and v/scale>.68)
  label(c,x+(cw-1)/2,y+7,txt,5.2,colors.white if contrast else COL['ink'],align='center')
label(c,32,30,'Safety gap = FCC minus C-RWR; larger values indicate a stronger clinical-error penalty. Judge SD is the mean within-trajectory FCC SD across four Judges.',6.15,COL['muted'])
label(c,32,13,'Turn and workspace counts describe process behavior and are not quality scores. All models were evaluated on the same 120 cases.',6.15,COL['muted'])
c.showPage();c.save()

manifest='''# FCC-primary supplementary figure manifest\n\nRetained figures:\n\n1. `cl120_sfig_fcc_task_burden_associations.pdf`: five pre-execution burden dimensions versus FCC, C-RWR, and interaction length.\n2. `cl120_sfig_fcc_matched_model_judge_association.pdf`: exploratory matched model–Judge association under FCC and C-RWR.\n3. `cl120_sfig_fcc_model_diagnostic_map.pdf`: compact model-level FCC/C-RWR, safety-gap, Judge-dispersion, and operational profile.\n\nExisting friction/HO coverage and Judge-specific six-domain figures remain valid because they already use the friction-centered measurement layer.\n\nDeliberately omitted from the revised manuscript figure set:\n\n- threshold-dependent ordinal screening descriptors;\n- the score-ordered 120-case review heatmap;\n- the model-by-burden-bin ordinal heatmap;\n- the workspace-frequency figure;\n- the aggregate-index scatterplot.\n\nTheir underlying public records remain available where useful for audit, but these plots were removed because they were redundant, threshold-dependent, difficult to interpret, or did not add a clear result beyond the retained analyses.\n'''
(OUT/'FCC_SUPPLEMENTARY_FIGURE_MANIFEST.md').write_text(manifest,encoding='utf-8')
print('generated',*[p.name for p in sorted(OUT.glob('cl120_sfig_fcc_*.pdf'))],sep='\n')
