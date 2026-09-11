# CL120 canonical analysis protocol

This document defines the executable analysis applied to the final CL120 Judge matrix.
It is intentionally independent of any scores or empirical-difficulty labels embedded in
older trajectory exports.

## Authoritative inputs

1. The frozen 1,200-row outcome-blind Judge packet manifest.
2. Exactly four lineage-valid `canonical.json` records for every packet (4,800 cells).
3. The SHA-256-matched public trajectory JSON referenced by each packet-manifest row.
4. The pre-authored, de-identified case metadata table.

`aggregate_canonical_judge_results.py` rejects a missing, duplicate, invalid, truncated,
chunked, or lineage-mismatched cell. `analyze_cl120_canonical_results.py` independently
reconstructs every 1--5 grade from the frozen gate flags before calculating statistics.
The Judge-proposed grade is retained separately as an observed output-quality field.

## Ordinal comparator aggregation

For one trajectory, the ordinal comparator is the median of its four canonical Judge grades.
For one tested doctor model, the ordinal comparator is the arithmetic mean of the 120
trajectory-level medians. All generated trajectories remain in the denominator,
including `open_at_100` and `runtime_error` records. Confidence intervals use a paired
case bootstrap with a fixed seed and a dependency-independent standard-library random
number generator so released Monte Carlo endpoints are byte-reproducible across installs.

## Task-burden dimensions and supplementary aggregate index

The canonical analysis does **not** define case difficulty from model scores, trajectory
length, nonclosure, runtime errors, or Judge disagreement. Those quantities are outcomes;
using them to construct a difficulty tier and then demonstrating an outcome gradient by
tier would be circular.

Instead, the secondary design analysis keeps five frozen case-contract burdens separate:

1. responsibility-chain breadth;
2. expected evidence-integration burden;
3. authored real-world friction intensity;
4. authored temporal/turn-budget burden; and
5. closure-coordination burden.

Every CL120 contract contains all four authored HO responsibility types. Because the HO
count is invariant at four, it cannot discriminate case complexity and is not added as a
sixth burden axis; case-specific HO content remains available for qualitative and
overlapping-domain analyses.

For an explicitly exploratory summary, each burden is mapped deterministically to 0--2
and summed into a 0--10 `contract_complexity_index`:

- responsibility-chain breadth: 5--6 required items = 0, 7 = 1, 8--9 = 2;
- expected evidence channels: 5 = 0, 6 = 1, 7--8 = 2;
- friction intensity: light = 0, moderate = 1, heavy/extreme = 2;
- expected turn budget: short = 0, medium = 1, long/extra-long = 2;
- closure coordination: 0 when none of the defined burden conditions is present, 1
  when one is present, and 2 when at least two are present. The conditions are
  transition-only handoff, at least three required receipts, at least six blocking
  residual risks, and at least six premature-closure traps.

The resulting display levels are low (0--3), moderate (4--6), and high (7--10). Groups
are not forced to equal size. The formula reads only the byte-verified contract copied
into the outcome-blind Judge packet and is therefore independent of model performance.
However, it was formulated after the CL120 runs, lacks external validation, and did not
produce a lower-score gradient in CL120. It is therefore retained only as a supplementary
negative construct-validation result, not as a preregistered or validated difficulty
label. The five component axes remain separate task-burden descriptors.

## Failure-mode screening

Failure flags are reproducible screening signals, not clinical adjudications:

- `low_score`: trajectory ordinal-comparator score <= 3.0;
- `very_low_score`: trajectory ordinal-comparator score <= 2.5;
- `below_strong`: trajectory ordinal-comparator score <= 3.5;
- `high_turn`: at least 50 completed turns;
- `very_high_turn`: at least 90 completed turns;
- `no_workspace`: zero turns containing a doctor workspace request;
- `sparse_workspace`: at most one such turn;
- `heavy_workspace`: at least six such turns;
- `high_judge_disagreement`: canonical Judge score range at least 2;
- `workspace_underuse_candidate`: below-strong score plus sparse workspace use;
- `workspace_overuse_candidate`: below-strong score plus heavy workspace use;
- `long_or_tool_intensive_low_score`: below-strong score plus either high-turn or
  heavy-workspace behavior;
- `closed_but_low_score`: `closed_success` with primary score <= 3.0;
- `model_specific_weakness`: trajectory ordinal-comparator score at least 1.0 below that case's
  cross-model mean.

Each trajectory is also assigned exactly one primary screening category in this order:
`runtime_error`, `open_at_100`, `very_low_score`, `long_or_tool_intensive_low_score`,
`workspace_underuse_candidate`, `closed_but_low_score`, `high_judge_disagreement`, then
`no_major_failure_flag`.

Workspace counts are reconstructed from SHA-256-verified `events_public`:
`doctor_workspace_message` defines entries and unique workspace turns,
`doctor_workspace_result` defines returned workspace results, and
`clinical_workspace_response` defines operation-routing responses.

## Case-metadata stress tests

The analysis reports model performance within pre-authored case tags for disease domain,
target problem, starting phase, friction intensity, primary and secondary friction,
tool dependency, expected turn budget, and high-order capability domains. Secondary
frictions and high-order capability domains are overlapping multi-valued strata.
These descriptive comparisons do not alter the score, the frozen 1--5 rubric, or any case-contract burden field.

## Judge identity, pairwise ranking, and matched-model association

Publication text and figures use the official display names in
`judge_model_identity.csv`; internal gateway/run identifiers remain available only for
provenance. The four display names are GPT-5.5, GPT-5.6 Sol, DeepSeek-V4-Pro, and
GLM-5.

`judge_specific_model_rankings.csv` computes a complete 10-model ranking independently
for each Judge. `pairwise_judge_model_ranking_consistency.csv` then reports all six
unordered Judge pairs using Kendall's tau-b, Spearman's rho, paired-case bootstrap
confidence intervals, Top-3 overlap, and absolute rank differences. These comparisons
measure similarity in model ordering; they do not imply trajectory-level absolute
agreement, which is reported separately using ICC(A,1) and ICC(A,4).

Three Judge models are also tested doctor models. For each overlap,
`judge_self_association_summary.csv` compares the focal Judge's score relative to the
other three Judges on the matched doctor model against the same residual on all other
doctor models. Confidence intervals use a case bootstrap, and the two-sided null uses
case-stratified pseudo-self randomization. Because every packet concealed tested-model
identity, this is described as a matched model--Judge association or self-associated
differential scoring, not intentional self-preference. Raw matched-model scores,
calibration-adjusted effects, and leave-matched-Judge-out rankings must be interpreted
together.

## Evidence-audited disagreement example

The representative `case_CL120_EHR_058` / GPT-5.6 Sol trajectory is identified by packet
`case_CL120_EHR_058__anon_bccc18593eaa2fc0`. Its RC1 contract requires postoperative
red-flag screening that includes abnormal urine volume or color and classifies a miss as
serious. A complete scan of all patient-visible doctor messages finds no urinary-volume,
urinary-color, urination, or hematuria warning. GPT-5.5 records this as an RC1 serious
omission; GPT-5.6 Sol records the same omission but classifies it as minor/moderate;
DeepSeek-V4-Pro and GLM-5 mark RC1 met. The public packet and four canonical records are
the authoritative evidence. This example illustrates a real localized error detected by
another Judge, not a claim that any one Judge is globally superior.

## Reproduction

```bash
python scripts/aggregate_canonical_judge_results.py \
  --root <workspace-root> \
  --packet-manifest <packet-manifest.json> \
  --result-roots <formal-result-root> \
  --output-dir <aggregation-output>

python scripts/analyze_cl120_canonical_results.py \
  --root <workspace-root> \
  --canonical-scores <aggregation-output>/canonical_scores.csv \
  --packet-manifest <packet-manifest.json> \
  --case-metadata <case_metadata_deidentified.csv> \
  --output-dir <analysis-output>
```

The canonical ordinal comparator tables remain reproducible from these CSVs. Main-text figures are generated by `generate_cl120_friction_figures.py`; FCC-primary Supplementary tables and figures are generated by `analyze_cl120_fcc_supplementary.py` and `generate_cl120_fcc_supplementary_figures.py`. The release contains only the generators required for the reported figure set.

## Formative friction-centered measurement and manuscript analysis hierarchy

The frozen ordinal comparator analysis above remains unchanged. CSV column names containing `primary_score` are retained for file compatibility; they refer only to the ordinal-comparator module and are not the manuscript-primary FCC/C-RWR endpoints. A separate post-hoc formative v0.2 analysis derives six 0–1 friction-response domains, FCC, and C-RWR from the same unmodified canonical Judge fields. It is documented in [`CL120_FRICTION_EVALUATION.md`](CL120_FRICTION_EVALUATION.md) and released under `public_supplement/cl120_20260911/analysis/friction_v0_2/`. It does not alter canonical grades or overwrite Judge findings.
