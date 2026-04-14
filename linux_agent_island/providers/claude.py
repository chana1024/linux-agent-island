from __future__ import annotations

from datetime import datetime
import json
import shlex
from pathlib import Path
from typing import Any

from ..core.models import AgentSession, ClaudeSessionMetadata, SessionOrigin, SessionPhase
from .base import BaseProvider
from .utils import (
    current_timestamp,
    extract_prompt_title,
    fallback_session_title,
)


HOOK_EVENTS = (
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "PermissionRequest",
    "Notification",
    "Stop",
    "SessionStart",
    "SessionEnd",
    "PreCompact",
)


def _legacy_script_command(script_path: Path, event_name: str) -> str:
    return f"/usr/bin/python3 {script_path} {event_name}"


def _looks_like_managed_legacy_command(command: object, event_name: str, script_name: str) -> bool:
    if not isinstance(command, str):
        return False
    if not command.endswith(f"{script_name} {event_name}"):
        return False
    return "linux-agent-island" in command or f".claude/{script_name}" in command


def _looks_like_managed_module_command(command: object, provider: str, event_name: str) -> bool:
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
        return tokens[index + 2 :] == [provider, event_name]
    return False


class ClaudeProvider(BaseProvider):
    def __init__(
        self,
        settings_path: Path,
        hook_command_prefix: str,
        socket_path: Path,
        legacy_hook_script_paths: tuple[Path, ...] = (),
        projects_dir: Path | None = None,
        recent_window_seconds: int = 86_400,
    ) -> None:
        self.settings_path = settings_path
        self.hook_command_prefix = hook_command_prefix
        self.socket_path = socket_path
        self.legacy_hook_script_paths = legacy_hook_script_paths
        self.projects_dir = projects_dir or Path.home() / ".claude" / "projects"
        self.recent_window_seconds = recent_window_seconds

    @property
    def name(self) -> str:
        return "claude"

    def install_hooks(self) -> None:
        payload = self._load_settings()
        hooks = payload.setdefault("hooks", {})
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

    def load_sessions(self) -> list[AgentSession]:
        if not self.projects_dir.exists():
            return []

        now_ts = current_timestamp()
        sessions: list[AgentSession] = []

        # Claude projects are typically directories like: ~/.claude/projects/home-user-project/
        # Each contains session files like <session_id>.jsonl
        for session_file in self.projects_dir.glob("*/*.jsonl"):
            try:
                # Read last line to get most recent state
                lines = session_file.read_text(encoding="utf-8").strip().splitlines()
                if not lines:
                    continue
                last_event = json.loads(lines[-1])
            except (OSError, json.JSONDecodeError, IndexError):
                continue

            session_id = session_file.stem
            updated_at = _session_timestamp_to_seconds(last_event.get("timestamp"), now_ts)

            if updated_at < now_ts - self.recent_window_seconds:
                continue

            # Project dir name is a slug of the path, e.g., home-user-project
            # We can't perfectly recover CWD from it, but the last_event usually has it
            cwd = str(last_event.get("cwd", ""))
            
            # Prefer the latest user prompt for the session title.
            first_user_prompt: str | None = None
            last_user_prompt: str | None = None
            for line in lines:
                try:
                    ev = json.loads(line)
                    if ev.get("type") == "user":
                        msg = ev.get("message", {})
                        if isinstance(msg, dict):
                            text = _content_to_text(msg.get("content"))[:50].strip()
                            if text:
                                if first_user_prompt is None:
                                    first_user_prompt = text
                                last_user_prompt = text
                except (json.JSONDecodeError, TypeError):
                    continue
            title = last_user_prompt or session_id[:8]

            sessions.append(
                AgentSession(
                    provider=self.name,
                    session_id=session_id,
                    cwd=cwd,
                    title=title,
                    phase=self._map_phase(str(last_event.get("status", "completed"))),
                    model=str(last_event.get("model")) if last_event.get("model") else None,
                    sandbox=str(last_event.get("sandbox")) if last_event.get("sandbox") else None,
                    approval_mode=str(last_event.get("approval_mode")) if last_event.get("approval_mode") else None,
                    updated_at=updated_at,
                    origin=SessionOrigin.RESTORED,
                    is_hook_managed=True,
                    is_process_alive=True, # Initially assume alive for matching
                    claude_metadata=ClaudeSessionMetadata(
                        transcript_path=str(session_file),
                        initial_user_prompt=first_user_prompt,
                        last_user_prompt=last_user_prompt,
                        last_assistant_message=_last_assistant_text(lines),
                        permission_mode=str(last_event.get("approval_mode")) if last_event.get("approval_mode") else None,
                        model=str(last_event.get("model")) if last_event.get("model") else None,
                    ),
                )
            )

        return sessions

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
        return f"{self.hook_command_prefix} claude {event_name}"

    def _managed_commands(self, event_name: str) -> set[str]:
        commands = {self._managed_command(event_name)}
        commands.update(_legacy_script_command(path, event_name) for path in self.legacy_hook_script_paths)
        return commands

    def _merge_hook_entries(self, existing: object, event_name: str) -> list[object]:
        entries = self._prune_managed_hook_entries(existing, event_name)
        new_entry = {
            "matcher": "*" if event_name not in {"Stop", "SessionStart", "SessionEnd"} else None,
            "hooks": [
                {
                    "type": "command",
                    "command": self._managed_command(event_name),
                }
            ],
        }
        if new_entry["matcher"] is None:
            new_entry.pop("matcher")
        entries.append(new_entry)
        return entries

    def _prune_managed_hook_entries(self, existing: object, event_name: str) -> list[object]:
        entries = list(existing) if isinstance(existing, list) else []
        commands = self._managed_commands(event_name)
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
                        hook.get("command") in commands
                        or _looks_like_managed_module_command(
                            hook.get("command"),
                            "claude",
                            event_name,
                        )
                        or _looks_like_managed_legacy_command(
                            hook.get("command"),
                            event_name,
                            "claude-hook.py",
                        )
                    )
                )
            ]
            if filtered_hooks:
                updated_entry = dict(entry)
                updated_entry["hooks"] = filtered_hooks
                pruned_entries.append(updated_entry)
        return pruned_entries

    def load_transcript(self, session_id: str, cwd: str = "", **kwargs: Any) -> list[dict[str, str]]:
        transcript_path = self._transcript_path(session_id, cwd)
        if transcript_path is None or not transcript_path.exists():
            return []

        turns: list[dict[str, str]] = []
        with transcript_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                turn = self._transcript_turn_from_event(payload)
                if turn is not None:
                    turns.append(turn)
        return turns

    def _transcript_path(self, session_id: str, cwd: str) -> Path | None:
        if not session_id:
            return None
        if cwd:
            direct = self.projects_dir / _claude_project_dir_name(cwd) / f"{session_id}.jsonl"
            if direct.exists():
                return direct
        matches = list(self.projects_dir.glob(f"*/{session_id}.jsonl"))
        return matches[0] if matches else None

    def _transcript_turn_from_event(self, payload: dict[str, Any]) -> dict[str, str] | None:
        kind = str(payload.get("type", ""))
        if kind not in {"user", "assistant"}:
            return None
        message = payload.get("message")
        role = kind
        content: object = payload.get("text", "")
        if isinstance(message, dict):
            role = str(message.get("role", role))
            content = message.get("content", content)
        if role not in {"user", "assistant"}:
            return None
        text = _content_to_text(content)
        if not text:
            return None
        return {
            "role": role,
            "text": text,
            "timestamp": str(payload.get("timestamp", "")),
        }

    def get_process_signatures(self) -> dict[str, list[str]]:
        return {
            "commands": ["claude", "claude-code"],
            "arg_patterns": [],
        }

    def build_event(
        self,
        hook_name: str,
        payload: dict[str, Any],
        pid: int | None = None,
        tty: str | None = None,
    ) -> dict[str, Any]:
        now_ts = current_timestamp()
        status = payload.get("status")
        if not status:
            mapping = {
                "UserPromptSubmit": "processing",
                "PreToolUse": "processing",
                "PostToolUse": "processing",
                "PermissionRequest": "waiting_for_approval",
                "Notification": "waiting_for_input",
                "Stop": "waiting_for_input",
                "SessionStart": "waiting_for_input",
                "SessionEnd": "ended",
                "PreCompact": "processing",
            }
            status = mapping.get(hook_name, "completed")
        phase_map = {
            "processing": "running",
            "running_tool": "running",
            "waiting_for_approval": "waiting_approval",
            "waiting_for_input": "waiting_answer",
            "ended": "completed",
            "notification": "waiting_answer",
        }
        event_type = "activity_updated"
        title = ""
        if hook_name == "SessionStart":
            event_type = "session_started"
            title = fallback_session_title(payload)
        elif hook_name == "PermissionRequest":
            event_type = "permission_requested"
        elif hook_name == "SessionEnd":
            event_type = "session_completed"
        elif hook_name == "Stop":
            event_type = "session_completed"
        elif hook_name == "Notification":
            event_type = "question_asked"
        elif hook_name == "UserPromptSubmit":
            title = extract_prompt_title(payload)
        permission_request = _permission_request_from_payload(payload) if event_type == "permission_requested" else None
        question_prompt = _question_prompt_from_payload(payload) if event_type == "question_asked" else None
        claude_metadata = _claude_metadata_from_payload(payload)
        return {
            "event_type": event_type,
            "event_source": hook_name,
            "provider": self.name,
            "session_id": payload.get("session_id", "unknown"),
            "cwd": payload.get("cwd", ""),
            "title": title,
            "phase": phase_map.get(str(status), "completed"),
            "model": payload.get("model"),
            "updated_at": now_ts,
            "started_at": now_ts if hook_name == "UserPromptSubmit" else None,
            "origin": "live",
            "is_hook_managed": True,
            "pid": pid if pid is not None else payload.get("pid"),
            "tty": tty if tty is not None else payload.get("tty"),
            "is_session_end": hook_name == "SessionEnd",
            "summary": payload.get("message", ""),
            "last_message_preview": payload.get("message", ""),
            "permission_request": permission_request,
            "question_prompt": question_prompt,
            "metadata_kind": "claude",
            "claude_metadata": claude_metadata,
        }

    def session_from_event(self, payload: dict[str, object]) -> AgentSession:
        cwd = str(payload.get("cwd", ""))
        title = Path(cwd).name or str(payload.get("session_id", ""))
        return AgentSession(
            provider="claude",
            session_id=str(payload["session_id"]),
            cwd=cwd,
            title=title,
            phase=self._map_phase(str(payload.get("status", payload.get("phase", "completed")))),
            model=str(payload["model"]) if payload.get("model") is not None else None,
            sandbox=str(payload["sandbox"]) if payload.get("sandbox") is not None else None,
            approval_mode=str(payload["approval_mode"]) if payload.get("approval_mode") is not None else None,
            updated_at=int(payload.get("updated_at", current_timestamp())),
            is_hook_managed=True,
            last_message_preview=str(payload.get("last_message_preview", "")),
        )

    def _map_phase(self, status: str) -> SessionPhase:
        mapping = {
            "processing": SessionPhase.RUNNING,
            "running_tool": SessionPhase.RUNNING,
            "compacting": SessionPhase.RUNNING,
            "waiting_for_approval": SessionPhase.WAITING_APPROVAL,
            "waiting_for_input": SessionPhase.WAITING_ANSWER,
            "notification": SessionPhase.WAITING_ANSWER,
            "ended": SessionPhase.COMPLETED,
        }
        return mapping.get(status, SessionPhase.COMPLETED)


def _claude_project_dir_name(cwd: str) -> str:
    stripped = cwd.strip()
    if not stripped or stripped == "/":
        return "-"
    return stripped.replace("/", "-")


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


def _permission_request_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    summary = str(payload.get("message", "")).strip()
    tool_name = payload.get("tool_name") or payload.get("tool") or payload.get("permission_tool_name")
    affected_path = payload.get("affected_path") or payload.get("path") or payload.get("target_path") or ""
    title = "Permission required"
    if tool_name:
        title = f"Permission required for {tool_name}"
    return {
        "title": title,
        "summary": summary or title,
        "affected_path": str(affected_path),
        "primary_action_title": "Allow",
        "secondary_action_title": "Deny",
        "tool_name": str(tool_name) if tool_name is not None else None,
    }


def _question_prompt_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    title = str(payload.get("message", "")).strip() or "Input required"
    raw_options = payload.get("options", [])
    options: list[dict[str, str]] = []
    if isinstance(raw_options, list):
        for item in raw_options:
            if isinstance(item, dict):
                label = str(item.get("label", "")).strip()
                description = str(item.get("description", "")).strip()
            else:
                label = str(item).strip()
                description = ""
            if label:
                options.append({"label": label, "description": description})
    return {
        "title": title,
        "options": options,
    }


def _claude_metadata_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    event_name = str(payload.get("event", ""))
    last_user_prompt = payload.get("last_user_message")
    if last_user_prompt is None and event_name == "UserPromptSubmit":
        last_user_prompt = payload.get("message")
    return {
        "transcript_path": payload.get("transcript_path") or payload.get("agent_transcript_path"),
        "initial_user_prompt": payload.get("initial_user_prompt"),
        "last_user_prompt": last_user_prompt,
        "last_assistant_message": payload.get("assistant_message") or payload.get("message"),
        "current_tool": payload.get("tool_name") or payload.get("tool"),
        "current_tool_input_preview": payload.get("tool_input") or payload.get("tool_input_preview"),
        "permission_mode": payload.get("approval_mode"),
        "model": payload.get("model"),
    }


def _last_assistant_text(lines: list[str]) -> str | None:
    for line in reversed(lines):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if payload.get("type") != "assistant":
            continue
        message = payload.get("message", {})
        if isinstance(message, dict):
            text = _content_to_text(message.get("content"))
            if text:
                return text
    return None


def _session_timestamp_to_seconds(timestamp: object, fallback: int) -> int:
    if timestamp is None:
        return fallback
    if isinstance(timestamp, (int, float)):
        return int(timestamp / 1000) if timestamp > 10_000_000_000 else int(timestamp)
    if isinstance(timestamp, str):
        stripped = timestamp.strip()
        if not stripped:
            return fallback
        try:
            numeric = float(stripped)
        except ValueError:
            try:
                parsed = datetime.fromisoformat(stripped.replace("Z", "+00:00"))
            except ValueError:
                return fallback
            return int(parsed.timestamp())
        return int(numeric / 1000) if numeric > 10_000_000_000 else int(numeric)
    return fallback
