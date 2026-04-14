from __future__ import annotations

import json
import shlex
import sqlite3
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from ..core.models import AgentSession, SessionOrigin, SessionPhase
from .base import BaseProvider
from .codex_rollout import CodexRolloutWatcher, _snapshot_from_rollout
from .utils import (
    current_timestamp,
    extract_prompt_title,
    fallback_session_title,
    get_process_metadata,
)


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


class CodexProvider(BaseProvider):
    REQUIRED_HOOK_EVENTS = ("SessionStart", "UserPromptSubmit", "Stop")
    LEGACY_MANAGED_HOOK_EVENTS = ("PreToolUse", "PostToolUse")

    def __init__(
        self,
        state_db_path: Path,
        history_path: Path,
        hooks_config_path: Path,
        hook_command_prefix: str | None = None,
        hook_script_path: Path | None = None,
        managed_hook_script_paths: tuple[Path, ...] = (),
        recent_window_seconds: int = 86_400,
    ) -> None:
        self.state_db_path = state_db_path
        self.history_path = history_path
        self.hooks_config_path = hooks_config_path
        self.hook_command_prefix = hook_command_prefix
        self.hook_script_path = hook_script_path
        self.managed_hook_script_paths = tuple(
            path for path in (hook_script_path, *managed_hook_script_paths) if path is not None
        )
        self.recent_window_seconds = recent_window_seconds
        self.rollout_watcher = CodexRolloutWatcher()

    @property
    def name(self) -> str:
        return "codex"

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

    def load_transcript(self, session_id: str, cwd: str = "", **kwargs: Any) -> list[dict[str, str]]:
        rollout_path_str = self._get_rollout_path(session_id)
        if rollout_path_str:
            rollout_path = Path(rollout_path_str)
            if rollout_path.exists():
                return self._load_transcript_from_file(rollout_path)

        if self.history_path.is_file():
            return self._load_transcript_from_history_file(session_id)

        return []

    def _get_rollout_path(self, session_id: str) -> str | None:
        if not self.state_db_path.exists():
            return None
        try:
            with sqlite3.connect(self.state_db_path) as conn:
                cursor = conn.execute("SELECT rollout_path FROM threads WHERE id = ?", (session_id,))
                row = cursor.fetchone()
                return row[0] if row else None
        except sqlite3.Error:
            return None

    def _load_transcript_from_file(self, path: Path) -> list[dict[str, str]]:
        turns: list[dict[str, str]] = []
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if payload.get("type") == "response_item":
                        data = payload.get("payload", {})
                        if data.get("type") == "message":
                            role = data.get("role")
                            content = data.get("content")
                            text = self._extract_text_from_content(content)
                            if role and text:
                                turns.append({
                                    "role": role,
                                    "text": text,
                                    "timestamp": str(payload.get("timestamp", ""))
                                })
        except OSError:
            pass
        return turns

    def _load_transcript_from_history_file(self, session_id: str) -> list[dict[str, str]]:
        turns: list[dict[str, str]] = []
        try:
            with self.history_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if payload.get("session_id") == session_id:
                        text = str(payload.get("text", "")).strip()
                        if text:
                            turns.append({
                                "role": "user",
                                "text": text,
                                "timestamp": str(payload.get("ts", ""))
                            })
        except OSError:
            pass
        return turns

    def _extract_text_from_content(self, content: Any) -> str:
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text") or item.get("output_text") or item.get("input_text")
                    if text:
                        parts.append(str(text).strip())
            return "\n".join(parts).strip()
        return ""

    def get_process_signatures(self) -> dict[str, list[str]]:
        return {
            "commands": ["codex"],
            "arg_patterns": [],
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
        phase = "running"
        title = ""
        if hook_name == "Stop":
            event_type = "session_completed"
            phase = "completed"
        elif hook_name == "SessionStart":
            event_type = "session_started"
            title = fallback_session_title(payload)
        elif hook_name == "UserPromptSubmit":
            title = extract_prompt_title(payload)
        return {
            "event_type": event_type,
            "event_source": hook_name,
            "provider": self.name,
            "session_id": payload.get("session_id", "unknown"),
            "cwd": payload.get("cwd", ""),
            "title": title,
            "phase": phase,
            "model": payload.get("model"),
            "updated_at": now_ts,
            "started_at": now_ts if hook_name == "UserPromptSubmit" else None,
            "origin": "live",
            "is_hook_managed": True,
            "pid": pid,
            "tty": tty,
            "summary": payload.get("last_assistant_message", ""),
            "last_message_preview": payload.get("last_assistant_message", ""),
        }

    def poll_events(self, sessions: list[AgentSession]):
        codex_sessions = [session for session in sessions if session.provider == self.name]
        rollout_paths = {
            session.session_id: path
            for session in codex_sessions
            if (path := self._get_rollout_path(session.session_id))
        }
        return self.rollout_watcher.poll(codex_sessions, rollout_paths)

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
                        "codex-hook.py",
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
                        "codex-hook.py",
                    )
                ):
                    continue
                filtered_hooks.append(hook)
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

    def _looks_like_managed_legacy_command(self, command: object, event_name: str, script_name: str) -> bool:
        if not isinstance(command, str):
            return False
        if not command.endswith(f"{script_name} {event_name}"):
            return False
        return "linux-agent-island" in command or f".codex/{script_name}" in command

    def load_sessions(self, now: int | None = None) -> list[AgentSession]:
        db_sessions = self._load_from_db(now)
        history_sessions = self._load_from_history()

        # Merge by session_id, preferring DB sessions for more metadata
        sessions_dict = {s.session_id: s for s in history_sessions}
        for db_s in db_sessions:
            if db_s.session_id in sessions_dict:
                hist_s = sessions_dict[db_s.session_id]
                # Merge: use DB metadata but keep history's preview if DB's is empty
                if not db_s.last_message_preview and hist_s.last_message_preview:
                    db_s = replace(db_s, last_message_preview=hist_s.last_message_preview)
            sessions_dict[db_s.session_id] = db_s

        # FILTER: Ignore subagent sessions
        filtered_sessions = []
        for s in sessions_dict.values():
            if self.is_subagent_session(s.session_id):
                continue
            filtered_sessions.append(s)

        return filtered_sessions

    def _load_from_db(self, now: int | None = None) -> list[AgentSession]:
        if not self.state_db_path.exists():
            return []
        now_ts = now if now is not None else int(time.time())
        try:
            with sqlite3.connect(self.state_db_path) as conn:
                conn.row_factory = sqlite3.Row
                # Robust query: check if transcript table exists
                has_transcript = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='transcript'"
                ).fetchone()

                # Check for 'model' column
                cols = [c[1] for c in conn.execute("PRAGMA table_info(threads)").fetchall()]
                has_model_col = "model" in cols

                if has_transcript:
                    query = f"""
                        SELECT
                            id, rollout_path, created_at, updated_at, source, model_provider, cwd, title,
                            sandbox_policy, approval_mode,
                            {'model,' if has_model_col else ''}
                            (SELECT message FROM transcript WHERE thread_id = threads.id AND role = 'assistant' ORDER BY id DESC LIMIT 1) as last_assistant_message
                        FROM threads
                        WHERE updated_at > ?
                        ORDER BY updated_at DESC
                    """
                else:
                    query = f"""
                        SELECT
                            id, rollout_path, created_at, updated_at, source, model_provider, cwd, title,
                            sandbox_policy, approval_mode,
                            {'model,' if has_model_col else ''}
                            NULL as last_assistant_message
                        FROM threads
                        WHERE updated_at > ?
                        ORDER BY updated_at DESC
                    """

                cursor = conn.execute(query, (now_ts - self.recent_window_seconds,))
                rows = cursor.fetchall()
        except sqlite3.Error:
            return []

        sessions: list[AgentSession] = []
        for row in rows:
            codex_metadata = None
            title_from_rollout: str | None = None
            rollout_path = row["rollout_path"]
            if rollout_path:
                snapshot = _snapshot_from_rollout(Path(rollout_path))
                codex_metadata = snapshot.metadata if snapshot is not None else None
                if snapshot is not None:
                    title_from_rollout = snapshot.metadata.last_user_prompt
            sessions.append(
                AgentSession(
                    provider="codex",
                    session_id=row["id"],
                    cwd=row["cwd"] or "",
                    title=title_from_rollout or row["title"] or row["cwd"] or row["id"],
                    phase=SessionPhase.COMPLETED,
                    model=row["model"] if has_model_col else row["model_provider"],
                    sandbox=row["sandbox_policy"],
                    approval_mode=row["approval_mode"],
                    updated_at=row["updated_at"],
                    origin=SessionOrigin.RESTORED,
                    last_message_preview=row["last_assistant_message"] or "",
                    codex_metadata=codex_metadata,
                )
            )
        return sessions

    def _load_from_history(self) -> list[AgentSession]:
        if not self.history_path.is_file():
            return []
        sessions: dict[str, AgentSession] = {}
        try:
            with self.history_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    sid = payload.get("session_id")
                    if not sid:
                        continue
                    ts = int(payload.get("ts", 0))
                    text = str(payload.get("text", ""))
                    if sid not in sessions or ts > sessions[sid].updated_at:
                        sessions[sid] = AgentSession(
                            provider="codex",
                            session_id=sid,
                            cwd="",
                            title=sid,
                            phase=SessionPhase.COMPLETED,
                            model=None,
                            sandbox=None,
                            approval_mode=None,
                            updated_at=ts,
                            origin=SessionOrigin.RESTORED,
                            last_message_preview=text,
                        )
        except OSError:
            pass
        return list(sessions.values())

    def filter_cached_sessions(self, cached_sessions: list[AgentSession]) -> list[AgentSession]:
        if not self.state_db_path.exists():
            return []
        try:
            with sqlite3.connect(self.state_db_path) as conn:
                cursor = conn.execute("SELECT id, source FROM threads")
                db_data = {row[0]: row[1] for row in cursor.fetchall()}

        except sqlite3.Error:
            return []

        filtered = []
        for s in cached_sessions:
            if s.session_id not in db_data:
                continue
            if is_codex_subagent_source(db_data[s.session_id]):
                continue
            filtered.append(s)
        return filtered

    def is_subagent_session(self, session_id: str) -> bool:
        if not self.state_db_path.exists():
            return False
        try:
            with sqlite3.connect(self.state_db_path) as conn:
                cursor = conn.execute("SELECT source FROM threads WHERE id = ?", (session_id,))
                row = cursor.fetchone()
                if row:
                    return is_codex_subagent_source(row[0])
        except sqlite3.Error:
            pass
        return False
