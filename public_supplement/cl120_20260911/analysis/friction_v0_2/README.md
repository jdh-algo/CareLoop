# CL120 friction-centered analysis v0.2

**Status:** post-hoc formative measurement and central manuscript analysis. This hierarchy is reported transparently and does not imply prospective preregistration. The frozen 1–5 rubric and all canonical Judge records remain unchanged; ordinal results are retained as a conventional comparator.

## Population

- 120 frozen cases
- 10 tested doctor-role models
- 1,200 trajectories
- 4 model-blinded Judges per trajectory
- 4,800 canonical Judge assessments

## Core columns

### `friction_domains_judge_long.csv`

One row per Judge–trajectory cell.

- `packet_id`, `case_id`, `doctor_model`, `judge_model`: identifiers.
- `primary_friction`: prospectively authored dominant friction exposure.
- `friction_intensity`: authored contract descriptor.
- `ordinal_grade`: unchanged deterministic 1–5 grade.
- `domain_*`: applicable 0–1 capability scores; blank means the case had no authored HO of that domain.
- `friction_capability_composite`: FCC, with domain weights renormalized over applicable domains.
- `clinical_error_gate`: 1.00, 0.75, or 0.35 from unchanged structured error flags.
- `careloop_real_world_robustness`: FCC × clinical-error gate.
- `closure_process_score`: structured closure summary on 0–1.

### `friction_domains_trajectory.csv`

One row per trajectory. Every metric has `median4` and `mean4` variants across four Judges. `median4` is the principal formative aggregation.

### `friction_domains_model_summary.csv`

One row per tested model with 10,000-resample case-paired percentile confidence intervals, point ranks, bootstrap rank intervals, and domain-specific applicable-case counts.

### `friction_domains_by_primary_friction.csv`

One row per tested model × primary-friction stratum. Every stratum contains the same 12 cases for all models.

### `agreement/`

Multidimensional four-Judge agreement for FCC, C-RWR, and all six domains: ICC(A,1), ICC(A,4), all six pairwise trajectory correlations, and all six pairwise ten-model rank correlations.

### `robustness/`

Sensitivity analyses for Judge omission, Judge-specific ranks, pairwise Judge rank agreement, alternative untriggered-HO handling, mean versus median aggregation, domain-weight perturbation, friction-stratum omission, and domain denominators.

### `figures/`

Publication PDFs generated directly from the CSV outputs.

## Important interpretation boundaries

1. FCC is not a rescaled ordinal grade.
2. C-RWR is not a deployment-safety probability.
3. Primary-friction categories are not randomized causal difficulty groups.
4. Judge disagreement is preserved, not calibrated away.
5. The RC lexical crosswalk under `crosswalks/` is not used in v0.2 scoring.
6. Four Judge assessments of one trajectory are reviewer measurements, not four independent clinical episodes.

## FCC-primary Supplementary layer

The `supplementary/` directory joins the 1,200 trajectory-level FCC/C-RWR records to operational metadata and pre-execution case-contract burden descriptors. It contains case- and model-level summaries, burden associations, matched model--Judge analyses under FCC and C-RWR, a continuous trajectory review map, and the representative-case audit. No ordinal threshold defines the new review map.

The retained Supplementary figures are:

- `figures/cl120_sfig_fcc_task_burden_associations.pdf`;
- `figures/cl120_sfig_fcc_matched_model_judge_association.pdf`;
- `figures/cl120_sfig_fcc_model_diagnostic_map.pdf`.

See `figures/FCC_SUPPLEMENTARY_FIGURE_MANIFEST.md` for the rationale for omitting older threshold-dependent or redundant plots.
