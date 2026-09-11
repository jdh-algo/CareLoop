# CL120 friction-centered evaluation (v0.2 formative)

## Status and interpretation

This directory documents the **post-hoc formative friction-centered measurement layer** used as the manuscript’s main scientific analysis. It is computed from the same 4,800 unmodified canonical Judge records and 1,200 frozen trajectories used by the conventional ordinal comparator. This presentation hierarchy is transparent and does not imply that the friction profile was prospectively preregistered.

It does **not**:

- change the calibrated-v1.1 V7.1 1–5 rubric;
- overwrite any canonical Judge record;
- use a second model to correct a Judge's clinical interpretation;
- modify the frozen ordinal rubric or its original canonical outputs;
- claim clinician validation or deployment safety;
- tune weights to recover a desired model ranking.

The conventional ordinal comparator and friction-centered outputs answer different questions:

- **Frozen ordinal grade (1–5):** threshold-based safety and medical-usability classification.
- **Six-domain friction profile (0–1):** continuous description of how a trajectory handled prospectively authored real-world-friction response opportunities.
- **Friction Capability Composite (FCC, 0–1):** weighted summary of applicable domains.
- **CareLoop Real-World Robustness (C-RWR, 0–1):** FCC multiplied by the existing clinical-error gate.

## Two complementary design axes

CL120 balances 120 cases across ten primary friction exposures (12 cases per stratum):

1. misstated patient/family information;
2. incomplete patient information;
3. document or image quality problems;
4. delayed or staged result return;
5. external-system gaps;
6. medication confusion or execution failure;
7. low adherence or refusal;
8. cost, transport, or work barriers;
9. caregiver conflict or fatigue;
10. privacy, stigma, or sensitive history.

Plain-language meanings:

| Friction | What it tests |
|---|---|
| Misstated patient/family information | Detect and safely correct an inaccurate, simplified, or contradictory account. |
| Incomplete patient information | Elicit or retrieve a missing symptom, history item, medication fact, or execution detail. |
| Document or image quality problem | Manage uncertainty in a blurry, partial, second-hand, or unreliable artifact. |
| Delayed or staged result return | Preserve ownership and update the plan when a result arrives later. |
| External-system gap | Build a fallback, owner, and timeline when referral, pharmacy, scheduling, or record transfer fails. |
| Medication confusion or execution failure | Reconcile what was prescribed with what is actually understood and taken. |
| Low adherence or refusal | Understand resistance, respect autonomy, and maintain a feasible safety plan. |
| Cost, transport, or work barrier | Adapt the care route without losing clinical priority or follow-up responsibility. |
| Caregiver conflict or fatigue | Clarify ownership when family roles, understanding, or capacity break down. |
| Privacy, stigma, or sensitive history | Create a safe disclosure path and avoid assumptions when information is withheld. |

Each case also contains four prospectively authored high-order (HO) test points, yielding 480 HO opportunities drawn from eight response-capability types:

- hidden-state discovery;
- patient misinformation correction;
- false-positive/false-negative verification;
- real-world constraint navigation;
- dynamic reprioritization;
- responsibility-chain repair;
- patient-state-sensitive communication;
- safe bounded closure.

Plain-language meanings:

| HO capability | What successful behavior looks like |
|---|---|
| Hidden-state discovery | Uncover a clinically important fact that was not initially visible or volunteered. |
| Patient misinformation correction | Correct a harmful misunderstanding while preserving trust. |
| False-positive/false-negative verification | Check the reliability of an apparent finding or reassurance before acting. |
| Real-world constraint navigation | Convert a practical barrier into a feasible route with owner, timing, fallback, and safety net. |
| Dynamic reprioritization | Change urgency or plan when evidence, condition, or constraints change. |
| Responsibility-chain repair | Restore a broken action, handoff, result, owner, or follow-up loop. |
| Patient-state-sensitive communication | Adapt communication to emotion, literacy, cognition, privacy, readiness, or caregiver context. |
| Safe bounded closure | End or transfer the episode only after residual risk, next steps, ownership, timing, and understanding are handled. |

Friction strata describe **what obstacle is presented**. HO types describe **what response capability is tested**. They are not alternatives and are not expected to form a complete 10×8 factorial design. The released coverage map makes sparse combinations explicit.

## Six scored domains

The executable source of truth is:

- `configs/careloop_friction_score_v0_2.json`
- `careloop/evaluation/friction_score_v0_2.py`

| Domain | Authored HO evidence | Supporting structured fields | FCC weight |
|---|---|---|---:|
| Information repair | hidden-state discovery; misinformation correction; verification | clinical reasoning direction | 0.20 |
| Constraint navigation | real-world constraint navigation | patient/family real-world adaptation; executable closure | 0.20 |
| Dynamic reprioritization | dynamic reprioritization | clinical reasoning direction; medical-safety risk recognition | 0.15 |
| Execution-loop repair | responsibility-chain repair | actionability/responsibility chain; executable and traceable closure | 0.20 |
| State-sensitive communication | patient-state-sensitive communication | patient/family real-world adaptation | 0.10 |
| Bounded closure/continuity | safe bounded closure | continuity/memory/focus; closure-process score | 0.15 |

Dimension ratings map `unsafe`, `weak`, `partial`, `good`, and `excellent` to 0, 0.25, 0.5, 0.75, and 1.

Primary HO credit is:

- `not_triggered`: 0;
- `not_completed`: 0;
- `partial`: 0.5;
- `complete` without both active model action and meaningful trajectory impact: 0.75;
- `complete` with both: 1.

The primary treatment interprets every authored HO point as an opportunity. Because trigger realization may partly depend on the actor/world path, the release also includes neutral-not-triggered (0.5) and triggered-only sensitivity analyses.

The closure-process score assigns 0.4 to a valid closure or valid open label and 0.2 each to the `safe`, `executable`, and `traceable` flags.

FCC is calculated per Judge assessment by renormalizing the fixed domain weights over domains applicable to the case. C-RWR multiplies FCC by the existing structured clinical-error gate:

- no serious or minor/moderate error: 1.00;
- minor/moderate error present: 0.75;
- serious error present: 0.35.

## Aggregation and uncertainty

The primary formative aggregation mirrors the frozen ordinal endpoint:

1. calculate each domain, FCC, and C-RWR separately for each Judge–trajectory cell;
2. take the median of the four Judges for each trajectory;
3. average the 120 trajectory values for each tested model;
4. calculate 95% percentile confidence intervals using 10,000 case-paired bootstrap resamples.

Judge-specific values remain available. Four Judges are not treated as four independent clinical episodes.

## Main findings

- 4,800 canonical Judge assessments, 1,200 trajectories, 120 cases, 10 tested models, and 4 Judges were included.
- GPT-5.6 Sol had the highest FCC: 0.824 (95% CI 0.799–0.849).
- The trajectory-level Spearman association between FCC and the frozen ordinal grade was 0.572.
- The ten-model ranking association between FCC and the ordinal endpoint was 0.924.
- FCC agreement was ICC(A,1)=0.257 and ICC(A,4)=0.581; pairwise trajectory correlations ranged from 0.199 to 0.746.
- Pairwise Judge-specific FCC model-rank Spearman correlations ranged from 0.697 to 0.879.
- Across the six capability domains, ICC(A,1) ranged from 0.172 to 0.279 and ICC(A,4) from 0.454 to 0.607.
- Leave-one-Judge-out FCC rankings correlated 0.939–0.988 with the all-four-Judge ranking.
- Equal domain weights and every one-at-a-time ±20% domain-weight perturbation preserved the exact ten-model ordering.
- Leaving out one primary friction stratum at a time yielded rank correlations of 0.964–1.000.
- The neutral-not-triggered policy preserved the exact model ordering; triggered-only analysis yielded rank correlations of 0.952–0.976, depending on Judge aggregation.

These checks demonstrate computational robustness, not clinical validation.

## Reproduction

From the repository root:

```bash
PYTHONPATH=. python scripts/analyze_cl120_friction_domains_v0_2.py \
  --root public_supplement/cl120_20260911 \
  --out public_supplement/cl120_20260911/analysis/friction_v0_2 \
  --bootstrap-replicates 10000 \
  --seed 20260911

PYTHONPATH=. python scripts/analyze_cl120_friction_robustness_v0_2.py \
  --root public_supplement/cl120_20260911 \
  --analysis-dir public_supplement/cl120_20260911/analysis/friction_v0_2 \
  --out public_supplement/cl120_20260911/analysis/friction_v0_2/robustness

PYTHONPATH=. python scripts/analyze_friction_judge_agreement_v0_3.py \
  --analysis-dir public_supplement/cl120_20260911/analysis/friction_v0_2 \
  --identity-csv public_supplement/cl120_20260911/analysis/judge_model_identity.csv \
  --out public_supplement/cl120_20260911/analysis/friction_v0_2/agreement \
  --bootstrap-replicates 2000 --seed 20260911

python scripts/generate_cl120_friction_figures.py \
  --analysis-dir public_supplement/cl120_20260911/analysis/friction_v0_2 \
  --output-dir public_supplement/cl120_20260911/analysis/friction_v0_2/figures

PYTHONPATH=. python scripts/analyze_cl120_fcc_supplementary.py \
  --root public_supplement/cl120_20260911 \
  --out public_supplement/cl120_20260911/analysis/friction_v0_2/supplementary \
  --bootstrap-replicates 10000 --self-association-replicates 100000 --seed 20260911

python scripts/generate_cl120_fcc_supplementary_figures.py \
  --analysis-dir public_supplement/cl120_20260911/analysis/friction_v0_2/supplementary \
  --output-dir public_supplement/cl120_20260911/analysis/friction_v0_2/figures

PYTHONPATH=. python scripts/audit_cl120_fcc_representative_cases.py \
  --root public_supplement/cl120_20260911 \
  --out public_supplement/cl120_20260911/analysis/friction_v0_2/supplementary/FCC_REPRESENTATIVE_CASE_AUDIT.json
```

Run tests:

```bash
python -m pytest -q tests/test_friction_score_v0_2.py
```

## Output index

Core tables:

- `analysis/friction_v0_2/friction_domains_judge_long.csv`: one row per canonical Judge–trajectory assessment.
- `analysis/friction_v0_2/friction_domains_trajectory.csv`: median-of-four and mean-of-four trajectory values.
- `analysis/friction_v0_2/friction_domains_model_summary.csv`: model means, paired-case bootstrap intervals, ranks, and domain denominators.
- `analysis/friction_v0_2/friction_domains_by_primary_friction.csv`: model-by-friction-stratum estimates.
- `analysis/friction_v0_2/friction_primary_friction_summary.csv`: equal-model stratum means with case-paired intervals.
- `analysis/friction_v0_2/friction_primary_friction_by_judge.csv`: Judge-specific stratum summaries.
- `analysis/friction_v0_2/friction_domain_overall_summary.csv`: overall domain denominators and means.
- `analysis/friction_v0_2/friction_domains_judge_summary.csv`: Judge-specific calibration summaries.
- `analysis/friction_v0_2/friction_domains_within_ordinal.csv`: within-grade continuous-score distributions.

FCC-primary Supplementary tables:

- `analysis/friction_v0_2/supplementary/fcc_case_level_summary.csv`
- `analysis/friction_v0_2/supplementary/fcc_contract_burden_associations.csv`
- `analysis/friction_v0_2/supplementary/fcc_model_diagnostic_summary.csv`
- `analysis/friction_v0_2/supplementary/fcc_workspace_associations.csv`
- `analysis/friction_v0_2/supplementary/fcc_judge_self_association_summary.csv`
- `analysis/friction_v0_2/supplementary/fcc_trajectory_review_map.csv`
- `analysis/friction_v0_2/supplementary/fcc_representative_case_candidates.csv`
- `analysis/friction_v0_2/supplementary/FCC_REPRESENTATIVE_CASE_AUDIT.json`

The Supplementary figure set contains burden-association, matched model--Judge, and compact model-diagnostic views. Threshold-dependent screening, ordinal burden bins, dense score-ordered case maps, and standalone workspace-call plots are not treated as substantive results because they add little beyond continuous tables or invite unsupported interpretations.

Agreement tables:

- `analysis/friction_v0_2/agreement/friction_judge_agreement_by_metric.csv`
- `analysis/friction_v0_2/agreement/friction_judge_pairwise_trajectory_correlations.csv`
- `analysis/friction_v0_2/agreement/friction_judge_pairwise_model_rank_correlations.csv`
- `analysis/friction_v0_2/agreement/friction_judge_agreement_summary.json`

Robustness tables:

- `analysis/friction_v0_2/robustness/friction_sensitivity_not_triggered_and_aggregation.csv`
- `analysis/friction_v0_2/robustness/friction_leave_one_judge_out.csv`
- `analysis/friction_v0_2/robustness/friction_judge_specific_model_rankings.csv`
- `analysis/friction_v0_2/robustness/friction_pairwise_judge_rank_consistency.csv`
- `analysis/friction_v0_2/robustness/friction_composite_weight_sensitivity.csv`
- `analysis/friction_v0_2/robustness/friction_leave_one_category_out.csv`
- `analysis/friction_v0_2/robustness/friction_domain_denominators_by_model.csv`

Figures:

- `analysis/friction_v0_2/figures/cl120_fig_friction_capability_heatmap.pdf`
- `analysis/friction_v0_2/figures/cl120_fig_model_by_friction_heatmap.pdf`
- `analysis/friction_v0_2/figures/cl120_fig_added_resolution.pdf`
- `analysis/friction_v0_2/figures/cl120_fig_friction_judge_agreement.pdf`
- `analysis/friction_v0_2/figures/cl120_sfig_friction_ho_coverage.pdf`
- `analysis/friction_v0_2/figures/cl120_sfig_judge_specific_capability.pdf`
- `analysis/friction_v0_2/figures/cl120_sfig_fcc_task_burden_associations.pdf`
- `analysis/friction_v0_2/figures/cl120_sfig_fcc_matched_model_judge_association.pdf`
- `analysis/friction_v0_2/figures/cl120_sfig_fcc_model_diagnostic_map.pdf`

## Crosswalk boundary

`analysis/friction_v0_2/crosswalks/` contains deterministic draft mappings for HO exposure classes and responsibility-chain (RC) capability labels. The RC mapping is a lexical audit aid, includes low-confidence rows, and is **not used** in FCC or C-RWR. This boundary prevents an unvalidated automated RC taxonomy from silently entering the reported score.
