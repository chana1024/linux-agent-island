from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


DEFAULT_LOG_LEVEL = "INFO"
LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
LOG_MAX_BYTES = 1_000_000
LOG_BACKUP_COUNT = 3
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


def configure_logging(raw_level: str | None, *, log_file_path: Path | None = None) -> str:
    level_name = normalize_log_level(raw_level)
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file_path is not None:
        log_file_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(
            RotatingFileHandler(
                log_file_path,
                maxBytes=LOG_MAX_BYTES,
                backupCount=LOG_BACKUP_COUNT,
                encoding="utf-8",
            )
        )
    logging.basicConfig(
        level=VALID_LOG_LEVELS[level_name],
        format=LOG_FORMAT,
        force=True,
        handlers=handlers,
    )
    return level_name
