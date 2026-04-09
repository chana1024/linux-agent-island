from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..models import SessionOrigin, SessionPhase


class AgentEventType(str, Enum):
    SESSION_RESTORED = "session_restored"
    SESSION_STARTED = "session_started"
    ACTIVITY_UPDATED = "activity_updated"
    SESSION_COMPLETED = "session_completed"


@dataclass(slots=True)
class AgentEvent:
    type: AgentEventType
    provider: str
    session_id: str
    updated_at: int
    cwd: str = ""
    title: str = ""
    phase: SessionPhase | None = None
    model: str | None = None
    sandbox: str | None = None
    approval_mode: str | None = None
    origin: SessionOrigin = SessionOrigin.LIVE
    started_at: int | None = None
    completed_at: int | None = None
    summary: str = ""
    pid: int | None = None
    tty: str | None = None
    last_message_preview: str = ""
    is_hook_managed: bool = False
    is_session_end: bool = False
    is_process_alive: bool = False
    process_not_seen_count: int = 0

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "AgentEvent":
        event_type = AgentEventType(str(payload.get("event_type", AgentEventType.ACTIVITY_UPDATED.value)))
        phase_value = payload.get("phase")
        phase = SessionPhase(str(phase_value)) if phase_value is not None else None
        return cls(
            type=event_type,
            provider=str(payload["provider"]),
            session_id=str(payload["session_id"]),
            updated_at=int(payload.get("updated_at", 0)),
            cwd=str(payload.get("cwd", "")),
            title=str(payload.get("title", "")),
            phase=phase,
            model=str(payload["model"]) if payload.get("model") is not None else None,
            sandbox=str(payload["sandbox"]) if payload.get("sandbox") is not None else None,
            approval_mode=str(payload["approval_mode"]) if payload.get("approval_mode") is not None else None,
            origin=SessionOrigin(str(payload.get("origin", SessionOrigin.LIVE.value))),
            started_at=int(payload["started_at"]) if payload.get("started_at") is not None else None,
            completed_at=int(payload["completed_at"]) if payload.get("completed_at") is not None else None,
            summary=str(payload.get("summary", "")),
            pid=int(payload["pid"]) if payload.get("pid") is not None else None,
            tty=str(payload["tty"]) if payload.get("tty") is not None else None,
            last_message_preview=str(payload.get("last_message_preview", "")),
            is_hook_managed=bool(payload.get("is_hook_managed", False)),
            is_session_end=bool(payload.get("is_session_end", False)),
            is_process_alive=bool(payload.get("is_process_alive", False)),
            process_not_seen_count=int(payload.get("process_not_seen_count", 0)),
        )
