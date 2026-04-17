from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class SessionPhase(str, Enum):
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    WAITING_ANSWER = "waiting_answer"
    COMPLETED = "completed"

    @classmethod
    def coerce(cls, value: object, default: "SessionPhase" | None = None) -> "SessionPhase":
        if isinstance(value, cls):
            return value
        normalized = str(value or "").strip()
        legacy_map = {
            "idle": cls.COMPLETED,
            "waiting": cls.RUNNING,
            "error": cls.COMPLETED,
            "running": cls.RUNNING,
            "waiting_approval": cls.WAITING_APPROVAL,
            "waiting_answer": cls.WAITING_ANSWER,
            "completed": cls.COMPLETED,
        }
        if normalized in legacy_map:
            return legacy_map[normalized]
        if default is not None:
            return default
        raise ValueError(f"unknown session phase: {value!r}")


class SessionOrigin(str, Enum):
    LIVE = "live"
    RESTORED = "restored"


@dataclass(slots=True)
class CodexSessionMetadata:
    transcript_path: str | None = None
    initial_user_prompt: str | None = None
    last_user_prompt: str | None = None
    last_assistant_message: str | None = None
    current_tool: str | None = None
    current_command_preview: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "CodexSessionMetadata":
        return cls(
            transcript_path=str(payload["transcript_path"]) if payload.get("transcript_path") is not None else None,
            initial_user_prompt=(
                str(payload["initial_user_prompt"]) if payload.get("initial_user_prompt") is not None else None
            ),
            last_user_prompt=str(payload["last_user_prompt"]) if payload.get("last_user_prompt") is not None else None,
            last_assistant_message=(
                str(payload["last_assistant_message"]) if payload.get("last_assistant_message") is not None else None
            ),
            current_tool=str(payload["current_tool"]) if payload.get("current_tool") is not None else None,
            current_command_preview=(
                str(payload["current_command_preview"]) if payload.get("current_command_preview") is not None else None
            ),
        )


@dataclass(slots=True)
class ClaudeSessionMetadata:
    transcript_path: str | None = None
    initial_user_prompt: str | None = None
    last_user_prompt: str | None = None
    last_assistant_message: str | None = None
    current_tool: str | None = None
    current_tool_input_preview: str | None = None
    permission_mode: str | None = None
    model: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ClaudeSessionMetadata":
        return cls(
            transcript_path=str(payload["transcript_path"]) if payload.get("transcript_path") is not None else None,
            initial_user_prompt=(
                str(payload["initial_user_prompt"]) if payload.get("initial_user_prompt") is not None else None
            ),
            last_user_prompt=str(payload["last_user_prompt"]) if payload.get("last_user_prompt") is not None else None,
            last_assistant_message=(
                str(payload["last_assistant_message"]) if payload.get("last_assistant_message") is not None else None
            ),
            current_tool=str(payload["current_tool"]) if payload.get("current_tool") is not None else None,
            current_tool_input_preview=(
                str(payload["current_tool_input_preview"])
                if payload.get("current_tool_input_preview") is not None
                else None
            ),
            permission_mode=str(payload["permission_mode"]) if payload.get("permission_mode") is not None else None,
            model=str(payload["model"]) if payload.get("model") is not None else None,
        )


@dataclass(slots=True)
class PermissionRequest:
    title: str
    summary: str
    affected_path: str = ""
    primary_action_title: str = "Allow"
    secondary_action_title: str = "Deny"
    tool_name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PermissionRequest":
        return cls(
            title=str(payload.get("title", "")),
            summary=str(payload.get("summary", "")),
            affected_path=str(payload.get("affected_path", "")),
            primary_action_title=str(payload.get("primary_action_title", "Allow")),
            secondary_action_title=str(payload.get("secondary_action_title", "Deny")),
            tool_name=str(payload["tool_name"]) if payload.get("tool_name") is not None else None,
        )


@dataclass(slots=True)
class QuestionOption:
    label: str
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "QuestionOption":
        return cls(
            label=str(payload.get("label", "")),
            description=str(payload.get("description", "")),
        )


@dataclass(slots=True)
class QuestionPrompt:
    title: str
    options: list[QuestionOption] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["options"] = [option.to_dict() for option in self.options]
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "QuestionPrompt":
        raw_options = payload.get("options", [])
        options = [
            QuestionOption.from_dict(item)
            for item in raw_options
            if isinstance(item, dict)
        ]
        return cls(
            title=str(payload.get("title", "")),
            options=options,
        )


@dataclass(slots=True)
class CodexAccountSummary:
    account_id: str
    label: str
    is_default: bool = False
    is_active: bool = False
    has_credentials: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "CodexAccountSummary":
        return cls(
            account_id=str(payload.get("account_id", "")),
            label=str(payload.get("label", "")),
            is_default=bool(payload.get("is_default", False)),
            is_active=bool(payload.get("is_active", False)),
            has_credentials=bool(payload.get("has_credentials", True)),
        )


@dataclass(slots=True)
class CodexAccountStatus:
    logged_in: bool
    auth_mode: str | None = None
    current_account_id: str | None = None
    current_account_label: str | None = None
    current_account_managed: bool = False
    device_login_in_progress: bool = False
    switch_affects_new_sessions_only: bool = True
    has_running_codex_sessions: bool = False
    accounts: list[CodexAccountSummary] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["accounts"] = [account.to_dict() for account in self.accounts]
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "CodexAccountStatus":
        raw_accounts = payload.get("accounts", [])
        accounts = [
            CodexAccountSummary.from_dict(item)
            for item in raw_accounts
            if isinstance(item, dict)
        ]
        return cls(
            logged_in=bool(payload.get("logged_in", False)),
            auth_mode=str(payload["auth_mode"]) if payload.get("auth_mode") is not None else None,
            current_account_id=(
                str(payload["current_account_id"])
                if payload.get("current_account_id") is not None
                else None
            ),
            current_account_label=(
                str(payload["current_account_label"])
                if payload.get("current_account_label") is not None
                else None
            ),
            current_account_managed=bool(payload.get("current_account_managed", False)),
            device_login_in_progress=bool(payload.get("device_login_in_progress", False)),
            switch_affects_new_sessions_only=bool(payload.get("switch_affects_new_sessions_only", True)),
            has_running_codex_sessions=bool(payload.get("has_running_codex_sessions", False)),
            accounts=accounts,
        )


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
    process_anchor: bool = False
    synthetic_session: bool = False
    provider_stale: bool = False
    is_session_ended: bool = False
    is_process_alive: bool = False
    process_not_seen_count: int = 0
    last_message_preview: str = ""
    permission_request: PermissionRequest | None = None
    question_prompt: QuestionPrompt | None = None
    codex_metadata: CodexSessionMetadata | None = None
    claude_metadata: ClaudeSessionMetadata | None = None

    @property
    def is_running(self) -> bool:
        return self.phase is SessionPhase.RUNNING

    @property
    def requires_attention(self) -> bool:
        return self.phase in {SessionPhase.WAITING_APPROVAL, SessionPhase.WAITING_ANSWER}

    @property
    def is_visible_in_island(self) -> bool:
        if self.requires_attention:
            return True
        if self.is_hook_managed:
            return not self.is_session_ended
        return self.is_process_alive

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["phase"] = self.phase.value
        payload["origin"] = self.origin.value
        payload["permission_request"] = self.permission_request.to_dict() if self.permission_request is not None else None
        payload["question_prompt"] = self.question_prompt.to_dict() if self.question_prompt is not None else None
        payload["codex_metadata"] = self.codex_metadata.to_dict() if self.codex_metadata is not None else None
        payload["claude_metadata"] = self.claude_metadata.to_dict() if self.claude_metadata is not None else None
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "AgentSession":
        return cls(
            provider=payload["provider"],
            session_id=payload["session_id"],
            cwd=payload.get("cwd", ""),
            title=payload.get("title") or payload.get("cwd", "").rstrip("/").split("/")[-1] or payload["session_id"],
            phase=SessionPhase.coerce(payload.get("phase"), default=SessionPhase.COMPLETED),
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
            process_anchor=bool(payload.get("process_anchor", False)),
            synthetic_session=bool(payload.get("synthetic_session", False)),
            provider_stale=bool(payload.get("provider_stale", False)),
            is_session_ended=bool(payload.get("is_session_ended", False)),
            is_process_alive=bool(payload.get("is_process_alive", False)),
            process_not_seen_count=int(payload.get("process_not_seen_count", 0)),
            last_message_preview=payload.get("last_message_preview", ""),
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
        )
