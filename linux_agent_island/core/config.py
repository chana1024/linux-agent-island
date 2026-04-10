from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class FrontendSettings:
    top_bar_gap: int = 8


def load_frontend_settings(settings_path: Path) -> FrontendSettings:
    if not settings_path.exists():
        return FrontendSettings()

    try:
        payload = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return FrontendSettings()

    raw_gap = payload.get("top_bar_gap", FrontendSettings.top_bar_gap)
    try:
        top_bar_gap = max(0, int(raw_gap))
    except (TypeError, ValueError):
        top_bar_gap = FrontendSettings.top_bar_gap
    return FrontendSettings(top_bar_gap=top_bar_gap)


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
    event_socket_path: Path
    dbus_name: str = "com.openclaw.LinuxAgentIsland"
    dbus_path: str = "/com/openclaw/LinuxAgentIsland"

    @classmethod
    def default(cls, root: Path | None = None) -> "AppConfig":
        project_root = root or Path(__file__).resolve().parents[2]
        runtime_dir = Path.home() / ".local" / "state" / "linux-agent-island"
        config_dir = Path.home() / ".config" / "linux-agent-island"
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
            codex_hook_script_path=project_root / "bin" / "codex-hook.py",
            event_socket_path=runtime_dir / "events.sock",
        )
