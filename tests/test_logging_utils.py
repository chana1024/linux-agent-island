import logging
from pathlib import Path

from linux_agent_island.core.logging import configure_logging, normalize_log_level


def test_normalize_log_level_accepts_case_insensitive_values() -> None:
    assert normalize_log_level("debug") == "DEBUG"


def test_normalize_log_level_defaults_invalid_values() -> None:
    assert normalize_log_level("verbose") == "INFO"
    assert normalize_log_level(None) == "INFO"


def test_configure_logging_sets_root_level() -> None:
    configured = configure_logging("warning")

    assert configured == "WARNING"
    assert logging.getLogger().getEffectiveLevel() == logging.WARNING


def test_configure_logging_writes_to_file_when_configured(tmp_path: Path) -> None:
    log_path = tmp_path / "logs" / "app.log"

    configured = configure_logging("info", log_file_path=log_path)
    logging.getLogger("linux_agent_island.test").info("file logging works")
    for handler in logging.getLogger().handlers:
        handler.flush()

    assert configured == "INFO"
    assert log_path.exists()
    assert "file logging works" in log_path.read_text(encoding="utf-8")
