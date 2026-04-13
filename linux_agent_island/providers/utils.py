from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path


def load_stdin_json() -> dict[str, object]:
    try:
        return json.loads(sys.stdin.read())
    except json.JSONDecodeError:
        return {}


def normalize_tty(tty: str | None) -> str | None:
    if not tty:
        return None
    tty = tty.strip()
    if not tty or tty in {"??", "-"}:
        return None
    if not tty.startswith("/dev/"):
        tty = "/dev/" + tty
    return tty


def detect_tty_from_streams() -> str | None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            return normalize_tty(os.ttyname(stream.fileno()))
        except (OSError, AttributeError):
            continue
    return None


def get_process_metadata() -> tuple[int, str | None]:
    parent_pid = os.getppid()
    try:
        result = subprocess.run(
            ["ps", "-p", str(parent_pid), "-o", "tty="],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        tty = normalize_tty(result.stdout)
    except OSError:
        tty = None

    if tty is None:
        tty = detect_tty_from_streams()

    return parent_pid, tty


def fallback_session_title(payload: dict[str, object]) -> str:
    return Path(str(payload.get("cwd", ""))).name or str(payload.get("session_id", "unknown"))


def extract_prompt_title(payload: dict[str, object]) -> str:
    for key in ("prompt", "text", "message", "input", "last_user_message"):
        value = payload.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def current_timestamp() -> int:
    return int(time.time())
