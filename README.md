# CareLoop

CareLoop is an open-source clinical-world sandbox for measuring the gap between knowing medicine and practicing it in the context of patients' lives. Cases represent patients and families as stateful actors and distribute state across biological truth, lived beliefs, records, and executed actions. Hidden or incorrect information, delayed evidence, autonomy, feasibility constraints, failed handoffs, and closure traps create behavioral forks at which models must elicit, communicate, verify, adapt, act, track, and revise.

The framework makes latent capability limitations observable in complete trajectories. It tests whether medical knowledge remains usable with a person whose beliefs, choices, relationships, and material circumstances can reshape the clinical path. It separates an attempted response from meaningful world impact and runtime termination from safe clinical closure. Case-specific minimum duties and responsibility requirements remain safety guardrails around this patient-centered construct.

CareLoop is **not** a clinical deployment system, medical device, regulatory certification tool, or substitute for clinician judgment.

## CL120 result release

The release provides 120 frozen deidentified case contracts under `cases/public_cl120_deidentified_120/`. The companion directory `public_supplement/cl120_20260911/` contains 1,200 trajectories from 10 tested models, 4,800 accepted structured LLM-Judge assessments, and 6,000 structured physician assessments covering the same trajectories. The primary reported endpoints are six 0–1 capability domains, FCC, and safety-gated C-RWR. The frozen 1–5 rubric is reported as a conventional supplementary comparator.

The care-progression analysis follows realized high-order opportunities through active action, meaningful trajectory impact, and effective completion; it also reports model-gap decomposition, terminal-status/closure mismatch, case-level discriminatory yield, domain nonredundancy, friction-specific rank variation, and case-count/split-half ranking stability. These outputs are reproducible from the released records and do not alter the primary scores.

## Contributors and Contact

Main code contributor: Zhenhong Yang

For collaboration or questions, please contact: yzh_med_mllm@outlook.com

## 贡献者与联系

代码主要贡献人：杨振宏

合作或疑问请联系：yzh_med_mllm@outlook.com

## What is included

The public package contains the lightweight CareLoop runtime, the deidentified CL120 cases, reproducible evaluation utilities, and the canonical result supplement.

| Path | Description |
|---|---|
| `careloop/` | Public Python runtime package, including `careloop.runtime_lite` and executable evaluation protocol code. |
| `cases/public_cl120_deidentified_120/` | 120 deidentified EHR-seeded CareLoop case contracts for inspection and reuse. |
| `cases/authoring_template.case.json` | Template for creating additional CareLoop-compatible cases. |
| `manifests/public_cl120_manifest.json` | File index and metadata for the public CL120 case set. |
| `public_supplement/cl120_20260911/` | Canonical result release: 1,200 trajectories, 4,800 accepted structured Judge assessments, 6,000 structured physician assessments, analysis tables, mapping indexes, and reproducibility metadata. |
| `docs/CL120_CANONICAL_ANALYSIS.md` | Executable scoring, aggregation, multidimensional task-burden, supplementary aggregate-index, and screening-descriptor definitions. |
| `docs/CL120_FRICTION_EVALUATION.md` | Six-domain friction-response profile, 0–1 summaries, robustness checks, figures, limitations, and reproduction commands. |
| `docs/CARE_PROGRESSION_ANALYSIS.md` | Opportunity-to-impact, model-gap, closure-quality, diagnostic-yield, and ranking-stability analyses. |
| `docs/CASE_CONSTRUCTION_GUIDE.md` | Source-agnostic guidance for building CareLoop cases. |
| `docs/CL120_PUBLIC_DEIDENTIFICATION_AUDIT.md` | Summary of the CL120 deidentification and public-release review. |
| `docs/RELEASE_SCOPE_AND_LICENSE.md` | Release scope, intended-use, and license notes. |
| `rubrics/` | The canonical structured 1–5 ordinal-comparator rubric used for the conventional supplementary analysis. |
| `scripts/` | Packet construction, structured judging, aggregation, analysis, figure, release, and safety-check utilities. |
| `configs/` | Example provider configuration files; no credentials are included. |

The canonical supplement is self-contained for reanalysis of the reported results. Private EHR sources, source-to-case mappings, credentials, notebook state, and local operational logs are excluded.

## CL120 public case set

The public case directory is:

```text
cases/public_cl120_deidentified_120/
```

It contains the only public definition of the 120 deidentified EHR-seeded clinical simulation cases. Each JSON contract describes patient/family actors, the deidentified doctor-visible workspace, hidden simulation state, dynamic event space, the RC/HO evaluation contract, closure criteria, and real-world friction design. Trajectories and Judge assessments reference these cases by stable `case_id` and do not embed a second copy of the case contract.

### Human-review scope

Before the case set was frozen, three licensed physicians reviewed candidate case drafts case by case, covering 140, 143, and 165 cases, respectively. Their criterion was whether each case as a whole was clinically plausible and consistent with general standards of diagnosis and treatment. Among the cases assessed by all three physicians, 130 received unanimous approval; 120 were selected and frozen to improve the relative balance of case counts across clinical domains. A separate blinded trajectory evaluation used a final analytic panel of 139 physicians from tertiary or higher-level hospitals in China: 64 residents, 51 attendings, and 24 associate-chief or chief physicians. The physicians were assigned within the 13 frozen CL120 clinical domains; final domain panels contained 7–16 physicians. Every trajectory received five separately collected assessments from five distinct physicians in its assigned domain: two residents, two attendings, and one associate-chief or chief physician. Tested-model identity and trajectory provenance were absent from clinician-facing materials and were linked to the released records only after review completion. The released physician comparison uses FCC, C-RWR, and the six CareLoop capability domains derived from structured clinical fields. The physician study compares trajectory-derived FCC, C-RWR, and capability profiles with the LLM-Judge results. Physician consensus is interpreted as an independent clinical comparison panel rather than an error-free gold standard.

The case set supports research on longitudinal medical AI behavior involving incomplete reports, delayed or conflicting evidence, medication and adherence barriers, caregiver involvement, care-execution friction, record interpretation, follow-up responsibilities, and premature-closure risk.

## Deidentification and release review

The CL120 public cases were deidentified before release. The review process removed or generalized direct identifiers, clinician and staff names, real institution and location names, exact source dates, phone/ID-like strings, source archive paths, and source-to-case mapping fields. The full 120-case public set passed static sensitive-information scanning and LLM-assisted privacy review before inclusion.

The repository does not include raw EHR records, source-to-case mapping tables, provider credentials, API keys, notebook tokens, local or remote working paths, operational logs, or private experiment outputs.

See `docs/CL120_PUBLIC_DEIDENTIFICATION_AUDIT.md` for a concise release-review summary.

## Installation

```bash
python -m pip install -e .
```

To reproduce the released statistical tables and PDF figures, install the analysis extras:

```bash
python -m pip install -e ".[analysis]"
```

CareLoop requires Python 3.10 or later. The lightweight runtime depends on `jsonschema`.

## Validate the public cases without API calls

This command loads and validates all 120 public CL120 cases without constructing any external model client:

```bash
python -m careloop \
  --case-glob "cases/public_cl120_deidentified_120/*.json" \
  --validate-cases
```

The structural loader should report 120 loadable cases, but that check does **not** constitute approval of their evaluation contracts. Canonical packet construction additionally requires `evaluation_contract_v2` plus completed, output-blinded review records and intentionally fails closed until those fields are present.

## Run a scripted smoke trajectory

The scripted client is useful for verifying runtime wiring and output generation without calling an external model API:

```bash
python -m careloop.runtime_lite \
  --case cases/public_cl120_deidentified_120/case_CL120_EHR_001.json \
  --client scripted \
  --max-turns 3 \
  --output-dir outputs/scripted_smoke_demo
```

## Run with an OpenAI-compatible endpoint

A model-backed run requires a user-provided OpenAI-compatible endpoint and private API key. Credentials should be supplied through environment variables or local ignored files and must never be committed.

```bash
export CARELOOP_LITE_BASE_URL="https://your-openai-compatible-provider.example/v1/chat/completions"
export CARELOOP_LITE_API_KEY="replace-with-your-private-key"
export CARELOOP_LITE_MODEL="replace-with-provider-model-name"

python -m careloop.runtime_lite \
  --case cases/public_cl120_deidentified_120/case_CL120_EHR_001.json \
  --client openai-compatible \
  --base-url "$CARELOOP_LITE_BASE_URL" \
  --api-key-env CARELOOP_LITE_API_KEY \
  --model "$CARELOOP_LITE_MODEL" \
  --max-turns 100 \
  --stage-checkpoints \
  --output-dir outputs/live_demo
```

For multi-case experiments, use `--case-glob`, `--parallelism`, `--resume-existing`, and an output directory appropriate for your provider limits:

```bash
python -m careloop.runtime_lite \
  --case-glob "cases/public_cl120_deidentified_120/*.json" \
  --client openai-compatible \
  --base-url "$CARELOOP_LITE_BASE_URL" \
  --api-key-env CARELOOP_LITE_API_KEY \
  --model "$CARELOOP_LITE_MODEL" \
  --max-turns 100 \
  --parallelism 5 \
  --stage-checkpoints \
  --resume-existing \
  --output-dir outputs/cl120_run
```

## CL120 trajectory supplements

### Final canonical supplement

```text
public_supplement/cl120_20260911/
```

This is the authoritative CL120 result release. It contains:

- 1,200 frozen trajectories, including 1,106 `closed_success`, 56 `open_at_100`, and 38 authentic `runtime_error` outcomes;
- 4,800 accepted structured Judge assessments covering four Judges for every trajectory, consolidated in `judge_results/canonical_judge_results.jsonl`;
- canonical score matrices, FCC/C-RWR and six-domain analyses, all pairwise Judge-agreement results, 6,000 structured physician assessments and physician--Judge rank-concordance analyses, burden/process analyses, evidence-verified representative-case records, the frozen ordinal comparator, and publication data;
- a trajectory/evaluation index, release manifest and analysis metadata.

Start with `public_supplement/cl120_20260911/README.md` and `public_supplement/cl120_20260911/metadata/trajectory_evaluation_index.csv` before analysis.
The Supplementary Material uses FCC/C-RWR-primary representative records. Their selection is documented in `docs/CL120_FCC_REPRESENTATIVE_CASE_VERIFICATION.md`.

## Friction-centered main manuscript analysis

The release includes the manuscript’s central six-domain analysis, derived programmatically from the same 4,800 accepted canonical Judge records. It measures information repair, constraint navigation, dynamic reprioritization, execution-loop repair, state-sensitive communication, and bounded closure/continuity on a 0–1 scale. The frozen 1–5 rubric is reported as a conventional threshold-based comparator.

The executable method, all Judge-level and trajectory-level outputs, 10,000-resample case-paired confidence intervals, Judge-specific results, leave-one-Judge-out checks, domain-weight perturbations, alternative handling of untriggered HO opportunities, friction-stratum sensitivity, and publication figures are under:

```text
docs/CL120_FRICTION_EVALUATION.md
configs/careloop_friction_score_v0_2.json
careloop/evaluation/friction_score_v0_2.py
public_supplement/cl120_20260911/analysis/friction_v0_2/
```

The friction profile is an auditable measurement framework, not a clinician-validated gold standard. It does not overwrite any canonical Judge result and was not tuned to force a preferred model ranking.

### Reproduce the FCC-primary Supplementary analyses

```bash
PYTHONPATH=. python scripts/analyze_cl120_fcc_supplementary.py \
  --root public_supplement/cl120_20260911 \
  --out public_supplement/cl120_20260911/analysis/friction_v0_2/supplementary \
  --bootstrap-replicates 10000 \
  --self-association-replicates 100000 \
  --seed 20260911

python scripts/generate_cl120_fcc_supplementary_figures.py \
  --analysis-dir public_supplement/cl120_20260911/analysis/friction_v0_2/supplementary \
  --output-dir public_supplement/cl120_20260911/analysis/friction_v0_2/figures

```

The Supplementary figures are limited to analyses that clarify FCC/C-RWR results, explain CareLoop measurement behavior, or motivate future validation. Threshold-dependent screening views, ordinal burden bins, dense score-ordered case maps, and standalone workspace-call plots are not presented as substantive findings; underlying continuous tables remain available for audit where applicable.

## Blinded physician comparison

The complete physician comparison is released under `public_supplement/cl120_20260911/physician_comparison/`. It contains the clinician-facing instructions, 6,000 structured assessments, an auditable case-panel assignment table, executable FCC/C-RWR and six-domain analyses, machine-readable results, and publication figures. A case and its 10 tested-model trajectories formed the minimum assignment packet. The same five physicians reviewed all 10 trajectories for that case: two residents, two attendings, and one associate-chief/chief physician. Opaque pseudonymous identifiers permit fixed-panel and repeated-assessment audits without releasing names, institution names, or an identity map.

Reproduce the audit and analysis from the repository root:

```bash
python -m pip install -e '.[analysis]'
python scripts/audit_physician_comparison_release.py
python public_supplement/cl120_20260911/physician_comparison/analyze_physician_llm_rank_concordance.py
```

The primary physician comparison ranks the same 10 models within each case and summarizes the resulting 120 case-wise Spearman correlations. This design controls case content and reviewer panel while the tested model changes. Aggregate 10-model rankings are reported as descriptive context. Pooled trajectory correlations and exact-score agreement are not primary evidence because different cases use different physician panels. The comparison measures concordance under the CareLoop protocol and does not treat physicians as an error-free gold standard or rank clinical expertise.

## Runtime outputs

Typical runtime output directories contain:

- `trajectory.json`: final trajectory record;
- `trajectory.checkpoint.json`: latest checkpoint when stage checkpoints are enabled;
- `summary.md` and `summary.checkpoint.md`: human-readable summaries;
- `quality_report.json`: deterministic trajectory-quality and runtime-integrity signals;
- `runtime_phase.json`: lightweight status file for monitoring.

Runtime outputs may contain simulated clinical details generated from a case and model interaction. Review and deidentify outputs before any redistribution.

## Trajectory-quality judging utilities

The optional judging utilities implement the released outcome-blind judging protocol for new runs. `scripts/build_minimal_judge_packets.py` constructs model-blinded evaluation inputs directly from a selected case directory and trajectory collection; `scripts/run_final_trajectory_judge.py` validates structured outputs and accepts a cell only when the unmodified model response satisfies the protocol. Generated request, retry, and transport artifacts are operational files for a new run and are not part of the released CL120 result dataset.

The released formal results are provided as accepted structured assessments in `public_supplement/cl120_20260911/judge_results/canonical_judge_results.jsonl`. The single public case source is `cases/public_cl120_deidentified_120/`, and `public_supplement/cl120_20260911/metadata/trajectory_evaluation_index.csv` links each trajectory to its case and four accepted Judge assessments. Request packets and retry logs are not required for reanalysis.

## Case construction

Use `docs/CASE_CONSTRUCTION_GUIDE.md` and `cases/authoring_template.case.json` to build additional CareLoop-compatible cases. The guide is source-agnostic and can be adapted to private EHR data, public clinical records, or synthetic clinical material, provided the resulting cases satisfy the CareLoop case contract and privacy requirements.

## Exclusions

The public package intentionally excludes:

- raw or partially deidentified EHR records;
- source-to-case mapping tables;
- real provider credentials, API keys, Authorization headers, and notebook tokens;
- local or remote working paths, terminal sessions, operational monitoring logs, retry artifacts, checkpoints, and build caches;
- private experiment outputs and non-deidentified materials.

## License

CareLoop is released under the Apache License 2.0. See `LICENSE` and `NOTICE`. Unless otherwise noted, this license applies to the source code, public CL120 deidentified case definitions, rubrics, configuration templates, documentation, and utility scripts included in this repository.

The included cases are research simulation artifacts. They are not clinical advice and should not be used for real-world diagnosis, treatment, or patient management.

## Citation

If you use CareLoop, please cite the corresponding manuscript once the public preprint is available.

## Care-progression analysis

```bash
python scripts/analyze_care_progression.py
python scripts/generate_care_progression_figures.py
```

See `docs/CARE_PROGRESSION_ANALYSIS.md` for definitions and interpretation boundaries.
