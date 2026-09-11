# CareLoop

CareLoop is an open-source research framework for evaluating whether large language models can recognize and resolve clinically consequential real-world friction in longitudinal, patient-facing simulations. Cases deliberately expose models to incomplete or unreliable information, delayed evidence, patient and family constraints, adherence barriers, and care-system execution failures. Case-specific minimum duties and closure criteria serve as safety guardrails; the central measurement target is friction-sensitive clinical adaptation.

CareLoop is **not** a clinical deployment system, medical device, regulatory certification tool, or substitute for clinician judgment.

## Release correctness notice (2026-09-11)

`public_supplement/cl120_20260911/` is the final canonical CL120 result release. It contains the frozen population of 1,200 trajectories (10 tested doctor models x 120 cases), all 4,800 canonical Judge cells, and all 8,396 preserved request attempts and validation records. The formal matrix, aggregation, analysis, release-build, manifest, safety-scan, and clean-room reanalysis gates passed.

The four Judges received complete outcome-blind trajectory packets in one request. The canonical formal run used direct complete-context review for all 4,800 cells (`chunk_count = 0`); no transcript was clipped, sampled, summarized, or semantically repaired by another model. Canonical 1--5 grades were derived deterministically in code from each Judge's unmodified typed findings under the frozen calibrated-v1.1 V7.1 rubric. Judge-proposed grades remain separately available as experimental rubric-adherence observations.


## Contributors and Contact

Main code contributor: Zhenhong Yang

For collaboration or questions, please contact: yzh_med_mllm@outlook.com

## 贡献者与联系

代码主要贡献人：杨振宏

合作或疑问请联系：yzh_med_mllm@outlook.com

## What is included

The public package contains the lightweight CareLoop runtime, the deidentified CL120 cases, reproducible evaluation utilities, and the final canonical result supplement.

| Path | Description |
|---|---|
| `careloop/` | Public Python runtime package, including `careloop.runtime_lite` and executable evaluation protocol code. |
| `cases/public_cl120_deidentified_120/` | 120 deidentified EHR-seeded CareLoop case contracts for inspection and reuse. |
| `cases/authoring_template.case.json` | Template for creating additional CareLoop-compatible cases. |
| `manifests/public_cl120_manifest.json` | Case manifest for the public CL120 case set. |
| `manifests/cl120_v7_case_file_manifest.csv` | Byte-accurate manifest binding the frozen case files used by the canonical evaluation. |
| `public_supplement/cl120_20260911/` | Final canonical release: 1,200 trajectories, 1,200 complete-context Judge packets, 4,800 canonical cells, 8,396 attempts, analysis tables, provenance, and checksums. |
| `docs/CL120_CANONICAL_ANALYSIS.md` | Executable scoring, aggregation, multidimensional task-burden, supplementary aggregate-index, and screening-descriptor definitions. |
| `docs/CL120_FRICTION_EVALUATION.md` | Formative six-domain friction-response profile, 0–1 summaries, robustness checks, figures, limitations, and reproduction commands. |
| `docs/CL120_CASE058_EVIDENCE_AUDIT.md` | Traceable audit of the representative GPT-5.6 Sol doctor error correctly detected by GPT-5.5. |
| `docs/CASE_CONSTRUCTION_GUIDE.md` | Source-agnostic guidance for building CareLoop cases. |
| `docs/CL120_PUBLIC_DEIDENTIFICATION_AUDIT.md` | Summary of the CL120 deidentification and public-release review. |
| `docs/RELEASE_SCOPE_AND_LICENSE.md` | Release scope, intended-use, and license notes. |
| `rubrics/` | Frozen trajectory-quality rubric assets, including the canonical V7.1 rubric. |
| `scripts/` | Packet construction, strict judging, aggregation, analysis, figure, release, manifest, and safety-audit utilities. |
| `configs/` | Example provider configuration files; no credentials are included. |

The canonical supplement is self-contained for public reanalysis. Private EHR sources, source-to-case mappings, credentials, notebook state, and local operational logs are excluded.

## CL120 public case set

The public case directory is:

```text
cases/public_cl120_deidentified_120/
```

It contains 120 deidentified EHR-seeded clinical simulation cases. Each case is a JSON contract describing patient/family actors, doctor-visible workspace, hidden simulation state, dynamic event space, evaluation and closure contracts, and real-world friction design. Canonical scoring is provenance-bound to the frozen source bytes recorded in `manifests/cl120_v7_case_file_manifest.csv`; the release supplement records the trajectory, packet, protocol, runner, raw-attempt, and canonical-result lineage.

### Human-review scope

Before the case set was frozen, three licensed physicians reviewed candidate case drafts case by case, covering 140, 143, and 165 cases, respectively. Their criterion was whether each case as a whole was clinically plausible and consistent with general standards of diagnosis and treatment. Among the cases assessed by all three physicians, 130 received unanimous approval; 120 of those cases were selected and frozen to improve the relative balance of case counts across clinical departments. This was a case-level review. The 1,200 model-generated trajectories have not yet been scored by physicians, and the 4,800 LLM-Judge assessments have not yet been reviewed for clinical correctness by physicians. These human post-experiment evaluations are planned as future work. See `manifests/CL120_PHYSICIAN_CASE_REVIEW_SCOPE_20260911.json` for the machine-readable scope statement.

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

The legacy structural validator should report 120 loadable cases, but that check does **not** constitute approval of their evaluation contracts. Canonical packet construction additionally requires `evaluation_contract_v2` plus completed, output-blinded review records and intentionally fails closed until those fields are present.

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
- 1,200 exact outcome-blind complete-context Judge packets;
- 4,800 canonical Judge records covering four Judges for every trajectory;
- all 8,396 raw attempts, transport outcomes, parse/validation records, and retry lineage;
- canonical score matrices, FCC/C-RWR and six-domain analyses, all pairwise Judge-agreement results, burden/process analyses, evidence-audited representative-case records, the frozen ordinal comparator, and publication data;
- public trajectory and packet manifests, SHA-256 lineage, release audit, and package manifest.

Start with `public_supplement/cl120_20260911/README.md` and verify `RELEASE_BUILD_AUDIT.json` and `PACKAGE_MANIFEST.csv` before analysis.
The Supplementary Material uses FCC/C-RWR-primary representative records. Their selection and source checks are documented in `docs/CL120_FCC_REPRESENTATIVE_CASE_AUDIT.md`; Case 058 is included as a verified disagreement example.

## Friction-centered main manuscript analysis

The release includes a post-hoc formative six-domain profile derived from the same unmodified 4,800 canonical Judge records. It measures information repair, constraint navigation, dynamic reprioritization, execution-loop repair, state-sensitive communication, and bounded closure/continuity on a 0–1 scale. This is the manuscript’s central scientific analysis. The frozen 1–5 rubric remains unchanged and is reported as a conventional threshold-based comparator; the friction profile is explicitly identified as post-hoc rather than prospectively preregistered.

The executable method, all Judge-level and trajectory-level outputs, 10,000-resample case-paired confidence intervals, Judge-specific results, leave-one-Judge-out checks, domain-weight perturbations, alternative handling of untriggered HO opportunities, friction-stratum sensitivity, and publication figures are under:

```text
docs/CL120_FRICTION_EVALUATION.md
configs/careloop_friction_score_v0_2.json
careloop/evaluation/friction_score_v0_2.py
public_supplement/cl120_20260911/analysis/friction_v0_2/
```

The friction profile is an auditable formative measure, not a clinician-validated gold standard. It does not overwrite any canonical Judge result and was not tuned to force a preferred model ranking.

### Reproduce the retained FCC-primary Supplementary analyses

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

PYTHONPATH=. python scripts/audit_cl120_fcc_representative_cases.py \
  --root public_supplement/cl120_20260911 \
  --out public_supplement/cl120_20260911/analysis/friction_v0_2/supplementary/FCC_REPRESENTATIVE_CASE_AUDIT.json
```

The Supplementary figures are limited to analyses that clarify FCC/C-RWR results, explain CareLoop measurement behavior, or motivate future validation. Threshold-dependent screening views, ordinal burden bins, dense score-ordered case maps, and standalone workspace-call plots are not presented as substantive findings; underlying continuous tables remain available for audit where applicable.

## Runtime outputs

Typical runtime output directories contain:

- `trajectory.json`: final trajectory record;
- `trajectory.checkpoint.json`: latest checkpoint when stage checkpoints are enabled;
- `summary.md` and `summary.checkpoint.md`: human-readable summaries;
- `quality_report.json`: deterministic trajectory-quality and runtime-integrity signals;
- `runtime_phase.json`: lightweight status file for monitoring.

Runtime outputs may contain simulated clinical details generated from a case and model interaction. Review and deidentify outputs before any redistribution.

## Trajectory-quality judging utilities

The public judging utilities implement the frozen calibrated-v1.1 V7.1 outcome-blind protocol:

- `careloop/evaluation/protocol_v7.py` is the executable source of truth for deterministic selection of the original frozen case-authored contract, packet/citation/schema validation, and deterministic grade derivation. It preserves 5–9 authored RC items and all four authored HO items without model rewriting or item merging.
- `scripts/build_minimal_judge_packets.py` builds **lossless**, content-addressed, model-blinded packets from either a proven-complete `events_public` ledger or the raw formal `trajectory.events` ledger filtered only by declared tested-doctor-observable visibility. It rejects missing or byte-unbound frozen contracts, ambiguous responsibility-chain locations, duplicate event IDs, lossy evidence declarations, and invalid packets. Authentic runtime-error trajectories remain scoreable on their pre-termination evidence and typed terminal metadata.
- `scripts/run_final_trajectory_judge.py` performs direct or lossless chunked review, preserves every raw attempt, and writes a canonical record only when the model's unmodified output passes every validator. It never rounds, clamps, defaults, or repairs a score.
  It omits the optional `temperature` request field because the configured gateway rejects non-default values for some reasoning models; this omission is recorded and hash-bound in every request artifact. Provider HTTP error bodies are retained only as bounded, credential-redacted audit previews. The six-attempt policy keeps the historical 20/40/60/80/100-second base backoff, honors a bounded `Retry-After` header, and adds deterministic 429/503 jitter so ordinary gateway throttling does not synchronize all workers.
- `scripts/run_crossjudge_completion.py` is a compatibility entry point for the same resumable strict runner; it does not accept legacy CSV rows as completed judgments.

Canonical packet construction uses the original frozen 2026-09-04 case files, not model-rewritten contracts. `scripts/build_frozen_case_manifest_v7.py` recreates a byte-accurate manifest and can bind it to a verified outer archive manifest. Supply the frozen case directory and generated manifest explicitly when they are outside this public checkout. The no-network packet build is:

```bash
python scripts/build_minimal_judge_packets.py \
  --root /path/to/common/root \
  --case-dir path/to/frozen/cases \
  --case-manifest path/to/cl120_v7_case_file_manifest.csv \
  --trajectory-dir path/to/1200/trajectories/by_model
```

Inspect the run plan without making API calls:

```bash
python scripts/run_final_trajectory_judge.py \
  --packet-manifest outputs/judge_packets/cl120_blinded_ordinal_1to5_manifest.json
```

Execute only after setting the endpoint and key in the environment:

```bash
export CARELOOP_LITE_BASE_URL='https://provider.example/v1'
export CARELOOP_LITE_API_KEY='...'
python scripts/run_final_trajectory_judge.py \
  --packet-manifest outputs/judge_packets/cl120_blinded_ordinal_1to5_manifest.json \
  --concurrency-per-judge 20 \
  --execute
```

The default judge set is `GPT-5.5`, `gpt-5.6-sol`, `DeepSeek-V4-Pro`, and `GLM-5`. The conservative global concurrency default is 5. The completed formal run used `--concurrency-per-judge 40`, permitting at most 160 in-flight jobs while independently capping each Judge at 40; retries were resumable and only missing or transport/schema-failed cells were resubmitted. Each job remains append-only and resumable, so only failed judge--trajectory cells are retried.

After all runs finish, build the canonical matrix from one or more result roots:

```bash
python scripts/aggregate_canonical_judge_results.py \
  --packet-manifest outputs/judge_packets/cl120_blinded_ordinal_1to5_manifest.json \
  --result-roots outputs/judge_results/calibrated_v1_1_legacy_preserving_v7 \
  --output-dir outputs/judge_results/canonical_aggregation_v7
```

A batch is canonical only if every expected judge--trajectory job is accepted and the aggregator validates exactly 4,800 unique cells with complete raw-response and prompt lineage; any unscorable, invalid, failed, missing, or duplicate result invalidates the batch. Generated packets, raw responses, and results are ignored by Git unless deliberately reviewed and packaged.

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
