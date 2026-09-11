
from __future__ import annotations

"""Long-context sidecar store for runtime_lite.

This module is intentionally infrastructure-heavy and clinically light.  It
persists raw evidence, bounded memory rollups, and audit indexes so a 1000+
turn CareLoop trajectory can be resumed and evaluated without relying on an
unbounded prompt.  It does *not* decide medical meaning through hard rules; the
ClinicalMemorySteward and evaluator LLMs remain the semantic judges.
"""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Iterable, Mapping


@dataclass
class LongContextConfig:
    enabled: bool = False
    episode_turn_span: int = 20
    chapter_episode_span: int = 5
    working_memory_mode: str = "eventful"
    working_memory_forced_interval_turns: int = 3
    memory_integrity_audit_interval_turns: int = 50
    fragment_eval_interval_turns: int = 100
    append_only_ledger: bool = True
    externalize_memory_snapshots: bool = True

    def normalized(self) -> "LongContextConfig":
        self.episode_turn_span = max(1, int(self.episode_turn_span or 20))
        self.chapter_episode_span = max(1, int(self.chapter_episode_span or 5))
        self.working_memory_forced_interval_turns = max(1, int(self.working_memory_forced_interval_turns or 3))
        self.memory_integrity_audit_interval_turns = max(1, int(self.memory_integrity_audit_interval_turns or 50))
        self.fragment_eval_interval_turns = max(1, int(self.fragment_eval_interval_turns or 100))
        self.working_memory_mode = str(self.working_memory_mode or "eventful").strip().lower() or "eventful"
        return self


class LongContextMemoryStore:
    """Append-only evidence ledger plus layered memory sidecars.

    The store mirrors the authoritative runtime trajectory into jsonl ledgers and
    compact sidecars.  Source anchors are kept as event ids / turn spans / file
    refs so later LLM review can inspect raw evidence instead of trusting a
    deterministic summary.
    """

    protocol = "careloop.runtime_lite.long_context_memory.v1"

    def __init__(self, *, output_dir: str | Path, case_id: str, run_id: str, config: LongContextConfig | None = None) -> None:
        self.output_dir = Path(output_dir)
        self.case_id = str(case_id or "")
        self.run_id = str(run_id or "")
        self.config = (config or LongContextConfig(enabled=True)).normalized()
        self.ledger_dir = self.output_dir / "ledger"
        self.index_dir = self.output_dir / "indexes"
        self.memory_dir = self.output_dir / "memory"
        self.episodes_dir = self.memory_dir / "episodes"
        self.chapters_dir = self.memory_dir / "chapters"
        self.snapshots_dir = self.memory_dir / "snapshots"
        for path in [self.ledger_dir, self.index_dir, self.memory_dir, self.episodes_dir, self.chapters_dir, self.snapshots_dir]:
            path.mkdir(parents=True, exist_ok=True)
        self._event_ids = self._load_jsonl_keys(self.ledger_dir / "events.jsonl", "event_id")
        self._transcript_keys = self._load_jsonl_keys(self.ledger_dir / "transcript.jsonl", "ledger_key")
        self._llm_call_keys = self._load_jsonl_keys(self.ledger_dir / "llm_calls.jsonl", "ledger_key")
        self._workspace_keys = self._load_jsonl_keys(self.ledger_dir / "workspace_results.jsonl", "ledger_key")
        self._receipt_ids = self._load_jsonl_keys(self.ledger_dir / "care_system_receipts.jsonl", "receipt_id")
        self._latest_turn = self._load_latest_turn()
        self._latest_snapshot: dict[str, Any] = self._read_json(self.memory_dir / "working_memory.json")
        self.write_manifest()
        self.write_status(turn=self._latest_turn, reason="store_initialized")

    # ------------------------------------------------------------------
    # Public API

    def sync_from_trajectory(
        self,
        trajectory: Any,
        *,
        turn: int | None = None,
        closure: Mapping[str, Any] | None = None,
        latest_snapshot: Mapping[str, Any] | None = None,
        reason: str = "sync",
        rollup: bool = False,
        force_audit: bool = False,
    ) -> dict[str, Any]:
        """Mirror new trajectory rows and optionally refresh memory rollups."""

        traj = trajectory.to_dict() if hasattr(trajectory, "to_dict") else (trajectory if isinstance(trajectory, Mapping) else {})
        events = [dict(item) for item in (traj.get("events") or []) if isinstance(item, Mapping)]
        transcript = [dict(item) for item in (traj.get("transcript") or []) if isinstance(item, Mapping)]
        llm_calls = [dict(item) for item in (traj.get("llm_calls") or []) if isinstance(item, Mapping)]
        latest_turn = self._safe_int(turn)
        if latest_turn is None:
            latest_turn = self._max_turn_from_rows(events, transcript, llm_calls)
        if latest_turn is not None:
            self._latest_turn = max(self._latest_turn, int(latest_turn))

        appended_events = self._append_new_events(events)
        appended_transcript = self._append_new_transcript(transcript)
        appended_llm = self._append_new_llm_calls(llm_calls)
        appended_workspace = self._append_workspace_rows_from_events(events)
        appended_receipts = self._append_receipts_from_events(events)

        snapshot = dict(latest_snapshot) if isinstance(latest_snapshot, Mapping) else self._latest_clinical_snapshot_from_events(events)
        if snapshot:
            self.write_clinical_memory_snapshot(snapshot)
            self.update_working_memory(snapshot, turn=self._latest_turn, reason=reason)
            self.update_ledgers_from_snapshot(snapshot, turn=self._latest_turn, reason=reason)
            rollup = True or rollup

        if rollup:
            self.ensure_episode_memories_through_turn(
                self._latest_turn,
                events=events,
                transcript=transcript,
                latest_snapshot=snapshot or self._latest_snapshot,
                reason=reason,
            )
            self.ensure_chapter_memories_through_turn(self._latest_turn, reason=reason)
            self.update_master_memory(self._latest_turn, reason=reason)
            self.write_indexes(events=events, transcript=transcript)
        else:
            # Indexes are cheap enough at checkpoint boundaries and make the
            # append-only ledgers immediately navigable after crashes.
            self.write_indexes(events=events, transcript=transcript)

        should_audit = force_audit or rollup or (
            self._latest_turn > 0
            and self._latest_turn % max(1, self.config.memory_integrity_audit_interval_turns) == 0
        )
        audit = self.write_integrity_audit(turn=self._latest_turn, reason=reason) if should_audit else self._read_json(self.memory_dir / "memory_integrity_audit.json")
        status = self.write_status(
            turn=self._latest_turn,
            reason=reason,
            closure=dict(closure or {}),
            appended={
                "events": appended_events,
                "transcript": appended_transcript,
                "llm_calls": appended_llm,
                "workspace_results": appended_workspace,
                "care_system_receipts": appended_receipts,
            },
            integrity_audit=audit,
        )
        return status

    def write_clinical_memory_snapshot(self, snapshot: Mapping[str, Any]) -> dict[str, Any]:
        turn = self._safe_int(snapshot.get("turn") if isinstance(snapshot, Mapping) else None) or self._latest_turn
        digest = self._stable_hash(snapshot)
        path = self.snapshots_dir / f"clinical_memory_turn_{turn:04d}_{digest[:10]}.json"
        payload = {
            "protocol": "careloop.runtime_lite.long_context_memory.clinical_snapshot.v1",
            "case_id": self.case_id,
            "run_id": self.run_id,
            "turn": turn,
            "snapshot_sha256": digest,
            "snapshot": dict(snapshot),
            "written_at": self._now(),
        }
        self._write_json_atomic(path, payload)
        latest_path = self.snapshots_dir / "latest.json"
        self._write_json_atomic(latest_path, payload)
        self._latest_snapshot = dict(snapshot)
        return {
            "protocol": "careloop.runtime_lite.long_context_memory.snapshot_ref.v1",
            "turn": turn,
            "snapshot_sha256": digest,
            "snapshot_relative_path": str(path.relative_to(self.output_dir)),
            "latest_relative_path": str(latest_path.relative_to(self.output_dir)),
        }

    def update_working_memory(self, snapshot: Mapping[str, Any], *, turn: int, reason: str = "") -> dict[str, Any]:
        payload = {
            "protocol": "careloop.runtime_lite.long_context_memory.working_memory.v1",
            "case_id": self.case_id,
            "run_id": self.run_id,
            "turn": int(turn or 0),
            "updated_at": self._now(),
            "reason": reason,
            "mode": self.config.working_memory_mode,
            "principle": "Bounded backstage working memory. Not doctor-visible; semantic content is from ClinicalMemorySteward, not hard rules.",
            "latest_clinical_memory_snapshot": dict(snapshot),
        }
        self._write_json_atomic(self.memory_dir / "working_memory.json", payload)
        self._latest_snapshot = dict(snapshot)
        return payload

    def update_ledgers_from_snapshot(self, snapshot: Mapping[str, Any], *, turn: int, reason: str = "") -> tuple[dict[str, Any], dict[str, Any]]:
        non_compressible = self._as_list(snapshot.get("non_compressible_clinical_kernel") if isinstance(snapshot, Mapping) else [])
        responsibilities = self._as_list(
            snapshot.get("action_responsibility_ledger")
            if isinstance(snapshot, Mapping)
            else []
        )
        if not responsibilities and isinstance(snapshot, Mapping):
            responsibilities = self._as_list(snapshot.get("responsibility_ledger"))
        non_payload = {
            "protocol": "careloop.runtime_lite.long_context_memory.non_compressible_ledger.v1",
            "case_id": self.case_id,
            "run_id": self.run_id,
            "turn": int(turn or 0),
            "updated_at": self._now(),
            "reason": reason,
            "principle": "Decision-changing facts to preserve across compression; selected by LLM memory steward plus deterministic carry-forward.",
            "items": non_compressible,
        }
        resp_payload = {
            "protocol": "careloop.runtime_lite.long_context_memory.responsibility_ledger.v1",
            "case_id": self.case_id,
            "run_id": self.run_id,
            "turn": int(turn or 0),
            "updated_at": self._now(),
            "reason": reason,
            "principle": "Open actions, ownership, receipts and follow-up risks; preserved independently from narrative summaries.",
            "items": responsibilities,
        }
        self._write_json_atomic(self.memory_dir / "non_compressible_ledger.json", non_payload)
        self._write_json_atomic(self.memory_dir / "responsibility_ledger.json", resp_payload)
        return non_payload, resp_payload

    def ensure_episode_memories_through_turn(
        self,
        turn: int,
        *,
        events: list[dict[str, Any]],
        transcript: list[dict[str, Any]],
        latest_snapshot: Mapping[str, Any] | None = None,
        reason: str = "",
    ) -> None:
        current_index = ((max(1, int(turn or 0)) - 1) // self.config.episode_turn_span) + 1
        for episode_index in range(1, current_index + 1):
            start_turn = (episode_index - 1) * self.config.episode_turn_span + 1
            end_turn = episode_index * self.config.episode_turn_span
            path = self.episodes_dir / f"episode_{start_turn:04d}_{end_turn:04d}.json"
            if path.exists() and episode_index != current_index:
                continue
            self.write_episode_memory(
                min(max(0, int(turn or 0)), end_turn),
                events=events,
                transcript=transcript,
                latest_snapshot=latest_snapshot,
                reason=reason,
                episode_index=episode_index,
            )

    def ensure_chapter_memories_through_turn(self, turn: int, *, reason: str = "") -> None:
        current_episode_index = ((max(1, int(turn or 0)) - 1) // self.config.episode_turn_span) + 1
        current_chapter_index = ((current_episode_index - 1) // self.config.chapter_episode_span) + 1
        for chapter_index in range(1, current_chapter_index + 1):
            episode_start = (chapter_index - 1) * self.config.chapter_episode_span + 1
            turn_start = (episode_start - 1) * self.config.episode_turn_span + 1
            turn_end = chapter_index * self.config.chapter_episode_span * self.config.episode_turn_span
            path = self.chapters_dir / f"chapter_{turn_start:04d}_{turn_end:04d}.json"
            if path.exists() and chapter_index != current_chapter_index:
                continue
            self.write_chapter_memory(min(max(0, int(turn or 0)), turn_end), reason=reason, chapter_index=chapter_index)

    def write_episode_memory(
        self,
        turn: int,
        *,
        events: list[dict[str, Any]],
        transcript: list[dict[str, Any]],
        latest_snapshot: Mapping[str, Any] | None = None,
        reason: str = "",
        episode_index: int | None = None,
    ) -> dict[str, Any]:
        turn = max(0, int(turn or 0))
        if episode_index is None:
            if turn <= 0:
                episode_index = 1
            else:
                episode_index = ((turn - 1) // self.config.episode_turn_span) + 1
        episode_index = max(1, int(episode_index or 1))
        start_turn = (episode_index - 1) * self.config.episode_turn_span + 1
        end_turn = episode_index * self.config.episode_turn_span
        episode_events = [e for e in events if start_turn <= (self._safe_int(e.get("turn")) or 0) <= min(turn, end_turn)]
        episode_transcript = [t for t in transcript if start_turn <= (self._safe_int(t.get("turn")) or 0) <= min(turn, end_turn)]
        snapshot = dict(latest_snapshot or {})
        payload = {
            "protocol": "careloop.runtime_lite.long_context_memory.episode.v1",
            "case_id": self.case_id,
            "run_id": self.run_id,
            "episode_index": episode_index,
            "turn_start": start_turn,
            "turn_end_planned": end_turn,
            "turn_end_observed": min(turn, end_turn),
            "updated_at": self._now(),
            "reason": reason,
            "principle": "Episode memory is a source-anchored compression aid, not a clinical rule engine.",
            "event_count": len(episode_events),
            "transcript_count": len(episode_transcript),
            "event_type_counts": self._counts(e.get("event_type") for e in episode_events),
            "source_event_ids": [str(e.get("event_id") or "") for e in episode_events if e.get("event_id")],
            "transcript_tail": episode_transcript[-12:],
            "snapshot_digest": self._stable_hash(snapshot) if snapshot else "",
            "summary_from_latest_snapshot": self._memory_summary_from_snapshot(snapshot),
        }
        path = self.episodes_dir / f"episode_{start_turn:04d}_{end_turn:04d}.json"
        self._write_json_atomic(path, payload)
        return payload

    def write_chapter_memory(self, turn: int, *, reason: str = "", chapter_index: int | None = None) -> dict[str, Any]:
        turn = max(0, int(turn or 0))
        episode_index = ((max(1, turn) - 1) // self.config.episode_turn_span) + 1
        if chapter_index is None:
            chapter_index = ((episode_index - 1) // self.config.chapter_episode_span) + 1
        chapter_index = max(1, int(chapter_index or 1))
        episode_start = (chapter_index - 1) * self.config.chapter_episode_span + 1
        episode_end = chapter_index * self.config.chapter_episode_span
        turn_start = (episode_start - 1) * self.config.episode_turn_span + 1
        turn_end = episode_end * self.config.episode_turn_span
        episode_files = sorted(self.episodes_dir.glob("episode_*.json"))
        selected = []
        for path in episode_files:
            payload = self._read_json(path)
            idx = self._safe_int(payload.get("episode_index"))
            if idx is not None and episode_start <= idx <= episode_end:
                selected.append({
                    "episode_index": idx,
                    "path": str(path.relative_to(self.output_dir)),
                    "turn_start": payload.get("turn_start"),
                    "turn_end_observed": payload.get("turn_end_observed"),
                    "event_count": payload.get("event_count"),
                    "summary_from_latest_snapshot": payload.get("summary_from_latest_snapshot"),
                })
        master = self._read_json(self.memory_dir / "master_memory.json")
        payload = {
            "protocol": "careloop.runtime_lite.long_context_memory.chapter.v1",
            "case_id": self.case_id,
            "run_id": self.run_id,
            "chapter_index": chapter_index,
            "episode_start": episode_start,
            "episode_end_planned": episode_end,
            "turn_start": turn_start,
            "turn_end_planned": turn_end,
            "turn_end_observed": turn,
            "updated_at": self._now(),
            "reason": reason,
            "principle": "Chapter memory aggregates episode summaries while preserving pointers to raw ledgers.",
            "episodes": selected,
            "master_memory_digest_before_update": self._stable_hash(master) if master else "",
            "active_ledgers": self._active_ledger_refs(),
        }
        path = self.chapters_dir / f"chapter_{turn_start:04d}_{turn_end:04d}.json"
        self._write_json_atomic(path, payload)
        return payload

    def update_master_memory(self, turn: int, *, reason: str = "") -> dict[str, Any]:
        working = self._read_json(self.memory_dir / "working_memory.json")
        snapshot = working.get("latest_clinical_memory_snapshot") if isinstance(working.get("latest_clinical_memory_snapshot"), dict) else {}
        payload = {
            "protocol": "careloop.runtime_lite.long_context_memory.master.v1",
            "case_id": self.case_id,
            "run_id": self.run_id,
            "turn": int(turn or 0),
            "updated_at": self._now(),
            "reason": reason,
            "principle": "Master memory is a compact backstage navigation map for evaluator/director use; never injected into tested doctor prompts.",
            "non_compressible_clinical_kernel": self._as_list(snapshot.get("non_compressible_clinical_kernel")),
            "active_clinical_problem_list": self._as_list(snapshot.get("active_clinical_problem_list")),
            "active_responsibility_ledger": self._as_list(snapshot.get("action_responsibility_ledger") or snapshot.get("responsibility_ledger")),
            "real_world_constraint_model": self._as_list(snapshot.get("real_world_constraint_model")),
            "open_threads": self._as_list(snapshot.get("open_threads")),
            "uncertainties": self._as_list(snapshot.get("uncertainties")),
            "latest_snapshot_digest": self._stable_hash(snapshot) if snapshot else "",
            "episode_count": len(list(self.episodes_dir.glob("episode_*.json"))),
            "chapter_count": len(list(self.chapters_dir.glob("chapter_*.json"))),
            "ledger_refs": self._active_ledger_refs(),
        }
        self._write_json_atomic(self.memory_dir / "master_memory.json", payload)
        return payload

    def write_indexes(self, *, events: list[dict[str, Any]], transcript: list[dict[str, Any]]) -> None:
        turn_index: dict[str, Any] = {}
        event_type_index: dict[str, list[str]] = {}
        source_anchors: list[dict[str, Any]] = []
        for event in events:
            eid = str(event.get("event_id") or "")
            turn = str(event.get("turn") if event.get("turn") is not None else "")
            if turn:
                entry = turn_index.setdefault(turn, {"event_ids": [], "event_types": {}, "transcript_count": 0})
                if eid:
                    entry["event_ids"].append(eid)
                et = str(event.get("event_type") or "")
                if et:
                    entry["event_types"][et] = int(entry["event_types"].get(et, 0)) + 1
                    if eid:
                        event_type_index.setdefault(et, []).append(eid)
            for anchor in self._extract_source_anchors(event.get("content")):
                source_anchors.append({"event_id": eid, "turn": event.get("turn"), "event_type": event.get("event_type"), "anchor": anchor})
        for item in transcript:
            turn = str(item.get("turn") if item.get("turn") is not None else "")
            if turn:
                entry = turn_index.setdefault(turn, {"event_ids": [], "event_types": {}, "transcript_count": 0})
                entry["transcript_count"] = int(entry.get("transcript_count") or 0) + 1
        receipt_index = self._receipt_index_from_ledger()
        self._write_json_atomic(self.index_dir / "turn_index.json", {"protocol": self.protocol + ".turn_index", "case_id": self.case_id, "run_id": self.run_id, "turns": turn_index})
        self._write_json_atomic(self.index_dir / "event_type_index.json", {"protocol": self.protocol + ".event_type_index", "case_id": self.case_id, "run_id": self.run_id, "event_types": event_type_index})
        self._write_json_atomic(self.index_dir / "source_anchor_index.json", {"protocol": self.protocol + ".source_anchor_index", "case_id": self.case_id, "run_id": self.run_id, "source_anchors": source_anchors[-5000:]})
        self._write_json_atomic(self.index_dir / "receipt_index.json", {"protocol": self.protocol + ".receipt_index", "case_id": self.case_id, "run_id": self.run_id, "receipts": receipt_index})

    def write_integrity_audit(self, *, turn: int, reason: str = "") -> dict[str, Any]:
        event_rows = list(self._iter_jsonl(self.ledger_dir / "events.jsonl"))
        transcript_rows = list(self._iter_jsonl(self.ledger_dir / "transcript.jsonl"))
        llm_rows = list(self._iter_jsonl(self.ledger_dir / "llm_calls.jsonl"))
        event_ids = [str(row.get("event_id") or "") for row in event_rows if row.get("event_id")]
        duplicate_event_ids = sorted({eid for eid in event_ids if event_ids.count(eid) > 1})[:20]
        missing_hash = [str(row.get("event_id") or "") for row in event_rows if row.get("event_id") and not row.get("audit_hash")][:20]
        artifact_presence = {
            "events_jsonl": (self.ledger_dir / "events.jsonl").exists(),
            "transcript_jsonl": (self.ledger_dir / "transcript.jsonl").exists(),
            "llm_calls_jsonl": (self.ledger_dir / "llm_calls.jsonl").exists(),
            "working_memory": (self.memory_dir / "working_memory.json").exists(),
            "master_memory": (self.memory_dir / "master_memory.json").exists(),
            "non_compressible_ledger": (self.memory_dir / "non_compressible_ledger.json").exists(),
            "responsibility_ledger": (self.memory_dir / "responsibility_ledger.json").exists(),
            "turn_index": (self.index_dir / "turn_index.json").exists(),
            "event_type_index": (self.index_dir / "event_type_index.json").exists(),
            "source_anchor_index": (self.index_dir / "source_anchor_index.json").exists(),
            "receipt_index": (self.index_dir / "receipt_index.json").exists(),
        }
        issues: list[str] = []
        if duplicate_event_ids:
            issues.append("duplicate_event_ids")
        if missing_hash:
            issues.append("event_audit_hash_missing")
        for name, present in artifact_presence.items():
            if not present:
                issues.append(f"missing_{name}")
        payload = {
            "protocol": "careloop.runtime_lite.long_context_memory.integrity_audit.v1",
            "case_id": self.case_id,
            "run_id": self.run_id,
            "turn": int(turn or 0),
            "reason": reason,
            "audited_at": self._now(),
            "status": "pass" if not issues else "review",
            "issues": issues,
            "ledger_counts": {
                "events": len(event_rows),
                "transcript": len(transcript_rows),
                "llm_calls": len(llm_rows),
                "workspace_results": self._jsonl_count(self.ledger_dir / "workspace_results.jsonl"),
                "care_system_receipts": self._jsonl_count(self.ledger_dir / "care_system_receipts.jsonl"),
            },
            "duplicate_event_ids_sample": duplicate_event_ids,
            "events_missing_audit_hash_sample": missing_hash,
            "artifact_presence": artifact_presence,
            "principle": "Integrity audit checks persistence/index invariants only; it does not grade clinical performance.",
        }
        self._write_json_atomic(self.memory_dir / "memory_integrity_audit.json", payload)
        return payload

    def build_final_evidence_pack(
        self,
        *,
        turn: int,
        closure: Mapping[str, Any] | None = None,
        evaluation_mode: str = "",
        evaluation: Mapping[str, Any] | None = None,
        reason: str = "final_evaluation",
    ) -> dict[str, Any]:
        self.write_integrity_audit(turn=turn, reason=reason)
        master = self._read_json(self.memory_dir / "master_memory.json")
        current_chapter = self._latest_json_from_dir(self.chapters_dir, "chapter_*.json")
        recent_episodes = self._latest_jsons_from_dir(self.episodes_dir, "episode_*.json", limit=5)
        pack = {
            "protocol": "careloop.runtime_lite.long_context_memory.final_evidence_pack.v1",
            "case_id": self.case_id,
            "run_id": self.run_id,
            "turn": int(turn or 0),
            "created_at": self._now(),
            "reason": reason,
            "evaluation_mode": evaluation_mode,
            "closure_assessment": dict(closure or {}),
            "careloop_visibility_boundary": {
                "tested_doctor_visibility": "no_careloop_backstage_memory_auto_injection",
                "doctor_allowed_context_management": [
                    "recent raw transcript window",
                    "doctor-visible workspace/care-system results",
                    "doctor-initiated raw history review",
                    "doctor-owned note save/query",
                    "doctor-initiated care-system receipt/status query",
                ],
                "principle": "This evidence pack is for CareLoop internal evaluator/director audit only and must never be inserted into tested doctor prompts.",
            },
            "master_memory": master,
            "current_chapter": current_chapter,
            "recent_episodes": recent_episodes,
            "non_compressible_ledger": self._read_json(self.memory_dir / "non_compressible_ledger.json"),
            "responsibility_ledger": self._read_json(self.memory_dir / "responsibility_ledger.json"),
            "integrity_audit": self._read_json(self.memory_dir / "memory_integrity_audit.json"),
            "index_refs": {
                "turn_index": "indexes/turn_index.json",
                "event_type_index": "indexes/event_type_index.json",
                "source_anchor_index": "indexes/source_anchor_index.json",
                "receipt_index": "indexes/receipt_index.json",
            },
            "ledger_refs": {
                "raw_events": "ledger/events.jsonl",
                "raw_transcript": "ledger/transcript.jsonl",
                "llm_calls": "ledger/llm_calls.jsonl",
                "workspace_results": "ledger/workspace_results.jsonl",
                "care_system_receipts": "ledger/care_system_receipts.jsonl",
            },
            "ledger_counts": self._ledger_counts(),
            "evaluation_summary_after_run": dict(evaluation or {}),
        }
        self._write_json_atomic(self.memory_dir / "final_long_context_evidence_pack.json", pack)
        return pack

    def prompt_evidence_pack(self, *, turn: int, closure: Mapping[str, Any] | None = None, evaluation_mode: str = "") -> dict[str, Any]:
        pack = self.build_final_evidence_pack(turn=turn, closure=closure, evaluation_mode=evaluation_mode, reason="pre_final_evaluator_prompt")
        return {
            "available": True,
            "protocol": pack["protocol"],
            "turn": pack.get("turn"),
            "evaluation_mode": evaluation_mode,
            "principle": pack["careloop_visibility_boundary"]["principle"],
            "master_memory": self._bounded(pack.get("master_memory"), list_limit=16, text_limit=900),
            "current_chapter": self._bounded(pack.get("current_chapter"), list_limit=8, text_limit=700),
            "recent_episodes": self._bounded(pack.get("recent_episodes"), list_limit=5, text_limit=700),
            "non_compressible_ledger": self._bounded(pack.get("non_compressible_ledger"), list_limit=20, text_limit=700),
            "responsibility_ledger": self._bounded(pack.get("responsibility_ledger"), list_limit=24, text_limit=700),
            "integrity_audit": self._bounded(pack.get("integrity_audit"), list_limit=20, text_limit=500),
            "ledger_refs": pack.get("ledger_refs"),
            "index_refs": pack.get("index_refs"),
            "ledger_counts": pack.get("ledger_counts"),
        }

    def write_manifest(self) -> dict[str, Any]:
        payload = {
            "protocol": "careloop.runtime_lite.long_context_memory.manifest.v1",
            "case_id": self.case_id,
            "run_id": self.run_id,
            "created_or_updated_at": self._now(),
            "config": asdict(self.config),
            "principle": "Infrastructure persistence for 1000+ turn runs; clinical interpretation remains LLM-led and source-anchored.",
            "doctor_visibility_boundary": "The tested doctor must not receive memory/, ledger/, indexes/ sidecars unless it explicitly requests doctor-visible raw history or own notes through allowed tools.",
            "artifacts": {
                "ledger/events.jsonl": "append-only raw runtime events",
                "ledger/transcript.jsonl": "append-only patient/family/doctor-visible transcript rows",
                "ledger/llm_calls.jsonl": "append-only LLM call metadata and previews",
                "ledger/workspace_results.jsonl": "append-only workspace/care-system result evidence rows",
                "ledger/care_system_receipts.jsonl": "append-only doctor operation receipts",
                "indexes/turn_index.json": "turn to event/transcript navigation",
                "indexes/event_type_index.json": "event type to event ids",
                "indexes/source_anchor_index.json": "memory/source anchors to raw event ids",
                "indexes/receipt_index.json": "receipt id navigation",
                "memory/working_memory.json": "latest backstage working memory",
                "memory/master_memory.json": "compact master memory",
                "memory/non_compressible_ledger.json": "must-preserve clinical kernel",
                "memory/responsibility_ledger.json": "open action/responsibility ledger",
                "memory/memory_integrity_audit.json": "periodic persistence integrity audit",
                "memory/final_long_context_evidence_pack.json": "final evaluator evidence pack",
                "memory/episodes/": "episode summaries",
                "memory/chapters/": "chapter summaries",
                "memory/snapshots/": "externalized clinical memory snapshots",
            },
        }
        self._write_json_atomic(self.memory_dir / "memory_store_manifest.json", payload)
        return payload

    def write_status(
        self,
        *,
        turn: int,
        reason: str = "",
        closure: Mapping[str, Any] | None = None,
        appended: Mapping[str, int] | None = None,
        integrity_audit: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        audit_payload = dict(integrity_audit or {}) or self._read_json(self.memory_dir / "memory_integrity_audit.json")
        payload = {
            "protocol": "careloop.runtime_lite.long_context_memory.status.v1",
            "case_id": self.case_id,
            "run_id": self.run_id,
            "turn": int(turn or 0),
            "updated_at": self._now(),
            "reason": reason,
            "enabled": bool(self.config.enabled),
            "closure_status": dict(closure or {}).get("status", ""),
            "ledger_counts": self._ledger_counts(),
            "last_sync_appended": dict(appended or {}),
            "episode_count": len(list(self.episodes_dir.glob("episode_*.json"))),
            "chapter_count": len(list(self.chapters_dir.glob("chapter_*.json"))),
            "integrity_status": audit_payload.get("status", ""),
            "manifest": "memory/memory_store_manifest.json",
            "final_evidence_pack": "memory/final_long_context_evidence_pack.json",
        }
        self._write_json_atomic(self.memory_dir / "long_context_status.json", payload)
        return payload

    # ------------------------------------------------------------------
    # Append helpers

    def _append_new_events(self, events: list[dict[str, Any]]) -> int:
        count = 0
        for event in events:
            eid = str(event.get("event_id") or "")
            if not eid or eid in self._event_ids:
                continue
            row = dict(event)
            row.setdefault("ledger_recorded_at", self._now())
            self._append_jsonl(self.ledger_dir / "events.jsonl", row)
            self._event_ids.add(eid)
            count += 1
        return count

    def _append_new_transcript(self, transcript: list[dict[str, Any]]) -> int:
        count = 0
        for item in transcript:
            key = self._transcript_key(item)
            if key in self._transcript_keys:
                continue
            row = dict(item)
            row["ledger_key"] = key
            row.setdefault("ledger_recorded_at", self._now())
            self._append_jsonl(self.ledger_dir / "transcript.jsonl", row)
            self._transcript_keys.add(key)
            count += 1
        return count

    def _append_new_llm_calls(self, llm_calls: list[dict[str, Any]]) -> int:
        count = 0
        for index, item in enumerate(llm_calls, start=1):
            key = self._llm_call_key(item, index=index)
            if key in self._llm_call_keys:
                continue
            row = dict(item)
            row["ledger_key"] = key
            row.setdefault("ledger_recorded_at", self._now())
            self._append_jsonl(self.ledger_dir / "llm_calls.jsonl", row)
            self._llm_call_keys.add(key)
            count += 1
        return count

    def _append_workspace_rows_from_events(self, events: list[dict[str, Any]]) -> int:
        relevant = {
            "world_event_resolution",
            "diagnostic_service_simulation",
            "doctor_care_system_receipt",
            "doctor_workspace_query_result",
            "doctor_operation_result",
            "care_system_update",
        }
        count = 0
        for event in events:
            if str(event.get("event_type") or "") not in relevant:
                continue
            key = self._stable_hash({"event_id": event.get("event_id"), "event_type": event.get("event_type"), "content": event.get("content")})
            if key in self._workspace_keys:
                continue
            row = {
                "ledger_key": key,
                "event_id": event.get("event_id"),
                "turn": event.get("turn"),
                "event_type": event.get("event_type"),
                "sim_time": event.get("sim_time"),
                "content": event.get("content"),
                "ledger_recorded_at": self._now(),
            }
            self._append_jsonl(self.ledger_dir / "workspace_results.jsonl", row)
            self._workspace_keys.add(key)
            count += 1
        return count

    def _append_receipts_from_events(self, events: list[dict[str, Any]]) -> int:
        count = 0
        for event in events:
            content = event.get("content") if isinstance(event.get("content"), Mapping) else {}
            candidates: list[Mapping[str, Any]] = []
            if str(event.get("event_type") or "") == "doctor_care_system_receipt" and isinstance(content, Mapping):
                candidates.append(content)
            if isinstance(content, Mapping):
                for key in ("receipts", "care_system_receipts"):
                    value = content.get(key)
                    if isinstance(value, list):
                        candidates.extend(item for item in value if isinstance(item, Mapping))
            for receipt in candidates:
                rid = str(receipt.get("receipt_id") or receipt.get("id") or "").strip()
                if not rid:
                    rid = self._stable_hash(receipt)[:16]
                if rid in self._receipt_ids:
                    continue
                row = dict(receipt)
                row["receipt_id"] = rid
                row.setdefault("source_event_id", event.get("event_id"))
                row.setdefault("turn", event.get("turn"))
                row.setdefault("ledger_recorded_at", self._now())
                self._append_jsonl(self.ledger_dir / "care_system_receipts.jsonl", row)
                self._receipt_ids.add(rid)
                count += 1
        return count

    # ------------------------------------------------------------------
    # Utility helpers

    def _latest_clinical_snapshot_from_events(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        for event in reversed(events):
            if str(event.get("event_type") or "") != "clinical_memory_snapshot":
                continue
            content = event.get("content") if isinstance(event.get("content"), Mapping) else {}
            if isinstance(content, Mapping) and content.get("externalized_snapshot_ref"):
                continue
            return dict(content)
        return {}

    def _memory_summary_from_snapshot(self, snapshot: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(snapshot, Mapping):
            return {}
        keys = [
            "turn",
            "trigger",
            "non_compressible_clinical_kernel",
            "active_clinical_problem_list",
            "action_responsibility_ledger",
            "real_world_constraint_model",
            "open_threads",
            "resolved_or_dormant_threads",
            "uncertainties",
            "phase_timeline",
        ]
        return {key: self._bounded(snapshot.get(key), list_limit=12, text_limit=600) for key in keys if snapshot.get(key) not in (None, "", [], {})}

    def _active_ledger_refs(self) -> dict[str, str]:
        return {
            "non_compressible_ledger": "memory/non_compressible_ledger.json",
            "responsibility_ledger": "memory/responsibility_ledger.json",
            "working_memory": "memory/working_memory.json",
            "master_memory": "memory/master_memory.json",
        }

    def _receipt_index_from_ledger(self) -> list[dict[str, Any]]:
        rows = []
        for row in self._iter_jsonl(self.ledger_dir / "care_system_receipts.jsonl"):
            rows.append({
                "receipt_id": row.get("receipt_id"),
                "turn": row.get("turn"),
                "source_event_id": row.get("source_event_id"),
                "status": row.get("status") or row.get("receipt_status") or row.get("state") or "",
                "operation": row.get("operation") or row.get("request_type") or row.get("name") or "",
            })
        return rows

    def _ledger_counts(self) -> dict[str, int]:
        return {
            "events": self._jsonl_count(self.ledger_dir / "events.jsonl"),
            "transcript": self._jsonl_count(self.ledger_dir / "transcript.jsonl"),
            "llm_calls": self._jsonl_count(self.ledger_dir / "llm_calls.jsonl"),
            "workspace_results": self._jsonl_count(self.ledger_dir / "workspace_results.jsonl"),
            "care_system_receipts": self._jsonl_count(self.ledger_dir / "care_system_receipts.jsonl"),
        }

    def _extract_source_anchors(self, value: Any) -> list[Any]:
        anchors: list[Any] = []
        if isinstance(value, Mapping):
            if "source_anchors" in value:
                raw = value.get("source_anchors")
                if isinstance(raw, list):
                    anchors.extend(raw[:50])
                elif raw not in (None, "", {}, []):
                    anchors.append(raw)
            for key in ("event_id", "event_ids", "receipt_id", "receipt_ids"):
                raw = value.get(key)
                if raw not in (None, "", {}, []):
                    anchors.append({key: raw})
            for child in value.values():
                anchors.extend(self._extract_source_anchors(child))
        elif isinstance(value, list):
            for child in value:
                anchors.extend(self._extract_source_anchors(child))
        return anchors

    def _bounded(self, value: Any, *, list_limit: int = 10, text_limit: int = 500) -> Any:
        if isinstance(value, str):
            return value if len(value) <= text_limit else value[: text_limit - 1] + "…"
        if isinstance(value, Mapping):
            bounded: dict[str, Any] = {}
            for index, (key, child) in enumerate(value.items()):
                if index >= list_limit:
                    bounded["_truncated_keys"] = max(0, len(value) - list_limit)
                    break
                bounded[str(key)] = self._bounded(child, list_limit=list_limit, text_limit=text_limit)
            return bounded
        if isinstance(value, list):
            items = [self._bounded(item, list_limit=list_limit, text_limit=text_limit) for item in value[:list_limit]]
            if len(value) > list_limit:
                items.append({"_truncated_items": len(value) - list_limit})
            return items
        return value

    def _as_list(self, value: Any) -> list[Any]:
        if value in (None, ""):
            return []
        if isinstance(value, list):
            return value
        if isinstance(value, tuple):
            return list(value)
        return [value]

    def _counts(self, values: Iterable[Any]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for value in values:
            key = str(value or "")
            if not key:
                continue
            counts[key] = int(counts.get(key, 0)) + 1
        return dict(sorted(counts.items()))

    def _transcript_key(self, item: Mapping[str, Any]) -> str:
        return self._stable_hash({
            "turn": item.get("turn"),
            "speaker": item.get("speaker"),
            "event_type": item.get("event_type"),
            "text": item.get("text"),
            "sim_time": item.get("sim_time"),
        })

    def _llm_call_key(self, item: Mapping[str, Any], *, index: int) -> str:
        metadata = item.get("metadata") if isinstance(item.get("metadata"), Mapping) else {}
        return self._stable_hash({
            "index": index,
            "turn": item.get("turn"),
            "purpose": item.get("purpose"),
            "prompt_digest": item.get("prompt_digest"),
            "result_preview": item.get("result_preview"),
            "runtime_attempt": metadata.get("runtime_empty_retry_attempt"),
            "status": metadata.get("status"),
            "model": metadata.get("model"),
        })

    def _load_jsonl_keys(self, path: Path, key: str) -> set[str]:
        values: set[str] = set()
        for row in self._iter_jsonl(path):
            value = row.get(key)
            if value not in (None, ""):
                values.add(str(value))
        return values

    def _iter_jsonl(self, path: Path):
        if not path.exists():
            return
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    yield row

    def _jsonl_count(self, path: Path) -> int:
        if not path.exists():
            return 0
        with path.open("r", encoding="utf-8") as handle:
            return sum(1 for line in handle if line.strip())

    def _append_jsonl(self, path: Path, row: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")

    def _write_json_atomic(self, path: Path, payload: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.tmp")
        tmp.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(path)

    def _read_json(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _latest_json_from_dir(self, directory: Path, pattern: str) -> dict[str, Any]:
        items = self._latest_jsons_from_dir(directory, pattern, limit=1)
        return items[0] if items else {}

    def _latest_jsons_from_dir(self, directory: Path, pattern: str, *, limit: int) -> list[dict[str, Any]]:
        paths = sorted(directory.glob(pattern))[-max(0, int(limit or 0)) :]
        return [self._read_json(path) for path in paths if self._read_json(path)]

    def _load_latest_turn(self) -> int:
        status = self._read_json(self.memory_dir / "long_context_status.json")
        value = self._safe_int(status.get("turn"))
        return int(value or 0)

    def _max_turn_from_rows(self, *rows_lists: list[dict[str, Any]]) -> int:
        max_turn = 0
        for rows in rows_lists:
            for row in rows:
                value = self._safe_int(row.get("turn"))
                if value is not None:
                    max_turn = max(max_turn, int(value))
        return max_turn

    def _safe_int(self, value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _stable_hash(self, value: Any) -> str:
        try:
            text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        except TypeError:
            text = str(value)
        return sha256(text.encode("utf-8")).hexdigest()

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()
