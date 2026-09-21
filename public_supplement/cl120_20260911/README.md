# CareLoop CL120 final canonical supplement

This release contains 1,200 frozen trajectories, 4,800 Judge cells, and 6,000 structured physician assessments collected under fixed case-level panels.
All four ordinal-comparator grades for every trajectory were derived deterministically from each
Judge's released structured findings under the frozen 1–5 rubric. Judge-proposed grades
remain available separately as rubric-adherence observations.

- `trajectories/`: public interaction and workspace trajectories with terminal-state metadata. Scores and pre-execution burden profiles are stored in the analysis and metadata tables; no outcome-derived case-difficulty label is included.
- `judge_results/canonical_judge_results.jsonl`: the 4,800 accepted structured Judge assessments used in analysis.
- `analysis/friction_v0_2/`: the manuscript-primary FCC, C-RWR, six-domain, Judge-agreement, robustness, and retained Supplementary analyses.
- `analysis/`: the frozen ordinal comparator, canonical matrices, operational summaries, and reproducibility tables.
- The five pre-execution burden dimensions are retained separately. Their aggregate index is a negative construct check and is not a validated case-difficulty scale.
- `analysis/judge_model_identity.csv`: official display names linked to exact internal run identifiers.
- `metadata/trajectory_evaluation_index.csv`: stable mapping among case IDs, trajectories, tested models, and evaluation IDs.
- `physician_comparison/`: clinician-facing instructions, 6,000 structured assessments, fixed case-panel assignments, case-wise 10-model rank analyses, and physician-comparison figures.
- `figures/`: main-text and supplementary publication figures generated from the canonical tables.

Clinical-domain stratification and case-level evaluation definitions are resolved from the single public case set under `cases/public_cl120_deidentified_120/`. Trajectories and Judge assessments contain IDs rather than duplicated case contracts.

Each trajectory contains the complete released interaction and workspace-event sequence used by the public analyses.
Runtime errors in the 1,200-trajectory experiment are included as observed outcomes.

## Human-review scope

Three licensed physicians performed case-by-case review of candidate drafts, covering 140, 143, and 165 cases, respectively. Among the cases assessed by all three, 130 were unanimously judged clinically plausible and consistent with general standards of diagnosis and treatment. CL120 freezes 120 of those cases to improve relative balance across clinical domains. A separate blinded trajectory evaluation used a final analytic panel of 139 physicians from tertiary or higher-level hospitals in China: 64 residents, 51 attendings, and 24 associate-chief or chief physicians. The panel covered the 13 frozen CL120 clinical domains, with 7–16 physicians assigned per domain. A case and its 10 tested-model trajectories formed the minimum assignment packet. The same five physicians reviewed all 10 trajectories for that case—two residents, two attendings, and one associate-chief/chief physician—for a total of 6,000 structured reviews. Tested-model identity and trajectory provenance were absent from clinician-facing materials and linked only after review completion. The primary physician study compares within-case 10-model rankings for FCC, C-RWR, and the six capability domains. It includes a symmetric case-wise comparison of physician-internal concordance with the concordance between each LLM Judge and the three physician-title strata. The clinician-facing structured-review instructions are released at `physician_comparison/physician_review_instructions_zh.md`.

The Supplementary Material uses FCC/C-RWR-primary representative records selected for mechanistic interpretability. Their selection is documented in `docs/CL120_FCC_REPRESENTATIVE_CASE_VERIFICATION.md`.

## Friction-centered analysis (v0.2)

`analysis/friction_v0_2/` contains the manuscript’s central six-domain profile and two 0–1 summaries calculated from the same 4,800 accepted canonical Judge records. It does not modify the frozen 1–5 ordinal comparator or any canonical Judge record. The directory includes Judge-level and trajectory-level outputs, 10,000-resample case-paired confidence intervals, model-by-friction results, multidimensional Judge-agreement analysis, Judge-specific analyses, leave-one-Judge-out and leave-one-friction-out checks, alternative untriggered-HO policies, weight perturbations, redundant-field normalization sensitivity, FCC-primary Supplementary tables, representative-case evidence verification, figures, reproducibility metadata, and a data dictionary. See `docs/CL120_FRICTION_EVALUATION.md` in the repository root for the full method and reproduction commands.

## Care-progression analysis

`analysis/care_progression/` reports the opportunity-to-action-to-impact funnel, model-level action-to-impact gaps, six-domain sources of model separation, FCC-to-C-RWR safety penalties, terminal-status/closure mismatch, case-level discriminatory yield, domain nonredundancy, friction-specific ranking variation, and case-count/split-half ranking stability. The directory is generated by:

```bash
python scripts/analyze_care_progression.py
python scripts/generate_care_progression_figures.py
```

The analysis reorganizes existing structured fields and does not create a new primary composite score. Complete definitions and interpretation limits are in `docs/CARE_PROGRESSION_ANALYSIS.md`.
