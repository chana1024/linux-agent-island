from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any


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


class GeminiProvider:
    def __init__(
        self,
        settings_path: Path,
        tmp_dir: Path,
        hook_command_prefix: str,
    ) -> None:
        self.settings_path = settings_path
        self.tmp_dir = tmp_dir
        self.hook_command_prefix = hook_command_prefix

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

    def load_transcript(self, session_id: str) -> list[dict[str, str]]:
        transcript_path = self._transcript_path(session_id)
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

    def _transcript_path(self, session_id: str) -> Path | None:
        if not session_id or not self.tmp_dir.exists():
            return None
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
