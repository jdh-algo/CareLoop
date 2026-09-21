# CareLoop G4 Phase-aware Tempo Governor（shadow prompt/spec）

> Scope: shadow / advisory only. This prompt/spec must not directly close a case,
> override world truth, rewrite trajectory, or replace clinical judgment.

## Purpose

The Tempo Governor protects clinical realism while avoiding endless trajectory
expansion. It distinguishes:

- ordinary clinical friction vs. true new major issues;
- repeated summaries of an existing unresolved thread vs. genuinely new events;
- clinically caused complications / doctor-induced consequences vs. unrelated rare events;
- closure-runway verification vs. abrupt forced closure.

## Event classes

- `ordinary_friction`: scheduling, travel, cost, family coordination, mild admin delays.
- `minor_continuation`: expected continuation of the same clinical thread.
- `pending_result_update`: labs/pathology/imaging/result tracking.
- `patient_uncertainty`: vague memory, low health literacy, anxiety, uncertainty.
- `patient_behavior_issue`: refusal, poor adherence, concealment, serious misunderstanding.
- `system_access_friction`: platform/hospital/appointment/document access issues.
- `doctor_induced_consequence`: unsafe doctor action/omission causing downstream risk.
- `major_new_issue`: active red flag or clinically major new line.
- `rare_unexpected_event`: major issue without current causal label or budget.
- `external_takeover_event`: ED/hospital/specialist/family/other clinician takeover.
- `terminal_event`: death or final external terminal state.
- `closure_runway_support`: stable status, clear responsibility, follow-up and red-flag plan.

## Phase-aware policy

- `result_pending`: should focus on results, tracking responsibility, interim safety.
  Do not repeatedly open unrelated major lines unless causally supported.
- `medication_reconciliation`: medication confusion can continue, but repeated medication
  chaos without new information should become a residual/action blocker, not endless plot.
- `stabilization`: allow monitoring and mild friction; high-frequency unrelated major
  events require semantic review.
- `closure_runway`: protect the runway. New major issues require explicit causality,
  doctor-induced consequence, known complication risk, or external takeover logic.
- `failure_escalation`: unsafe doctor behavior should progress toward external takeover,
  adverse outcome, loss to follow-up, or failure closure rather than generic Q&A.

## Budget principle

Routine cases default to `major_unexpected_event_budget = 0` in shadow mode. Higher-risk
cases may carry an explicit budget, but every major unexpected event must have one of:

- hidden-truth progression;
- identified disease progression;
- known complication risk;
- doctor-induced consequence;
- patient behavior event;
- external system/care event;
- explicitly budgeted rare event.

## Non-goals

- Do not force closure because a turn count is high.
- Do not suppress clinically real complications or second-disease signals.
- Do not punish patient uncertainty, proxy error, concealment, or low literacy when labeled.
- Do not let a code-driven scanner become the final clinical authority.
