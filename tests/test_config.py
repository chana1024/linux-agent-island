import json
from pathlib import Path

from linux_agent_island.core.config import (
    AppConfig,
    FrontendSettings,
    load_frontend_settings,
    save_frontend_settings,
)


def test_default_config_uses_codex_hook_copy_under_home(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / "project"
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)

    config = AppConfig.default(root=project_root)

    assert config.codex_hook_script_path == home / ".codex" / "hook" / "codex-hook.py"
    assert config.codex_hook_script_source_path == project_root / "bin" / "codex-hook.py"
    assert config.hook_command_prefix.endswith(" -m linux_agent_island.hooks")


def test_load_frontend_settings_reads_top_bar_gap(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps({"top_bar_gap": 14}), encoding="utf-8")

    settings = load_frontend_settings(settings_path)

    assert settings == FrontendSettings(top_bar_gap=14, log_level="INFO", start_on_login=True)


def test_load_frontend_settings_defaults_when_file_missing(tmp_path: Path) -> None:
    settings = load_frontend_settings(tmp_path / "missing.json")

    assert settings == FrontendSettings(top_bar_gap=8)


def test_load_frontend_settings_clamps_invalid_gap(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps({"top_bar_gap": -5}), encoding="utf-8")

    settings = load_frontend_settings(settings_path)

    assert settings == FrontendSettings(top_bar_gap=0)


def test_load_frontend_settings_reads_log_level_and_autostart(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        json.dumps({"top_bar_gap": 14, "log_level": "debug", "start_on_login": False}),
        encoding="utf-8",
    )

    settings = load_frontend_settings(settings_path)

    assert settings == FrontendSettings(top_bar_gap=14, log_level="DEBUG", start_on_login=False)


def test_save_frontend_settings_writes_supported_fields(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"

    save_frontend_settings(settings_path, FrontendSettings(top_bar_gap=3, log_level="ERROR", start_on_login=False))

    assert json.loads(settings_path.read_text(encoding="utf-8")) == {
        "log_level": "ERROR",
        "start_on_login": False,
        "top_bar_gap": 3,
    }
