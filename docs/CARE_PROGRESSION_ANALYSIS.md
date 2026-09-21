# Care progression, model-gap, and benchmark-stability analyses

CareLoop measures how models exercise medical knowledge in the context of patients' lives, where beliefs, choices, relationships, resources, evidence, and care-system constraints shape the clinical path. The care-progression analysis follows released structured assessments from realized opportunity through active action, meaningful trajectory impact, and effective completion. It also reports model-gap decomposition, closure-quality comparisons, case-level diagnostic yield, and ranking stability. FCC, C-RWR, and the ordinal comparator follow their documented definitions.

## Reproduce

```bash
python scripts/analyze_care_progression.py
python scripts/generate_care_progression_figures.py
```

Install the analysis extras first:

```bash
pip install -e '.[analysis]'
```

Outputs are written to:

```text
public_supplement/cl120_20260911/analysis/care_progression/
public_supplement/cl120_20260911/figures/care_progression/
```

All bootstrap and resampling procedures use seed `20260919`.

## Analyses

### Opportunity-to-impact conversion

For every Judge-marked triggered high-order opportunity, the analysis reports:

1. active model action;
2. meaningful trajectory impact;
3. effective completion, requiring a complete HO together with active action and meaningful impact;
4. the active-action rate minus the meaningful-impact rate.

Case-cluster bootstrap intervals resample the 120 cases and preserve all within-case models, Judges, and HO records.

`triggered` means that the authored opportunity became realized in the trajectory. It is not interpreted as a model recognition label.

### Sources of model separation

The six-domain table reports raw between-model ranges and standard deviations. FCC domain contributions are decomposed at the additive Judge-cell level, where the identity is exact after case-specific weight renormalization. The primary manuscript ranking remains the trajectory-level median across four Judges. The difference between FCC and C-RWR is reported as a safety-gate penalty and is not relabeled as pure medical knowledge.

### Terminal status and closure quality

Runtime status (`closed_success`, `open_at_100`, or runtime error) is compared with four Judge closure labels. The release reports exact vote counts rather than declaring a new binary ground truth.

### Diagnostic yield

For every case, the analysis reports the cross-model FCC/C-RWR range and the range in each applicable domain. This quantifies whether a fixed contract produces observable model divergence. It does not establish causal intrinsic difficulty of a friction category.

### Ranking stability

Random case subsets are compared with the complete 120-case ranking as a convergence visualization. The stronger internal check partitions each 12-case friction stratum into two disjoint six-case halves and compares independently aggregated 10-model rankings across 5,000 partitions.

## Interpretation boundary

The outputs support statements about simulated trajectory behavior and benchmark discrimination. They do not establish clinical effectiveness, deployment safety, a clinician gold standard, or a causal ordering of friction difficulty.
