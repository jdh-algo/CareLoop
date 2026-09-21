import csv, json, subprocess, sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"public_supplement/cl120_20260911/analysis/care_progression"

def test_care_progression_release_outputs():
    subprocess.run([sys.executable,str(ROOT/"scripts/analyze_care_progression.py"),"--repo-root",str(ROOT)],check=True,capture_output=True,text=True)
    summary=json.loads((OUT/"analysis_summary.json").read_text())
    assert summary["cases"]==120
    assert summary["trajectories"]==1200
    assert summary["judge_records"]==4800
    assert summary["judge_ho_assessments"]==19200
    assert summary["terminal_counts"]["closed_success"]==1106
    assert summary["terminal_counts"]["open_at_100"]==56

def test_domain_contributions_are_documented_as_diagnostic():
    rows=list(csv.DictReader((OUT/"fcc_domain_contribution_by_model.csv").open()))
    assert len(rows)==10
    assert all("fcc_primary_median4" in r for r in rows)
    assert max(float(r["fcc_primary_median4"]) for r in rows)>0.8

def test_split_halves_cover_both_endpoints():
    rows=list(csv.DictReader((OUT/"stratified_split_half_rank_stability.csv").open()))
    assert {r["metric"] for r in rows}=={"FCC","C-RWR"}
    assert all(int(r["resamples"])==5000 for r in rows)
