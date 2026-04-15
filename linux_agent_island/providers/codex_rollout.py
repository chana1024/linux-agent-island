from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.models import AgentSession, CodexSessionMetadata
from ..runtime.agent_events import AgentEvent, AgentEventType


@dataclass(slots=True)
class CodexRolloutSnapshot:
    metadata: CodexSessionMetadata
    updated_at: int


class CodexRolloutWatcher:
    def __init__(self) -> None:
        self._snapshots: dict[str, CodexRolloutSnapshot] = {}

    def poll(self, sessions: list[AgentSession], rollout_paths: dict[str, str]) -> list[AgentEvent]:
        events: list[AgentEvent] = []

        tracked_session_ids = {session.session_id for session in sessions if session.provider == "codex"}
        self._snapshots = {
            session_id: snapshot
            for session_id, snapshot in self._snapshots.items()
            if session_id in tracked_session_ids
        }

        for session in sessions:
            if session.provider != "codex":
                continue
            rollout_path = rollout_paths.get(session.session_id)
            if not rollout_path:
                continue
            snapshot = _snapshot_from_rollout(Path(rollout_path))
            if snapshot is None:
                continue
            previous = self._snapshots.get(session.session_id)
            if previous is not None and previous == snapshot:
                continue
            self._snapshots[session.session_id] = snapshot
            if session.codex_metadata == snapshot.metadata:
                continue
            events.append(
                AgentEvent(
                    type=AgentEventType.METADATA_UPDATED,
                    provider="codex",
                    session_id=session.session_id,
                    updated_at=snapshot.updated_at,
                    title=snapshot.metadata.last_user_prompt or "",
                    metadata_kind="codex",
                    codex_metadata=snapshot.metadata,
                    source="codex_rollout_watcher",
                    origin=session.origin,
                )
            )

        return events


def _snapshot_from_rollout(path: Path) -> CodexRolloutSnapshot | None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None

    initial_user_prompt: str | None = None
    last_user_prompt: str | None = None
    last_assistant_message: str | None = None
    current_tool: str | None = None
    current_command_preview: str | None = None
    updated_at = int(path.stat().st_mtime)

    for line in lines:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue

        timestamp = _timestamp_to_seconds(payload.get("timestamp"))
        if timestamp is not None:
            updated_at = max(updated_at, timestamp)

        if payload.get("type") != "response_item":
            continue
        item = payload.get("payload")
        if not isinstance(item, dict):
            continue

        item_type = item.get("type")
        if item_type == "message":
            role = item.get("role")
            text = _extract_text(item.get("content"))
            if role == "user" and text:
                if initial_user_prompt is None:
                    initial_user_prompt = text
                last_user_prompt = text
            elif role == "assistant" and text:
                last_assistant_message = text
            continue

        if item_type in {"function_call", "tool_call"}:
            current_tool = _first_str(item, "name", "tool_name")
            current_command_preview = _extract_text(item.get("arguments") or item.get("input"))

    metadata = CodexSessionMetadata(
        transcript_path=str(path),
        initial_user_prompt=initial_user_prompt,
        last_user_prompt=last_user_prompt,
        last_assistant_message=last_assistant_message,
        current_tool=current_tool,
        current_command_preview=current_command_preview,
    )
    return CodexRolloutSnapshot(metadata=metadata, updated_at=updated_at)


def _extract_text(content: Any) -> str | None:
    if isinstance(content, str):
        text = content.strip()
        return text or None
    if not isinstance(content, list):
        return None

    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        text = item.get("text") or item.get("input_text") or item.get("output_text")
        if text:
            parts.append(str(text).strip())
    rendered = "\n".join(part for part in parts if part)
    return rendered or None


def _timestamp_to_seconds(raw: Any) -> int | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        value = int(raw)
        return int(value / 1000) if value > 10_000_000_000 else value
    if not isinstance(raw, str):
        return None
    stripped = raw.strip()
    if not stripped:
        return None
    if stripped.isdigit():
        value = int(stripped)
        return int(value / 1000) if value > 10_000_000_000 else value
    try:
        from datetime import datetime

        return int(datetime.fromisoformat(stripped.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def _first_str(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if value:
            return str(value)
    return None
