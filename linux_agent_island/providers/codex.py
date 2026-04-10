from __future__ import annotations

import json
import shlex
import sqlite3
import time
from pathlib import Path
from typing import Any

from ..core.models import AgentSession, SessionOrigin, SessionPhase


def is_codex_subagent_source(source: object) -> bool:
    if not isinstance(source, str):
        return False
    source = source.strip()
    if not source.startswith("{"):
        return False
    try:
        payload = json.loads(source)
    except json.JSONDecodeError:
        return False
    return isinstance(payload, dict) and isinstance(payload.get("subagent"), dict)


class CodexProvider:
    REQUIRED_HOOK_EVENTS = ("SessionStart", "UserPromptSubmit", "Stop")
    LEGACY_MANAGED_HOOK_EVENTS = ("PreToolUse", "PostToolUse")

    def __init__(
        self,
        state_db_path: Path,
        history_path: Path,
        hooks_config_path: Path,
        hook_command_prefix: str | None = None,
        hook_script_path: Path | None = None,
        hook_script_source_path: Path | None = None,
        managed_hook_script_paths: tuple[Path, ...] = (),
        recent_window_seconds: int = 86_400,
    ) -> None:
        self.state_db_path = state_db_path
        self.history_path = history_path
        self.hooks_config_path = hooks_config_path
        self.hook_command_prefix = hook_command_prefix
        self.hook_script_path = hook_script_path
        self.hook_script_source_path = hook_script_source_path
        self.managed_hook_script_paths = tuple(
            path for path in (hook_script_path, *managed_hook_script_paths) if path is not None
        )
        self.recent_window_seconds = recent_window_seconds

    def install_hooks(self) -> None:
        payload: dict[str, object] = {}
        if self.hooks_config_path.exists():
            try:
                loaded = json.loads(self.hooks_config_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                loaded = {}
            if isinstance(loaded, dict):
                payload = loaded
        hooks_obj = payload.get("hooks")
        if not isinstance(hooks_obj, dict):
            hooks_obj = {}
            payload["hooks"] = hooks_obj

        for event in self.REQUIRED_HOOK_EVENTS:
            hooks_obj[event] = self._merge_hook_entries(hooks_obj.get(event, []), event)

        for event in self.LEGACY_MANAGED_HOOK_EVENTS:
            if event in hooks_obj:
                pruned_entries = self._prune_managed_hook_entries(hooks_obj.get(event, []), event)
                if pruned_entries:
                    hooks_obj[event] = pruned_entries
                else:
                    del hooks_obj[event]

        self.hooks_config_path.parent.mkdir(parents=True, exist_ok=True)
        self.hooks_config_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def uninstall_hooks(self) -> None:
        if not self.hooks_config_path.exists():
            return
        try:
            payload = json.loads(self.hooks_config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return
        if not isinstance(payload, dict):
            return
        hooks_obj = payload.get("hooks")
        if not isinstance(hooks_obj, dict):
            return
        for event in (*self.REQUIRED_HOOK_EVENTS, *self.LEGACY_MANAGED_HOOK_EVENTS):
            if event not in hooks_obj:
                continue
            pruned_entries = self._prune_managed_hook_entries(hooks_obj.get(event, []), event)
            if pruned_entries:
                hooks_obj[event] = pruned_entries
            else:
                del hooks_obj[event]
        self.hooks_config_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def _install_hook_script(self) -> None:
        return

    def _managed_command(self, event_name: str) -> str:
        if self.hook_command_prefix is not None:
            return f"{self.hook_command_prefix} codex {event_name}"
        if self.hook_script_path is None:
            raise ValueError("hook_command_prefix or hook_script_path is required")
        return f"/usr/bin/python3 {self.hook_script_path} {event_name}"

    def _managed_commands(self, event_name: str) -> set[str]:
        commands = {
            f"/usr/bin/python3 {hook_script_path} {event_name}"
            for hook_script_path in self.managed_hook_script_paths
        }
        if self.hook_command_prefix is not None:
            commands.add(self._managed_command(event_name))
        return commands

    def _managed_hook(self, event_name: str) -> dict[str, object]:
        return {
            "type": "command",
            "command": self._managed_command(event_name),
            "timeout": 10,
        }

    def _merge_hook_entries(self, existing: object, event_name: str) -> list[object]:
        entries = list(existing) if isinstance(existing, list) else []
        commands = self._managed_commands(event_name)
        merged_entries: list[object] = []
        for entry in entries:
            if not isinstance(entry, dict):
                merged_entries.append(entry)
                continue
            hooks = entry.get("hooks")
            if not isinstance(hooks, list):
                merged_entries.append(dict(entry))
                continue
            filtered_hooks: list[object] = []
            for hook in hooks:
                if not isinstance(hook, dict):
                    filtered_hooks.append(hook)
                    continue
                if (
                    hook.get("command") in commands
                    or self._looks_like_managed_module_command(hook.get("command"), event_name)
                    or self._looks_like_managed_legacy_command(
                        hook.get("command"),
                        event_name,
                    )
                ):
                    continue
                filtered_hooks.append(hook)
            if filtered_hooks:
                updated_entry = dict(entry)
                updated_entry["hooks"] = filtered_hooks
                merged_entries.append(updated_entry)
        merged_entries.append({"hooks": [self._managed_hook(event_name)]})
        return merged_entries

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
                        or self._looks_like_managed_module_command(hook.get("command"), event_name)
                        or self._looks_like_managed_legacy_command(hook.get("command"), event_name)
                    )
                )
            ]
            if filtered_hooks:
                updated_entry = dict(entry)
                updated_entry["hooks"] = filtered_hooks
                pruned_entries.append(updated_entry)
        return pruned_entries

    def _looks_like_managed_module_command(self, command: object, event_name: str) -> bool:
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
            return tokens[index + 2 :] == ["codex", event_name]
        return False

    def _looks_like_managed_legacy_command(self, command: object, event_name: str) -> bool:
        if not isinstance(command, str):
            return False
        if not command.endswith(f"codex-hook.py {event_name}"):
            return False
        return "linux-agent-island" in command or ".codex/hook/codex-hook.py" in command

    def load_sessions(self, now: int | None = None) -> list[AgentSession]:
        now_ts = now if now is not None else int(time.time())
        history = self._load_last_history_messages()
        if not self.state_db_path.exists():
            return []
        conn = sqlite3.connect(self.state_db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, cwd, title, updated_at, approval_mode, sandbox_policy, model, archived, source
            FROM threads
            WHERE archived = 0
            ORDER BY updated_at DESC
            """
        ).fetchall()
        conn.close()
        sessions: list[AgentSession] = []
        for row in rows:
            if is_codex_subagent_source(row["source"]):
                continue
            updated_at = int(row["updated_at"])
            if now_ts - updated_at > self.recent_window_seconds:
                continue
            sessions.append(
                AgentSession(
                    provider="codex",
                    session_id=str(row["id"]),
                    cwd=str(row["cwd"]),
                    title=str(row["title"]),
                    phase=SessionPhase.COMPLETED,
                    model=str(row["model"]) if row["model"] else None,
                    sandbox=str(row["sandbox_policy"]) if row["sandbox_policy"] else None,
                    approval_mode=str(row["approval_mode"]) if row["approval_mode"] else None,
                    updated_at=updated_at,
                    origin=SessionOrigin.RESTORED,
                    is_process_alive=True,
                    last_message_preview=history.get(str(row["id"]), ""),
                )
            )
        return sessions

    def filter_cached_sessions(self, sessions: list[AgentSession]) -> list[AgentSession]:
        if not sessions:
            return []
        if not self.state_db_path.exists():
            return sessions

        conn = sqlite3.connect(self.state_db_path)
        conn.row_factory = sqlite3.Row
        try:
            sources_by_id = {
                str(row["id"]): row["source"]
                for row in conn.execute("SELECT id, source FROM threads")
            }
        finally:
            conn.close()

        filtered: list[AgentSession] = []
        for session in sessions:
            source = sources_by_id.get(session.session_id)
            if source is None or is_codex_subagent_source(source):
                continue
            filtered.append(session)
        return filtered

    def is_subagent_session(self, session_id: str) -> bool:
        if not session_id or not self.state_db_path.exists():
            return False
        conn = sqlite3.connect(self.state_db_path)
        try:
            row = conn.execute(
                "SELECT source FROM threads WHERE id = ?",
                (session_id,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return False
        return is_codex_subagent_source(row[0])

    def load_transcript(self, session_id: str) -> list[dict[str, str]]:
        rollout_path = self._rollout_path_for_session(session_id)
        if rollout_path is None or not rollout_path.exists():
            return []

        turns: list[dict[str, str]] = []
        with rollout_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                turn = self._transcript_turn_from_rollout_event(payload)
                if turn is not None:
                    turns.append(turn)
        return turns

    def _rollout_path_for_session(self, session_id: str) -> Path | None:
        if not session_id or not self.state_db_path.exists():
            return None
        conn = sqlite3.connect(self.state_db_path)
        try:
            row = conn.execute(
                "SELECT rollout_path FROM threads WHERE id = ?",
                (session_id,),
            ).fetchone()
        finally:
            conn.close()
        if row is None or not row[0]:
            return None
        return Path(str(row[0])).expanduser()

    def _transcript_turn_from_rollout_event(self, event: dict[str, Any]) -> dict[str, str] | None:
        if event.get("type") != "response_item":
            return None
        payload = event.get("payload")
        if not isinstance(payload, dict) or payload.get("type") != "message":
            return None
        role = str(payload.get("role", ""))
        if role not in {"user", "assistant"}:
            return None
        text = _content_to_text(payload.get("content"))
        if not text:
            return None
        return {
            "role": role,
            "text": text,
            "timestamp": str(event.get("timestamp", "")),
        }

    def _load_last_history_messages(self) -> dict[str, str]:
        messages: dict[str, str] = {}
        if not self.history_path.exists():
            return messages
        with self.history_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                session_id = str(payload.get("session_id", ""))
                text = str(payload.get("text", "")).strip()
                if session_id and text:
                    messages[session_id] = text
        return messages


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
