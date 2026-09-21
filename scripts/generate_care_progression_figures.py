#!/usr/bin/env python3
"""Render the released CareLoop care-progression figures with Pillow."""
from pathlib import Path
import argparse, csv, html
from PIL import Image, ImageDraw, ImageFont

COLORS={"blue":"#1f5a85","teal":"#2a9d8f","orange":"#e76f51","navy":"#183a59","grey":"#687681","grid":"#e5e9ec","ink":"#17202a"}
def rows(p):
    with p.open(newline="") as f:return list(csv.DictReader(f))
def font(size,bold=False):
    names = [
        "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ]
    for name in names:
        try:
            return ImageFont.truetype(name, size, index=1 if bold and name.endswith(".ttc") else 0)
        except OSError:
            continue
    return ImageFont.load_default()
def txt(d,xy,s,size=24,anchor="la",bold=False,fill=None):d.text(xy,str(s),font=font(size,bold),fill=fill or COLORS["ink"],anchor=anchor)
def save(img,p):img.save(p,optimize=True,dpi=(220,220))

def figure_ontology(out):
    """Render the conceptual task ontology as both PNG and editable SVG."""
    width, height = 2200, 780
    im = Image.new("RGB", (width, height), "white")
    d = ImageDraw.Draw(im)

    def box_png(x, y, w, h, title, subtitle, fill):
        d.rounded_rectangle((x, y, x + w, y + h), 18, fill=fill, outline="#d7dee4", width=2)
        txt(d, (x + w / 2, y + 48), title, 27, "mm", True)
        txt(d, (x + w / 2, y + 83), subtitle, 19, "mm", False, "#43515c")

    def arrow_png(x1, y1, x2, y2, color="#355b73"):
        d.line((x1, y1, x2 - 12, y2), fill=color, width=6)
        d.polygon([(x2, y2), (x2 - 18, y2 - 10), (x2 - 18, y2 + 10)], fill=color)

    txt(d, (70, 65), "From latent limitations to observable care consequences", 40, "la", True)
    xs, y, w, h = [55, 395, 735, 1075, 1415, 1755], 150, 275, 125
    boxes = [
        ("Distributed state", "truth · belief · record", "#edf4f7"),
        ("Real-world friction", "hidden · delayed · infeasible", "#fff1e9"),
        ("Behavioral fork", "ask · verify · act · wait", "#eef6f3"),
        ("World response", "patient · family · system", "#edf4f7"),
        ("Plan revision", "reprioritize · repair", "#eef6f3"),
        ("Episode state", "safe closure · bounded open", "#fff1e9"),
    ]
    for i, values in enumerate(boxes):
        box_png(xs[i], y, w, h, *values)
        if i < len(boxes) - 1:
            arrow_png(xs[i] + w + 8, y + h / 2, xs[i + 1] - 10, y + h / 2)
    # A dashed cubic return path represents delayed evidence reopening the plan.
    p0, p1, p2, p3 = (1890, 300), (1720, 440), (1120, 430), (890, 305)
    curve = []
    for step in range(101):
        t = step / 100
        x = (1-t)**3*p0[0] + 3*(1-t)**2*t*p1[0] + 3*(1-t)*t*t*p2[0] + t**3*p3[0]
        yv = (1-t)**3*p0[1] + 3*(1-t)**2*t*p1[1] + 3*(1-t)*t*t*p2[1] + t**3*p3[1]
        curve.append((x, yv))
    for start in range(0, len(curve)-1, 6):
        d.line(curve[start:min(start+4, len(curve))], fill=COLORS["teal"], width=5)
    arrow_png(930, 326, 890, 305, COLORS["teal"])
    txt(d, (1390, 455), "Later evidence can invalidate earlier assumptions and reopen the plan", 22, "mm", False, "#267b70")
    txt(d, (70, 500), "Observable diagnostic readout", 28, "la", True)
    readout = [
        ("Opportunity", "Was the designed pressure point realized?"),
        ("Action", "Did the model respond actively?"),
        ("Impact", "Did the response change the trajectory?"),
        ("Continuity", "Was residual risk safely closed or owned?"),
    ]
    for i, values in enumerate(readout):
        x = 90 + i * 505
        box_png(x, 535, 430, 110, *values, "#eef6f3" if i % 2 else "#edf4f7")
        if i < len(readout) - 1:
            arrow_png(x + 438, 590, x + 495, 590)
    txt(d, (70, 720), "Responsibility and minimum duties constrain safety throughout the process; they are guardrails around friction-sensitive care progression.", 22, "la", False, "#43515c")
    save(im, out / "careloop_task_ontology.png")

    def esc(value):
        return html.escape(str(value), quote=True)
    def svg_text(x, y, value, size=28, anchor="middle", weight="600", fill="#17202a"):
        return f'<text x="{x}" y="{y}" font-family="Arial,Helvetica,sans-serif" font-size="{size}" text-anchor="{anchor}" font-weight="{weight}" fill="{fill}">{esc(value)}</text>'
    def svg_box(x, y, w, h, title, subtitle, fill):
        return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="18" fill="{fill}" stroke="#d7dee4" stroke-width="2"/>' + svg_text(x+w/2,y+48,title,27) + svg_text(x+w/2,y+83,subtitle,19,weight="400",fill="#43515c")
    def svg_arrow(x1, y1, x2, y2):
        return f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="#355b73" stroke-width="6" marker-end="url(#a)"/>'
    body = '<defs><marker id="a" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto"><path d="M0,0 L0,6 L9,3 z" fill="#355b73"/></marker></defs>'
    body += svg_text(70, 65, "From latent limitations to observable care consequences", 40, "start", "700")
    for i, values in enumerate(boxes):
        body += svg_box(xs[i], y, w, h, *values)
        if i < len(boxes) - 1:
            body += svg_arrow(xs[i] + w + 8, y + h / 2, xs[i + 1] - 10, y + h / 2)
    body += '<path d="M 1890 300 C 1720 440, 1120 430, 890 305" fill="none" stroke="#2a9d8f" stroke-width="5" stroke-dasharray="12 9" marker-end="url(#a)"/>'
    body += svg_text(1390, 430, "Later evidence can invalidate earlier assumptions and reopen the plan", 22, weight="500", fill="#267b70")
    body += svg_text(70, 500, "Observable diagnostic readout", 28, "start", "700")
    for i, values in enumerate(readout):
        x = 90 + i * 505
        body += svg_box(x, 535, 430, 110, *values, "#eef6f3" if i % 2 else "#edf4f7")
        if i < len(readout) - 1:
            body += svg_arrow(x + 438, 590, x + 495, 590)
    body += svg_text(70, 720, "Responsibility and minimum duties constrain safety throughout the process; they are guardrails around friction-sensitive care progression.", 22, "start", "500", "#43515c")
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}"><rect width="100%" height="100%" fill="white"/>{body}</svg>'
    (out / "careloop_task_ontology.svg").write_text(svg, encoding="utf-8")

def figure_conversion(data,out):
    im=Image.new("RGB",(2200,1600),"white");d=ImageDraw.Draw(im)
    overall=rows(data/"ho_opportunity_stage_overall.csv")[0]
    types=sorted(rows(data/"ho_opportunity_stage_by_type.csv"),key=lambda r:float(r["effective_completion_rate"]))
    models=sorted(rows(data/"ho_opportunity_stage_by_model.csv"),key=lambda r:float(r["action_to_impact_gap"]))
    terminal={r["quantity"]:int(r["count"]) for r in rows(data/"terminal_status_closure_quality_summary.csv")}
    txt(d,(80,55),"CareLoop exposes where action fails to become care progress",42,bold=True)
    txt(d,(80,125),"A. From realized opportunity to impact",29,bold=True)
    vals=[1,float(overall["active_action_rate"]),float(overall["meaningful_impact_rate"]),float(overall["effective_completion_rate"])]
    labs=["Triggered opportunity","Active action","Meaningful impact","Effective completion"]
    cols=[COLORS["grey"],COLORS["blue"],COLORS["teal"],COLORS["navy"]]
    for i,v in enumerate(vals):
        x=100+i*235;h=v*370;y=560-h;d.rounded_rectangle((x,y,x+160,560),8,fill=cols[i]);txt(d,(x+80,y-15),f"{v:.3f}",25,"ms",True);txt(d,(x+80,590),labs[i],20,"ma")
    d.line((80,560,1030,560),fill=COLORS["ink"],width=2)
    txt(d,(1140,125),"B. Effective completion differs by opportunity",29,bold=True)
    hmap={"hidden_state_discovery":"Hidden-state discovery","safe_bounded_closure":"Safe bounded closure","false_positive_or_false_negative_verification":"Result verification","patient_misinformation_correction":"Misinformation correction","responsibility_chain_repair":"Execution-chain repair","dynamic_reprioritization":"Dynamic reprioritization","real_world_constraint_navigation":"Constraint navigation","patient_state_sensitive_communication":"State-sensitive communication"}
    for i,r in enumerate(types):
        y=180+i*56;v=float(r["effective_completion_rate"]);txt(d,(1140,y+18),hmap[r["ho_type"]],19);d.rounded_rectangle((1510,y,1510+500*v,y+32),4,fill=COLORS["teal"]);txt(d,(1520+500*v,y+18),f"{v:.2f}",18)
    d.line((1510,650,2010,650),fill=COLORS["ink"],width=2);txt(d,(1510,675),"0",18,"ma");txt(d,(2010,675),"1",18,"ma")
    txt(d,(80,780),"C. Action-to-impact gap by tested model",29,bold=True)
    short={"Qwen3-30B-A3B-Instruct-2507":"Qwen3-30B","Doubao-Seed-2.0-lite":"Doubao-lite","DeepSeek-V4-Pro":"DeepSeek-V4"}
    for i,r in enumerate(models):
        y=830+i*55;v=float(r["action_to_impact_gap"]);txt(d,(80,y+18),short.get(r["doctor_model"],r["doctor_model"]),19);d.rounded_rectangle((320,y,320+520*v/.31,y+31),4,fill=COLORS["orange"]);txt(d,(330+520*v/.31,y+18),f"{v:.3f}",18)
    d.line((320,1405,840,1405),fill=COLORS["ink"],width=2);txt(d,(320,1435),"0",18,"ma");txt(d,(840,1435),"0.31",18,"ma")
    txt(d,(1140,780),"D. Runtime termination and closure quality diverge",29,bold=True)
    tv=[("Closed trajectories",terminal["closed_success"],COLORS["grey"]),("Closed; ≥2 premature votes",terminal["closed_success_premature_2plus_votes"],COLORS["orange"]),("Open at 100 turns",terminal["open_at_100"],COLORS["grey"]),("Open; ≥3 valid-open votes",terminal["open_at_100_valid_open_3plus_votes"],COLORS["teal"])]
    for i,(lab,v,col) in enumerate(tv):
        y=840+i*120;txt(d,(1140,y+24),lab,21);d.rounded_rectangle((1510,y,1510+520*v/1106,y+42),5,fill=col);txt(d,(1525+520*v/1106,y+24),v,21,bold=True)
    save(im,out/"cl120_fig_care_progression_conversion.png")

def figure_stability(data,out):
    im=Image.new("RGB",(2200,1240),"white");d=ImageDraw.Draw(im);txt(d,(80,55),"Capability sources and benchmark-level stability",42,bold=True)
    stab=rows(data/"ranking_stability_by_case_count.csv");split=rows(data/"stratified_split_half_rank_stability.csv");con=sorted(rows(data/"fcc_domain_contribution_by_model.csv"),key=lambda r:-float(r["fcc_primary_median4"]))
    txt(d,(80,125),"A. Rankings stabilize with case coverage",29,bold=True);x0,y0,pw,ph=150,650,780,440
    d.line((x0,y0,x0+pw,y0),fill=COLORS["ink"],width=2);d.line((x0,y0-ph,x0,y0),fill=COLORS["ink"],width=2)
    for t in range(6):
        y=y0-t*ph/5;d.line((x0,y,x0+pw,y),fill=COLORS["grid"],width=1);txt(d,(x0-20,y),f"{t/5:.1f}",18,"rm")
    for metric,col in [("FCC",COLORS["blue"]),("C-RWR",COLORS["teal"])]:
        pts=[]
        for r in stab:
            if r["metric"]==metric:pts.append((x0+float(r["n_cases"])/120*pw,y0-float(r["median_spearman"])*ph))
        d.line(pts,fill=col,width=7,joint="curve")
        for x,y in pts:d.ellipse((x-9,y-9,x+9,y+9),fill=col)
    for n in range(0,121,20):txt(d,(x0+n/120*pw,y0+35),n,18,"ma")
    txt(d,(520,715),"Number of cases",22,"ma");txt(d,(660,190),"FCC",20,bold=True,fill=COLORS["blue"]);txt(d,(770,190),"C-RWR",20,bold=True,fill=COLORS["teal"])
    st=" | ".join(f"{r['metric']}: disjoint 60-vs-60 median rho {float(r['median_spearman']):.3f}" for r in split);txt(d,(110,765),st,18)
    txt(d,(1080,125),"B. Domain contributions to model separation",29,bold=True)
    domains=["information_repair","constraint_navigation","dynamic_reprioritization","execution_loop_repair","state_sensitive_communication","bounded_closure_continuity"]; dl=["Information","Constraints","Reprioritize","Execution","Communication","Closure"]
    vals=[float(r[x+"_vs_grand"]) for r in con for x in domains];vmax=max(map(abs,vals));hx,hy,cw,ch=1320,205,125,85
    for j,l in enumerate(dl):txt(d,(hx+j*cw+cw/2,hy-18),l,17,"ms",True)
    short={"Qwen3-30B-A3B-Instruct-2507":"Qwen3-30B","Doubao-Seed-2.0-lite":"Doubao-lite","DeepSeek-V4-Pro":"DeepSeek-V4"}
    for i,r in enumerate(con):
        txt(d,(hx-20,hy+i*ch+42),short.get(r["doctor_model"],r["doctor_model"]),18,"rm")
        for j,x in enumerate(domains):
            v=float(r[x+"_vs_grand"]);z=min(1,abs(v)/vmax)
            col=(int(245-180*z),int(250-120*z),int(255-70*z)) if v>=0 else (int(255-35*z),int(245-105*z),int(240-145*z))
            d.rectangle((hx+j*cw,hy+i*ch,hx+(j+1)*cw-3,hy+(i+1)*ch-3),fill=col);txt(d,(hx+j*cw+cw/2,hy+i*ch+42),f"{v:+.3f}",16,"mm",True)
    txt(d,(1080,1120),"Blue: above the across-model contribution; red: below.",18);txt(d,(1080,1160),"The diagnostic decomposition uses additive Judge-cell FCC; primary ranks use median-of-four.",17)
    save(im,out/"cl120_fig_gap_sources_and_stability.png")

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--repo-root',type=Path,default=Path(__file__).resolve().parents[1]);a=ap.parse_args();root=a.repo_root.resolve();data=root/'public_supplement/cl120_20260911/analysis/care_progression';out=root/'public_supplement/cl120_20260911/figures/care_progression';out.mkdir(parents=True,exist_ok=True);figure_ontology(out);figure_conversion(data,out);figure_stability(data,out)
if __name__=='__main__':main()
