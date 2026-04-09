import logging

from linux_agent_shell.logging_utils import configure_logging, normalize_log_level


def test_normalize_log_level_accepts_case_insensitive_values() -> None:
    assert normalize_log_level("debug") == "DEBUG"


def test_normalize_log_level_defaults_invalid_values() -> None:
    assert normalize_log_level("verbose") == "INFO"
    assert normalize_log_level(None) == "INFO"


def test_configure_logging_sets_root_level() -> None:
    configured = configure_logging("warning")

    assert configured == "WARNING"
    assert logging.getLogger().getEffectiveLevel() == logging.WARNING
