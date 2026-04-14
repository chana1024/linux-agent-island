from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from .core.config import AppConfig, load_frontend_settings
from .core.logging import configure_logging
from .providers import get_provider
from .providers.utils import (
    current_timestamp,
    detect_tty_from_streams,
    extract_prompt_title,
    fallback_session_title,
    get_process_metadata,
    load_stdin_json,
    normalize_tty,
)
from .runtime.events import emit_runtime_event

logger = logging.getLogger(__name__)


# Compatibility wrappers for tests
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


def _load_stdin_json() -> dict[str, object]:
    return load_stdin_json()


def _normalize_tty(tty: str | None) -> str | None:
    return normalize_tty(tty)


def _detect_tty_from_streams() -> str | None:
    return detect_tty_from_streams()


def _fallback_session_title(payload: dict[str, object]) -> str:
    return fallback_session_title(payload)


def _extract_prompt_title(payload: dict[str, object]) -> str:
    return extract_prompt_title(payload)


def _build_codex_event(hook_name: str, payload: dict[str, object]) -> dict[str, object]:
    config = AppConfig.default()
    provider = get_provider("codex", config)
    pid, tty = _get_process_metadata()
    return provider.build_event(hook_name, payload, pid=pid, tty=tty) if provider else {}


def _build_gemini_event(hook_name: str, payload: dict[str, object]) -> dict[str, object]:
    config = AppConfig.default()
    provider = get_provider("gemini", config)
    pid, tty = _get_process_metadata()
    return provider.build_event(hook_name, payload, pid=pid, tty=tty) if provider else {}


def _build_claude_event(hook_name: str, payload: dict[str, object]) -> dict[str, object]:
    config = AppConfig.default()
    provider = get_provider("claude", config)
    return provider.build_event(hook_name, payload) if provider else {}


def _is_codex_subagent_session(state_db_path: Path, session_id: str) -> bool:
    from .providers.codex import CodexProvider
    # Minimal provider for subagent check
    provider = CodexProvider(
        state_db_path=state_db_path,
        history_path=Path(),
        hooks_config_path=Path(),
    )
    return provider.is_subagent_session(session_id)


def _configure_hook_logging(config: AppConfig) -> str:
    settings = load_frontend_settings(config.frontend_settings_path)
    return configure_logging(
        settings.log_level,
        log_file_path=config.runtime_dir / "logs" / "hooks.log",
    )


def main() -> int:
    if len(sys.argv) < 3:
        return 1
    provider_name = sys.argv[1]
    hook_name = sys.argv[2]
    payload = _load_stdin_json()
    config = AppConfig.default()
    _configure_hook_logging(config)
    logger.info(
        "hook triggered provider=%s hook=%s session_id=%s cwd=%s",
        provider_name,
        hook_name,
        payload.get("session_id"),
        payload.get("cwd"),
    )

    provider = get_provider(provider_name, config)
    if not provider:
        logger.warning("hook ignored because provider is unavailable provider=%s hook=%s", provider_name, hook_name)
        return 1

    # Codex specific subagent check
    if provider_name == "codex":
        session_id = str(payload.get("session_id", ""))
        if _is_codex_subagent_session(config.codex_state_db_path, session_id):
            logger.info(
                "hook skipped for codex subagent provider=%s hook=%s session_id=%s",
                provider_name,
                hook_name,
                session_id,
            )
            if hook_name == "Stop":
                print(json.dumps({"continue": True}))
            return 0

    event = provider.build_event(hook_name, payload)
    emit_runtime_event(config.event_socket_path, event)
    logger.info(
        "hook emitted runtime event provider=%s hook=%s session_id=%s event_type=%s phase=%s",
        provider_name,
        hook_name,
        event.get("session_id"),
        event.get("event_type"),
        event.get("phase"),
    )

    # Provider specific responses
    if provider_name == "codex" and hook_name == "Stop":
        print(json.dumps({"continue": True}))
    elif provider_name == "gemini":
        print(json.dumps({"suppressOutput": True}))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
