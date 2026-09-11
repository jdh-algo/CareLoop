# CareLoop CL120 final canonical supplement

This release contains 1,200 frozen trajectories and 4,800 Judge cells.
All four canonical grades for every trajectory were derived deterministically from each
Judge's unmodified structured findings under the frozen 1--5 rubric. Judge-proposed
grades and output-quality diagnostics remain available separately.

- `trajectories/`: public trajectories with canonical scores and contract-only pre-execution burden profiles; no outcome-derived case-difficulty label is included.
- `judge_packets/`: exact complete-context, outcome-blind packets sent to each Judge.
- `judge_results/`: canonical records plus every raw attempt and validation record.
- `analysis/friction_v0_2/`: the manuscript-primary FCC, C-RWR, six-domain, Judge-agreement, robustness, and retained Supplementary analyses.
- `analysis/`: the frozen ordinal comparator, canonical matrices, operational summaries, and current-release audit tables.
- The five pre-execution burden dimensions are retained separately. Their aggregate index is a negative construct check and is not a validated case-difficulty scale.
- `analysis/judge_model_identity.csv`: official display names linked to exact internal run identifiers.
- `metadata/trajectory_lineage.csv`: frozen-source to final-public trajectory hashes.
- `metadata/packet_manifest_public.json`: release-rebased manifest for self-contained reanalysis.
- `provenance/`: original manifest hash, run metadata, retry lineage, and validation audits.
- `figures/`: main-text and supplementary publication figures generated from the canonical tables.

No trajectory was clipped, sampled, chunked, summarized, or repaired by another model.
Runtime errors in the original 1,200-trajectory experiment are retained as observed outcomes.

## Human-review scope

Three licensed physicians performed case-by-case review of candidate drafts, covering 140, 143, and 165 cases, respectively. Among the cases assessed by all three, 130 were unanimously judged clinically plausible and consistent with general standards of diagnosis and treatment. CL120 freezes 120 of those cases to improve relative balance across clinical departments. This review applies to the case designs only: the 1,200 model trajectories have not yet been physician-scored, and the 4,800 LLM-Judge assessments have not yet undergone physician review for clinical correctness. These human post-experiment evaluations remain future work.

The manuscript uses several FCC/C-RWR-primary representative records. Their selection and source checks are documented in `docs/CL120_FCC_REPRESENTATIVE_CASE_AUDIT.md`. Case 058 remains the verified stronger-doctor omission example.

## Formative friction-centered analysis (v0.2)

`analysis/friction_v0_2/` contains a post-hoc formative six-domain profile and two 0–1 summaries calculated from the same unmodified 4,800 canonical Judge records. It does not modify the frozen 1–5 ordinal comparator or any canonical Judge record. The manuscript uses the friction profile as its central scientific analysis while stating its post-hoc formative status. The directory includes Judge-level and trajectory-level outputs, 10,000-resample case-paired confidence intervals, model-by-friction results, multidimensional Judge-agreement analysis, Judge-specific analyses, leave-one-Judge-out and leave-one-friction-out checks, alternative untriggered-HO policies, weight perturbations, FCC-primary Supplementary tables, representative-case audits, figures, checksums, and a data dictionary. See `docs/CL120_FRICTION_EVALUATION.md` in the repository root for the full method and reproduction commands.
