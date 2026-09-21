# Release scope and license notes

The public CareLoop package includes the runtime framework, the CL120 deidentified public case set, documentation, rubrics, provider configuration examples, and generic utility scripts, including lossless packet construction, fail-closed outcome-blind judging, and exact 4,800-cell canonical aggregation.

## Result-release contents

`public_supplement/cl120_20260911/` includes 1,200 trajectories, 4,800 accepted structured Judge assessments, 6,000 structured physician assessments, reproducible analyses, a trajectory/evaluation index, and reproducibility metadata.

## Included case data

- `cases/public_cl120_deidentified_120/`: 120 deidentified EHR-seeded public case contracts. The release includes the case files used for public inspection and reuse.
- `cases/authoring_template.case.json`: a template for writing additional compatible cases.

No raw EHR records, source-to-case mappings, exact source paths, provider payload logs, API keys, notebook tokens, or private experiment working directories are included.

## Physician-review scope

Before freezing CL120, three licensed physicians reviewed candidate case drafts case by case, covering 140, 143, and 165 cases, respectively. Their criterion was whether each case as a whole was clinically plausible and consistent with general standards of diagnosis and treatment. Among the cases assessed by all three physicians, 130 received unanimous approval, and 120 were selected from that set to improve relative balance across clinical domains. A separate blinded trajectory evaluation used a final analytic panel of 139 physicians from tertiary or higher-level hospitals in China: 64 residents, 51 attendings, and 24 associate-chief or chief physicians. The panel covered the 13 frozen CL120 clinical domains, with 7–16 physicians assigned per domain. A case and its 10 tested-model trajectories formed the minimum assignment packet. The same five physicians reviewed all 10 trajectories for that case: two residents, two attendings, and one associate-chief or chief physician. Model identity and trajectory provenance were absent from clinician-facing materials and linked only after review completion. The physician records support within-case 10-model rank comparisons for FCC, C-RWR, and the six capability domains. Physician consensus is interpreted as an independent clinical comparison panel rather than an error-free clinical gold standard. Opaque pseudonymous reviewer identifiers are released for assignment and repeated-measurement auditing; title-group labels retain only resident, attending, or associate-chief/chief physician status and do not encode an A/B or 1/2 subgroup. The identity mapping and institution names are excluded.

## License

CareLoop is released under the Apache License 2.0. Unless otherwise noted, the license applies to the source code, CL120 public deidentified case definitions, rubrics, configuration templates, documentation, and scripts included in this repository.

The included cases are research simulation artifacts. They are not medical advice and should not be used for real-world diagnosis, treatment, or patient management.

## Canonical re-evaluation boundary

No prior score table was supplied to the Judges. Canonical scoring used the released outcome-blind judging protocol and one accepted structured assessment for every trajectory--Judge cell. Judge requests exclude prior scores, empirical difficulty labels, case-level downstream evaluative metadata, and framework-generated closure or quality summaries; the packet validator admits only a fixed set of neutral provenance and terminal-state fields. Every formal cell sends the complete outcome-blind trajectory packet in one request: the release protocol forbids transcript clipping, sampling, chunk-summary substitution, and cross-model semantic repair. The formal execution used four Judges and complete-context review. Model-specific completion budgets were selected to keep every observed trajectory within the available provider context window. These transport settings do not alter the scoring rubric. The authoritative canonical score is derived deterministically from each Judge's unmodified typed findings; the Judge-reported score is retained separately as an experimental rubric-adherence outcome.
