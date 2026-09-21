# Residual Risk Governor — G2 Shadow Contract

This prompt is a documentation stub for future optional LLM shadow review. G2
currently uses deterministic offline audit only and must not be invoked by the
runtime.

The reviewer should classify residual issues as:

- acceptable
- acceptable_with_tracking
- conditional_review_required
- not_acceptable_for_safe_closure
- action_blocking
- failure_terminal_candidate

A residual issue is acceptable only when clinical risk tier, responsible actor,
time/trigger, abnormal-result escalation path, and patient/family executability
are all adequate for the specific disease context. Pending pathology, acute ED
labs, and medication stop/restart confusion require disease- and action-specific
review; “has a follow-up plan” is not sufficient by itself.
