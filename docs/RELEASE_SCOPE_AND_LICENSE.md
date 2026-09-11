# Release scope and license notes

The public CareLoop package includes the runtime framework, the CL120 deidentified public case set, documentation, rubrics, provider configuration examples, and generic utility scripts, including lossless packet construction, fail-closed V7.1 outcome-blind judging, and exact 4,800-cell canonical aggregation.

## Current result-release status

As of 2026-09-11, `public_supplement/cl120_20260911/` is the sole public CL120 result release. It includes 1,200 trajectories, 4,800 complete canonical Judge cells, 8,396 preserved attempts, reproducible analyses, public manifests, and audit records. Superseded result snapshots and derived statistics are not included in the public repository.

## Included case data

- `cases/public_cl120_deidentified_120/`: 120 deidentified EHR-seeded public case contracts. Canonical V7.1 scoring is bound to the frozen source bytes through `manifests/cl120_v7_case_file_manifest.csv` and the supplement provenance records.
- `cases/authoring_template.case.json`: a template for writing additional compatible cases.

No raw EHR records, source-to-case mappings, exact source paths, provider payload logs, API keys, notebook tokens, or private experiment working directories are included.

## Physician-review scope

Before freezing CL120, three licensed physicians reviewed candidate case drafts case by case, covering 140, 143, and 165 cases, respectively. Their criterion was whether each case as a whole was clinically plausible and consistent with general standards of diagnosis and treatment. Among the cases assessed by all three physicians, 130 received unanimous approval, and 120 were selected from that set to improve relative balance across clinical departments. This establishes case-level review only. The 1,200 model trajectories have not yet been evaluated by physicians, and the 4,800 LLM-Judge assessments have not yet been reviewed for clinical correctness by physicians; those human evaluations are planned for future work.

## License

CareLoop is released under the Apache License 2.0. Unless otherwise noted, the license applies to the source code, CL120 public deidentified case definitions, rubrics, configuration templates, documentation, and scripts included in this repository.

The included cases are research simulation artifacts. They are not medical advice and should not be used for real-world diagnosis, treatment, or patient management.

## Canonical re-evaluation boundary

No prior score table is an input to the V7.1 canonical aggregator. Canonical scoring is bound to the byte-verified original 2026-09-04 frozen cases, the lossless 1,200-packet manifest, the versioned V7.1 outcome-blind protocol and runner hashes, and exactly one lineage-valid canonical result for every packet--judge cell. Judge requests exclude prior scores, empirical difficulty labels, case-level post-hoc metadata, and framework-generated closure or quality summaries; the packet validator admits only a fixed set of neutral provenance and terminal-state fields. Every formal cell sends the complete outcome-blind trajectory packet in one request: the release protocol forbids transcript clipping, sampling, chunk-summary substitution, and cross-model semantic repair. The user-authorized formal execution profile uses four judges with an independent rolling concurrency cap of 40 per judge (160 total). GPT-5.5, gpt-5.6-sol, and DeepSeek-V4-Pro use a 12,000-token completion budget; GLM-5 uses 6,000 so that the longest measured 192,067-token input remains within the authorized gateway's observed 202,752-token context limit. These operational settings do not alter the frozen rubric. The authoritative canonical score is derived deterministically from each Judge's unmodified typed findings; the Judge-reported score is retained separately as an experimental rubric-adherence outcome.
