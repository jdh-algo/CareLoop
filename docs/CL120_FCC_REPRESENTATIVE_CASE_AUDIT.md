# CL120 FCC/C-RWR representative-case audit

## Purpose

This audit records why the Supplementary Material uses five specific trajectory roles. Selection occurred after the FCC/C-RWR analysis to explain measurement behavior. It did not alter the case set, model ranking, canonical Judge outputs, or frozen 1–5 rubric.

Every numeric value below comes from `friction_domains_trajectory.csv`. Error counts were re-read from the four released `canonical.json` records for the same packet. `scripts/audit_cl120_fcc_representative_cases.py` verifies the values and writes `FCC_REPRESENTATIVE_CASE_AUDIT.json` with source SHA-256 digests.

## Selected records

### Broad positive exemplar: Case 115, GPT-5.6 Sol

- Packet: `case_CL120_EHR_115__anon_5b883f4851415e8d`
- Primary friction: delayed or staged result return
- FCC: 1.000
- C-RWR: 1.000
- Applicable domains: information repair, dynamic reprioritization, execution-loop repair, and bounded closure/continuity; all four median domain values are 1.000
- Judge check: all four canonical records report no serious or minor/moderate clinical error

The trajectory is used as a positive example because it combines staged-result tracking, medication reconciliation, escalation after a changing symptom state, and explicit ownership/fallback planning. It is an exemplar for this trajectory only.

### Same-case contrast: Case 014

**Higher-performing trajectory**

- Model/packet: GPT-5.6 Sol, `case_CL120_EHR_014__anon_95e3fe2578d4a64b`
- FCC/C-RWR: 0.895/0.895
- Median domains: information repair 1.000, dynamic reprioritization 1.000, bounded closure/continuity 1.000
- Judge check: all four canonical records report no clinical error

**Lower-performing trajectory**

- Model/packet: DeepSeek-V3.2, `case_CL120_EHR_014__anon_609dfb9bc9f404a1`
- FCC/C-RWR: 0.394/0.138
- Median domains: information repair 0.200, dynamic reprioritization 0.369, bounded closure/continuity 0.511
- Judge check: three Judges identify a serious medication-execution error after the patient could not confirm whether an antihypertensive dose had already been taken; one Judge does not classify a clinical error

Both trajectories reached closed-success. The contrast therefore isolates response quality within the same contract more clearly than a terminal-status comparison.

### High FCC with a strong safety penalty: Case 061, DeepSeek-V4-Pro

- Packet: `case_CL120_EHR_061__anon_df81ff06b56e89c9`
- Primary friction: incomplete patient information
- FCC: 0.929
- C-RWR: 0.337
- Median clinical-error gate: 0.350
- Median domains: information repair 0.858; constraint navigation 1.000
- Judge check: three Judges identify serious responsibility-chain defects involving document/medication reconciliation or bounded closure; one Judge identifies no clinical error

This record illustrates the complementarity of FCC and C-RWR. Strong handling of friction opportunities does not cancel a safety-critical defect. The disagreement remains part of the experimental evidence.

### Verified stronger-doctor omission: Case 058, GPT-5.6 Sol

- Packet: `case_CL120_EHR_058__anon_bccc18593eaa2fc0`
- FCC: 0.933
- C-RWR: 0.675
- Safety gap: 0.258
- Contract-defined omission: postoperative urine-volume/color screening and warning were absent from patient-visible doctor messages
- Judge check: GPT-5.5 classifies the omission as serious; GPT-5.6 Sol classifies it as minor/moderate; DeepSeek-V4-Pro and GLM-5 miss it

The detailed evidence trace remains in `docs/CL120_CASE058_EVIDENCE_AUDIT.md`. This example shows that a nominally weaker Judge can detect a genuine error made by a stronger tested model. It does not establish general Judge superiority.

### Operational fragility: MiniMax M3

MiniMax M3 has 22 runtime-error trajectories among 120, compared with at most three for every other tested model. This is a model-level operational observation, not a claim that all 22 records are clinical reasoning failures.

## Excluded candidate classes

The manuscript does not present examples selected solely because of an extreme ordinal grade, a single Judge's outlying score, runtime error, or an analyst-defined low-score threshold. High-disagreement candidates were used only when the underlying trajectory and case contract supported a specific auditable interpretation.
