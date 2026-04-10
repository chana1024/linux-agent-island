from __future__ import annotations

import json
import shlex
import time
from pathlib import Path
from typing import Any

from ..core.models import AgentSession, SessionPhase


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


class ClaudeProvider:
    def __init__(
        self,
        settings_path: Path,
        hook_command_prefix: str,
        socket_path: Path,
        legacy_hook_script_paths: tuple[Path, ...] = (),
        projects_dir: Path | None = None,
    ) -> None:
        self.settings_path = settings_path
        self.hook_command_prefix = hook_command_prefix
        self.socket_path = socket_path
        self.legacy_hook_script_paths = legacy_hook_script_paths
        self.projects_dir = projects_dir or Path.home() / ".claude" / "projects"

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
        entries = list(existing) if isinstance(existing, list) else []
        entries = self._prune_managed_hook_entries(entries, event_name)
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

    def load_transcript(self, session_id: str, cwd: str = "") -> list[dict[str, str]]:
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

    def session_from_event(self, payload: dict[str, object]) -> AgentSession:
        cwd = str(payload.get("cwd", ""))
        title = Path(cwd).name or str(payload.get("session_id", ""))
        return AgentSession(
            provider="claude",
            session_id=str(payload["session_id"]),
            cwd=cwd,
            title=title,
            phase=self._map_phase(str(payload.get("status", payload.get("phase", "idle")))),
            model=str(payload["model"]) if payload.get("model") is not None else None,
            sandbox=str(payload["sandbox"]) if payload.get("sandbox") is not None else None,
            approval_mode=str(payload["approval_mode"]) if payload.get("approval_mode") is not None else None,
            updated_at=int(payload.get("updated_at", time.time())),
            last_message_preview=str(payload.get("last_message_preview", "")),
        )

    def _map_phase(self, status: str) -> SessionPhase:
        mapping = {
            "processing": SessionPhase.RUNNING,
            "running_tool": SessionPhase.RUNNING,
            "compacting": SessionPhase.RUNNING,
            "waiting_for_approval": SessionPhase.WAITING_APPROVAL,
            "waiting_for_input": SessionPhase.WAITING,
            "notification": SessionPhase.WAITING,
            "ended": SessionPhase.COMPLETED,
        }
        return mapping.get(status, SessionPhase.IDLE)


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
