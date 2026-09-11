# CareLoop Case Construction Guide

Version: 1.0  
Date: 2026-09-08  
Status: public-use guide for CareLoop case authors

## 1. Purpose

CareLoop cases are not ordinary clinical vignettes. A CareLoop case is a **simulation contract** for a patient-facing medical world. It must support a dynamic conversation in which an evaluated doctor model encounters incomplete information, patient or family misunderstanding, delayed evidence, real-world friction, external-system gaps, and responsibility-transfer problems.

This guide explains how to construct CareLoop-compatible cases from private EHR, public EHR, synthetic records, or manually curated clinical scenarios. It is intentionally flexible about the earliest EHR ingestion steps because different institutions organize records differently. It is strict about the downstream case standards because a case that is too vague, too scripted, too revealing, or inconsistent with CareLoop closure governance will produce misleading trajectories.

A good CareLoop case should answer three questions:

1. **Can the simulator play a plausible patient-facing world?**  
   The patient, caregiver, documents, results, delays, misunderstandings, and external systems must behave like clinically plausible reality rather than like a mechanical case report.

2. **Can the evaluated doctor model be held responsible for the right things?**  
   The case must define what is inside the doctor responsibility chain, what is a higher-order opportunity, and what residual risks can remain after a bounded episode closes.

3. **Can closure be judged without conflict?**  
   The case must not ask CareLoop to close a whole disease journey when the entry point only supports a short bounded task; conversely, it must not allow handoff or reassurance to count as closure when unresolved safety obligations remain.

## 2. Scope and intended users

This guide is intended for researchers and developers who want to build new CareLoop case sets from their own data sources. Sources may include:

- longitudinal hospital EHR;
- outpatient records;
- discharge summaries;
- laboratory and imaging reports;
- public deidentified EHR datasets;
- clinician-authored synthetic records;
- hybrid EHR-seeded and synthetic cases.

The guide does **not** prescribe a single EHR schema. It instead defines a source-agnostic pipeline and a strict final case standard.

CareLoop is a research evaluation framework. Cases produced using this guide are for simulation and benchmarking. They are not clinical advice, diagnostic protocols, or treatment recommendations.

## 3. Core design principle

CareLoop evaluates whether a doctor model can act responsibly in a changing care environment. Therefore:

> A CareLoop case should model a **patient-facing clinical episode with real-world friction**, not a static question with a hidden answer.

The doctor model should see only what a patient-facing AI assistant could reasonably see. Hidden ground truth may guide the simulator and evaluators, but hidden truth must not be injected into the doctor path unless it becomes visible through patient action, workspace records, returned results, or external-system feedback.

## 4. Source-agnostic EHR ingestion

The first stage may remain deliberately flexible. Different source systems may be organized as patient-level timelines, encounter-level notes, FHIR resources, document folders, CSV exports, public benchmark tables, or clinician summaries. The construction workflow should adapt to the source rather than require a fixed record layout.

However, every candidate clinical source material should eventually be abstracted into the same authoring ingredients:

1. **Clinical anchor**: the central clinical problem or risk.
2. **Timeline**: enough temporal structure to distinguish what is known now, what happened earlier, and what may return later.
3. **Available evidence**: diagnoses, symptoms, medications, tests, procedures, discharge instructions, referrals, follow-up plans, and relevant negatives.
4. **Uncertainty or friction**: at least one realistic reason why the patient-facing task is nontrivial.
5. **Patient-facing entry point**: the moment at which the doctor model is contacted.
6. **Closure horizon**: the bounded episode that can reasonably close inside the simulation.
7. **Privacy boundary**: a safe abstraction that removes direct identifiers and avoids raw note leakage.

If a source cannot support these ingredients, do not force it into a CareLoop case. Use it only as background inspiration or reject it.

## 5. Privacy and data governance

When deriving cases from EHR, privacy constraints are part of the case construction standard, not a final cleanup step.

### 5.1 Required privacy practices

- Assign new anonymous case IDs and, if needed, anonymous source IDs.
- Keep any source-to-case mapping in a private, non-public location.
- Do not publish raw names, addresses, phone numbers, ID numbers, medical record numbers, admission numbers, clinician signatures, exact local source paths, or raw note blocks.
- Shift, coarsen, or abstract exact dates unless exact timing is essential and deidentified.
- Replace institution names with generic or fictional names unless explicit permission exists.
- Convert raw EHR text into structured, deidentified summaries.
- If using an LLM for authoring, send only deidentified structured briefs unless institutional governance explicitly permits otherwise.

### 5.2 Public-release rule

A public CareLoop case should be reproducible as a benchmark contract but should not be reversible to a real patient or institution. The public artifact should contain enough clinical detail for simulation and judging, not enough source detail for reidentification.

## 6. Case-set design before individual case writing

Do not build a case set by simply selecting the most interesting records. First design the case set blueprint.

### 6.1 Disease-domain coverage

A moderate-size case set cannot cover every disease. Prefer a common-disease-dominant design with some rare, high-risk, or easily missed cases. At the set level, include broad coverage across systems such as:

- cardiovascular;
- respiratory;
- endocrine and metabolic disease;
- renal and urologic disease;
- gastroenterology and hepatology;
- neurology;
- obstetrics and gynecology;
- pediatrics and neonatology;
- oncology and hematology;
- infectious disease;
- musculoskeletal, surgery, rehabilitation, and geriatrics;
- mental health and somatic overlap;
- dermatology, ENT, and ophthalmology.

Multi-morbidity is valuable. Real patients often have interacting problems: malignancy plus anticoagulation, diabetes plus wound infection, stroke plus dysphagia, pregnancy plus medication safety, chemotherapy plus infection risk, or psychiatric illness plus cancer treatment adherence.

### 6.2 Entry-point coverage

The case set should cover many starting points in the care journey. Suggested entry-point families:

| Entry point | What it tests |
|---|---|
| `pre_handoff` | Whether the doctor recognizes risk before the patient reaches definitive care. |
| `post_handoff` | Whether the doctor supports safe execution after a transition has already occurred. |
| `result_return` | Whether the doctor interprets new or delayed results and acts on them. |
| `discharge_transition` | Whether the doctor reconciles discharge instructions, medications, red flags, and follow-up. |
| `home_execution_failure` | Whether the doctor handles failed adherence, misunderstanding, deterioration, or access barriers at home. |
| `longitudinal_followup` | Whether the doctor maintains continuity across recurring decisions and residual risk. |
| `palliative_or_goal_concordant_care` | Whether the doctor handles advanced illness, goals, burdens, and escalation boundaries. |

A good case set avoids a single dominant entry point. CareLoop's purpose is to reveal where a patient-facing AI fails across the clinical world, not only at first contact.

### 6.3 Friction diversity

Across the case set, include heterogeneous friction types. Examples:

- incomplete patient or caregiver information;
- misremembered diagnosis, medication, procedure, or discharge instruction;
- delayed or staged result return;
- false reassurance from a prior negative test;
- possible false negative or false positive test;
- medication confusion, duplication, omission, allergy, contraindication, or interaction;
- low adherence, refusal, minimization, or competing life priorities;
- family disagreement, caregiver fatigue, or decision conflict;
- cost, transportation, work, insurance, or appointment barriers;
- external-system gaps, bed waiting, referral ambiguity, or cross-institution information loss;
- privacy, stigma, mental health, substance use, reproductive history, or sensitive family context;
- document or image quality problems;
- language, literacy, numeracy, or technology barriers.

The set should not make every case maximally difficult. Use a distribution such as light, moderate, heavy, and extreme friction. Light cases help evaluate basic responsibility; heavy cases test resilience and higher-order capability.

### 6.4 Prospective task burden and expected trajectory length

Case complexity is not the same as disease severity or observed model failure. A high-acuity case may be short if the only safe action is immediate emergency transfer. A lower-acuity case may be long if it involves delayed results, medication reconciliation, family disagreement, and follow-up planning.

For a future benchmark that intends to make confirmatory complexity-gradient claims, define and freeze the complexity rubric and every case label before any tested-model trajectory is generated. Never construct a case label from post-run model score, turn count, nonclosure, runtime error, or Judge disagreement and then use those same outcomes to validate the label.

Design prospective task burden using:

- clinical risk;
- information incompleteness;
- number of active threads;
- likelihood of patient/caregiver misunderstanding;
- external-system complexity;
- amount of required follow-up or teach-back;
- number and subtlety of higher-order test points.

## 7. Candidate inclusion and exclusion

### 7.1 Include a candidate when it has

- a clinically meaningful patient-facing task;
- enough evidence to define hidden truth and visible records;
- at least one plausible real-world friction;
- a bounded episode that can close without resolving the entire disease journey;
- at least two higher-order test opportunities or a clear reason why the case belongs to a lower-complexity validation layer;
- no unavoidable privacy exposure;
- a plausible patient or caregiver voice.

### 7.2 Exclude or rework a candidate when

- the source is too sparse to support clinical claims;
- the only possible task is static medical knowledge recall;
- the case requires hidden truth that the doctor can never discover or act on;
- closure would depend on unrealistic omniscience;
- the patient-facing entry point contradicts the EHR timeline;
- the case is a handoff trap with no downstream action path;
- all friction is generic and not tied to the individual episode;
- the case would reveal identifiable source information;
- the record is so rare or institution-specific that it cannot be safely generalized.

## 8. From source record to CareLoop case

The authoring process should proceed in this order.

### Step 1: Build a deidentified source brief

Summarize the source into a safe brief containing:

- age band and sex when relevant;
- high-level departments or care settings;
- key diagnoses or suspected diagnoses;
- important symptoms and negatives;
- medications and medication changes;
- test results, with timing and reliability;
- procedures, handoffs, discharge plans, and follow-up obligations;
- realistic uncertainties and patient/caregiver constraints.

Do not copy raw note text unless it has been reviewed and deidentified.

### Step 2: Choose the patient-facing entry point

Select the simulation start point deliberately. Ask:

- What does the patient or caregiver know at this moment?
- What is missing, misunderstood, delayed, or contested?
- What action can the doctor model reasonably influence?
- What is the bounded episode that could close?

The entry point must be clear enough that the simulator can generate natural first contact. It should not require the patient to narrate a full clinical abstract.

### Step 3: Define the clinical anchor

The clinical anchor is the main risk or decision axis. It should be concise and specific. It should identify the core issue without turning the initial patient message into a diagnosis dump.

Good anchor:

> Recent non-ST elevation myocardial infarction after balloon angioplasty, discharge medication reconciliation uncertain, dual antiplatelet therapy at risk of interruption, family also distracted by incidental pulmonary nodule.

Poor anchor:

> Elderly patient with cardiovascular disease and many problems needs follow-up.

### Step 4: Define hidden truth and visible evidence separately

CareLoop requires a strict separation between:

- **hidden simulation state**: what the world knows;
- **doctor-visible information**: what the doctor can see through the patient, family, workspace, or results;
- **patient/family beliefs**: what the patient or caregiver thinks, which may be incomplete or wrong.

Never let hidden truth leak into the opening message unless the patient would realistically know it.

### Step 5: Construct workspace records

Workspace records should be useful, not exhaustive. Include deidentified records that can support responsible doctor behavior:

- current problem summary;
- medication list or partial medication list;
- laboratory or imaging results;
- procedure notes or discharge summaries;
- referral, admission, or appointment status;
- prior history relevant to risk stratification;
- external records requiring upload or patient reading when appropriate.

Avoid clutter that cannot affect the trajectory. Noise is useful only when it changes reasoning, prioritization, trust, or execution.

### Step 6: Write the patient or caregiver opening

The opening message should sound like a real patient or caregiver asking for help. It should be:

- brief enough to be natural;
- specific enough to establish the entry point;
- incomplete in plausible ways;
- not a full medical abstract;
- not a list of all hidden facts;
- aligned with the speaker's literacy, stress, relationship, and context.

Avoid fixed templates such as:

- "This case is about...";
- "The main problems are...";
- "The patient has diagnosis X and needs management...";
- mechanically enumerated histories that a layperson would not say.

### Step 7: Define actor profiles

A CareLoop patient or family actor should have bounded knowledge and realistic behavior. Define:

- relationship to patient;
- education or medical literacy;
- emotional state;
- trust and adherence pattern;
- ability to read documents, send photos, call hospital desks, arrange transport, buy medication, or monitor symptoms;
- likely misunderstandings;
- topics they avoid, minimize, or overemphasize.

Actors should not be perfectly cooperative unless the case intentionally tests a low-friction scenario. They should not be so obstructive that no responsible doctor can make progress.

### Step 8: Build the friction storyboard

For each primary friction, specify:

- why it exists in this case;
- how it may first appear;
- what action by the doctor can reveal or reduce it;
- how the world may respond;
- what failure would look like;
- whether the friction is required, likely, conditional, or optional.

A friction should be more than a label. It should change the trajectory.

### Step 9: Define dynamic events and result returns

If the case involves delayed evidence or external systems, define plausible world events:

- lab result returns;
- imaging report availability;
- nurse station or pharmacy clarification;
- specialist call-back;
- bed availability;
- patient deterioration or stabilization;
- family refusal or agreement;
- appointment success or failure;
- medication access issue.

Avoid deterministic event ladders unless the case is explicitly scripted. CareLoop works best when event probabilities and timing remain plausible but not rigid.

### Step 10: Write the closure contract

Closure is not simply the end of conversation. It is an accountability decision. The closure contract must define what counts as safe bounded closure for this episode.

At minimum, specify:

- managed problem IDs;
- episode scope;
- closure horizon;
- required actions or verifications;
- blocking residual risks;
- allowed residual risks;
- handoff policy;
- escalation boundary;
- evidence required for closure;
- whether closure can occur at home, after transfer, after result review, after follow-up scheduling, or only after external custody is confirmed.

## 9. Responsibility-chain and higher-order capabilities

CareLoop case design should separate **responsibility-chain requirements** from **higher-order capabilities**.

### 9.1 Responsibility-chain requirements

These are duties that a responsible patient-facing doctor must satisfy inside the case. Failure may make the trajectory borderline or unsafe.

Examples:

- identify urgent red flags;
- avoid dangerous reassurance;
- recommend emergency care when indicated;
- avoid unsafe self-transport when ambulance or monitored transport is required;
- reconcile high-risk medication changes;
- avoid telling the patient to stop essential therapy without verification;
- recognize when information is insufficient for a definitive recommendation;
- ask for or obtain necessary visible evidence;
- ensure the patient or caregiver can execute the plan;
- establish escalation criteria;
- define follow-up responsibility, timing, and fallback path;
- communicate safety-critical uncertainty.

### 9.2 Higher-order capability test points

Higher-order capabilities are not the basic safety obligations. They test whether the doctor model can go beyond minimum responsibility without hiding or compensating for responsibility-chain failures.

Examples:

- detect and correct a patient or caregiver's wrong statement;
- identify a misleading false reassurance or possible false negative;
- suspect a false positive, sample error, or inconsistent result and request verification;
- notice cross-disease interactions, such as anticoagulation plus bleeding risk or diabetes plus wound infection;
- resolve medication duplication or naming confusion using a safe verification path;
- handle family disagreement without losing the clinical priority;
- manage privacy-sensitive or stigmatized history without forcing disclosure;
- adapt plan to cost, transport, work, literacy, or technology limits;
- maintain continuity across delayed results and prior advice;
- keep a non-urgent but important residual issue safely bounded.

### 9.3 Non-overlap rule

Do not count the same item as both a responsibility-chain requirement and a higher-order capability. For example, in an acute chest pain case, recognizing the need for emergency evaluation is not higher-order; it is core responsibility. A higher-order point might be recognizing that a prior negative ECG does not rule out the current episode, or preventing unsafe self-transport after the patient resists calling emergency services.

### 9.4 Case authoring implication

Each full-strength evaluation case should contain at least two distinct higher-order test points. Otherwise its maximum plausible quality score may be capped by design: a case with no higher-order opportunities can test acceptable care but cannot fairly test perfect care.

## 10. Closure compatibility standard

A case is closure-compatible when its closure contract matches its entry point and world affordances.

### 10.1 Bounded episode, not whole-disease closure

Most CareLoop cases should close a bounded episode, not the entire disease course. Examples:

- safe emergency handoff for acute chest pain;
- medication reconciliation and red-flag plan after discharge;
- result interpretation and follow-up arrangement;
- safe home monitoring until a scheduled specialist visit;
- escalation from home to emergency department after deterioration.

A case should not require cure, full oncologic staging, complete chronic disease control, or lifetime follow-up unless the simulation scope explicitly supports it.

### 10.2 Handoff is not automatically closure

Handoff may be:

- a terminal closure milestone when operational custody is confirmed and no current responsibility remains;
- a transition milestone when the doctor must still help the patient execute, verify, or understand next steps;
- the entry point of a downstream case.

The case must say which one applies. Do not allow a handoff to close the episode merely because the doctor said "go to hospital" if the patient cannot execute that instruction or if safety-critical information was not communicated.

### 10.3 Allowed versus blocking residual risk

Every case should distinguish:

- **blocking residual risks**: unresolved issues that prevent closure;
- **allowed residual risks**: unresolved issues that can remain if responsibility, timing, fallback, and escalation are clear.

Example: after acute coronary syndrome discharge, uncertainty about a long-term pulmonary nodule may be an allowed residual issue if it has a follow-up path. Uncertainty about dual antiplatelet therapy in the first days after intervention may be blocking.

### 10.4 Observable closure evidence

Closure conditions should require observable evidence in the trajectory:

- patient or caregiver performed an action;
- result was returned and reviewed;
- medication plan was verified;
- appointment was scheduled or fallback path set;
- red flags were understood;
- external team accepted custody;
- teach-back was sufficient for safety-critical steps.

A closure condition that cannot be observed by the simulator or judge should be rewritten.

## 11. Real-world friction standard

Every case should include some friction, but not every case should be heavily obstructive. The key is clinical plausibility and case specificity.

### 11.1 Friction intensity

Suggested levels:

- **Light**: one or two small execution barriers; the patient can follow guidance with minor clarification.
- **Moderate**: several barriers that affect sequencing, adherence, or verification.
- **Heavy**: friction repeatedly changes the safe plan or requires external-system navigation.
- **Extreme**: severe access, adherence, family, stigma, cost, or clinical instability constraints; use sparingly.

### 11.2 Friction quality criteria

A good friction is:

- anchored in the clinical source material;
- plausible for the patient or caregiver;
- visible or triggerable during conversation;
- clinically consequential;
- resolvable or bounded by responsible action;
- not merely decorative.

Poor friction examples:

- "family is anxious" with no effect on behavior;
- "records are incomplete" but all facts are immediately available;
- "patient has cost concerns" but cost never affects choices;
- "low adherence" but the patient follows every instruction perfectly;
- "language barrier" without any communication consequence.

## 12. Natural patient-facing simulation standard

A case should let CareLoop simulate a realistic patient or family member. Authors should define what natural means for that individual.

### 12.1 The opening message

The opening should reflect what the patient or caregiver would actually say. For example:

- "My father's discharge medicines are in several bags and I cannot tell which ones he still needs."
- "The nurse said the report came back, but I only remember one word and now everyone at home is worried."
- "The wound looks worse and his blood sugar is all over the place, but he says he does not want to go back."

Avoid:

- full differential diagnosis statements;
- exact EHR-style lists;
- unnatural timestamps and document titles in the first message;
- revealing all high-order traps at once.

### 12.2 Imperfect but not incoherent actors

Patients and caregivers may be mistaken, worried, or incomplete. They should still be communicable. The actor should not be so confused that the doctor cannot reasonably help, unless that is the central tested access problem.

### 12.3 Avoid over-cooperation

If every actor perfectly follows instructions and repeats plans in ideal checklist format, the simulation becomes unrealistic and less discriminative. Build in plausible partial execution, delay, uncertainty, or selective recall.

## 13. Workspace and evidence design

CareLoop should give the doctor model a realistic clinical workspace. The workspace is not a gold-answer page. It should include enough evidence for responsible action while preserving patient-facing uncertainty.

### 13.1 Include

- relevant current records;
- selected prior history;
- medication information;
- key laboratory, imaging, pathology, or procedure results;
- discharge or referral instructions;
- pending tests or delayed reports;
- patient-uploaded or family-read documents when appropriate;
- explicit reliability notes when a record is incomplete, external, or patient-transcribed.

### 13.2 Avoid

- raw EHR dumps;
- irrelevant normal findings;
- hidden facts in doctor-visible form;
- excessive institution-specific details;
- overly clean summaries that remove the need for patient interaction;
- records that contradict the closure contract without explanation.

## 14. Suggested case JSON sections

CareLoop versions may evolve, but a robust case usually contains the following conceptual sections:

```json
{
  "case_id": "case_example_001",
  "title": "Short patient-facing title",
  "case_type": "domain_or_entry_point_family",
  "clinical_anchor": "Concise clinical problem/risk",
  "initial_chat": {
    "speaker": "patient_or_family_role",
    "message": "Natural opening message",
    "metadata": {
      "speaker_display": "Patient / caregiver label",
      "speaker_category": "patient|family|caregiver",
      "relationship_to_patient": "..."
    }
  },
  "hidden_simulation_state": {
    "root_truth": {},
    "world_state": {},
    "patient_beliefs": {},
    "family_beliefs": {}
  },
  "workspace_contract": {
    "records": [],
    "available_panels": [],
    "visibility_rules": []
  },
  "actor_profiles": {},
  "real_world_friction_design": {
    "primary_friction": {},
    "secondary_friction": [],
    "case_specific_friction": [],
    "friction_storyboard": []
  },
  "dynamic_event_space": [],
  "closure_contract_v2": {},
  "evaluation_contract": {
    "responsibility_chain_requirements": [],
    "higher_order_test_points": [],
    "judge_notes": []
  },
  "authoring_metadata": {
    "source_type": "deidentified_ehr|public_ehr|synthetic|hybrid",
    "privacy_review_status": "...",
    "case_construction_version": "..."
  }
}
```

The exact schema can vary by runtime version, but the conceptual separation should remain stable.

## 15. Quality assurance workflow

A case should pass four layers of review.

### 15.1 Structural validation

- JSON parses.
- Required fields exist.
- Case IDs are unique.
- Workspace records are well formed.
- Dynamic event and closure fields match runtime expectations.

### 15.2 Privacy validation

Check for:

- names;
- phone numbers;
- ID numbers;
- medical record numbers;
- admission or outpatient numbers;
- exact source paths;
- clinician signatures;
- institution names if not licensed for release;
- raw note blocks copied from private records.

### 15.3 Clinical and closure validation

Ask:

- Is the clinical anchor supported by evidence?
- Does the entry point match what the patient could know?
- Are responsibility-chain duties clear?
- Are higher-order test points distinct from duties?
- Are there at least two higher-order test points for full-strength cases?
- Are blocking and allowed residual risks separated?
- Can closure be reached in a plausible bounded episode?
- Would a safe but non-identical doctor path still be admissible?

### 15.4 Runtime validation

Run small smoke tests before large experiments:

- validate case loading;
- run one or two trajectories with a trusted model;
- inspect whether actors sound natural;
- inspect whether friction appears without being forced;
- check whether closure is neither impossible nor too easy;
- verify that hidden truth does not leak into the doctor path;
- verify that final trajectory judges can identify responsibility-chain and higher-order evidence.

## 16. Case-set QA metrics

For a case set, maintain a manifest with at least:

- case ID;
- source type;
- disease domain;
- entry point;
- age band and broad sex when relevant;
- primary friction category;
- friction intensity;
- expected trajectory length band;
- number of higher-order test points;
- closure horizon;
- privacy review status;
- runtime validation status;
- manual review status.

Recommended set-level audits:

1. Domain distribution.
2. Entry-point distribution.
3. Friction distribution.
4. Difficulty distribution.
5. Duplicate or near-duplicate case detection.
6. Overuse of the same opening style.
7. Overuse of the same friction pattern.
8. Cases with fewer than two higher-order test points.
9. Cases where closure horizon conflicts with entry point.
10. Cases where handoff is ambiguous.

## 17. LLM-assisted authoring workflow

LLMs can help convert deidentified source briefs into natural CareLoop cases, but they should be used with guardrails.

### 17.1 Safe input to LLM authoring

Prefer sending:

- deidentified structured summaries;
- abstracted diagnoses;
- age bands;
- deidentified medication and test summaries;
- source-free timeline anchors;
- author-defined clinical goals and friction requirements.

Avoid sending:

- raw notes from private EHR;
- direct identifiers;
- source file names or internal paths;
- exact dates or locations not needed for simulation;
- long unreviewed record dumps.

### 17.2 Author-critic pattern

Use at least two passes:

1. **Author pass**: draft the case contract.
2. **Critic pass**: check privacy, clinical support, naturalness, friction specificity, closure compatibility, and higher-order point diversity.
3. **Repair pass**: revise flagged cases.
4. **Validation pass**: run schema, privacy, and smoke tests.

### 17.3 Do not outsource authority to the LLM

An LLM may draft, rewrite, or identify possible problems, but final authority should come from schema validation, clinical review, privacy review, and runtime evidence. Do not let an LLM invent unsupported diagnoses, procedures, terminal states, or hidden facts.

## 18. Common failure modes

### 18.1 Static vignette disguised as a CareLoop case

The case describes a disease but gives no patient-facing friction, no dynamic evidence, no action-dependent response, and no closure horizon.

### 18.2 Overexposed opening

The first message reveals the final diagnosis, all medications, all lab values, all hidden complications, and the correct plan. This eliminates simulation value.

### 18.3 Handoff closure conflict

The case expects closure after the doctor says "go to hospital," but the patient has not accepted, transport is unsafe, key risk was not communicated, or no operational custody was established.

### 18.4 Impossible closure

The case asks the doctor to close an entire cancer, chronic kidney disease, pregnancy, or rehabilitation journey in a short patient-facing episode.

### 18.5 Generic friction

The case lists friction categories but does not make them specific to the patient. Generic friction produces shallow trajectories.

### 18.6 Hidden-truth unfairness

The judge penalizes the doctor for not knowing a hidden fact that never became visible or reasonably inferable.

### 18.7 Over-cooperative actor

The patient or family perfectly reads documents, follows every instruction, and performs ideal teach-back. This may make trajectories look clean but reduces realism and discriminative power.

### 18.8 Overly obstructive actor

The patient refuses every step regardless of communication, making responsible closure impossible. Use severe nonadherence only when the case's closure contract supports bounded failure or safe escalation.

## 19. Public-release checklist

Before releasing a case set publicly, confirm:

- all cases pass structural validation;
- all cases pass privacy scan;
- no private source map is included;
- no raw EHR text is included unless explicitly licensed and deidentified;
- all case IDs are anonymous;
- all institution identifiers are removed or fictionalized;
- licenses and data-use permissions permit release;
- documentation states that cases are for research simulation only;
- sample trajectories have been inspected for hidden-truth leakage and unrealistic actor behavior;
- evaluation rubrics are published separately from hidden truth where appropriate.

## 20. Minimal authoring worksheet

Use this worksheet before writing the JSON.

```text
Case ID:
Source type:
Clinical domain:
Entry point:
Clinical anchor:
Patient/caregiver opening in one or two natural sentences:
What the patient/caregiver knows:
What the patient/caregiver misunderstands or omits:
Hidden truth:
Visible workspace records:
Pending or delayed evidence:
Primary responsibility-chain requirements:
Higher-order test point 1:
Higher-order test point 2:
Primary real-world friction:
Secondary frictions:
Actor persona and literacy:
External-system affordances:
Blocking residual risks:
Allowed residual risks:
Closure horizon:
Safe terminal closure evidence:
Privacy transformations applied:
Validation notes:
```

## 21. Recommended build sequence

For new users building a CareLoop case set from their own EHR:

1. Define the research question and target care settings.
2. Create a source-agnostic inventory of candidate episodes.
3. Design the case-set blueprint before selecting final records.
4. Select candidates for domain, entry-point, difficulty, and friction diversity.
5. Convert each candidate into a deidentified source brief.
6. Draft case contracts using the worksheet.
7. Add workspace records, actor profiles, dynamic events, friction storyboard, and closure contract.
8. Mark responsibility-chain requirements and higher-order test points.
9. Run schema and privacy checks.
10. Perform clinical and closure review.
11. Use LLM-assisted naturalization only on deidentified case drafts.
12. Run smoke trajectories and inspect naturalness, closure behavior, and hidden-truth boundaries.
13. Revise cases that are too easy, impossible to close, mechanically worded, or inconsistent with closure governance.
14. Freeze the case set and record a manifest.
15. Only then run formal model comparisons.

## 22. Summary standard

A CareLoop-ready case is acceptable only if it satisfies all of the following:

- It is deidentified and source-safe.
- It has a clear patient-facing entry point.
- It supports a realistic patient or caregiver actor.
- It contains clinically meaningful visible evidence and hidden state without leakage.
- It includes case-specific real-world friction.
- It defines responsibility-chain requirements.
- It includes higher-order test opportunities when used for full-strength evaluation.
- It defines a bounded closure horizon compatible with the entry point.
- It distinguishes blocking from allowed residual risks.
- It can be simulated without forcing a single gold path.
- It can be judged from trajectory evidence.

If a case fails any of these standards, revise it before formal CareLoop evaluation.
