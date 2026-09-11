# CL120 public deidentification and release review

> **Correctness status (2026-09-11):** Privacy/deidentification review and scientific-result validation are separate gates. The sole public scientific result release is `public_supplement/cl120_20260911/`; superseded result snapshots are excluded.

This document summarizes the public-release privacy review for `cases/public_cl120_deidentified_120/`. It is intentionally summary-only. Raw EHR records, source-to-case mappings, provider credentials, notebook URLs, local paths, operational logs, and raw review transcripts are not included in the repository.

## Scope

- Public case set: 120 deidentified EHR-seeded CareLoop case contracts.
- Public directory: `cases/public_cl120_deidentified_120/`.
- Manifest: `manifests/public_cl120_manifest.json`.
- Intended use: research simulation and benchmark reproducibility.

The cases are not clinical advice and must not be used for real-world diagnosis, treatment, or patient management.

## Deidentification actions

Before public release, privacy-sensitive and source-linkage fields were removed or generalized, including:

- direct patient identifiers;
- clinician, staff, and care-team names;
- phone, ID, encounter, accession, specimen, bed, ward, and long source-system number-like strings when not clinically necessary;
- real institution names, location names, department/ward codes, outpatient room/building references, and storage references;
- absolute source dates and source timestamps, normalized to relative simulated time or placeholders;
- source provenance fields, source seed IDs, raw file names, source paths, and source-to-case mapping material.

Clinically relevant non-identifying information was preserved where needed for simulation fidelity, including diagnoses, symptoms, medication classes/names, test patterns, procedure context, patient/caregiver behavior, friction design, closure contracts, and hidden-state evaluation points.

## Release gate

The release gate combined deterministic scanning and LLM-assisted privacy review. The final public case set passed the review before inclusion in the repository.

## Exclusions

The public repository does not include:

- raw or partially deidentified EHR source files;
- source-to-case mapping tables;
- API keys, provider routes, private endpoint configuration, or notebook tokens;
- local or remote working-directory paths;
- private experiment outputs, operational monitoring logs, process-control scripts, or retry artifacts;
- raw privacy-review transcripts.

## Residual-risk note

The CL120 cases are deidentified clinical simulation artifacts, not original records. Users should not attempt re-identification, linkage, or clinical use.


## CL120 trajectory supplement privacy scope

The final canonical supplement is `public_supplement/cl120_20260911/`. It contains 1,200 deidentified trajectories, 1,200 outcome-blind Judge packets, 4,800 canonical Judge cells, all 8,396 preserved attempts, and public analysis/provenance files. Its release safety audit scanned the complete release tree and reported zero credential, notebook-token, private-path, or prohibited sensitive-string findings. The package manifest verifies byte integrity; the release-build and analysis audits separately verify scientific lineage and matrix completeness.

