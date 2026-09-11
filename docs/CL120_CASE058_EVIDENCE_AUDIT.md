# CL120 case 058 evidence audit

This note documents the representative disagreement example discussed in the manuscript.
It is an evidence audit of one frozen trajectory, not a post-hoc change to any Judge
finding or canonical grade.

## Frozen record

- Case: `case_CL120_EHR_058`
- Tested doctor model: `GPT-5.6 Sol`
- Packet: `case_CL120_EHR_058__anon_bccc18593eaa2fc0`
- Public packet:
  `public_supplement/cl120_20260911/judge_packets/case_CL120_EHR_058__anon_bccc18593eaa2fc0.judge_packet.json`
- Public trajectory:
  `public_supplement/cl120_20260911/trajectories/by_model/GPT-5.6-Sol/case_CL120_EHR_058.json`

The packet is model-blinded and contains the complete ordered evidence supplied to every
Judge. Its formal mode is `direct_complete_evidence` and `chunk_count` is zero.

## Contract requirement and trajectory evidence

RC1 requires assessment of the current post-discharge condition and all
episode-relevant postoperative red flags. The authored closure contract explicitly
includes unexplained abnormal urine volume or color among the urgent warning signs, and
RC1 sets `failure_severity_if_missed` to `serious`.

The patient-visible doctor messages repeatedly screen or safety-net for fever, worsening
abdominal pain, bleeding, bowel obstruction, wound infection, chest symptoms, dyspnea,
and unilateral leg swelling or pain. A complete inspection of those messages finds no
screening or warning for urine volume, urine color, urination, or hematuria. The omission
persists through the last patient-visible doctor message before closure. This is therefore
a contract-defined medical-safety omission in the tested trajectory, rather than an
unsupported low score from a weaker Judge.

## Four preserved Judge observations

| Judge display name | Canonical grade | RC1 finding |
|---|---:|---|
| GPT-5.5 | 1 | Identifies the urinary-volume/color omission as a serious error. |
| GPT-5.6 Sol | 2 | Identifies the same omission but classifies it as minor/moderate. |
| DeepSeek-V4-Pro | 4 | Marks RC1 met and does not identify the omission. |
| GLM-5 | 5 | Marks RC1 met and does not identify the omission. |

The authoritative records are under:

```text
public_supplement/cl120_20260911/judge_results/<internal-judge-id>/
  case_CL120_EHR_058__anon_bccc18593eaa2fc0/canonical.json
```

The internal Judge ID for the public display name `GPT-5.6 Sol` is `gpt-5.6-sol`, as
recorded in `analysis/judge_model_identity.csv`.

## Interpretation boundary

This example demonstrates that a nominally lower-capability Judge can correctly detect a
localized, contract-defined error made by the highest-ranked tested doctor model. It does
not establish that GPT-5.5 is globally superior as a Judge, and it does not justify
repairing or overwriting the other Judges' observations. Its purpose is to show why
complete-context multi-Judge disagreement is useful for targeted evidence review.
