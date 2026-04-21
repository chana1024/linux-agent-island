from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import logging
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .codex_accounts import CodexAccountService
from .core.config import AppConfig, load_frontend_settings
from .core.logging import configure_logging
from .providers import get_all_providers


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
    # GApplication path is usually the application ID with dots replaced by slashes
    obj_path = "/" + config.frontend_application_id.replace(".", "/")
    
    for _attempt in range(20):
        # Try direct D-Bus call first (more robust for already running processes)
        result = subprocess.run(
            [
                "gdbus", "call", "--session",
                "--dest", config.frontend_application_id,
                "--object-path", obj_path,
                "--method", "org.gtk.Actions.Activate",
                action_name, "[]", "{}"
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return 0
            
        # Fallback to gapplication
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
    for provider in get_all_providers(config):
        provider.install_hooks()


def _uninstall_hooks(config: AppConfig) -> None:
    for provider in get_all_providers(config):
        provider.uninstall_hooks()


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

    frontend = subprocess.Popen(
        [sys.executable, "-m", "linux_agent_island.frontend", "--log-level", log_level],
        env=env,
    )
    children.append(frontend)

    hotkeys = subprocess.Popen(
        [sys.executable, "-m", "linux_agent_island.app.hotkeys", "--log-level", log_level],
        env=env,
    )
    children.append(hotkeys)

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
            if hotkeys.poll() is not None:
                return hotkeys.returncode or 0
            time.sleep(0.2)
    finally:
        stop_children()


def _run_frontend_action_starting_service(action_name: str) -> int:
    config = AppConfig.default()
    started = _start_service(config)
    if started != 0:
        return started
    return _run_application_action(config, action_name)


def open_app(_args: argparse.Namespace) -> int:
    return _run_frontend_action_starting_service("show-island")


def open_settings(_args: argparse.Namespace) -> int:
    return _run_frontend_action_starting_service("open-settings")


def toggle_app(_args: argparse.Namespace) -> int:
    return _run_frontend_action_starting_service("toggle-island-focus")


def highlight_selected(_args: argparse.Namespace) -> int:
    return _run_frontend_action_starting_service("toggle-highlight-selected")


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


def _build_codex_account_service() -> CodexAccountService:
    config = AppConfig.default()
    settings_path = getattr(config, "frontend_settings_path", None)
    settings = load_frontend_settings(settings_path) if settings_path is not None else load_frontend_settings(Path("/nonexistent"))
    return CodexAccountService(
        auth_path=config.codex_auth_path,
        accounts_dir=config.codex_accounts_dir,
        manifest_path=config.codex_accounts_manifest_path,
        configured_codex_bin=settings.codex_bin_path,
    )


def codex_login(args: argparse.Namespace) -> int:
    service = _build_codex_account_service()
    try:
        success = service.run_device_login(args.label)
    except ValueError as exc:
        sys.stderr.write(f"{exc}\n")
        return 2
    except Exception as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    return 0 if success else 3


def codex_status(_args: argparse.Namespace) -> int:
    status = _build_codex_account_service().get_status()
    print(f"logged_in: {'yes' if status.logged_in else 'no'}")
    print(f"auth_mode: {status.auth_mode or '-'}")
    print(f"current_account_label: {status.current_account_label or '-'}")
    print(f"current_account_no: {_account_number_for_id(status.accounts, status.current_account_id)}")
    print(f"current_account_id: {status.current_account_id or '-'}")
    print(f"current_account_managed: {'yes' if status.current_account_managed else 'no'}")
    print(f"device_login_in_progress: {'yes' if status.device_login_in_progress else 'no'}")
    print(f"has_running_codex_sessions: {'yes' if status.has_running_codex_sessions else 'no'}")
    print(f"account_count: {len(status.accounts)}")
    if status.accounts:
        print("accounts:")
        for index, account in enumerate(status.accounts, start=1):
            flags: list[str] = []
            if account.is_default:
                flags.append("default")
            if account.is_active:
                flags.append("active")
            flag_suffix = f" [{' '.join(flags)}]" if flags else ""
            print(f"  {index}\t{account.account_id}\t{account.label}{flag_suffix}")
    return 0


def _remaining_percent(used_percent: float | None) -> float | str:
    if used_percent is None:
        return "-"
    return max(0.0, min(100.0, 100.0 - used_percent))


def _human_timestamp(unix_timestamp: int | None) -> str:
    if unix_timestamp is None:
        return "-"
    return datetime.fromtimestamp(unix_timestamp, tz=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def _human_duration_until(unix_timestamp: int | None) -> str:
    if unix_timestamp is None:
        return "-"
    remaining_seconds = max(0, int(unix_timestamp - time.time()))
    days, rem = divmod(remaining_seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _seconds = divmod(rem, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if not parts:
        return "<1m"
    return " ".join(parts)


def _human_datetime(iso_timestamp: str | None) -> str:
    if not iso_timestamp:
        return "-"
    try:
        parsed = datetime.fromisoformat(iso_timestamp)
    except ValueError:
        return iso_timestamp
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def _title_case_plan(plan_type: str | None) -> str:
    if not plan_type:
        return "-"
    return plan_type.replace("_", " ").title()


def _usage_account_label(usage: object) -> str:
    for field in ("label", "email", "account_id"):
        value = getattr(usage, field, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "Current account"


def _account_number_for_id(accounts: list[object], account_id: object) -> str:
    if not isinstance(account_id, str) or not account_id.strip():
        return "-"
    for index, account in enumerate(accounts, start=1):
        if getattr(account, "account_id", None) == account_id:
            return str(index)
    return "-"


def _percent_text(value: float | str) -> str:
    if value == "-":
        return "-"
    return f"{value}%"


def _print_usage_block(usage: object, *, header: str | None = None, account_number: str = "-") -> None:
    five_hour_reset = getattr(usage, "five_hour_resets_at", None)
    weekly_reset = getattr(usage, "weekly_resets_at", None)
    if header:
        print(header)
    print(f"Codex account : {_usage_account_label(usage)}")
    print(f"Account no    : {account_number}")
    print(f"Plan          : {_title_case_plan(getattr(usage, 'plan_type', None))}")
    print(f"Expires       : {_human_datetime(getattr(usage, 'subscription_active_until', None))}")
    print(f"5h left       : {_percent_text(_remaining_percent(getattr(usage, 'five_hour_used_percent', None)))}")
    print(f"5h resets     : {_human_timestamp(five_hour_reset)} (in {_human_duration_until(five_hour_reset)})")
    print(f"Week left     : {_percent_text(_remaining_percent(getattr(usage, 'weekly_used_percent', None)))}")
    print(f"Week resets   : {_human_timestamp(weekly_reset)} (in {_human_duration_until(weekly_reset)})")



def _print_usage_table(accounts_with_usage: list[tuple[object, object]]) -> None:
    rows: list[list[str]] = []
    for index, (account, usage) in enumerate(accounts_with_usage, start=1):
        expires = _human_datetime(getattr(usage, "subscription_active_until", None))
        if expires != "-":
            expires = expires[:16]
        rows.append(
            [
                str(index),
                _usage_account_label(usage),
                "yes" if bool(getattr(account, "is_active", False)) else "",
                _title_case_plan(getattr(usage, "plan_type", None)),
                expires,
                _percent_text(_remaining_percent(getattr(usage, "five_hour_used_percent", None))),
                _human_duration_until(getattr(usage, "five_hour_resets_at", None)),
                _percent_text(_remaining_percent(getattr(usage, "weekly_used_percent", None))),
                _human_duration_until(getattr(usage, "weekly_resets_at", None)),
            ]
        )

    headers = ["No", "Account", "Active", "Plan", "Expires", "5h Left", "5h Reset In", "Week Left", "Week Reset In"]
    widths = [len(header) for header in headers]
    for row in rows:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(value))

    print("  ".join(header.ljust(widths[index]) for index, header in enumerate(headers)))
    for row in rows:
        print("  ".join(value.ljust(widths[index]) for index, value in enumerate(row)).rstrip())



def codex_usage(args: argparse.Namespace) -> int:
    service = _build_codex_account_service()
    try:
        account_selector = (getattr(args, "account", None) or "").strip()
        if getattr(args, "all_accounts", False) and account_selector:
            raise ValueError("cannot combine --all with an account selector")
        if getattr(args, "all_accounts", False):
            accounts = service.list_accounts()
            if not accounts:
                usage = service.get_usage_info(None)
                _print_usage_block(usage)
                return 0
            if len(accounts) == 1:
                usages = [service.get_usage_info(accounts[0].account_id)]
            else:
                max_workers = min(8, len(accounts))
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    usages = list(executor.map(lambda account: service.get_usage_info(account.account_id), accounts))
            accounts_with_usage = list(zip(accounts, usages))
            _print_usage_table(accounts_with_usage)
            return 0
        usage = service.get_usage_info(account_selector or None)
        accounts = service.list_accounts()
    except ValueError as exc:
        sys.stderr.write(f"{exc}\n")
        return 2
    _print_usage_block(usage, account_number=_account_number_for_id(accounts, getattr(usage, "account_id", None)))
    return 0


def codex_sync_auth(args: argparse.Namespace) -> int:
    service = _build_codex_account_service()
    selector = (getattr(args, "account", None) or getattr(args, "email", None) or "").strip()
    try:
        result = service.sync_credentials(selector or None)
    except ValueError as exc:
        sys.stderr.write(f"{exc}\n")
        return 2
    except Exception as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    account_text = result.account_email or result.account_label or "current account"
    print(f"synced account: {account_text}")
    print(f"openclaw_targets: {len(result.openclaw_paths)}")
    for path in result.openclaw_paths:
        print(f"openclaw: {path}")
    print(f"openclaw_runtime: {result.openclaw_reload_status}")
    if result.openclaw_reload_message:
        print(f"openclaw_runtime_detail: {result.openclaw_reload_message}")
    print(f"hermes: {result.hermes_auth_path}")
    return 0


def codex_accounts_list(_args: argparse.Namespace) -> int:
    accounts = _build_codex_account_service().list_accounts()
    if not accounts:
        print("No managed Codex accounts.")
        return 0
    for index, account in enumerate(accounts, start=1):
        flags: list[str] = []
        if account.is_default:
            flags.append("default")
        if account.is_active:
            flags.append("active")
        flag_suffix = f" [{' '.join(flags)}]" if flags else ""
        print(f"{index}\t{account.account_id}\t{account.label}{flag_suffix}")
    return 0


def codex_accounts_switch(args: argparse.Namespace) -> int:
    service = _build_codex_account_service()
    selector = getattr(args, "account", None) or getattr(args, "account_id", None)
    try:
        status = service.switch_account(selector)
    except ValueError as exc:
        sys.stderr.write(f"{exc}\n")
        return 2
    print(f"current_account_no: {_account_number_for_id(getattr(status, 'accounts', []), status.current_account_id)}")
    print(f"current_account_id: {status.current_account_id or '-'}")
    print(f"current_account_label: {status.current_account_label or '-'}")
    return 0


def codex_accounts_rename(args: argparse.Namespace) -> int:
    service = _build_codex_account_service()
    try:
        service.rename_account(args.account_id, args.label)
    except ValueError as exc:
        sys.stderr.write(f"{exc}\n")
        return 2
    print(f"renamed {args.account_id} -> {args.label}")
    return 0


def codex_accounts_delete(args: argparse.Namespace) -> int:
    service = _build_codex_account_service()
    try:
        service.delete_account(args.account_id)
    except ValueError as exc:
        sys.stderr.write(f"{exc}\n")
        return 2
    print(f"deleted {args.account_id}")
    return 0


def codex_accounts_set_default(args: argparse.Namespace) -> int:
    service = _build_codex_account_service()
    try:
        service.set_default_account(args.account_id)
    except ValueError as exc:
        sys.stderr.write(f"{exc}\n")
        return 2
    print(f"default account: {args.account_id}")
    return 0


def codex_accounts_import_current(args: argparse.Namespace) -> int:
    service = _build_codex_account_service()
    try:
        imported = service.import_current_auth(args.label)
    except ValueError as exc:
        sys.stderr.write(f"{exc}\n")
        return 2
    print(f"imported {imported.account_id}\t{imported.label}")
    return 0


def _configure_codex_login_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--label", default="", help="Optional label for the new account")
    parser.set_defaults(func=codex_login)


def _configure_codex_accounts_subcommands(
    codex_subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    accounts_parser = codex_subparsers.add_parser("accounts", help="Manage multiple Codex accounts")
    accounts_subparsers = accounts_parser.add_subparsers(dest="codex_accounts_command", required=True)

    accounts_list_parser = accounts_subparsers.add_parser("list", help="List managed Codex accounts")
    accounts_list_parser.set_defaults(func=codex_accounts_list)

    accounts_switch_parser = accounts_subparsers.add_parser("switch", help="Switch the active Codex account")
    accounts_switch_parser.add_argument("account", help="Account number, ID, label, or email to switch to")
    accounts_switch_parser.set_defaults(func=codex_accounts_switch)

    accounts_rename_parser = accounts_subparsers.add_parser("rename", help="Rename a managed Codex account")
    accounts_rename_parser.add_argument("account_id", help="Current account ID")
    accounts_rename_parser.add_argument("label", help="New label for the account")
    accounts_rename_parser.set_defaults(func=codex_accounts_rename)

    accounts_delete_parser = accounts_subparsers.add_parser("delete", help="Delete a managed Codex account")
    accounts_delete_parser.add_argument("account_id", help="Account ID to delete")
    accounts_delete_parser.set_defaults(func=codex_accounts_delete)

    accounts_default_parser = accounts_subparsers.add_parser("set-default", help="Set the default Codex account")
    accounts_default_parser.add_argument("account_id", help="Account ID to set as default")
    accounts_default_parser.set_defaults(func=codex_accounts_set_default)

    accounts_import_current_parser = accounts_subparsers.add_parser(
        "import-current", help="Import the current Codex auth from ~/.codex/auth.json"
    )
    accounts_import_current_parser.add_argument("--label", default="", help="Label for the imported account")
    accounts_import_current_parser.set_defaults(func=codex_accounts_import_current)


def _add_codex_subcommands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    codex_parser = subparsers.add_parser("codex", help="Codex CLI integration commands")
    codex_subparsers = codex_parser.add_subparsers(dest="codex_command", required=True)

    codex_login_parser = codex_subparsers.add_parser("login", help="Start a new Codex login flow")
    _configure_codex_login_parser(codex_login_parser)

    codex_status_parser = codex_subparsers.add_parser("status", help="Show current Codex authentication status")
    codex_status_parser.set_defaults(func=codex_status)

    codex_usage_parser = codex_subparsers.add_parser("usage", help="Show usage and quota information for Codex accounts")
    codex_usage_parser.add_argument(
        "account",
        nargs="?",
        help="Account number, ID, label, or email to inspect",
    )
    codex_usage_parser.add_argument(
        "--all", dest="all_accounts", action="store_true", help="Show usage for all managed accounts"
    )
    codex_usage_parser.set_defaults(func=codex_usage)

    codex_sync_parser = codex_subparsers.add_parser(
        "sync-auth",
        help="Sync Codex OAuth credentials to OpenClaw and Hermes",
    )
    codex_sync_parser.add_argument(
        "account",
        nargs="?",
        help="Account number, ID, label, or email to sync",
    )
    codex_sync_parser.add_argument("--email", default="", help="Sync a managed account selected by email")
    codex_sync_parser.set_defaults(func=codex_sync_auth)

    _configure_codex_accounts_subcommands(codex_subparsers)

    # Legacy flat alias kept for compatibility while the canonical command becomes
    # `linux-agent-island codex <subcommand>`.
    legacy_codex_login_parser = subparsers.add_parser(
        "codex-login", help="Legacy alias for 'codex login' (deprecated)"
    )
    _configure_codex_login_parser(legacy_codex_login_parser)
    legacy_codex_login_parser.set_defaults(func=codex_login, _legacy_codex_login_alias=True)


def main(argv: list[str] | None = None) -> int:
    prog = Path(sys.argv[0]).name if argv is None and sys.argv else "linux-agent-island"
    parser = argparse.ArgumentParser(prog=prog)
    subparsers = parser.add_subparsers(dest="command", required=True)

    daemon_parser = subparsers.add_parser("daemon", help="Start the background daemon service")
    daemon_parser.add_argument("--log-level", help="Set logging level (DEBUG, INFO, WARNING, ERROR)")
    daemon_parser.set_defaults(func=daemon)

    open_parser = subparsers.add_parser("open", help="Show the floating island UI")
    open_parser.set_defaults(func=open_app)

    toggle_parser = subparsers.add_parser("toggle", help="Toggle island focus or restore the previous window")
    toggle_parser.set_defaults(func=toggle_app)

    settings_parser = subparsers.add_parser("settings", help="Open the settings window")
    settings_parser.set_defaults(func=open_settings)

    highlight_parser = subparsers.add_parser(
        "highlight-selected",
        help="Toggle highlight on the currently selected session",
    )
    highlight_parser.set_defaults(func=highlight_selected)

    status_parser = subparsers.add_parser("status", help="Show the current service and D-Bus status")
    status_parser.set_defaults(func=status)

    install_hooks_parser = subparsers.add_parser("install-hooks", help="Install event hooks for supported agents")
    install_hooks_parser.set_defaults(func=install_hooks)

    uninstall_hooks_parser = subparsers.add_parser("uninstall-hooks", help="Uninstall managed agent event hooks")
    uninstall_hooks_parser.set_defaults(func=uninstall_hooks)

    _add_codex_subcommands(subparsers)

    args = parser.parse_args(argv)
    if getattr(args, "_legacy_codex_login_alias", False):
        sys.stderr.write(
            "warning: `linux-agent-island codex-login` is deprecated; use `linux-agent-island codex login` instead\n"
        )
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
