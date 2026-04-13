from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


class SessionPhase(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    WAITING = "waiting"
    COMPLETED = "completed"
    ERROR = "error"


class SessionOrigin(str, Enum):
    LIVE = "live"
    RESTORED = "restored"


@dataclass(slots=True)
class AgentSession:
    provider: str
    session_id: str
    cwd: str
    title: str
    phase: SessionPhase
    model: str | None
    sandbox: str | None
    approval_mode: str | None
    updated_at: int
    started_at: int | None = None
    completed_at: int | None = None
    origin: SessionOrigin = SessionOrigin.RESTORED
    summary: str = ""
    pid: int | None = None
    tty: str | None = None
    has_interactive_window: bool = False
    is_focused: bool = False
    is_hook_managed: bool = False
    identity_confirmed_by_hook: bool = False
    is_session_ended: bool = False
    is_process_alive: bool = False
    process_not_seen_count: int = 0
    last_message_preview: str = ""

    @property
    def is_running(self) -> bool:
        return self.phase is SessionPhase.RUNNING

    @property
    def requires_attention(self) -> bool:
        return self.phase is SessionPhase.WAITING_APPROVAL

    @property
    def is_visible_in_island(self) -> bool:
        if self.is_hook_managed:
            return not self.is_session_ended
        return self.is_process_alive

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["phase"] = self.phase.value
        payload["origin"] = self.origin.value
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "AgentSession":
        return cls(
            provider=payload["provider"],
            session_id=payload["session_id"],
            cwd=payload.get("cwd", ""),
            title=payload.get("title") or payload.get("cwd", "").rstrip("/").split("/")[-1] or payload["session_id"],
            phase=SessionPhase(payload.get("phase", SessionPhase.IDLE.value)),
            model=payload.get("model"),
            sandbox=payload.get("sandbox"),
            approval_mode=payload.get("approval_mode"),
            updated_at=int(payload.get("updated_at", 0)),
            started_at=int(payload["started_at"]) if payload.get("started_at") is not None else None,
            completed_at=int(payload["completed_at"]) if payload.get("completed_at") is not None else None,
            origin=SessionOrigin(payload.get("origin", SessionOrigin.RESTORED.value)),
            summary=payload.get("summary", ""),
            pid=int(payload["pid"]) if payload.get("pid") is not None else None,
            tty=payload.get("tty"),
            has_interactive_window=bool(payload.get("has_interactive_window", False)),
            is_focused=bool(payload.get("is_focused", False)),
            is_hook_managed=bool(payload.get("is_hook_managed", False)),
            identity_confirmed_by_hook=bool(payload.get("identity_confirmed_by_hook", False)),
            is_session_ended=bool(payload.get("is_session_ended", False)),
            is_process_alive=bool(payload.get("is_process_alive", False)),
            process_not_seen_count=int(payload.get("process_not_seen_count", 0)),
            last_message_preview=payload.get("last_message_preview", ""),
        )
