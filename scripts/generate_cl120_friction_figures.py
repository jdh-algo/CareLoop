#!/usr/bin/env python3
"""Generate publication-ready CL120 friction-centered figures with ReportLab.

All heatmaps use fixed 0--1 scales. Column labels are wrapped horizontally to
avoid the unreadable diagonal-label collisions present in earlier drafts.
"""
from pathlib import Path
import argparse, csv, statistics
from collections import defaultdict
from reportlab.pdfgen import canvas
from reportlab.lib import colors
from reportlab.lib.colors import HexColor
from reportlab.pdfbase.pdfmetrics import stringWidth

P = argparse.ArgumentParser()
P.add_argument('--analysis-dir', type=Path, required=True)
P.add_argument('--output-dir', type=Path, required=True)
A = P.parse_args(); SRC=A.analysis_dir.resolve(); OUT=A.output_dir.resolve(); OUT.mkdir(parents=True,exist_ok=True)

def read(name):
    with (SRC/name).open(encoding='utf-8-sig', newline='') as handle:
        return list(csv.DictReader(handle))
def f(x): return float(x)
def heat(v):
    lo=(244,233,226); mid=(244,205,142); hi=(38,105,110); v=max(0,min(1,v))
    a,b,t=(lo,mid,v*2) if v<.5 else (mid,hi,(v-.5)*2)
    return colors.Color(*[(a[i]+(b[i]-a[i])*t)/255 for i in range(3)])
INK=HexColor('#17343A'); MUT=HexColor('#64777C'); GRID=HexColor('#D7E1E3'); TEAL=HexColor('#2F6F73'); ORANGE=HexColor('#D8795F'); PURPLE=HexColor('#5B658F'); PALE=HexColor('#EEF4F4')
def txt(c,x,y,s,size=7,align='left',bold=False,color=INK):
    c.setFillColor(color); c.setFont('Helvetica-Bold' if bold else 'Helvetica',size)
    {'right':c.drawRightString,'center':c.drawCentredString}.get(align,c.drawString)(x,y,str(s))
def fit(c,x,y,s,w,size=7,align='right',bold=False,color=INK,min_size=4.8):
    font='Helvetica-Bold' if bold else 'Helvetica'; z=size
    while z>min_size and stringWidth(str(s),font,z)>w: z-=.2
    txt(c,x,y,s,z,align,bold,color)
def title(c,w,h,t,sub):
    txt(c,34,h-30,t,15,bold=True); fit(c,34,h-47,sub,w-68,7.6,align='left',color=MUT,min_size=6.0)
def box(c,x,y,w,h,fill,stroke=GRID):
    c.setFillColor(fill); c.setStrokeColor(stroke); c.rect(x,y,w,h,fill=1,stroke=1)
def lines(c,x,y,parts,size=6.8,color=MUT,bold=False,leading=8.0,align='center'):
    for i,p in enumerate(parts): txt(c,x,y-i*leading,p,size,align,bold,color)
def legend(c,x0,y0,sw=170):
    for k in range(50): box(c,x0+k*sw/50,y0,sw/50,8,heat(k/49),heat(k/49))
    for v in [0,.25,.5,.75,1]: txt(c,x0+sw*v,y0-11,f'{v:.2f}',5.8,align='center',color=MUT)
    txt(c,x0+sw+14,y0-1,'Fixed 0–1 scale',6.2,color=MUT)

def heatmap(path,title_text,subtitle,cols,lookup,row_order,col_labels,side=None,w=940,h=520):
    c=canvas.Canvas(str(path),pagesize=(w,h)); title(c,w,h,title_text,subtitle)
    lx=205; rx=w-30; top=h-120; bottom=78; cw=(rx-lx)/(len(cols)+(1 if side else 0)); rh=(top-bottom)/len(row_order)
    for j,col in enumerate(cols): lines(c,lx+(j+.5)*cw,top+34,col_labels[col],6.4,bold=True)
    if side: lines(c,lx+(len(cols)+.5)*cw,top+34,side['label'],6.4,color=PURPLE,bold=True)
    for i,row in enumerate(row_order):
        y=top-(i+1)*rh; fit(c,lx-10,y+rh*.38,row,174,7.2,align='right',bold=i<3,min_size=5.6)
        for j,col in enumerate(cols):
            v=lookup[(row,col)]; box(c,lx+j*cw,y,cw,rh,heat(v)); txt(c,lx+(j+.5)*cw,y+rh*.38,f'{v:.2f}',6.4,align='center',bold=v>=.82,color=colors.white if v>=.72 else INK)
        if side:
            v=side['values'][row]; box(c,lx+len(cols)*cw,y,cw,rh,heat(v),PURPLE); txt(c,lx+(len(cols)+.5)*cw,y+rh*.38,f'{v:.2f}',6.6,align='center',bold=True,color=colors.white if v>=.72 else INK)
    legend(c,lx,38); c.showPage(); c.save()

models=read('friction_domains_model_summary.csv'); order=[r['doctor_model'] for r in models]
domains=['information_repair','constraint_navigation','dynamic_reprioritization','execution_loop_repair','state_sensitive_communication','bounded_closure_continuity']
dlabels={
 'information_repair':['Information','repair'], 'constraint_navigation':['Constraint','navigation'],
 'dynamic_reprioritization':['Dynamic','reprioritization'], 'execution_loop_repair':['Execution-loop','repair'],
 'state_sensitive_communication':['State-sensitive','communication'], 'bounded_closure_continuity':['Bounded closure','and continuity']}
lookup={(r['doctor_model'],d):f(r['domain_'+d+'_mean']) for r in models for d in domains}
side={'label':['Friction capability','composite'],'values':{r['doctor_model']:f(r['friction_capability_composite_median4_mean']) for r in models}}
heatmap(OUT/'cl120_fig_friction_capability_heatmap.pdf','Real-world friction capability profile','Cells are model means over applicable authored opportunities; the per-case composite renormalizes prespecified weights over applicable domains.',domains,lookup,order,dlabels,side)

fr=read('friction_domains_by_primary_friction.csv')
frictions=['misstated_family_or_patient_information','incomplete_patient_information','document_or_image_quality_problem','delayed_or_staged_result_return','external_system_gap','medication_confusion_or_execution_failure','low_adherence_or_refusal','cost_transport_or_work_barrier','caregiver_conflict_or_fatigue','privacy_stigma_or_sensitive_history']
flabel={
 frictions[0]:['Misstated','information'],frictions[1]:['Incomplete','information'],frictions[2]:['Document/image','quality'],frictions[3]:['Delayed/staged','result'],frictions[4]:['External-system','gap'],frictions[5]:['Medication/execution','confusion'],frictions[6]:['Low adherence','or refusal'],frictions[7]:['Cost/transport/','work barrier'],frictions[8]:['Caregiver conflict','or fatigue'],frictions[9]:['Privacy/stigma/','sensitive history']}
fl={(r['doctor_model'],r['primary_friction']):f(r['friction_capability_mean']) for r in fr}
macro={m:statistics.mean(fl[(m,z)] for z in frictions) for m in order}
heatmap(OUT/'cl120_fig_model_by_friction_heatmap.pdf','Capability under prospectively authored friction exposure','Each column contains 12 case contracts shared by every model. The macro mean gives equal weight to the 10 exposure strata.',frictions,fl,order,flabel,{'label':['Equal-stratum','macro mean'],'values':macro},w=1120,h=550)

# Benchmark coverage: friction exposures x HO response-capability types.
import json
release_root=SRC.parents[1]
cases_by_id={}
for p in sorted((release_root/'judge_packets').glob('*.judge_packet.json')):
    q=json.loads(p.read_text())['case_contract']; cases_by_id.setdefault(q['case_id'],q)
cases=list(cases_by_id.values())
caps=['hidden_state_discovery','patient_misinformation_correction','false_positive_or_false_negative_verification','real_world_constraint_navigation','dynamic_reprioritization','responsibility_chain_repair','patient_state_sensitive_communication','safe_bounded_closure']
clab={
 caps[0]:['Hidden-state','discovery'],caps[1]:['Misinformation','correction'],caps[2]:['Evidence','verification'],caps[3]:['Constraint','navigation'],caps[4]:['Dynamic','reprioritization'],caps[5]:['Responsibility-chain','repair'],caps[6]:['State-sensitive','communication'],caps[7]:['Safe bounded','closure']}
counts=defaultdict(int)
for z in cases:
    ff=z['closure_contract_v2']['coverage_tags']['primary_friction']
    for hp in z['evaluation_contract_v2']['high_order_test_points']:
        cap=hp['source_item']['capability_type']; counts[(ff,cap)]+=1
w,h=1040,540;c=canvas.Canvas(str(OUT/'cl120_sfig_friction_ho_coverage.pdf'),pagesize=(w,h));title(c,w,h,'Benchmark design: friction exposure and response capabilities','Counts show authored HO opportunities across the 12 cases in each exposure stratum. This is a coverage map, not a performance heatmap.')
lx=215;top=400;bottom=86;cw=(w-lx-30)/len(caps);rh=(top-bottom)/len(frictions)
for j,cap in enumerate(caps): lines(c,lx+(j+.5)*cw,top+36,clab[cap],6.2,bold=True)
for i,ff in enumerate(frictions):
    y=top-(i+1)*rh; fit(c,lx-10,y+rh*.38,' '.join(flabel[ff]).replace('/ ','/'),180,6.8,align='right',min_size=5.4)
    for j,cap in enumerate(caps):
        v=counts[(ff,cap)]; box(c,lx+j*cw,y,cw,rh,heat(v/12)); txt(c,lx+(j+.5)*cw,y+rh*.38,str(v),6.4,align='center',bold=v>=8,color=colors.white if v>=8 else INK)
txt(c,lx,43,'0 = capability absent from all 12 cases in the stratum; 12 = present in every case.',6.6,color=MUT); c.showPage(); c.save()

# Within-grade resolution and rank comparison.
traj=read('friction_domains_trajectory.csv');w,h=980,500;c=canvas.Canvas(str(OUT/'cl120_fig_added_resolution.pdf'),pagesize=(w,h));title(c,w,h,'The friction profile retains variation within ordinal grades','A, trajectory distributions within each frozen grade. B, the two summaries yield broadly similar, but non-identical, model ordering.')
ax0,ay0,aw,ah=72,86,470,300;grades=sorted({f(r['ordinal_grade_median4']) for r in traj})
for tick in [0,.25,.5,.75,1]:
    y=ay0+ah*tick;c.setStrokeColor(GRID);c.line(ax0,y,ax0+aw,y);txt(c,ax0-10,y-2,f'{tick:.2f}',6.2,align='right',color=MUT)
for i,g in enumerate(grades):
    vals=sorted(f(r['friction_capability_composite_median4']) for r in traj if f(r['ordinal_grade_median4'])==g);x=ax0+(i+.5)*aw/len(grades);q=lambda p:vals[int(round((len(vals)-1)*p))]
    lo,qa,md,qb,hi=q(.05),q(.25),q(.5),q(.75),q(.95);c.setStrokeColor(INK);c.line(x,ay0+ah*lo,x,ay0+ah*hi);box(c,x-11,ay0+ah*qa,22,max(1,ah*(qb-qa)),HexColor('#DCEBEC'),TEAL);c.setStrokeColor(ORANGE);c.setLineWidth(1.4);c.line(x-11,ay0+ah*md,x+11,ay0+ah*md);txt(c,x,ay0-17,f'{g:g}',6.2,align='center',color=MUT);txt(c,x,ay0+ah*hi+5,f'n={len(vals)}',5.1,align='center',color=MUT)
txt(c,ax0,ay0+ah+16,'A',9,bold=True);txt(c,ax0+aw/2,48,'Frozen median-of-four ordinal grade',7,align='center',color=MUT)
c.saveState();c.translate(24,ay0+ah/2);c.rotate(90);txt(c,0,0,'Friction Capability Composite (0–1)',7,align='center',color=MUT);c.restoreState()
# Panel B uses a rank table instead of point labels, preventing collisions.
bx=610;top=380;rh=29;ord_rank={r['doctor_model']:f(r['ordinal_grade_median4_rank']) for r in models};fri_rank={r['doctor_model']:f(r['friction_capability_composite_median4_rank']) for r in models}
txt(c,bx,top+22,'B',9,bold=True);txt(c,bx+58,top+22,'Model',7,bold=True);txt(c,bx+250,top+22,'Ordinal',7,align='center',bold=True);txt(c,bx+310,top+22,'FCC',7,align='center',bold=True)
for i,m in enumerate(order):
    y=top-i*rh; box(c,bx,y-8,340,rh,PALE if i%2==0 else colors.white,GRID); fit(c,bx+10,y,m,214,6.7,align='left',bold=i<3,min_size=5.3);txt(c,bx+250,y,f'{ord_rank[m]:g}',6.8,align='center');txt(c,bx+310,y,f'{fri_rank[m]:g}',6.8,align='center',bold=True,color=PURPLE)
txt(c,bx,54,'Rank 1 is best; average ranks are used for ties.',6.3,color=MUT);c.showPage();c.save()

# Main-text multidimensional Judge agreement figure.
ag=read('agreement/friction_judge_agreement_by_metric.csv');w,h=1040,610;c=canvas.Canvas(str(OUT/'cl120_fig_friction_judge_agreement.pdf'),pagesize=(w,h));title(c,w,h,'Four-Judge agreement for friction-centered measurements','Absolute agreement is limited for a single Judge, improves after four-Judge averaging, and is generally higher for model ordering than for individual trajectories.')
labels={'Friction Capability Composite':'FCC','Safety-gated C-RWR':'C-RWR','Information repair':'Information repair','Constraint navigation':'Constraint navigation','Dynamic reprioritization':'Dynamic reprioritization','Execution-loop repair':'Execution-loop repair','State-sensitive communication':'State-sensitive communication','Bounded closure/continuity':'Bounded closure/continuity'}
left=218;right=w-35;top=487;bottom=96;rh=(top-bottom)/len(ag)
# common grid in three panels
panels=[(left,235,'A  Absolute-agreement ICC'),(left+280,235,'B  Trajectory-level Spearman range'),(left+560,235,'C  Ten-model rank Spearman range')]
for x,pw,lab in panels:
    txt(c,x,top+44,lab,7.2,bold=True)
    for v in [0,.25,.5,.75,1]:
        xx=x+pw*v;c.setStrokeColor(GRID);c.line(xx,bottom,xx,top);txt(c,xx,bottom-15,f'{v:.2f}',5.8,align='center',color=MUT)
for i,r in enumerate(ag):
    y=top-(i+.5)*rh
    fit(c,left-12,y-2,labels[r['metric_label']],170,7.0,align='right',bold=i<2,min_size=5.8)
    txt(c,left-185,y-12,f"n={r['n_trajectories']}",5.3,color=MUT)
    if i%2==0: box(c,left,bottom+(len(ag)-i-1)*rh,795,rh,HexColor('#F7F9F9'),HexColor('#F7F9F9'))
    # A two points with CI bars
    x,pw=left,235
    for key,lo,hi,col,dy in [('icc_a1','icc_a1_ci95_low','icc_a1_ci95_high',ORANGE,-5),('icc_a4','icc_a4_ci95_low','icc_a4_ci95_high',TEAL,5)]:
        xx=x+pw*f(r[key]);xl=x+pw*f(r[lo]);xh=x+pw*f(r[hi]);c.setStrokeColor(col);c.setLineWidth(1.7);c.line(xl,y+dy,xh,y+dy);c.setFillColor(col);c.circle(xx,y+dy,3.2,fill=1,stroke=0)
    # B/C min-max bars, mean point
    for x,pw,lo,hi,mean,col in [(left+280,235,'pairwise_trajectory_spearman_min','pairwise_trajectory_spearman_max','pairwise_trajectory_spearman_mean',ORANGE),(left+560,235,'pairwise_model_rank_spearman_min','pairwise_model_rank_spearman_max','pairwise_model_rank_spearman_mean',PURPLE)]:
        xl=x+pw*f(r[lo]);xh=x+pw*f(r[hi]);xm=x+pw*f(r[mean]);c.setStrokeColor(col);c.setLineWidth(3);c.line(xl,y,xh,y);c.setFillColor(col);c.circle(xm,y,3.5,fill=1,stroke=0)
# legends
txt(c,left,58,'ICC: ',6.4,bold=True);c.setFillColor(ORANGE);c.circle(left+31,61,3,fill=1,stroke=0);txt(c,left+39,58,'single Judge (A,1)',6.2,color=MUT);c.setFillColor(TEAL);c.circle(left+145,61,3,fill=1,stroke=0);txt(c,left+153,58,'mean of four (A,4)',6.2,color=MUT)
txt(c,left+280,58,'Range = six Judge pairs; point = pairwise mean.',6.2,color=MUT);txt(c,left+560,58,'Rank correlations use each Judge’s ten model means.',6.2,color=MUT)
c.showPage();c.save()

# Judge-specific six-domain heatmaps, split into four full-width bands for legibility.
jlong=read('friction_domains_judge_long.csv');judges=sorted({r['judge_model'] for r in jlong});display={r['judge_model_id']:r['display_name'] for r in read('../judge_model_identity.csv')}
w,h=1100,900;c=canvas.Canvas(str(OUT/'cl120_sfig_judge_specific_capability.pdf'),pagesize=(w,h));title(c,w,h,'Judge-specific six-domain capability profiles','Each band preserves one experimental reviewer. Differences are reported rather than calibrated away; every cell uses the same 0–1 scale.')
for pi,j in enumerate(judges):
    x0=35;y0=650-pi*205;lx=230;top=y0+128;bottom=y0-42;cw=118;rh=(top-bottom)/10
    z=[r for r in jlong if r['judge_model']==j];vals={(m,d):statistics.mean(f(r['domain_'+d]) for r in z if r['doctor_model']==m and r['domain_'+d]!='') for m in order for d in domains}
    txt(c,x0,y0+151,display.get(j,j),8.2,bold=True,color=PURPLE)
    for k,d in enumerate(domains): lines(c,lx+(k+.5)*cw,top+29,dlabels[d],5.8,bold=True)
    for i,m in enumerate(order):
        yy=top-(i+1)*rh;fit(c,lx-8,yy+rh*.30,m,180,5.7,align='right',bold=i<3,min_size=4.9)
        for k,d in enumerate(domains):
            v=vals[(m,d)];box(c,lx+k*cw,yy,cw,rh,heat(v));txt(c,lx+(k+.5)*cw,yy+rh*.30,f'{v:.2f}',5.3,align='center',color=colors.white if v>=.72 else INK)
legend(c,38,25,160);c.showPage();c.save()
print('generated',len(list(OUT.glob('*.pdf'))),'friction figures; cases=',len(cases),'coverage_total=',sum(counts.values()))
