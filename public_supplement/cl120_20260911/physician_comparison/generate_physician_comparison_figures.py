#!/usr/bin/env python3
"""Generate deterministic vector figures for the fixed-case-panel analysis."""
from pathlib import Path
import csv, math
from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor, Color, black, white

HERE=Path(__file__).resolve().parent; OUT=HERE/'figures'; OUT.mkdir(exist_ok=True)
NAVY=HexColor('#17313a'); GRID=HexColor('#d7e0e3'); GREY=HexColor('#6b7378')
COLORS=[GREY,HexColor('#2f6f89'),HexColor('#3e8c8c'),HexColor('#d97941'),HexColor('#b64b4b')]
GROUPS=['Physician title strata','GPT-5.5','GPT-5.6-Sol','DeepSeek-V4-Pro','GLM-5']
METRIC={'FCC':'FCC','C_RWR':'C-RWR','information_repair':'Information repair','constraint_navigation':'Constraint navigation','dynamic_reprioritization':'Dynamic reprioritization','execution_loop_repair':'Execution-loop repair','state_sensitive_communication':'State-sensitive communication','bounded_closure_continuity':'Bounded closure & continuity'}

def rows(name):
    with (HERE/name).open(encoding='utf-8',newline='') as f:return list(csv.DictReader(f,delimiter='\t'))
def fnum(x):
    try:return float(x)
    except:return math.nan
def text(c,x,y,s,size=8,bold=False,center=False,right=False,color=black):
    c.setFont('Helvetica-Bold' if bold else 'Helvetica',size); c.setFillColor(color)
    (c.drawCentredString if center else c.drawRightString if right else c.drawString)(x,y,str(s))
def mix(value,lo=-.1,hi=.75):
    t=max(0,min(1,(value-lo)/(hi-lo))); a=(247,244,238); b=(31,111,120)
    return Color(*[(a[i]+(b[i]-a[i])*t)/255 for i in range(3)])
def axis(c,x0,y0,w,h,xmin=-.1,xmax=.75):
    c.setStrokeColor(GRID); c.setLineWidth(.6)
    for tick in [-.0,.2,.4,.6]:
        x=x0+(tick-xmin)/(xmax-xmin)*w; c.line(x,y0,x,y0+h); text(c,x,y0-14,f'{tick:.1f}',7,center=True,color=GREY)
    c.setStrokeColor(NAVY); c.line(x0,y0,x0+w,y0)
def xpos(v,x0,w,xmin=-.1,xmax=.75):return x0+(v-xmin)/(xmax-xmin)*w

def main():
    data=rows('evaluator_class_case_rank_concordance.tsv'); W,H=900,400; c=canvas.Canvas(str(OUT/'cl120_fig_physician_evaluator_concordance.pdf'),pagesize=(W,H),invariant=1)
    text(c,30,H-32,'Case-panel concordance across the same 10 tested models',14,True,color=NAVY)
    for p,metric in enumerate(['FCC','C_RWR']):
        x0=175+p*355; y0=70; w=285; h=245; axis(c,x0,y0,w,h)
        text(c,x0+w/2,H-65,METRIC[metric],11,True,center=True,color=NAVY)
        sub={r['comparison_group']:r for r in data if r['metric']==metric}
        for i,(g,col) in enumerate(zip(GROUPS,COLORS)):
            y=y0+h-30-i*47; r=sub[g]; med=fnum(r['median_rho']); lo=fnum(r['median_case_bootstrap_ci_low']); hi=fnum(r['median_case_bootstrap_ci_high'])
            if p==0:text(c,x0-10,y-3,g,8.5,right=True,color=NAVY)
            c.setStrokeColor(col);c.setLineWidth(2.5);c.line(xpos(lo,x0,w),y,xpos(hi,x0,w),y)
            c.setFillColor(col);c.circle(xpos(med,x0,w),y,4.2,fill=1,stroke=0); text(c,xpos(hi,x0,w)+7,y-3,f'{med:.2f}',8,color=NAVY)
        text(c,x0+w/2,38,'Median within-case Spearman rho (95% case-bootstrap CI)',8,center=True,color=GREY)
    text(c,W/2,15,'Each case contributes one summary of three pairwise correlations; the case is the independent unit.',8,center=True,color=GREY)
    c.save()

def heatmap():
    pair=rows('evaluator_pairwise_case_rank_concordance.tsv'); case=rows('case_level_10model_rank_concordance_summary.tsv')
    W,H=960,720;c=canvas.Canvas(str(OUT/'cl120_sfig_physician_judge_rank_concordance.pdf'),pagesize=(W,H),invariant=1)
    text(c,30,H-28,'Physician-Judge rank concordance under fixed case panels',14,True,color=NAVY)
    titles=['Resident physician','Attending physician','Associate-chief/chief physician']; tl=['Resident','Attending','Senior']; judges=['GPT-5.5','GPT-5.6-Sol','DeepSeek-V4-Pro','GLM-5']
    def matrix(x0,y0,metric,letter):
        cw=82;ch=42
        text(c,x0,y0+3*ch+40,f'{letter}. {METRIC[metric]}: title stratum vs Judge',10,True,color=NAVY)
        for j,q in enumerate(judges):text(c,x0+(j+.5)*cw,y0+3*ch+17,q,7.5,center=True,color=NAVY)
        for i,t in enumerate(titles):
            yy=y0+(2-i)*ch;text(c,x0-8,yy+ch/2-3,tl[i],8,right=True,color=NAVY)
            for j,q in enumerate(judges):
                r=next(z for z in pair if z['metric']==metric and {z['evaluator_1'],z['evaluator_2']}=={t,q}); v=fnum(r['median_rho']); n=int(r['n_cases_defined']); xx=x0+j*cw
                c.setFillColor(mix(v));c.setStrokeColor(white);c.rect(xx,yy,cw,ch,fill=1,stroke=1);text(c,xx+cw/2,yy+24,f'{v:.2f}',9,True,center=True);text(c,xx+cw/2,yy+9,f'n={n}',6.5,center=True,color=GREY)
    matrix(115,485,'FCC','A');matrix(570,485,'C_RWR','B')
    domains=list(METRIC)[2:];x0=275;y0=90;cw=130;ch=45
    text(c,30,420,'C. Physician consensus vs Judge across six capability domains',10,True,color=NAVY)
    for j,q in enumerate(judges):text(c,x0+(j+.5)*cw,397,q,8,center=True,color=NAVY)
    for i,m in enumerate(domains):
        yy=y0+(5-i)*ch;text(c,x0-10,yy+ch/2-3,METRIC[m],8,right=True,color=NAVY)
        for j,q in enumerate(judges):
            r=next(z for z in case if z['metric']==m and z['comparison']==q);v=fnum(r['median_rho']);n=int(r['n_cases_defined']);xx=x0+j*cw
            c.setFillColor(mix(v));c.setStrokeColor(white);c.rect(xx,yy,cw,ch,fill=1,stroke=1);text(c,xx+cw/2,yy+26,f'{v:.2f}',9,True,center=True);text(c,xx+cw/2,yy+10,f'n={n}',6.5,center=True,color=GREY)
    # compact legend
    for i in range(86):c.setFillColor(mix(-.1+i*.01));c.rect(310+i*4,38,4,10,fill=1,stroke=0)
    text(c,300,25,'-0.1',7,right=True,color=GREY);text(c,658,25,'0.75',7,color=GREY);text(c,484,23,'Median within-case Spearman rho',8,center=True,color=GREY)
    c.save()

def percentile(x,p):
    x=sorted(x); pos=(len(x)-1)*p; lo=int(pos); hi=min(lo+1,len(x)-1); return x[lo]+(x[hi]-x[lo])*(pos-lo)
def distributions():
    data=rows('case_level_10model_rank_concordance.tsv'); labels=['GPT-5.5','GPT-5.6-Sol','Four-judge median','DeepSeek-V4-Pro','GLM-5']
    W,H=940,430;c=canvas.Canvas(str(OUT/'cl120_sfig_physician_case_rank_distributions.pdf'),pagesize=(W,H),invariant=1)
    text(c,30,H-30,'Distribution of physician-consensus-Judge rank concordance across 120 cases',14,True,color=NAVY)
    for p,metric in enumerate(['FCC','C_RWR']):
        x0=170+p*385;y0=70;w=300;h=260;axis(c,x0,y0,w,h,xmin=-.7,xmax=1)
        text(c,x0+w/2,H-63,METRIC[metric],11,True,center=True,color=NAVY)
        for i,(lab,col) in enumerate(zip(labels,COLORS[1:3]+[GREY]+COLORS[3:])):
            vals=[fnum(r['spearman_rho']) for r in data if r['metric']==metric and r['comparison']==lab and math.isfinite(fnum(r['spearman_rho']))]
            q1,md,q3=percentile(vals,.25),percentile(vals,.5),percentile(vals,.75); lo,hi=percentile(vals,.05),percentile(vals,.95);y=y0+h-30-i*48
            if p==0:text(c,x0-10,y-3,lab,8.5,right=True,color=NAVY)
            c.setStrokeColor(col);c.setFillColor(Color(col.red,col.green,col.blue,alpha=.55));c.line(xpos(lo,x0,w,-.7,1),y,xpos(hi,x0,w,-.7,1),y);c.rect(xpos(q1,x0,w,-.7,1),y-8,xpos(q3,x0,w,-.7,1)-xpos(q1,x0,w,-.7,1),16,fill=1,stroke=1);c.setStrokeColor(white);c.setLineWidth(1.5);c.line(xpos(md,x0,w,-.7,1),y-8,xpos(md,x0,w,-.7,1),y+8)
        text(c,x0+w/2,38,'Within-case Spearman rho across 10 models',8,center=True,color=GREY)
    c.save()
if __name__=='__main__':
    main();heatmap();distributions()
    for p in sorted(OUT.glob('*.pdf')):print(p)
