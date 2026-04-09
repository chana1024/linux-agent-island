from __future__ import annotations

import logging


DEFAULT_LOG_LEVEL = "INFO"
LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
VALID_LOG_LEVELS = {
    "CRITICAL": logging.CRITICAL,
    "ERROR": logging.ERROR,
    "WARNING": logging.WARNING,
    "INFO": logging.INFO,
    "DEBUG": logging.DEBUG,
}


def normalize_log_level(raw_level: str | None) -> str:
    if raw_level is None:
        return DEFAULT_LOG_LEVEL
    level = raw_level.strip().upper()
    return level if level in VALID_LOG_LEVELS else DEFAULT_LOG_LEVEL


def configure_logging(raw_level: str | None) -> str:
    level_name = normalize_log_level(raw_level)
    logging.basicConfig(level=VALID_LOG_LEVELS[level_name], format=LOG_FORMAT, force=True)
    return level_name
