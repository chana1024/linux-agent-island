from __future__ import annotations

import argparse
import contextlib
import io
from datetime import datetime, timezone
import threading
import time
from types import SimpleNamespace

from linux_agent_island import cli


def test_main_dispatches_nested_codex_login_subcommand(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_codex_login(args: argparse.Namespace) -> int:
        captured["label"] = args.label
        return 7

    monkeypatch.setattr(cli, "codex_login", fake_codex_login)

    result = cli.main(["codex", "login", "--label", "Work"])

    assert result == 7
    assert captured == {"label": "Work"}


def test_main_keeps_legacy_codex_login_alias(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_codex_login(args: argparse.Namespace) -> int:
        captured["label"] = args.label
        return 9

    monkeypatch.setattr(cli, "codex_login", fake_codex_login)

    result = cli.main(["codex-login", "--label", "Work"])

    assert result == 9
    assert captured == {"label": "Work"}


def test_main_warns_when_using_legacy_codex_login_alias(monkeypatch) -> None:
    monkeypatch.setattr(cli, "codex_login", lambda _args: 0)
    stderr = io.StringIO()

    with contextlib.redirect_stderr(stderr):
        result = cli.main(["codex-login", "--label", "Work"])

    assert result == 0
    assert "deprecated" in stderr.getvalue().lower()
    assert "codex login" in stderr.getvalue()


def test_main_dispatches_codex_status_subcommand(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_codex_status(_args: argparse.Namespace) -> int:
        captured["called"] = True
        return 11

    monkeypatch.setattr(cli, "codex_status", fake_codex_status)

    result = cli.main(["codex", "status"])

    assert result == 11
    assert captured == {"called": True}


def test_main_dispatches_codex_usage_subcommand(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_codex_usage(args: argparse.Namespace) -> int:
        captured["all_accounts"] = args.all_accounts
        return 19

    monkeypatch.setattr(cli, "codex_usage", fake_codex_usage)

    result = cli.main(["codex", "usage"])

    assert result == 19
    assert captured == {"all_accounts": False}


def test_main_dispatches_codex_usage_all_subcommand(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_codex_usage(args: argparse.Namespace) -> int:
        captured["all_accounts"] = args.all_accounts
        return 29

    monkeypatch.setattr(cli, "codex_usage", fake_codex_usage)

    result = cli.main(["codex", "usage", "--all"])

    assert result == 29
    assert captured == {"all_accounts": True}


def test_main_dispatches_codex_usage_account_selector(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_codex_usage(args: argparse.Namespace) -> int:
        captured["account"] = args.account
        return 30

    monkeypatch.setattr(cli, "codex_usage", fake_codex_usage)

    result = cli.main(["codex", "usage", "2"])

    assert result == 30
    assert captured == {"account": "2"}


def test_main_dispatches_codex_sync_auth_subcommand(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_codex_sync_auth(args: argparse.Namespace) -> int:
        captured["account"] = args.account
        captured["email"] = args.email
        return 31

    monkeypatch.setattr(cli, "codex_sync_auth", fake_codex_sync_auth)

    result = cli.main(["codex", "sync-auth", "--email", "work@example.com"])

    assert result == 31
    assert captured == {"account": None, "email": "work@example.com"}


def test_main_dispatches_codex_sync_auth_account_selector(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_codex_sync_auth(args: argparse.Namespace) -> int:
        captured["account"] = args.account
        captured["email"] = args.email
        return 32

    monkeypatch.setattr(cli, "codex_sync_auth", fake_codex_sync_auth)

    result = cli.main(["codex", "sync-auth", "2"])

    assert result == 32
    assert captured == {"account": "2", "email": ""}


def test_main_dispatches_codex_accounts_switch_sync_auth_flag(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_codex_accounts_switch(args: argparse.Namespace) -> int:
        captured["account"] = args.account
        captured["sync_auth"] = args.sync_auth
        return 33

    monkeypatch.setattr(cli, "codex_accounts_switch", fake_codex_accounts_switch)

    result = cli.main(["codex", "accounts", "switch", "2", "-s"])

    assert result == 33
    assert captured == {"account": "2", "sync_auth": True}


def test_main_dispatches_toggle_subcommand(monkeypatch) -> None:
    monkeypatch.setattr(cli, "toggle_app", lambda _args: 41)

    result = cli.main(["toggle"])

    assert result == 41


def test_main_dispatches_highlight_selected_subcommand(monkeypatch) -> None:
    monkeypatch.setattr(cli, "highlight_selected", lambda _args: 42)

    result = cli.main(["highlight-selected"])

    assert result == 42


def test_main_dispatches_codex_accounts_rename_subcommand(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_codex_accounts_rename(args: argparse.Namespace) -> int:
        captured["account_id"] = args.account_id
        captured["label"] = args.label
        return 13

    monkeypatch.setattr(cli, "codex_accounts_rename", fake_codex_accounts_rename)

    result = cli.main(["codex", "accounts", "rename", "acct-1", "Work"])

    assert result == 13
    assert captured == {"account_id": "acct-1", "label": "Work"}


def test_main_dispatches_codex_accounts_import_current_subcommand(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_codex_accounts_import_current(args: argparse.Namespace) -> int:
        captured["label"] = args.label
        return 17

    monkeypatch.setattr(cli, "codex_accounts_import_current", fake_codex_accounts_import_current)

    result = cli.main(["codex", "accounts", "import-current", "--label", "Imported"])

    assert result == 17
    assert captured == {"label": "Imported"}


def test_codex_login_returns_special_code_when_login_already_in_progress(monkeypatch) -> None:
    class FakeService:
        def run_device_login(self, _label: str) -> bool:
            raise ValueError("Codex login already in progress")

    monkeypatch.setattr(cli, "AppConfig", SimpleNamespace(default=lambda: SimpleNamespace(
        codex_auth_path="auth",
        codex_accounts_dir="accounts",
        codex_accounts_manifest_path="manifest",
    )))
    monkeypatch.setattr(cli, "CodexAccountService", lambda **_kwargs: FakeService())

    result = cli.codex_login(argparse.Namespace(label="Work"))

    assert result == 2


def test_codex_status_prints_current_account(monkeypatch) -> None:
    class FakeService:
        def get_status(self):
            return SimpleNamespace(
                logged_in=True,
                auth_mode="chatgpt",
                current_account_label="Work",
                current_account_id="acct-1",
                current_account_managed=True,
                device_login_in_progress=False,
                accounts=[
                    SimpleNamespace(account_id="acct-1", label="Work", is_default=True, is_active=True),
                ],
                has_running_codex_sessions=False,
            )

    monkeypatch.setattr(cli, "AppConfig", SimpleNamespace(default=lambda: SimpleNamespace(
        codex_auth_path="auth",
        codex_accounts_dir="accounts",
        codex_accounts_manifest_path="manifest",
    )))
    monkeypatch.setattr(cli, "CodexAccountService", lambda **_kwargs: FakeService())
    stdout = io.StringIO()

    with contextlib.redirect_stdout(stdout):
        result = cli.codex_status(argparse.Namespace())

    assert result == 0
    output = stdout.getvalue()
    assert "logged_in: yes" in output
    assert "current_account_label: Work" in output
    assert "current_account_no: 1" in output
    assert "1\tacct-1\tWork [default active]" in output


def test_codex_usage_prints_pretty_local_output(monkeypatch) -> None:
    class FakeUsage:
        label = "work@example.com"
        account_id = "acct-1"
        plan_type = "plus"
        subscription_active_until = "2026-05-01T00:00:00+00:00"
        five_hour_used_percent = 49.0
        five_hour_resets_at = 1776579494
        weekly_used_percent = 70.0
        weekly_resets_at = 1776997593

    class FakeService:
        def get_usage_info(self, selector: str | None):
            assert selector is None
            return FakeUsage()

        def list_accounts(self):
            return [SimpleNamespace(account_id="acct-1")]

    monkeypatch.setattr(cli, "AppConfig", SimpleNamespace(default=lambda: SimpleNamespace(
        codex_auth_path="auth",
        codex_accounts_dir="accounts",
        codex_accounts_manifest_path="manifest",
    )))
    monkeypatch.setattr(cli, "CodexAccountService", lambda **_kwargs: FakeService())
    monkeypatch.setattr(cli.time, "time", lambda: 1776575894)
    stdout = io.StringIO()
    expires_local = datetime.fromisoformat("2026-05-01T00:00:00+00:00").astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    five_hour_reset = datetime.fromtimestamp(1776579494, tz=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    weekly_reset = datetime.fromtimestamp(1776997593, tz=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")

    with contextlib.redirect_stdout(stdout):
        result = cli.codex_usage(argparse.Namespace(all_accounts=False))

    assert result == 0
    output = stdout.getvalue()
    assert output == (
        "Codex account : work@example.com\n"
        "Account no    : 1\n"
        "Plan          : Plus\n"
        f"Expires       : {expires_local}\n"
        "5h left       : 51.0%\n"
        f"5h resets     : {five_hour_reset} (in 1h)\n"
        "Week left     : 30.0%\n"
        f"Week resets   : {weekly_reset} (in 4d 21h 8m)\n"
    )


def test_codex_usage_all_prints_table_by_default(monkeypatch) -> None:
    class FakeUsage:
        def __init__(self, label: str, plan_type: str, until: str, five_used: float, weekly_used: float) -> None:
            self.label = label
            self.plan_type = plan_type
            self.subscription_active_until = until
            self.five_hour_used_percent = five_used
            self.five_hour_resets_at = 1776579494
            self.weekly_used_percent = weekly_used
            self.weekly_resets_at = 1776997593

    class FakeAccount:
        def __init__(self, account_id: str, label: str, *, is_active: bool = False) -> None:
            self.account_id = account_id
            self.label = label
            self.is_active = is_active

    class FakeService:
        def list_accounts(self):
            return [
                FakeAccount("acct-1", "first@example.com", is_active=False),
                FakeAccount("acct-2", "second@example.com", is_active=True),
            ]

        def get_usage_info(self, selector: str | None):
            assert selector in {"acct-1", "acct-2"}
            if selector == "acct-1":
                return FakeUsage("first@example.com", "plus", "2026-05-01T00:00:00+00:00", 49.0, 70.0)
            return FakeUsage("second@example.com", "team", "2026-06-01T00:00:00+00:00", 10.0, 20.0)

    monkeypatch.setattr(cli, "AppConfig", SimpleNamespace(default=lambda: SimpleNamespace(
        codex_auth_path="auth",
        codex_accounts_dir="accounts",
        codex_accounts_manifest_path="manifest",
    )))
    monkeypatch.setattr(cli, "CodexAccountService", lambda **_kwargs: FakeService())
    monkeypatch.setattr(cli.time, "time", lambda: 1776575894)
    stdout = io.StringIO()
    first_expires = datetime.fromisoformat("2026-05-01T00:00:00+00:00").astimezone().strftime("%Y-%m-%d %H:%M")
    second_expires = datetime.fromisoformat("2026-06-01T00:00:00+00:00").astimezone().strftime("%Y-%m-%d %H:%M")

    with contextlib.redirect_stdout(stdout):
        result = cli.codex_usage(argparse.Namespace(all_accounts=True))

    assert result == 0
    output = stdout.getvalue()
    assert "No" in output and "Account" in output and "5h Reset In" in output and "Week Reset In" in output
    assert "first@example.com" in output and first_expires in output
    assert "51.0%" in output and "30.0%" in output and "4d 21h 8m" in output
    assert "second@example.com" in output and second_expires in output
    assert "90.0%" in output and "80.0%" in output and "yes" in output


def test_codex_usage_all_falls_back_to_current_account_when_no_managed_accounts(monkeypatch) -> None:
    class FakeUsage:
        label = "current@example.com"
        plan_type = "plus"
        subscription_active_until = "2026-06-01T00:00:00+00:00"
        five_hour_used_percent = 20.0
        five_hour_resets_at = None
        weekly_used_percent = 25.0
        weekly_resets_at = None

    class FakeService:
        def list_accounts(self):
            return []

        def get_usage_info(self, selector: str | None):
            assert selector is None
            return FakeUsage()

    monkeypatch.setattr(cli, "AppConfig", SimpleNamespace(default=lambda: SimpleNamespace(
        codex_auth_path="auth",
        codex_accounts_dir="accounts",
        codex_accounts_manifest_path="manifest",
    )))
    monkeypatch.setattr(cli, "CodexAccountService", lambda **_kwargs: FakeService())
    stdout = io.StringIO()

    with contextlib.redirect_stdout(stdout):
        result = cli.codex_usage(argparse.Namespace(all_accounts=True))

    assert result == 0
    output = stdout.getvalue()
    assert "Codex account : current@example.com" in output
    assert "Account no    : -" in output
    assert "Plan          : Plus" in output
    assert "5h left       : 80.0%" in output


def test_codex_usage_all_keeps_printing_when_one_account_fails(monkeypatch) -> None:
    class FakeUsage:
        label = "second@example.com"
        plan_type = "team"
        subscription_active_until = "2026-06-01T00:00:00+00:00"
        five_hour_used_percent = 10.0
        five_hour_resets_at = 1776579494
        weekly_used_percent = 20.0
        weekly_resets_at = 1776997593

    class FakeAccount:
        def __init__(self, account_id: str, label: str, *, is_active: bool = False) -> None:
            self.account_id = account_id
            self.label = label
            self.is_active = is_active

    class FakeService:
        def list_accounts(self):
            return [
                FakeAccount("acct-1", "first@example.com"),
                FakeAccount("acct-2", "second@example.com", is_active=True),
            ]

        def get_usage_info(self, selector: str | None):
            if selector == "acct-1":
                raise RuntimeError("token expired")
            assert selector == "acct-2"
            return FakeUsage()

    monkeypatch.setattr(cli, "AppConfig", SimpleNamespace(default=lambda: SimpleNamespace(
        codex_auth_path="auth",
        codex_accounts_dir="accounts",
        codex_accounts_manifest_path="manifest",
    )))
    monkeypatch.setattr(cli, "CodexAccountService", lambda **_kwargs: FakeService())
    monkeypatch.setattr(cli.time, "time", lambda: 1776575894)
    stdout = io.StringIO()
    stderr = io.StringIO()

    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        result = cli.codex_usage(argparse.Namespace(all_accounts=True))

    assert result == 0
    output = stdout.getvalue()
    assert "first@example.com" in output
    assert "Error" in output
    assert "token expired" in output
    assert "second@example.com" in output
    assert "90.0%" in output and "80.0%" in output
    assert "warning: failed to fetch usage for first@example.com: token expired" in stderr.getvalue()


def test_codex_usage_fetches_all_accounts_in_parallel(monkeypatch) -> None:
    stdout = io.StringIO()

    class ParallelUsage:
        def __init__(self, label: str) -> None:
            self.label = label
            self.plan_type = "plus"
            self.subscription_active_until = "2026-05-01T00:00:00+00:00"
            self.five_hour_used_percent = 49.0
            self.five_hour_resets_at = 1776579494
            self.weekly_used_percent = 70.0
            self.weekly_resets_at = 1776997593

    class FakeAccount:
        def __init__(self, account_id: str, label: str) -> None:
            self.account_id = account_id
            self.label = label
            self.is_active = False

    started_event = threading.Event()
    release_event = threading.Event()
    state = {"running": 0, "max_running": 0}
    state_lock = threading.Lock()

    class FakeService:
        def list_accounts(self):
            return [
                FakeAccount("acct-1", "first@example.com"),
                FakeAccount("acct-2", "second@example.com"),
            ]

        def get_usage_info(self, selector: str | None):
            assert selector in {"acct-1", "acct-2"}
            with state_lock:
                state["running"] += 1
                state["max_running"] = max(state["max_running"], state["running"])
                if state["running"] == 2:
                    started_event.set()
            if not started_event.wait(1):
                raise AssertionError("expected both usage requests to start")
            if not release_event.wait(1):
                raise AssertionError("usage requests were not released")
            with state_lock:
                state["running"] -= 1
            return ParallelUsage(f"{selector}@example.com")

    monkeypatch.setattr(cli, "AppConfig", SimpleNamespace(default=lambda: SimpleNamespace(
        codex_auth_path="auth",
        codex_accounts_dir="accounts",
        codex_accounts_manifest_path="manifest",
    )))
    monkeypatch.setattr(cli, "CodexAccountService", lambda **_kwargs: FakeService())

    result_holder: dict[str, int] = {}

    def run_usage() -> None:
        try:
            with contextlib.redirect_stdout(stdout):
                result_holder["result"] = cli.codex_usage(argparse.Namespace(all_accounts=True))
        finally:
            release_event.set()

    thread = threading.Thread(target=run_usage)
    thread.start()
    assert started_event.wait(1)
    release_event.set()
    thread.join(timeout=1)

    assert not thread.is_alive()
    assert result_holder["result"] == 0
    assert state["max_running"] >= 2


def test_codex_accounts_list_prints_accounts(monkeypatch) -> None:
    class FakeAccount:
        def __init__(self, account_id: str, label: str, *, is_default: bool, is_active: bool) -> None:
            self.account_id = account_id
            self.label = label
            self.is_default = is_default
            self.is_active = is_active

    class FakeService:
        def list_accounts(self):
            return [
                FakeAccount("acct-1", "Work", is_default=True, is_active=False),
                FakeAccount("acct-2", "Personal", is_default=False, is_active=True),
            ]

    monkeypatch.setattr(cli, "AppConfig", SimpleNamespace(default=lambda: SimpleNamespace(
        codex_auth_path="auth",
        codex_accounts_dir="accounts",
        codex_accounts_manifest_path="manifest",
    )))
    monkeypatch.setattr(cli, "CodexAccountService", lambda **_kwargs: FakeService())
    stdout = io.StringIO()

    with contextlib.redirect_stdout(stdout):
        result = cli.codex_accounts_list(argparse.Namespace())

    assert result == 0
    output = stdout.getvalue()
    assert "1\tacct-1" in output
    assert "2\tacct-2" in output
    assert "default" in output
    assert "active" in output


def test_codex_accounts_switch_uses_selector(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeService:
        def switch_account(self, selector: str):
            captured["selector"] = selector
            return SimpleNamespace(current_account_id="acct-2", current_account_label="second@example.com")

    monkeypatch.setattr(cli, "AppConfig", SimpleNamespace(default=lambda: SimpleNamespace(
        codex_auth_path="auth",
        codex_accounts_dir="accounts",
        codex_accounts_manifest_path="manifest",
    )))
    monkeypatch.setattr(cli, "CodexAccountService", lambda **_kwargs: FakeService())
    stdout = io.StringIO()

    with contextlib.redirect_stdout(stdout):
        result = cli.codex_accounts_switch(argparse.Namespace(account="second@example.com"))

    assert result == 0
    assert captured == {"selector": "second@example.com"}
    assert "current_account_label: second@example.com" in stdout.getvalue()


def test_codex_accounts_switch_syncs_auth_when_requested(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeResult:
        account_label = "second@example.com"
        account_email = "second@example.com"
        openclaw_paths = ("openclaw-auth.json",)
        hermes_auth_path = "hermes-auth.json"
        openclaw_reload_status = "reloaded"
        openclaw_reload_message = None

    class FakeService:
        def switch_account(self, selector: str):
            captured["switch_selector"] = selector
            return SimpleNamespace(
                current_account_id="acct-2",
                current_account_label="second@example.com",
                accounts=[SimpleNamespace(account_id="acct-2")],
            )

        def sync_credentials(self, selector: str | None):
            captured["sync_selector"] = selector
            return FakeResult()

    monkeypatch.setattr(cli, "AppConfig", SimpleNamespace(default=lambda: SimpleNamespace(
        codex_auth_path="auth",
        codex_accounts_dir="accounts",
        codex_accounts_manifest_path="manifest",
    )))
    monkeypatch.setattr(cli, "CodexAccountService", lambda **_kwargs: FakeService())
    stdout = io.StringIO()

    with contextlib.redirect_stdout(stdout):
        result = cli.codex_accounts_switch(argparse.Namespace(account="2", sync_auth=True))

    assert result == 0
    assert captured == {"switch_selector": "2", "sync_selector": "2"}
    output = stdout.getvalue()
    assert "current_account_no: 1" in output
    assert "synced account: second@example.com" in output
    assert "openclaw: openclaw-auth.json" in output
    assert "hermes: hermes-auth.json" in output


def test_codex_accounts_rename_reports_validation_errors(monkeypatch) -> None:
    class FakeService:
        def rename_account(self, _account_id: str, _label: str) -> None:
            raise ValueError("account label is required")

    monkeypatch.setattr(cli, "AppConfig", SimpleNamespace(default=lambda: SimpleNamespace(
        codex_auth_path="auth",
        codex_accounts_dir="accounts",
        codex_accounts_manifest_path="manifest",
    )))
    monkeypatch.setattr(cli, "CodexAccountService", lambda **_kwargs: FakeService())
    stderr = io.StringIO()

    with contextlib.redirect_stderr(stderr):
        result = cli.codex_accounts_rename(argparse.Namespace(account_id="acct-1", label=""))

    assert result == 2
    assert "account label is required" in stderr.getvalue()


def test_codex_accounts_import_current_prints_imported_account(monkeypatch) -> None:
    class FakeImported:
        account_id = "acct-import"
        label = "Imported"

    class FakeService:
        def import_current_auth(self, label: str):
            assert label == "Imported"
            return FakeImported()

    monkeypatch.setattr(cli, "AppConfig", SimpleNamespace(default=lambda: SimpleNamespace(
        codex_auth_path="auth",
        codex_accounts_dir="accounts",
        codex_accounts_manifest_path="manifest",
    )))
    monkeypatch.setattr(cli, "CodexAccountService", lambda **_kwargs: FakeService())
    stdout = io.StringIO()

    with contextlib.redirect_stdout(stdout):
        result = cli.codex_accounts_import_current(argparse.Namespace(label="Imported"))

    assert result == 0
    assert "acct-import" in stdout.getvalue()
    assert "Imported" in stdout.getvalue()


def test_codex_sync_auth_prints_target_paths(monkeypatch) -> None:
    class FakeResult:
        account_label = "work@example.com"
        account_email = "work@example.com"
        openclaw_paths = ("openclaw-a.json", "openclaw-b.json")
        hermes_auth_path = "hermes-auth.json"
        openclaw_reload_status = "reloaded"
        openclaw_reload_message = '{"ok":true,"warningCount":0}'

    class FakeService:
        def sync_credentials(self, selector: str | None):
            assert selector == "work@example.com"
            return FakeResult()

    monkeypatch.setattr(cli, "AppConfig", SimpleNamespace(default=lambda: SimpleNamespace(
        codex_auth_path="auth",
        codex_accounts_dir="accounts",
        codex_accounts_manifest_path="manifest",
    )))
    monkeypatch.setattr(cli, "CodexAccountService", lambda **_kwargs: FakeService())
    stdout = io.StringIO()

    with contextlib.redirect_stdout(stdout):
        result = cli.codex_sync_auth(argparse.Namespace(email="work@example.com"))

    assert result == 0
    output = stdout.getvalue()
    assert "synced account: work@example.com" in output
    assert "openclaw_targets: 2" in output
    assert "openclaw: openclaw-a.json" in output
    assert "openclaw_runtime: reloaded" in output
    assert 'openclaw_runtime_detail: {"ok":true,"warningCount":0}' in output
    assert "hermes: hermes-auth.json" in output


def test_toggle_app_starts_service_and_runs_toggle_action(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(cli, "_run_frontend_action_starting_service", lambda action: calls.append(action) or 0)

    result = cli.toggle_app(argparse.Namespace())

    assert result == 0
    assert calls == ["toggle-island-focus"]


def test_highlight_selected_starts_service_and_runs_highlight_action(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(cli, "_run_frontend_action_starting_service", lambda action: calls.append(action) or 0)

    result = cli.highlight_selected(argparse.Namespace())

    assert result == 0
    assert calls == ["toggle-highlight-selected"]


def test_daemon_starts_hotkey_process(monkeypatch, tmp_path) -> None:
    launched: list[list[str]] = []

    class FakeProc:
        def __init__(self, returncode: int | None = None) -> None:
            self.returncode = returncode

        def poll(self) -> int | None:
            return self.returncode

        def terminate(self) -> None:
            return None

        def wait(self, timeout: float | None = None) -> int:
            return 0

        def kill(self) -> None:
            return None

    def fake_popen(args: list[str], env: dict[str, str] | None = None) -> FakeProc:
        launched.append(args)
        if args[2] == "linux_agent_island.backend":
            return FakeProc(0)
        return FakeProc(None)

    monkeypatch.setattr(
        cli,
        "AppConfig",
        SimpleNamespace(default=lambda: SimpleNamespace(runtime_dir=tmp_path, frontend_settings_path=tmp_path / "settings.json")),
    )
    monkeypatch.setattr(cli, "load_frontend_settings", lambda _path: SimpleNamespace(log_level="INFO"))
    monkeypatch.setattr(cli, "configure_logging", lambda *_args, **_kwargs: "INFO")
    monkeypatch.setattr(cli.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(cli.signal, "signal", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli.time, "sleep", lambda *_args, **_kwargs: None)

    result = cli.daemon(argparse.Namespace(log_level="INFO"))

    assert result == 0
    assert [args[2] for args in launched] == [
        "linux_agent_island.backend",
        "linux_agent_island.frontend",
        "linux_agent_island.app.hotkeys",
        "linux_agent_island.app.tray",
    ]
