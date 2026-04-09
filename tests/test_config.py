import json
from pathlib import Path

from linux_agent_shell.config import FrontendSettings, load_frontend_settings


def test_load_frontend_settings_reads_top_bar_gap(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps({"top_bar_gap": 14}), encoding="utf-8")

    settings = load_frontend_settings(settings_path)

    assert settings == FrontendSettings(top_bar_gap=14)


def test_load_frontend_settings_defaults_when_file_missing(tmp_path: Path) -> None:
    settings = load_frontend_settings(tmp_path / "missing.json")

    assert settings == FrontendSettings(top_bar_gap=8)


def test_load_frontend_settings_clamps_invalid_gap(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps({"top_bar_gap": -5}), encoding="utf-8")

    settings = load_frontend_settings(settings_path)

    assert settings == FrontendSettings(top_bar_gap=0)
