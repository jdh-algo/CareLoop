# CareLoop runtime_lite

`careloop.runtime_lite` is the lightweight public runtime used to generate longitudinal simulated care trajectories. It is designed for research evaluation of patient-facing AI doctor behavior in a changing clinical world, not for clinical deployment or medical advice.

The runtime separates four layers:

1. **Doctor endpoint interaction** — the evaluated model receives only doctor-visible conversation/workspace context.
2. **Patient/family actors** — simulated people respond with incomplete information, misunderstanding, emotional pressure, and execution barriers.
3. **Care workspace** — records, results, medication lists, referrals, follow-up tasks, and returned evidence can be exposed through explicit actions.
4. **World progression and closure** — external events, time, results, handoffs, residual risk, and terminal closure are adjudicated by the simulation/evaluation layer.

## Design boundaries

- The evaluated doctor model should not receive hidden ground truth, evaluator-only hints, case author notes, or scoring rubrics.
- A doctor-side receipt means an action was registered; it does not mean the patient executed it or that results returned.
- Closure is a bounded episode-level judgement. Acute handoff, test ordering, transient improvement, or reassurance does not automatically equal terminal closure.
- Runtime checkpoints and summaries are audit artifacts, not patient-visible content.
- API credentials are read from environment variables or private local files and must not be committed.

## Public quick start

Install from the repository root:

```bash
python -m pip install -e .
```

Validate the released CL120 public case set without live model calls:

```bash
python -m careloop.runtime_lite \
  --validate-cases \
  --case-glob "cases/public_cl120_deidentified_120/*.json"
```

Run a short scripted smoke trajectory:

```bash
python -m careloop.runtime_lite \
  --case cases/public_cl120_deidentified_120/case_CL120_EHR_001.json \
  --client scripted \
  --max-turns 3 \
  --output-dir outputs/scripted_smoke_demo
```

Run with an OpenAI-compatible endpoint supplied by the user:

```bash
export CARELOOP_LITE_BASE_URL="https://your-openai-compatible-provider.example/v1/chat/completions"
export CARELOOP_LITE_API_KEY="replace-with-your-private-key"
export CARELOOP_LITE_MODEL="replace-with-runtime-model-id"

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

## Useful runtime flags

- `--max-turns N`: maximum interaction turns before a valid open-at-limit trajectory is written.
- `--parallelism N`: number of cases to run concurrently when multiple cases are supplied.
- `--stage-checkpoints`: write latest checkpoint files during long runs.
- `--resume-existing`: skip already completed trajectories in an output directory.
- `--allow-stage-checkpoint-resume`: allow continuation from stage checkpoints where supported.
- `--continue-on-error`: write a runtime-error trajectory record instead of stopping the whole batch.
- `--no-eval`: skip end-of-run evaluator calls when only trajectory generation is desired.
- `--llm-bounded-retries`, `--llm-max-retries`, `--llm-retry-backoff-seconds`, `--llm-retry-max-delay-seconds`: bound retry behavior for large experiments.

## Outputs

Typical output directories contain:

- `trajectory.json`: final trajectory record;
- `trajectory.checkpoint.json`: latest checkpoint when stage checkpoints are enabled;
- `summary.md` / `summary.checkpoint.md`: human-readable summaries;
- `quality_report.json`: deterministic trajectory-quality and runtime-integrity signals;
- `runtime_phase.json`: lightweight runtime status file for monitoring.

These artifacts are intended for research auditing. They may contain simulated clinical details from the input case and generated conversation and should be reviewed before public redistribution.
