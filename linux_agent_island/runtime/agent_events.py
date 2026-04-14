from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from ..core.models import ClaudeSessionMetadata, CodexSessionMetadata, PermissionRequest, QuestionPrompt, SessionOrigin, SessionPhase


class AgentEventType(str, Enum):
    SESSION_RESTORED = "session_restored"
    SESSION_STARTED = "session_started"
    ACTIVITY_UPDATED = "activity_updated"
    PERMISSION_REQUESTED = "permission_requested"
    QUESTION_ASKED = "question_asked"
    METADATA_UPDATED = "metadata_updated"
    SESSION_COMPLETED = "session_completed"
    ACTIONABLE_STATE_RESOLVED = "actionable_state_resolved"


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
    source: str | None = None
    origin: SessionOrigin = SessionOrigin.LIVE
    started_at: int | None = None
    completed_at: int | None = None
    summary: str = ""
    pid: int | None = None
    tty: str | None = None
    last_message_preview: str = ""
    permission_request: PermissionRequest | None = None
    question_prompt: QuestionPrompt | None = None
    metadata_kind: str | None = None
    codex_metadata: CodexSessionMetadata | None = None
    claude_metadata: ClaudeSessionMetadata | None = None
    is_hook_managed: bool | None = None
    identity_confirmed_by_hook: bool | None = None
    is_session_end: bool | None = None
    is_process_alive: bool | None = None
    process_not_seen_count: int | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "AgentEvent":
        event_type = AgentEventType(str(payload.get("event_type", AgentEventType.ACTIVITY_UPDATED.value)))
        phase_value = payload.get("phase")
        phase = SessionPhase.coerce(phase_value) if phase_value is not None else None
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
            source=str(payload["event_source"]) if payload.get("event_source") is not None else None,
            origin=SessionOrigin(str(payload.get("origin", SessionOrigin.LIVE.value))),
            started_at=int(payload["started_at"]) if payload.get("started_at") is not None else None,
            completed_at=int(payload["completed_at"]) if payload.get("completed_at") is not None else None,
            summary=str(payload.get("summary", "")),
            pid=int(payload["pid"]) if payload.get("pid") is not None else None,
            tty=str(payload["tty"]) if payload.get("tty") is not None else None,
            last_message_preview=str(payload.get("last_message_preview", "")),
            permission_request=(
                PermissionRequest.from_dict(payload["permission_request"])
                if isinstance(payload.get("permission_request"), dict)
                else None
            ),
            question_prompt=(
                QuestionPrompt.from_dict(payload["question_prompt"])
                if isinstance(payload.get("question_prompt"), dict)
                else None
            ),
            metadata_kind=str(payload["metadata_kind"]) if payload.get("metadata_kind") is not None else None,
            codex_metadata=(
                CodexSessionMetadata.from_dict(payload["codex_metadata"])
                if isinstance(payload.get("codex_metadata"), dict)
                else None
            ),
            claude_metadata=(
                ClaudeSessionMetadata.from_dict(payload["claude_metadata"])
                if isinstance(payload.get("claude_metadata"), dict)
                else None
            ),
            is_hook_managed=_optional_bool(payload, "is_hook_managed"),
            identity_confirmed_by_hook=_optional_bool(payload, "identity_confirmed_by_hook"),
            is_session_end=_optional_bool(payload, "is_session_end"),
            is_process_alive=_optional_bool(payload, "is_process_alive"),
            process_not_seen_count=(
                int(payload["process_not_seen_count"])
                if payload.get("process_not_seen_count") is not None
                else None
            ),
        )


def _optional_bool(payload: dict[str, object], key: str) -> bool | None:
    value: Any = payload.get(key)
    if value is None:
        return None
    return bool(value)
