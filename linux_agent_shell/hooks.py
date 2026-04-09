from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from .config import AppConfig
from .providers.codex import CodexProvider
from .runtime.events import emit_runtime_event


def _load_stdin_json() -> dict[str, object]:
    try:
        return json.load(sys.stdin)
    except json.JSONDecodeError:
        return {}


def _normalize_tty(tty: str | None) -> str | None:
    if not tty:
        return None
    tty = tty.strip()
    if not tty or tty in {"??", "-"}:
        return None
    if not tty.startswith("/dev/"):
        tty = "/dev/" + tty
    return tty


def _detect_tty_from_streams() -> str | None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            return _normalize_tty(os.ttyname(stream.fileno()))
        except (OSError, AttributeError):
            continue
    return None


def _fallback_session_title(payload: dict[str, object]) -> str:
    return Path(str(payload.get("cwd", ""))).name or str(payload.get("session_id", "unknown"))


def _extract_prompt_title(payload: dict[str, object]) -> str:
    for key in ("prompt", "text", "message", "input", "last_user_message"):
        value = payload.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _get_process_metadata() -> tuple[int, str | None]:
    parent_pid = os.getppid()
    try:
        result = subprocess.run(
            ["ps", "-p", str(parent_pid), "-o", "tty="],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        tty = _normalize_tty(result.stdout)
    except OSError:
        tty = None

    if tty is None:
        tty = _detect_tty_from_streams()

    return parent_pid, tty


def _build_codex_event(hook_name: str, payload: dict[str, object]) -> dict[str, object]:
    parent_pid, tty = _get_process_metadata()
    event_type = "activity_updated"
    phase = "running"
    title = ""
    if hook_name == "Stop":
        event_type = "session_completed"
        phase = "completed"
    elif hook_name == "SessionStart":
        event_type = "session_started"
        title = _fallback_session_title(payload)
    elif hook_name == "UserPromptSubmit":
        title = _extract_prompt_title(payload)
    return {
        "event_type": event_type,
        "provider": "codex",
        "session_id": payload.get("session_id", "unknown"),
        "cwd": payload.get("cwd", ""),
        "title": title,
        "phase": phase,
        "model": payload.get("model"),
        "updated_at": int(time.time()),
        "origin": "live",
        "is_hook_managed": True,
        "pid": parent_pid,
        "tty": tty,
        "summary": payload.get("last_assistant_message", ""),
        "last_message_preview": payload.get("last_assistant_message", ""),
    }


def _is_codex_subagent_session(state_db_path: Path, session_id: str) -> bool:
    provider = CodexProvider(
        state_db_path=state_db_path,
        history_path=Path(),
        hooks_config_path=Path(),
        hook_script_path=Path(),
    )
    return provider.is_subagent_session(session_id)


def _build_claude_event(hook_name: str, payload: dict[str, object]) -> dict[str, object]:
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
        status = mapping.get(hook_name, "idle")
    phase_map = {
        "processing": "running",
        "running_tool": "running",
        "waiting_for_approval": "waiting_approval",
        "waiting_for_input": "waiting",
        "ended": "completed",
        "notification": "waiting",
    }
    event_type = "activity_updated"
    title = ""
    if hook_name == "SessionStart":
        event_type = "session_started"
        title = _fallback_session_title(payload)
    elif hook_name == "SessionEnd":
        event_type = "session_completed"
    elif hook_name == "Stop":
        event_type = "session_completed"
    elif hook_name == "UserPromptSubmit":
        title = _extract_prompt_title(payload)
    return {
        "event_type": event_type,
        "provider": "claude",
        "session_id": payload.get("session_id", "unknown"),
        "cwd": payload.get("cwd", ""),
        "title": title,
        "phase": phase_map.get(str(status), "idle"),
        "model": payload.get("model"),
        "updated_at": int(time.time()),
        "origin": "live",
        "is_hook_managed": True,
        "pid": payload.get("pid"),
        "tty": payload.get("tty"),
        "is_session_end": hook_name == "SessionEnd",
        "summary": payload.get("message", ""),
        "last_message_preview": payload.get("message", ""),
    }


def main() -> int:
    if len(sys.argv) < 3:
        return 1
    provider = sys.argv[1]
    hook_name = sys.argv[2]
    payload = _load_stdin_json()
    config = AppConfig.default()
    if provider == "codex":
        session_id = str(payload.get("session_id", ""))
        if _is_codex_subagent_session(config.codex_state_db_path, session_id):
            if hook_name == "Stop":
                print(json.dumps({"continue": True}))
            return 0
        event = _build_codex_event(hook_name, payload)
        emit_runtime_event(config.event_socket_path, event)
        if hook_name == "Stop":
            print(json.dumps({"continue": True}))
    elif provider == "claude":
        event = _build_claude_event(hook_name, payload)
        emit_runtime_event(config.event_socket_path, event)
    else:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
