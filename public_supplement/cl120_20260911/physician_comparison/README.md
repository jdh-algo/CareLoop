# Blinded physician comparison

This directory contains the released physician comparison for the CL120 experiment.

## Design

- The final analytic panel comprised 139 physicians from tertiary or higher-level hospitals in China: 64 residents, 51 attendings, and 24 associate-chief or chief physicians.
- Physicians were assigned within the 13 frozen CL120 clinical domains.
- All 1,200 trajectories were reviewed, yielding 6,000 separately collected structured assessments.
- A case and its 10 tested-model trajectories formed the minimum assignment packet. The same five physicians reviewed all 10 trajectories for that case: two residents, two attendings, and one associate-chief/chief physician.
- Tested-model identity and trajectory provenance were absent from clinician-facing task materials and linked to the released records only after review completion.
- Released clinical fields cover responsibility-chain status, high-order-goal completion, five foundational clinical dimensions, bounded closure, and important non-chain defects.

The analysis endpoints are FCC, C-RWR, and the six CareLoop capability domains computed deterministically from these structured fields. A derived 1–5 score is not treated as physician-authored data.

## Files

- `physician_review_instructions_zh.md`: Chinese clinician-facing instructions used for the structured trajectory reviews. The structured severity label `关键质量缺陷` maps deterministically to the machine scoring category `strong_blocker`.
- `physician_title_group_reviews.jsonl`: 6,000 structured assessments linked to public `packet_id` values. Each row has a unique `review_id`, one opaque pseudonymous `reviewer_id`, and a three-level `physician_title_group`. No real identity mapping or institution name is included.
- `physician_case_panel_assignments.tsv`: auditable case-level assignment table. Each of 120 cases has one fixed five-physician panel assignment and each listed physician reviewed all 10 model trajectories for that case. The assignments involve 105 unique five-person combinations because some physicians and some exact panels reviewed more than one case.
- `analyze_physician_llm_rank_concordance.py`: scoring, fixed-panel integrity checks, workload summaries, and rank-concordance analysis.
- `generate_physician_comparison_figures.py`: deterministic generation of the publication figures.
- `../../../scripts/audit_physician_comparison_release.py`: fail-closed checks for record counts, title composition, fixed case panels, domain consistency, structured-field logic, and public-field safety.
- `physician_llm_rank_concordance.json`: complete machine-readable results.
- `case_level_10model_rank_concordance.tsv` and `case_level_10model_rank_concordance_summary.tsv`: case-wise physician-consensus--Judge comparisons, tie-aware top-3 overlap, and their summaries.
- `panel_cluster_bootstrap_sensitivity.tsv`: exact-five-physician-panel cluster-bootstrap sensitivity intervals for the primary FCC/C-RWR comparisons.
- `evaluator_pairwise_case_rank_concordance.tsv`: all pairwise comparisons among the three physician-title streams and four individual LLM Judges, calculated within case across the same 10 models.
- `evaluator_class_case_rank_concordance.tsv`: case-wise symmetric summaries of physician-internal concordance and each Judge's concordance with the three physician strata.
- `within_title_case_rank_concordance.tsv`: resident-pair and attending-pair rank agreement within each fixed case panel.
- `aggregate_10model_rank_concordance.tsv`: descriptive aggregate 10-model rankings; these have only 10 model-level observations and are not the primary physician result.
- `clinical_domain_panel_summary.tsv` and `reviewer_workload_summary.tsv`: assignment summaries derived from the released case-panel table.
- `figures/`: one main-text and two Supplementary publication figures.

## Primary analysis

The assignment design controls the case and reviewer panel while the tested model changes. The primary estimand is therefore the ordering of the same 10 tested models within each case. For every case, Spearman rank correlation is calculated across those 10 trajectories. The resulting 120 case-level coefficients are summarized by their median, interquartile range, and a 95% bootstrap interval obtained by resampling cases. Because 105 unique five-person combinations generated the 120 case assignments, an additional cluster bootstrap resamples exact panel combinations and retains all cases assigned to each sampled combination. Top-3 overlap uses fractional membership at the third-place cutoff, avoiding dependence on model order when scores are tied.

Within each trajectory, the two resident assessments and the two attending assessments are summarized by within-title medians. The single associate-chief/chief assessment represents the senior stratum. The three title strata receive equal weight through a second median to form physician consensus. Direct all-five median, all-five mean, and resident-plus-attending-only summaries are retained as sensitivity analyses.

The symmetric evaluator comparison uses seven streams: resident-title median, attending-title median, senior assessment, and the four individual LLM Judges. Within each case, physician-internal concordance is the median of the three title-stratum pair correlations. Each Judge summary is the median of its three correlations with the physician strata. These case-wise quantities are then summarized across 120 cases. This comparison assesses reproducibility under the CareLoop protocol; it does not rank clinical expertise or treat physicians as an error-free gold standard.
Paired case-level differences and bootstrap intervals are reported in `evaluator_class_case_rank_concordance.tsv`. Positive values mean that a Judge's median concordance with the three physician-title strata exceeded the physician-internal median for the same case; negative values indicate the reverse. The GPT-5.5 and GPT-5.6-Sol comparisons were higher on both FCC and C-RWR, while DeepSeek-V4-Pro was lower and GLM-5 was lower except for an FCC interval that included zero. This is a protocol-concordance result and does not establish comparative clinical expertise.

Aggregate 10-model ranking is retained only as descriptive context because it collapses the experiment to 10 model-level observations. Pooled cross-case trajectory correlations and exact-score agreement are not used as primary evidence: panels differ across cases, so those summaries can mix model performance, case content, and panel calibration.

## Privacy and provenance

Released `reviewer_id` values are opaque pseudonyms that support fixed-panel and repeated-assessment audits. The private identity mapping, physician names, institution names, contact details, recruitment records, and credential-verification materials are excluded. The public files establish assignment and record consistency within the released study data.

## Reproduce

From the repository root:

```bash
python -m pip install -e '.[analysis]'
python scripts/audit_physician_comparison_release.py
python public_supplement/cl120_20260911/physician_comparison/analyze_physician_llm_rank_concordance.py
python public_supplement/cl120_20260911/physician_comparison/generate_physician_comparison_figures.py
```
