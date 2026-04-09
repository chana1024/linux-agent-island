from __future__ import annotations

import json
import time
from pathlib import Path

from ..models import AgentSession, SessionPhase


class ClaudeProvider:
    def __init__(self, settings_path: Path, hook_script_path: Path, socket_path: Path) -> None:
        self.settings_path = settings_path
        self.hook_script_path = hook_script_path
        self.socket_path = socket_path

    def install_hooks(self) -> None:
        payload: dict[str, object] = {}
        if self.settings_path.exists():
            payload = json.loads(self.settings_path.read_text(encoding="utf-8"))
        hooks = payload.setdefault("hooks", {})
        for event in [
            "UserPromptSubmit",
            "PreToolUse",
            "PostToolUse",
            "PermissionRequest",
            "Notification",
            "Stop",
            "SessionStart",
            "SessionEnd",
            "PreCompact",
        ]:
            hooks[event] = self._merge_hook_entries(hooks.get(event, []), event)
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def _merge_hook_entries(self, existing: object, event_name: str) -> list[dict[str, object]]:
        entries = list(existing) if isinstance(existing, list) else []
        command = f"/usr/bin/python3 {self.hook_script_path} {event_name}"
        for entry in entries:
            for hook in entry.get("hooks", []):
                if hook.get("command") == command:
                    return entries
        new_entry = {
            "matcher": "*" if event_name not in {"Stop", "SessionStart", "SessionEnd"} else None,
            "hooks": [
                {
                    "type": "command",
                    "command": command,
                }
            ],
        }
        if new_entry["matcher"] is None:
            new_entry.pop("matcher")
        entries.append(new_entry)
        return entries

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
