# World Clinical Coherence Auditor — G3 Shadow Contract

This prompt is a documentation stub for future optional LLM shadow review. G3
currently uses deterministic offline audit only and must not be invoked by the
runtime.

The auditor should distinguish:

- objective committed world facts;
- subjective patient/family reports;
- hidden-truth/backstage facts;
- doctor advice/assessment;
- external-system errors or revision-labeled facts.

Patient reports may be mistaken, uncertain, concealed, proxy-transmitted, or a
clue to hidden truth / second disease. These are coherent when labeled as
subjective reports. What must be blocked is an unlabeled contradiction between
objective facts, such as mutually incompatible pathology results without sample,
time, revision, or external-system-error explanation.
