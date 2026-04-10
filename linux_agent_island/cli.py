from __future__ import annotations

import argparse
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from .core.config import AppConfig, load_frontend_settings
from .core.logging import configure_logging
from .providers import ClaudeProvider, CodexProvider


logger = logging.getLogger(__name__)


def _run_systemctl(args: list[str], check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["systemctl", "--user", *args],
        capture_output=True,
        text=True,
        check=check,
    )


def _service_is_active(config: AppConfig) -> bool:
    result = _run_systemctl(["is-active", "--quiet", config.service_name])
    return result.returncode == 0


def _start_service(config: AppConfig) -> int:
    if _service_is_active(config):
        return 0
    result = _run_systemctl(["start", config.service_name])
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
    return result.returncode


def _run_application_action(config: AppConfig, action_name: str) -> int:
    for _attempt in range(20):
        result = subprocess.run(
            ["gapplication", "action", config.frontend_application_id, action_name],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return 0
        time.sleep(0.15)
    sys.stderr.write(result.stderr)
    return result.returncode


def _install_hooks(config: AppConfig) -> None:
    claude = ClaudeProvider(
        settings_path=config.claude_settings_path,
        hook_command_prefix=config.hook_command_prefix,
        socket_path=config.event_socket_path,
        legacy_hook_script_paths=(config.claude_hook_script_path,),
    )
    codex = CodexProvider(
        state_db_path=config.codex_state_db_path,
        history_path=config.codex_history_path,
        hooks_config_path=config.codex_hooks_path,
        hook_command_prefix=config.hook_command_prefix,
        hook_script_path=config.codex_hook_script_path,
        hook_script_source_path=config.codex_hook_script_source_path,
        managed_hook_script_paths=(config.codex_hook_script_source_path,),
    )
    claude.install_hooks()
    codex.install_hooks()


def _uninstall_hooks(config: AppConfig) -> None:
    claude = ClaudeProvider(
        settings_path=config.claude_settings_path,
        hook_command_prefix=config.hook_command_prefix,
        socket_path=config.event_socket_path,
        legacy_hook_script_paths=(config.claude_hook_script_path,),
    )
    codex = CodexProvider(
        state_db_path=config.codex_state_db_path,
        history_path=config.codex_history_path,
        hooks_config_path=config.codex_hooks_path,
        hook_command_prefix=config.hook_command_prefix,
        hook_script_path=config.codex_hook_script_path,
        hook_script_source_path=config.codex_hook_script_source_path,
        managed_hook_script_paths=(config.codex_hook_script_source_path,),
    )
    claude.uninstall_hooks()
    codex.uninstall_hooks()


def daemon(args: argparse.Namespace) -> int:
    config = AppConfig.default()
    settings = load_frontend_settings(config.frontend_settings_path)
    log_level = args.log_level or settings.log_level
    log_file_path = config.runtime_dir / "logs" / "daemon.log"
    configure_logging(log_level, log_file_path=log_file_path)
    logger.info("daemon starting")

    env = os.environ.copy()
    children: list[subprocess.Popen[object]] = []

    def stop_children() -> None:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                child.kill()

    def handle_stop(*_args: object) -> None:
        stop_children()
        raise SystemExit(0)

    signal.signal(signal.SIGINT, handle_stop)
    signal.signal(signal.SIGTERM, handle_stop)

    backend = subprocess.Popen(
        [sys.executable, "-m", "linux_agent_island.backend", "--log-level", log_level],
        env=env,
    )
    children.append(backend)
    time.sleep(0.5)

    frontend = subprocess.Popen(
        [sys.executable, "-m", "linux_agent_island.frontend", "--log-level", log_level],
        env=env,
    )
    children.append(frontend)

    tray = subprocess.Popen(
        [sys.executable, "-m", "linux_agent_island.app.tray", "--log-level", log_level],
        env=env,
    )
    children.append(tray)

    try:
        while True:
            if backend.poll() is not None:
                return backend.returncode or 0
            if frontend.poll() is not None:
                return frontend.returncode or 0
            time.sleep(0.5)
    finally:
        stop_children()


def open_app(_args: argparse.Namespace) -> int:
    config = AppConfig.default()
    started = _start_service(config)
    if started != 0:
        return started
    return _run_application_action(config, "show-island")


def open_settings(_args: argparse.Namespace) -> int:
    config = AppConfig.default()
    started = _start_service(config)
    if started != 0:
        return started
    return _run_application_action(config, "open-settings")


def status(_args: argparse.Namespace) -> int:
    config = AppConfig.default()
    active = _service_is_active(config)
    print(f"{config.service_name}: {'active' if active else 'inactive'}")
    dbus = subprocess.run(
        [
            "gdbus",
            "call",
            "--session",
            "--dest",
            config.dbus_name,
            "--object-path",
            config.dbus_path,
            "--method",
            f"{config.dbus_name}.ListSessions",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    print(f"{config.dbus_name}: {'available' if dbus.returncode == 0 else 'unavailable'}")
    return 0 if active and dbus.returncode == 0 else 1


def install_hooks(_args: argparse.Namespace) -> int:
    _install_hooks(AppConfig.default())
    return 0


def uninstall_hooks(_args: argparse.Namespace) -> int:
    _uninstall_hooks(AppConfig.default())
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="linux-agent-island")
    subparsers = parser.add_subparsers(dest="command", required=True)

    daemon_parser = subparsers.add_parser("daemon")
    daemon_parser.add_argument("--log-level")
    daemon_parser.set_defaults(func=daemon)

    open_parser = subparsers.add_parser("open")
    open_parser.set_defaults(func=open_app)

    settings_parser = subparsers.add_parser("settings")
    settings_parser.set_defaults(func=open_settings)

    status_parser = subparsers.add_parser("status")
    status_parser.set_defaults(func=status)

    install_hooks_parser = subparsers.add_parser("install-hooks")
    install_hooks_parser.set_defaults(func=install_hooks)

    uninstall_hooks_parser = subparsers.add_parser("uninstall-hooks")
    uninstall_hooks_parser.set_defaults(func=uninstall_hooks)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
