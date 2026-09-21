from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Any


def _stable_hash(data: dict[str, Any]) -> str:
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return sha256(raw).hexdigest()


@dataclass
class LiteTrajectory:
    case_id: str
    run_id: str
    events: list[dict[str, Any]] = field(default_factory=list)
    transcript: list[dict[str, Any]] = field(default_factory=list)
    llm_calls: list[dict[str, Any]] = field(default_factory=list)
    probability_ledger: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def add_event(
        self,
        *,
        turn: int,
        actor: str,
        event_type: str,
        content: Any,
        sim_time: str = "",
        visibility: str = "internal",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        event = {
            "event_id": f"lite_evt_{len(self.events) + 1:05d}",
            "turn": turn,
            "actor": actor,
            "event_type": event_type,
            "sim_time": sim_time,
            "visibility": visibility,
            "content": content,
            "metadata": dict(metadata or {}),
        }
        event["audit_hash"] = _stable_hash(event)
        self.events.append(event)
        if event_type in {"doctor_message", "patient_message", "family_message"}:
            event_metadata = dict(metadata or {})
            speaker_category = str(event_metadata.get("speaker_category") or ("doctor" if event_type == "doctor_message" else "")).strip()
            speaker_display = str(event_metadata.get("speaker_display") or actor).strip()
            self.transcript.append(
                {
                    "turn": turn,
                    "speaker": actor,
                    "speaker_display": speaker_display,
                    "speaker_category": speaker_category,
                    "speaker_role": event_metadata.get("actor_role", ""),
                    "relationship_to_patient": event_metadata.get("relationship_to_patient", ""),
                    "event_type": event_type,
                    "text": content.get("text") if isinstance(content, dict) else str(content),
                    "sim_time": sim_time,
                    "route": event_metadata.get("route") or ("care" if event_type == "doctor_message" else "care"),
                    "patient_visible": bool(event_metadata.get("patient_visible", event_type in {"doctor_message", "patient_message", "family_message"})),
                }
            )
        return event

    def add_llm_call(
        self,
        *,
        turn: int,
        purpose: str,
        prompt_digest: str,
        result: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.llm_calls.append(
            {
                "turn": turn,
                "purpose": purpose,
                "prompt_digest": prompt_digest,
                "result_preview": result[:500],
                "metadata": dict(metadata or {}),
            }
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol": "careloop.runtime_lite.trajectory.v1",
            "case_id": self.case_id,
            "run_id": self.run_id,
            "created_at": self.created_at,
            "events": self.events,
            "transcript": self.transcript,
            "llm_calls": self.llm_calls,
            "probability_ledger": self.probability_ledger,
        }

    def write_json(self, path: str | Path) -> Path:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return output
