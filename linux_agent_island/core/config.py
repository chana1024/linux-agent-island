from __future__ import annotations

import json
import os
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path


LOG_LEVELS = ("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG")
DEFAULT_TOP_BAR_GAP = 8
DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_START_ON_LOGIN = True


@dataclass(slots=True)
class FrontendSettings:
    top_bar_gap: int = DEFAULT_TOP_BAR_GAP
    log_level: str = DEFAULT_LOG_LEVEL
    start_on_login: bool = DEFAULT_START_ON_LOGIN


def load_frontend_settings(settings_path: Path) -> FrontendSettings:
    if not settings_path.exists():
        return FrontendSettings()

    try:
        payload = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return FrontendSettings()

    raw_gap = payload.get("top_bar_gap", DEFAULT_TOP_BAR_GAP)
    try:
        top_bar_gap = max(0, int(raw_gap))
    except (TypeError, ValueError):
        top_bar_gap = DEFAULT_TOP_BAR_GAP

    raw_log_level = str(payload.get("log_level", DEFAULT_LOG_LEVEL)).upper()
    log_level = raw_log_level if raw_log_level in LOG_LEVELS else DEFAULT_LOG_LEVEL
    start_on_login = payload.get("start_on_login", DEFAULT_START_ON_LOGIN)
    if not isinstance(start_on_login, bool):
        start_on_login = DEFAULT_START_ON_LOGIN
    return FrontendSettings(
        top_bar_gap=top_bar_gap,
        log_level=log_level,
        start_on_login=start_on_login,
    )


def save_frontend_settings(settings_path: Path, settings: FrontendSettings) -> None:
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(
            {
                "top_bar_gap": settings.top_bar_gap,
                "log_level": settings.log_level,
                "start_on_login": settings.start_on_login,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


@dataclass(slots=True)
class AppConfig:
    root: Path
    runtime_dir: Path
    session_cache_path: Path
    frontend_settings_path: Path
    claude_settings_path: Path
    codex_state_db_path: Path
    codex_history_path: Path
    codex_hooks_path: Path
    claude_hook_script_path: Path
    codex_hook_script_path: Path
    codex_hook_script_source_path: Path
    event_socket_path: Path
    hook_command_prefix: str = ""
    dbus_name: str = "com.lzn.LinuxAgentIsland"
    dbus_path: str = "/com/lzn/LinuxAgentIsland"
    frontend_application_id: str = "com.lzn.LinuxAgentIsland.Frontend"
    service_name: str = "linux-agent-island.service"

    @classmethod
    def default(cls, root: Path | None = None) -> "AppConfig":
        project_root = root or Path(__file__).resolve().parents[2]
        runtime_dir = Path.home() / ".local" / "state" / "linux-agent-island"
        config_dir = Path.home() / ".config" / "linux-agent-island"
        hook_command_prefix = os.environ.get("LINUX_AGENT_ISLAND_HOOK_COMMAND_PREFIX")
        if not hook_command_prefix:
            hook_command_prefix = f"{shlex.quote(sys.executable)} -m linux_agent_island.hooks"
        return cls(
            root=project_root,
            runtime_dir=runtime_dir,
            session_cache_path=runtime_dir / "sessions.json",
            frontend_settings_path=config_dir / "settings.json",
            claude_settings_path=Path.home() / ".claude" / "settings.json",
            codex_state_db_path=Path.home() / ".codex" / "state_5.sqlite",
            codex_history_path=Path.home() / ".codex" / "history.jsonl",
            codex_hooks_path=Path.home() / ".codex" / "hooks.json",
            claude_hook_script_path=project_root / "bin" / "claude-hook.py",
            codex_hook_script_path=Path.home() / ".codex" / "hook" / "codex-hook.py",
            codex_hook_script_source_path=project_root / "bin" / "codex-hook.py",
            event_socket_path=runtime_dir / "events.sock",
            hook_command_prefix=hook_command_prefix,
        )
