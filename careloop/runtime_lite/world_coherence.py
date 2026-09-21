from __future__ import annotations

"""G3 World Clinical Coherence Rail shadow audit for runtime_lite trajectories.

This module is offline/shadow-only. It does not alter runtime flow, prompts,
closure/scoring, or world generation. It gives later tranches evidence about
whether the simulated world distinguishes objective facts, subjective patient /
family reports, hidden-truth signals, and external-system errors.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from typing import Any, Mapping

from careloop.runtime_lite.episode_governance import _as_dict, _as_list, _event_text, _short, _turn_of
from careloop.runtime_lite.residual_risk import load_trajectory_payload

Json = dict[str, Any]
SCHEMA_VERSION = "careloop.world_clinical_coherence_audit.v1"


OBJECTIVE_ACTOR_PATTERN = r"Diagnostic|CareSystem|Hospital|ED|Laboratory|Radiology|Pathology|Nurse|Clinician|Workspace|Receipt|WorldDirector|Timekeeper|Result|Service"
HIDDEN_PATTERN = r"hidden_truth|hidden truth|隐藏真相|evaluator_only|internal_full|not_doctor_visible|backstage"
EXPLANATION_PATTERN = r"更正|复核|复查|补充|修订|录入错误|系统错误|外院|不同样本|不同时间|旧报告|本次|原来|术后|瘢痕|污染|标本|重新|追加|解释"
EXTERNAL_ERROR_PATTERN = r"external_system_error|system_error|录入错误|系统错误|报告错误|标本污染|样本污染|更正报告|修订报告"

CONCEPT_PATTERNS: dict[str, str] = {
    "pregnancy": r"妊娠|怀孕|孕|产后|月经|停经",
    "substance_use": r"吸毒|静脉注射|毒品|成瘾|海洛因|冰毒|药物滥用|隐瞒.*用药",
    "cancer_or_pathology": r"癌|恶性|肿瘤|腺癌|病理|活检|切缘|浸润|脉管|ESD|瘢痕|直肠",
    "infection": r"感染|发热|寒战|脓|培养|抗生素|败血症|肺炎|结核|咳嗽|盗汗|infection|tuberculosis|cough|fever|night sweats",
    "bleeding": r"出血|便血|黑便|呕血|贫血|阿司匹林|抗凝",
    "metabolic": r"血糖|高血糖|低血糖|口干|尿多|酮体|脱水|电解质|二甲双胍|降糖",
    "asthma_respiratory": r"哮喘|喘|胸闷|血氧|吸氧|雾化|信必可|呼吸",
}

UNCERTAIN_REPORT_PATTERN = r"好像|可能|大概|记不清|听不清|看不清|不确定|不懂|不知道|似乎|不敢保证|没太听清"
CONCEALED_REPORT_PATTERN = r"隐瞒|刻意瞒|骗医生|不敢[^。；，,\n]{0,12}(告诉|说)|没敢[^。；，,\n]{0,12}(告诉|说)|怕[^。；，,\n]{0,12}知道|不好意思[^。；，,\n]{0,12}(说|告诉)|不想告诉|其实[^。；，,\n]{0,12}没告诉"
DENIAL_PATTERN = r"没有|没|否认|不是|从不|未"


@dataclass
class CoherenceEvidence:
    turn: int | None
    speaker: str
    event_type: str
    visibility: str
    source: str
    source_layer: str
    fact_status: str
    text: str
    reliability_labels: list[str] = field(default_factory=list)

    def to_dict(self, *, limit: int = 360) -> Json:
        return {
            "turn": self.turn,
            "speaker": self.speaker,
            "event_type": self.event_type,
            "visibility": self.visibility,
            "source": self.source,
            "source_layer": self.source_layer,
            "fact_status": self.fact_status,
            "reliability_labels": self.reliability_labels,
            "text": _short(self.text, limit),
        }


@dataclass
class ObjectiveClaim:
    domain: str
    polarity: str
    value: float | None
    metric: str | None
    evidence: CoherenceEvidence

    def to_dict(self) -> Json:
        return {
            "domain": self.domain,
            "polarity": self.polarity,
            "value": self.value,
            "metric": self.metric,
            "evidence": self.evidence.to_dict(limit=300),
        }


@dataclass
class CoherenceFinding:
    finding_type: str
    status: str
    severity: str
    why: str
    evidence: list[Json] = field(default_factory=list)
    recommended_action: str = ""

    def to_dict(self) -> Json:
        return {
            "finding_type": self.finding_type,
            "status": self.status,
            "severity": self.severity,
            "why": self.why,
            "evidence": self.evidence,
            "recommended_action": self.recommended_action,
        }


def _summary(payload: Mapping[str, Any]) -> Json:
    trajectory = _as_dict(payload.get("trajectory"))
    closure = _as_dict(payload.get("closure"))
    return {
        "case_id": payload.get("case_id") or trajectory.get("case_id"),
        "run_id": payload.get("run_id") or trajectory.get("run_id"),
        "turns_completed": payload.get("turns_completed"),
        "closure_status": closure.get("status"),
        "closure_kind": closure.get("closure_kind"),
        "event_count": len(_as_list(trajectory.get("events"))),
        "transcript_count": len(_as_list(trajectory.get("transcript"))),
        "llm_call_count": len(_as_list(trajectory.get("llm_calls"))),
    }


def _source_layer_for(*, speaker: str, event_type: str, visibility: str, source: str, text: str) -> tuple[str, str]:
    combined = f"{speaker} {event_type} {visibility} {source} {text[:500]}"
    if re.search(HIDDEN_PATTERN, combined, re.I):
        return "hidden_or_backstage", "hidden_truth_or_internal_state"
    if source == "transcript":
        if re.search(r"doctor", speaker, re.I):
            return "doctor_visible_dialogue", "doctor_advice_or_assessment"
        return "patient_family_dialogue", "subjective_report"
    if visibility == "evaluator_visible":
        return "evaluator_visible", "evaluation_or_closure_assessment"
    if re.search(EXTERNAL_ERROR_PATTERN, combined, re.I):
        return "external_system", "external_system_error_or_revision"
    if re.search(OBJECTIVE_ACTOR_PATTERN, combined, re.I) or visibility in {"doctor_visible", "patient_visible", "public"}:
        return "world_or_workspace", "objective_or_committed_world_fact"
    return "other", "unclassified"


def _reliability_labels(speaker: str, source_layer: str, text: str) -> list[str]:
    labels: list[str] = []
    if source_layer != "patient_family_dialogue":
        return labels
    if re.search(r"family|家属|孩子|儿子|女儿|丈夫|妻子", speaker, re.I):
        labels.append("proxy_report")
    else:
        labels.append("first_person_report")
    if re.search(UNCERTAIN_REPORT_PATTERN, text, re.I):
        labels.append("uncertain_or_low_literacy_report")
    if re.search(CONCEALED_REPORT_PATTERN, text, re.I):
        labels.append("concealed_or_intentionally_incomplete_report")
    if re.search(DENIAL_PATTERN, text) and re.search(r"症状|病|用药|吸毒|怀孕|出血|发热|胸痛|喘", text):
        labels.append("denial_report")
    return labels


def collect_coherence_evidence(payload: Mapping[str, Any]) -> list[CoherenceEvidence]:
    trajectory = _as_dict(payload.get("trajectory"))
    evidence: list[CoherenceEvidence] = []
    for row in _as_list(trajectory.get("transcript")):
        if not isinstance(row, dict):
            continue
        text = str(row.get("text") or row.get("content") or "")
        if not text.strip():
            continue
        speaker = str(row.get("speaker") or row.get("speaker_display") or "transcript")
        event_type = str(row.get("event_type") or "transcript")
        visibility = "patient_doctor_dialogue" if row.get("patient_visible", True) else "transcript"
        source_layer, fact_status = _source_layer_for(speaker=speaker, event_type=event_type, visibility=visibility, source="transcript", text=text)
        evidence.append(
            CoherenceEvidence(
                turn=_turn_of(row),
                speaker=speaker,
                event_type=event_type,
                visibility=visibility,
                source="transcript",
                source_layer=source_layer,
                fact_status=fact_status,
                text=text,
                reliability_labels=_reliability_labels(speaker, source_layer, text),
            )
        )
    for event in _as_list(trajectory.get("events")):
        if not isinstance(event, dict):
            continue
        text = _event_text(event)
        if not text.strip():
            continue
        speaker = str(event.get("actor") or "event")
        event_type = str(event.get("event_type") or "event")
        visibility = str(event.get("visibility") or "")
        source_layer, fact_status = _source_layer_for(speaker=speaker, event_type=event_type, visibility=visibility, source="event", text=text)
        # Keep hidden/backstage, evaluator, world/workspace objective material;
        # skip generic other internal noise.
        if source_layer == "other":
            continue
        evidence.append(
            CoherenceEvidence(
                turn=_turn_of(event),
                speaker=speaker,
                event_type=event_type,
                visibility=visibility,
                source="event",
                source_layer=source_layer,
                fact_status=fact_status,
                text=text,
                reliability_labels=_reliability_labels(speaker, source_layer, text),
            )
        )
    return evidence


def _negated(text: str, anchor_pattern: str) -> bool:
    return bool(re.search(rf"(未见|没有|无|排除|不支持).{{0,12}}({anchor_pattern})|({anchor_pattern}).{{0,12}}(未见|没有|无|排除|不支持)", text))


def _extract_lab_value(text: str, metric_pattern: str) -> float | None:
    m = re.search(rf"({metric_pattern})[^0-9０-９]{{0,12}}([0-9０-９]+(?:[\.,．][0-9０-９]+)?)", text, re.I)
    if not m:
        return None
    raw = m.group(2).translate(str.maketrans("０１２３４５６７８９．，", "0123456789.."))
    raw = raw.replace("，", ".").replace("．", ".")
    try:
        return float(raw)
    except Exception:
        return None


def extract_objective_claims(evidence: list[CoherenceEvidence]) -> list[ObjectiveClaim]:
    claims: list[ObjectiveClaim] = []
    for ev in evidence:
        if ev.fact_status not in {"objective_or_committed_world_fact", "external_system_error_or_revision"}:
            continue
        text = ev.text
        if re.search(r"病理|活检|腺癌|癌|恶性|良性|炎症|高级别", text):
            if re.search(r"腺癌|恶性|癌", text) and not _negated(text, r"腺癌|恶性|癌"):
                claims.append(ObjectiveClaim("pathology_malignancy", "positive", None, None, ev))
            if re.search(r"良性|慢性炎症|修复性增生|未见.*癌|未见.*恶性|没有癌|阴性", text):
                claims.append(ObjectiveClaim("pathology_malignancy", "negative", None, None, ev))
        if re.search(r"转移|远处转移|淋巴结|复发", text):
            if re.search(r"转移|复发", text) and not _negated(text, r"转移|复发|淋巴结"):
                claims.append(ObjectiveClaim("imaging_metastasis_or_recurrence", "positive", None, None, ev))
            if _negated(text, r"转移|复发|淋巴结"):
                claims.append(ObjectiveClaim("imaging_metastasis_or_recurrence", "negative", None, None, ev))
        for metric, pattern in {
            "creatinine": r"肌酐|Cr\b|creatinine",
            "egfr": r"eGFR|肾小球滤过",
            "cea": r"CEA|癌胚抗原",
            "glucose": r"血糖|glucose",
        }.items():
            val = _extract_lab_value(text, pattern)
            if val is not None:
                claims.append(ObjectiveClaim("lab_numeric", "value", val, metric, ev))
    return claims


def _has_explanation(evs: list[CoherenceEvidence]) -> bool:
    return any(re.search(EXPLANATION_PATTERN, ev.text, re.I) for ev in evs)


def detect_objective_contradictions(claims: list[ObjectiveClaim]) -> list[CoherenceFinding]:
    findings: list[CoherenceFinding] = []
    by_domain: dict[str, list[ObjectiveClaim]] = defaultdict(list)
    for claim in claims:
        by_domain[claim.domain].append(claim)
    for domain, rows in by_domain.items():
        if domain == "lab_numeric":
            continue
        positives = [r for r in rows if r.polarity == "positive"]
        negatives = [r for r in rows if r.polarity == "negative"]
        if positives and negatives:
            evs = [r.evidence for r in positives[:2] + negatives[:2]]
            explained = _has_explanation(evs)
            findings.append(
                CoherenceFinding(
                    finding_type="objective_fact_contradiction",
                    status="explained_or_revision_labeled" if explained else "incoherent_world_error",
                    severity="medium" if explained else "critical",
                    why=(
                        f"Objective {domain} has both positive and negative committed-world claims; explanation/revision language is present."
                        if explained
                        else f"Objective {domain} has both positive and negative committed-world claims without explanation."
                    ),
                    evidence=[r.to_dict() for r in positives[:2] + negatives[:2]],
                    recommended_action="G3/G7: require explicit sample/time/revision/external-system-error label before allowing contradictory objective facts",
                )
            )
    # Numeric contradictions: conservative. Only flag large same-metric conflicts
    # when no time/revision explanation exists.
    by_metric: dict[str, list[ObjectiveClaim]] = defaultdict(list)
    for claim in claims:
        if claim.domain == "lab_numeric" and claim.metric and claim.value is not None:
            by_metric[claim.metric].append(claim)
    for metric, rows in by_metric.items():
        if len(rows) < 2:
            continue
        values = [r.value for r in rows if r.value is not None]
        if not values:
            continue
        max_v, min_v = max(values), min(values)
        ratio = max_v / max(min_v, 1e-6)
        absolute = max_v - min_v
        if (ratio >= 2.5 or absolute >= 100) and not _has_explanation([r.evidence for r in rows[:4]]):
            findings.append(
                CoherenceFinding(
                    finding_type="objective_lab_numeric_conflict",
                    status="needs_semantic_review",
                    severity="medium",
                    why=f"Objective lab metric {metric} varies widely without visible timing/revision explanation in sampled evidence.",
                    evidence=[r.to_dict() for r in rows[:4]],
                    recommended_action="G3: check if this is true contradiction, temporal trend, different sample, or external-system error",
                )
            )
    return findings


def _concepts(text: str) -> set[str]:
    return {name for name, pattern in CONCEPT_PATTERNS.items() if re.search(pattern, text or "", re.I)}


def detect_hidden_truth_and_subjective_report_findings(evidence: list[CoherenceEvidence]) -> list[CoherenceFinding]:
    findings: list[CoherenceFinding] = []
    # Hidden-truth signals should be based on explicit hidden-truth/case-truth
    # artifacts, not every backstage WorldDirector/clinical-memory summary. The
    # latter often repeats doctor-visible facts and would overproduce findings.
    hidden = [
        ev
        for ev in evidence
        if ev.source_layer == "hidden_or_backstage"
        and re.search(r"hidden_truth|hidden truth|CaseHiddenTruth|隐藏真相|ROOT_HIDDEN|source_truth", f"{ev.speaker} {ev.event_type}", re.I)
    ]
    subjective = [ev for ev in evidence if ev.fact_status == "subjective_report"]
    hidden_concepts: dict[str, list[CoherenceEvidence]] = defaultdict(list)
    for ev in hidden:
        for concept in _concepts(ev.text):
            hidden_concepts[concept].append(ev)

    reliability_seen: set[str] = set()
    hidden_seen: set[str] = set()
    for ev in subjective:
        labels = ev.reliability_labels
        concepts = _concepts(ev.text)
        if "concealed_or_intentionally_incomplete_report" in labels and "concealed" not in reliability_seen:
            reliability_seen.add("concealed")
            findings.append(
                CoherenceFinding(
                    finding_type="patient_report_reliability",
                    status="coherent_as_concealed_report",
                    severity="info",
                    why="Patient/family concealment or intentionally incomplete reporting is clinically realistic when labeled as subjective behavior, not an objective world fact.",
                    evidence=[ev.to_dict()],
                    recommended_action="G3/G7: preserve as subjective/concealed report; do not treat as objective contradiction unless objective facts conflict",
                )
            )
        elif ("uncertain_or_low_literacy_report" in labels or "proxy_report" in labels) and "uncertain_proxy" not in reliability_seen:
            reliability_seen.add("uncertain_proxy")
            findings.append(
                CoherenceFinding(
                    finding_type="patient_report_reliability",
                    status="coherent_as_uncertain_or_proxy_report",
                    severity="info",
                    why="Uncertain/proxy patient-family report is allowed and should be reliability-labeled rather than forced to match objective facts exactly.",
                    evidence=[ev.to_dict()],
                    recommended_action="G3/G7: pass reliability label to actors/world director and require objective confirmation for high-stakes decisions",
                )
            )
        shared = concepts.intersection(hidden_concepts.keys())
        if shared:
            for concept in sorted(shared)[:2]:
                if concept in hidden_seen:
                    continue
                hidden_seen.add(concept)
                findings.append(
                    CoherenceFinding(
                        finding_type="hidden_truth_signal",
                        status="coherent_as_hidden_truth_signal",
                        severity="info",
                        why=f"Subjective report contains concept `{concept}` that is supported by explicit hidden truth; this can be a coherent clue or second-disease signal, not a world error.",
                        evidence=[ev.to_dict(), hidden_concepts[concept][0].to_dict()],
                        recommended_action="G3/G7: allow as clue if routed as subjective report or staged disclosure; do not suppress merely because it conflicts with current working diagnosis",
                    )
                )
    return findings


def detect_external_system_labels(evidence: list[CoherenceEvidence]) -> list[CoherenceFinding]:
    findings: list[CoherenceFinding] = []
    for ev in evidence:
        if ev.fact_status == "external_system_error_or_revision" or re.search(EXTERNAL_ERROR_PATTERN, ev.text, re.I):
            findings.append(
                CoherenceFinding(
                    finding_type="external_system_error_or_revision_label",
                    status="coherent_if_not_used_as_unlabeled_truth",
                    severity="info",
                    why="External-system error/revision language is explicitly labeled; objective inconsistency may be coherent if downstream facts preserve the correction history.",
                    evidence=[ev.to_dict()],
                    recommended_action="G3/G7: keep source/error/revision label in world state and patient-visible explanation when clinically relevant",
                )
            )
    return findings


def overall_status(findings: list[CoherenceFinding]) -> Json:
    if any(f.status == "incoherent_world_error" for f in findings):
        return {
            "status": "fail",
            "world_coherence_safe": False,
            "interpretation": "Unexplained objective-world contradiction detected; this should be fixed before runtime prompt integration.",
        }
    if any(f.status == "needs_semantic_review" for f in findings):
        return {
            "status": "review",
            "world_coherence_safe": False,
            "interpretation": "No deterministic critical contradiction, but some objective facts need semantic review.",
        }
    return {
        "status": "pass_with_notes" if findings else "pass",
        "world_coherence_safe": True,
        "interpretation": "No deterministic unexplained objective contradiction found by G3 shadow audit; subjective uncertainty/hidden-truth clues remain allowed with labels.",
    }


def audit_world_coherence(payload: Mapping[str, Any], *, name: str = "trajectory") -> Json:
    evidence = collect_coherence_evidence(payload)
    claims = extract_objective_claims(evidence)
    findings = []
    findings.extend(detect_objective_contradictions(claims))
    findings.extend(detect_hidden_truth_and_subjective_report_findings(evidence))
    findings.extend(detect_external_system_labels(evidence))
    finding_dicts = [f.to_dict() for f in findings]
    return {
        "schema_version": SCHEMA_VERSION,
        "name": name,
        "scope": "G3 offline/shadow world clinical coherence audit; does not alter runtime, closure, scoring, prompts, or world generation",
        "summary": _summary(payload),
        "overall": overall_status(findings),
        "source_layer_counts": Counter(ev.source_layer for ev in evidence).most_common(),
        "fact_status_counts": Counter(ev.fact_status for ev in evidence).most_common(),
        "reliability_label_counts": Counter(label for ev in evidence for label in ev.reliability_labels).most_common(),
        "objective_claim_counts": Counter((claim.domain, claim.polarity, claim.metric or "") for claim in claims).most_common(),
        "findings": finding_dicts,
        "next_recommended_actions": next_recommended_actions(finding_dicts),
    }


def next_recommended_actions(findings: list[Json]) -> list[str]:
    statuses = {str(f.get("status")) for f in findings}
    types = {str(f.get("finding_type")) for f in findings}
    actions: list[str] = []
    if "incoherent_world_error" in statuses:
        actions.append("G3/G7: fix or explicitly label objective-fact contradiction before runtime integration")
    if "objective_lab_numeric_conflict" in types:
        actions.append("G3: manually review lab numeric conflicts for temporal trend vs contradiction")
    if "hidden_truth_signal" in types:
        actions.append("G3/G7: preserve hidden-truth clues as subjective/staged disclosure, not objective contradiction")
    if "patient_report_reliability" in types:
        actions.append("G3/G7: pass patient/family reliability labels to actor/world director guidance")
    if "external_system_error_or_revision_label" in types:
        actions.append("G3/G7: retain external-system error/revision labels through world state and downstream summaries")
    if not actions:
        actions.append("No deterministic world coherence blocker found; proceed to next shadow tranche after manual review")
    return actions


def render_markdown(audit: Mapping[str, Any]) -> str:
    summary = _as_dict(audit.get("summary"))
    overall = _as_dict(audit.get("overall"))
    lines: list[str] = [
        f"# World Clinical Coherence Audit: {audit.get('name')}",
        "",
        "> G3 offline/shadow audit. 本报告不改变 runtime、closure、scoring、prompt 或世界生成。",
        "",
        "## Summary",
        "",
        f"- case_id: `{summary.get('case_id')}`",
        f"- run_id: `{summary.get('run_id')}`",
        f"- turns_completed: **{summary.get('turns_completed')}**",
        f"- closure: `{summary.get('closure_status')}` / `{summary.get('closure_kind')}`",
        f"- overall: `{overall.get('status')}`",
        f"- world_coherence_safe: **{overall.get('world_coherence_safe')}**",
        "",
        "## Interpretation",
        "",
        str(overall.get("interpretation") or ""),
        "",
        "## Counts",
        "",
        f"- source_layer_counts: `{audit.get('source_layer_counts')}`",
        f"- fact_status_counts: `{audit.get('fact_status_counts')}`",
        f"- reliability_label_counts: `{audit.get('reliability_label_counts')}`",
        f"- objective_claim_counts: `{audit.get('objective_claim_counts')}`",
        "",
        "## Findings",
        "",
    ]
    for finding in _as_list(audit.get("findings")):
        if not isinstance(finding, dict):
            continue
        lines += [
            f"### {finding.get('finding_type')} — `{finding.get('status')}`",
            "",
            f"- severity: `{finding.get('severity')}`",
            f"- why: {finding.get('why')}",
            f"- recommended_action: `{finding.get('recommended_action')}`",
            "- evidence:",
        ]
        for ev in _as_list(finding.get("evidence"))[:5]:
            if isinstance(ev, dict):
                lines.append(
                    f"  - turn {ev.get('turn')} {ev.get('speaker')}/{ev.get('event_type')} [{ev.get('source_layer')} / {ev.get('fact_status')}]: {_short(ev.get('text'), 220)}"
                )
        lines.append("")
    lines += ["## Next Recommended Actions", ""]
    for action in _as_list(audit.get("next_recommended_actions")):
        lines.append(f"- {action}")
    lines.append("")
    return "\n".join(lines)
