from __future__ import annotations

import json
import shlex
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .base import BaseProvider
from .utils import (
    current_timestamp,
    extract_prompt_title,
    fallback_session_title,
    get_process_metadata,
)

if TYPE_CHECKING:
    from ..core.models import AgentSession

from ..core.models import AgentSession, SessionOrigin, SessionPhase


HOOK_EVENTS = (
    "SessionStart",
    "BeforeAgent",
    "AfterAgent",
    "SessionEnd",
    "Notification",
)


def _looks_like_managed_module_command(command: object, event_name: str) -> bool:
    if not isinstance(command, str):
        return False
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    for index, token in enumerate(tokens[:-3]):
        if token != "-m":
            continue
        if tokens[index + 1] != "linux_agent_island.hooks":
            continue
        return tokens[index + 2 :] == ["gemini", event_name]
    return False


class GeminiProvider(BaseProvider):
    def __init__(
        self,
        settings_path: Path,
        tmp_dir: Path,
        hook_command_prefix: str,
        recent_window_seconds: int = 86_400,
    ) -> None:
        self.settings_path = settings_path
        self.tmp_dir = tmp_dir
        self.hook_command_prefix = hook_command_prefix
        self.recent_window_seconds = recent_window_seconds

    @property
    def name(self) -> str:
        return "gemini"

    def install_hooks(self) -> None:
        payload = self._load_settings()
        hooks = payload.setdefault("hooks", {})
        if not isinstance(hooks, dict):
            hooks = {}
            payload["hooks"] = hooks
        for event in HOOK_EVENTS:
            hooks[event] = self._merge_hook_entries(hooks.get(event, []), event)
        self._write_settings(payload)

    def uninstall_hooks(self) -> None:
        payload = self._load_settings()
        hooks = payload.get("hooks")
        if not isinstance(hooks, dict):
            return
        for event in HOOK_EVENTS:
            if event not in hooks:
                continue
            entries = self._prune_managed_hook_entries(hooks.get(event, []), event)
            if entries:
                hooks[event] = entries
            else:
                del hooks[event]
        self._write_settings(payload)

    def _load_settings(self) -> dict[str, object]:
        if self.settings_path.exists():
            try:
                payload = json.loads(self.settings_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                payload = {}
            if isinstance(payload, dict):
                return payload
        return {}

    def _write_settings(self, payload: dict[str, object]) -> None:
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def _managed_command(self, event_name: str) -> str:
        return f"{self.hook_command_prefix} gemini {event_name}"

    def _managed_hook(self, event_name: str) -> dict[str, object]:
        return {
            "type": "command",
            "command": self._managed_command(event_name),
            "timeout": 10000,
            "name": "linux-agent-island",
        }

    def _merge_hook_entries(self, existing: object, event_name: str) -> list[object]:
        entries = self._prune_managed_hook_entries(existing, event_name)
        entries.append({"hooks": [self._managed_hook(event_name)]})
        return entries

    def _prune_managed_hook_entries(self, existing: object, event_name: str) -> list[object]:
        entries = list(existing) if isinstance(existing, list) else []
        managed_command = self._managed_command(event_name)
        pruned_entries: list[object] = []
        for entry in entries:
            if not isinstance(entry, dict):
                pruned_entries.append(entry)
                continue
            hooks = entry.get("hooks")
            if not isinstance(hooks, list):
                pruned_entries.append(dict(entry))
                continue
            filtered_hooks = [
                hook
                for hook in hooks
                if not (
                    isinstance(hook, dict)
                    and (
                        hook.get("command") == managed_command
                        or hook.get("name") == "linux-agent-island"
                        or _looks_like_managed_module_command(hook.get("command"), event_name)
                    )
                )
            ]
            if filtered_hooks:
                updated_entry = dict(entry)
                updated_entry["hooks"] = filtered_hooks
                pruned_entries.append(updated_entry)
        return pruned_entries

    def load_transcript(self, session_id: str, cwd: str = "", **kwargs: Any) -> list[dict[str, str]]:
        transcript_path = self._transcript_path(session_id, cwd=cwd)
        if transcript_path is None:
            return []
        try:
            payload = json.loads(transcript_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        messages = payload.get("messages")
        if not isinstance(messages, list):
            return []
        turns: list[dict[str, str]] = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            turn = self._transcript_turn_from_message(message)
            if turn is not None:
                turns.append(turn)
        return turns

    def _transcript_path(self, session_id: str, cwd: str = "") -> Path | None:
        if not session_id or not self.tmp_dir.exists():
            return None

        # If we have cwd, we can find the project hash/nickname
        project_dir_name = ""
        if cwd:
            projects_config = self.settings_path.parent / "projects.json"
            if projects_config.exists():
                try:
                    data = json.loads(projects_config.read_text(encoding="utf-8"))
                    projects = data.get("projects", {})
                    if isinstance(projects, dict):
                        project_dir_name = projects.get(cwd, "")
                except (OSError, json.JSONDecodeError):
                    pass

        # If we have a project directory name, look there first
        if project_dir_name:
            # Since there can be multiple session-*.json that share a prefix, but we want the one with exact sessionId
            for p in self.tmp_dir.glob(f"{project_dir_name}/chats/session-*.json"):
                try:
                    payload = json.loads(p.read_text(encoding="utf-8"))
                    if str(payload.get("sessionId", "")) == session_id:
                        return p
                except (OSError, json.JSONDecodeError):
                    continue

        # Fallback to scanning all projects
        for path in self.tmp_dir.glob("*/chats/session-*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if str(payload.get("sessionId", "")) == session_id:
                return path
        return None

    def _transcript_turn_from_message(self, message: dict[str, Any]) -> dict[str, str] | None:
        kind = str(message.get("type", ""))
        role = {"user": "user", "gemini": "assistant"}.get(kind)
        if role is None:
            return None
        text = _content_to_text(message.get("content"))
        if not text:
            return None
        return {
            "role": role,
            "text": text,
            "timestamp": str(message.get("timestamp", "")),
        }

    def load_sessions(self) -> list[AgentSession]:
        if not self.tmp_dir.exists():
            return []

        now_ts = current_timestamp()
        nickname_to_cwd: dict[str, str] = {}
        projects_config = self.settings_path.parent / "projects.json"
        if projects_config.exists():
            try:
                data = json.loads(projects_config.read_text(encoding="utf-8"))
                projects = data.get("projects", {})
                if isinstance(projects, dict):
                    for cwd, nickname in projects.items():
                        nickname_to_cwd[nickname] = cwd
            except (OSError, json.JSONDecodeError):
                pass

        sessions: list[AgentSession] = []
        # Use glob to find all chats session files: ~/.gemini/tmp/*/chats/session-*.json
        for session_path in self.tmp_dir.glob("*/chats/session-*.json"):
            try:
                payload = json.loads(session_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue

            session_id = str(payload.get("sessionId", ""))
            if not session_id:
                continue

            project_hash = str(payload.get("projectHash", ""))
            cwd = nickname_to_cwd.get(project_hash, "")

            last_updated_str = payload.get("lastUpdated")
            start_time_str = payload.get("startTime")

            # Parse timestamps
            updated_at = now_ts
            started_at = updated_at

            try:
                if last_updated_str:
                    updated_at = int(datetime.fromisoformat(str(last_updated_str).replace("Z", "+00:00")).timestamp())
                if start_time_str:
                    started_at = int(datetime.fromisoformat(str(start_time_str).replace("Z", "+00:00")).timestamp())
            except (ValueError, TypeError):
                pass

            # Filter by time
            if updated_at < now_ts - self.recent_window_seconds:
                continue

            # Title from first user message if available
            title = session_id[:8]
            messages = payload.get("messages", [])
            if isinstance(messages, list):
                for msg in messages:
                    if isinstance(msg, dict) and msg.get("type") == "user":
                        title = _content_to_text(msg.get("content"))[:50].strip() or title
                        break

            sessions.append(
                AgentSession(
                    provider=self.name,
                    session_id=session_id,
                    cwd=cwd,
                    title=title,
                    phase=SessionPhase.COMPLETED,  # Default to completed for historical sessions
                    model=self._extract_gemini_model(payload),
                    sandbox=None,
                    approval_mode=None,
                    updated_at=updated_at,
                    started_at=started_at,
                    origin=SessionOrigin.RESTORED,
                    is_hook_managed=True,
                    is_process_alive=True, # Mark as alive initially to keep it visible
                )
            )

        return sessions

    def get_process_signatures(self) -> dict[str, list[str]]:
        return {
            "commands": ["gemini", "gemini-cli"],
            "arg_patterns": [
                "/gemini",
                "gemini-cli",
                "@google/gemini-cli",
                "google-gemini-cli",
            ],
        }

    def build_event(
        self,
        hook_name: str,
        payload: dict[str, Any],
        pid: int | None = None,
        tty: str | None = None,
    ) -> dict[str, Any]:
        if pid is None or tty is None:
            auto_pid, auto_tty = get_process_metadata()
            pid = pid if pid is not None else auto_pid
            tty = tty if tty is not None else auto_tty

        now_ts = current_timestamp()
        event_type = "activity_updated"
        phase = "waiting"
        title = ""
        summary = ""
        last_message_preview = ""
        is_session_end = False

        session_id = str(payload.get("session_id") or payload.get("sessionId") or "unknown")

        if hook_name == "SessionStart":
            event_type = "session_started"
            title = fallback_session_title(payload)
        elif hook_name == "BeforeAgent":
            phase = "running"
            title = extract_prompt_title(payload)
            started_at = now_ts
        elif hook_name == "AfterAgent":
            event_type = "session_completed"
            phase = "completed"
            summary = str(payload.get("prompt_response", ""))
            last_message_preview = summary
            started_at = None
        elif hook_name == "SessionEnd":
            event_type = "session_completed"
            phase = "completed"
            is_session_end = True
            started_at = None
        elif hook_name == "Notification":
            phase = "waiting_approval" if payload.get("notification_type") == "ToolPermission" else "waiting"
            summary = str(payload.get("message", ""))
            last_message_preview = summary
            started_at = None
        else:
            started_at = None

        if hook_name not in {"BeforeAgent", "AfterAgent", "SessionEnd", "Notification"}:
            started_at = None

        # If title is still empty, use a fallback
        if not title:
            title = extract_prompt_title(payload) or fallback_session_title(payload)

        return {
            "event_type": event_type,
            "provider": self.name,
            "session_id": session_id,
            "cwd": payload.get("cwd", ""),
            "title": title,
            "phase": phase,
            "model": self._extract_gemini_model(payload),
            "updated_at": now_ts,
            "started_at": started_at,
            "origin": "live",
            "is_hook_managed": True,
            "pid": pid,
            "tty": tty,
            "is_session_end": is_session_end,
            "summary": summary,
            "last_message_preview": last_message_preview,
        }

    def _extract_gemini_model(self, payload: dict[str, Any]) -> str | None:
        # 1. Try common locations in hook payloads
        llm_request = payload.get("llm_request")
        if isinstance(llm_request, dict) and llm_request.get("model") is not None:
            return str(llm_request["model"])
        
        metadata = payload.get("metadata")
        if isinstance(metadata, dict) and metadata.get("model") is not None:
            return str(metadata["model"])
            
        config = payload.get("config")
        if isinstance(config, dict) and config.get("model") is not None:
            return str(config["model"])

        # 2. Try top-level (common in simple events or some session formats)
        if payload.get("model") is not None:
            return str(payload["model"])

        # 3. Try scanning messages (common in full session JSON files)
        messages = payload.get("messages")
        if isinstance(messages, list):
            for msg in messages:
                if isinstance(msg, dict) and msg.get("model"):
                    return str(msg["model"])

        return None


def _content_to_text(content: object) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""

    parts: list[str] = []
    for item in content:
        if isinstance(item, str):
            text = item
        elif isinstance(item, dict):
            value = item.get("text")
            if value is None:
                value = item.get("content")
            text = str(value) if value is not None else ""
        else:
            text = ""
        text = text.strip()
        if text:
            parts.append(text)
    return "\n".join(parts).strip()
