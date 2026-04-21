import json
import os
import threading
import time
from pathlib import Path
import pwd as pwd_module

from linux_agent_island.codex_accounts import _OPENCLAW_PROFILE_ID, CodexAccountService
from linux_agent_island.core.models import AgentSession, SessionPhase


def _jwt(payload: dict[str, object]) -> str:
    import base64

    def _segment(value: dict[str, object]) -> str:
        raw = json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    return f"{_segment({'alg': 'none', 'typ': 'JWT'})}.{_segment(payload)}."


def _write_auth(
    path: Path,
    *,
    auth_mode: str = "chatgpt",
    account_id: str = "acct-1",
    email: str | None = None,
    include_id_token: bool = True,
    include_access_token: bool = True,
    last_refresh: str = "2026-04-17T00:00:00Z",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    token_payload: dict[str, object] = {"sub": account_id}
    if email is not None:
        token_payload["email"] = email
    access_token_payload: dict[str, object] = {
        "sub": account_id,
        "exp": 1893456000,
        "https://api.openai.com/profile": {"email": email} if email is not None else {},
    }
    path.write_text(
        json.dumps(
            {
                "auth_mode": auth_mode,
                "OPENAI_API_KEY": None,
                "tokens": {
                    "access_token": _jwt(access_token_payload) if include_access_token else None,
                    "refresh_token": f"refresh-{account_id}",
                    "id_token": _jwt(token_payload) if include_id_token else None,
                    "account_id": account_id,
                },
                "last_refresh": last_refresh,
            }
        ),
        encoding="utf-8",
    )


def test_codex_account_service_auto_imports_current_login_when_empty(tmp_path: Path) -> None:
    auth_path = tmp_path / ".codex" / "auth.json"
    _write_auth(auth_path, account_id="acct-initial", email="initial@example.com")
    service = CodexAccountService(
        auth_path=auth_path,
        accounts_dir=tmp_path / "accounts",
        manifest_path=tmp_path / "accounts" / "accounts.json",
        now=lambda: 123,
    )

    accounts = service.list_accounts()
    status = service.get_status()

    assert len(accounts) == 1
    assert accounts[0].label == "initial@example.com"
    assert accounts[0].is_default is True
    assert accounts[0].is_active is True
    assert status.logged_in is True
    assert status.current_account_managed is True
    assert status.current_account_label == "initial@example.com"


def test_codex_account_service_switches_active_snapshot(tmp_path: Path) -> None:
    auth_path = tmp_path / ".codex" / "auth.json"
    _write_auth(auth_path, account_id="acct-a", email="work@example.com")
    service = CodexAccountService(
        auth_path=auth_path,
        accounts_dir=tmp_path / "accounts",
        manifest_path=tmp_path / "accounts" / "accounts.json",
        now=lambda: 200,
    )

    first_account = service.list_accounts()[0]
    service.rename_account(first_account.account_id, "Work")

    _write_auth(auth_path, account_id="acct-b", email="personal@example.com")
    second_service = CodexAccountService(
        auth_path=auth_path,
        accounts_dir=tmp_path / "accounts",
        manifest_path=tmp_path / "accounts" / "accounts.json",
        now=lambda: 250,
    )
    stored_accounts = second_service._load_accounts_locked()  # type: ignore[attr-defined]
    second_service._import_current_auth_locked(stored_accounts, "Personal")  # type: ignore[attr-defined]

    second_account = [
        account for account in second_service.list_accounts()
        if account.label == "personal@example.com"
    ][0]
    switched_status = second_service.switch_account(first_account.account_id)

    assert switched_status.current_account_id == first_account.account_id
    assert json.loads(auth_path.read_text(encoding="utf-8"))["tokens"]["account_id"] == "acct-a"
    assert any(account.account_id == second_account.account_id for account in switched_status.accounts)


def test_codex_account_service_prevents_deleting_active_account(tmp_path: Path) -> None:
    auth_path = tmp_path / ".codex" / "auth.json"
    _write_auth(auth_path, email="active@example.com")
    service = CodexAccountService(
        auth_path=auth_path,
        accounts_dir=tmp_path / "accounts",
        manifest_path=tmp_path / "accounts" / "accounts.json",
    )

    account = service.list_accounts()[0]

    try:
        service.delete_account(account.account_id)
    except ValueError as exc:
        assert "active" in str(exc)
    else:
        raise AssertionError("expected active account deletion to fail")


def test_codex_account_service_login_imports_new_account(tmp_path: Path) -> None:
    auth_path = tmp_path / ".codex" / "auth.json"
    manifest_path = tmp_path / "accounts" / "accounts.json"
    codex_bin = tmp_path / "bin" / "codex"
    codex_bin.parent.mkdir(parents=True, exist_ok=True)
    codex_bin.write_text("#!/bin/sh\n", encoding="utf-8")
    codex_bin.chmod(0o755)
    callback_result: list[bool] = []
    callback_event = threading.Event()

    class FakeProcess:
        def wait(self) -> int:
            _write_auth(auth_path, account_id="acct-login", email="login@example.com")
            return 0

    service = CodexAccountService(
        auth_path=auth_path,
        accounts_dir=tmp_path / "accounts",
        manifest_path=manifest_path,
        configured_codex_bin=str(codex_bin),
        launch_login=lambda _command: FakeProcess(),
        now=lambda: 300,
    )

    started = service.start_device_login(
        "Work login",
        on_complete=lambda success: (callback_result.append(success), callback_event.set()),
    )

    assert started is True
    assert callback_event.wait(1)
    assert callback_result == [True]
    status = service.get_status(
        [
            AgentSession(
                provider="codex",
                session_id="thread-1",
                cwd="/tmp/demo",
                title="Demo",
                phase=SessionPhase.RUNNING,
                model=None,
                sandbox=None,
                approval_mode=None,
                updated_at=300,
                is_process_alive=True,
            )
        ]
    )
    assert status.current_account_label == "login@example.com"
    assert status.has_running_codex_sessions is True
    assert len(status.accounts) == 1


def test_codex_account_service_login_preserves_old_account_and_imports_new_one(tmp_path: Path) -> None:
    auth_path = tmp_path / ".codex" / "auth.json"
    _write_auth(auth_path, account_id="acct-old", email="old@example.com")
    manifest_path = tmp_path / "accounts" / "accounts.json"
    codex_bin = tmp_path / "bin" / "codex"
    codex_bin.parent.mkdir(parents=True, exist_ok=True)
    codex_bin.write_text("#!/bin/sh\n", encoding="utf-8")
    codex_bin.chmod(0o755)
    callback_result: list[bool] = []
    callback_event = threading.Event()

    class FakeProcess:
        def wait(self) -> int:
            _write_auth(auth_path, account_id="acct-new", email="new@example.com")
            return 0

    service = CodexAccountService(
        auth_path=auth_path,
        accounts_dir=tmp_path / "accounts",
        manifest_path=manifest_path,
        configured_codex_bin=str(codex_bin),
        launch_login=lambda _command: FakeProcess(),
        now=lambda: 320,
    )

    old_account = service.list_accounts()[0]
    started = service.start_device_login(
        "New login",
        on_complete=lambda success: (callback_result.append(success), callback_event.set()),
    )

    assert started is True
    assert callback_event.wait(1)
    assert callback_result == [True]

    status = service.get_status()
    assert status.current_account_label == "new@example.com"
    assert len(status.accounts) == 2
    assert json.loads(auth_path.read_text(encoding="utf-8"))["tokens"]["account_id"] == "acct-new"

    switched_status = service.switch_account(old_account.account_id)
    assert switched_status.current_account_id == old_account.account_id
    assert json.loads(auth_path.read_text(encoding="utf-8"))["tokens"]["account_id"] == "acct-old"
    assert not any((tmp_path / "accounts").glob(".login-backup-*"))


def test_codex_account_service_login_same_account_updates_existing_entry(tmp_path: Path) -> None:
    auth_path = tmp_path / ".codex" / "auth.json"
    _write_auth(auth_path, account_id="acct-same", email="same@example.com", last_refresh="2026-04-17T00:00:00Z")
    manifest_path = tmp_path / "accounts" / "accounts.json"
    codex_bin = tmp_path / "bin" / "codex"
    codex_bin.parent.mkdir(parents=True, exist_ok=True)
    codex_bin.write_text("#!/bin/sh\n", encoding="utf-8")
    codex_bin.chmod(0o755)
    callback_result: list[bool] = []
    callback_event = threading.Event()

    class FakeProcess:
        def wait(self) -> int:
            _write_auth(
                auth_path,
                account_id="acct-same",
                email="same@example.com",
                last_refresh="2026-04-18T00:00:00Z",
            )
            return 0

    service = CodexAccountService(
        auth_path=auth_path,
        accounts_dir=tmp_path / "accounts",
        manifest_path=manifest_path,
        configured_codex_bin=str(codex_bin),
        launch_login=lambda _command: FakeProcess(),
        now=lambda: 321,
    )

    original_account = service.list_accounts()[0]
    started = service.start_device_login(
        "Same login",
        on_complete=lambda success: (callback_result.append(success), callback_event.set()),
    )

    assert started is True
    assert callback_event.wait(1)
    assert callback_result == [True]

    accounts = service.list_accounts()
    assert len(accounts) == 1
    assert accounts[0].account_id == original_account.account_id
    assert accounts[0].label == "same@example.com"
    assert accounts[0].is_active is True


def test_codex_account_service_login_waits_for_auth_after_launcher_exit(tmp_path: Path, monkeypatch) -> None:
    auth_path = tmp_path / ".codex" / "auth.json"
    manifest_path = tmp_path / "accounts" / "accounts.json"
    codex_bin = tmp_path / "bin" / "codex"
    codex_bin.parent.mkdir(parents=True, exist_ok=True)
    codex_bin.write_text("#!/bin/sh\n", encoding="utf-8")
    codex_bin.chmod(0o755)
    callback_result: list[bool] = []
    callback_event = threading.Event()

    class FakePollingProcess:
        def __init__(self) -> None:
            self._return_code: int | None = None
            threading.Thread(target=self._finish, daemon=True).start()

        def _finish(self) -> None:
            time.sleep(0.02)
            self._return_code = 0
            time.sleep(0.02)
            _write_auth(auth_path, account_id="acct-browser", email="browser@example.com")

        def poll(self) -> int | None:
            return self._return_code

    monkeypatch.setattr("linux_agent_island.codex_accounts._DEVICE_LOGIN_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr("linux_agent_island.codex_accounts._DEVICE_LOGIN_POLL_INTERVAL_SECONDS", 0.01)

    service = CodexAccountService(
        auth_path=auth_path,
        accounts_dir=tmp_path / "accounts",
        manifest_path=manifest_path,
        configured_codex_bin=str(codex_bin),
        launch_login=lambda _command: FakePollingProcess(),
        now=lambda: 325,
    )

    started = service.start_device_login(
        "Browser login",
        on_complete=lambda success: (callback_result.append(success), callback_event.set()),
    )

    assert started is True
    assert callback_event.wait(1)
    assert callback_result == [True]
    status = service.get_status()
    assert status.current_account_label == "browser@example.com"
    assert len(status.accounts) == 1


def test_codex_account_service_login_failure_restores_previous_auth(tmp_path: Path) -> None:
    auth_path = tmp_path / ".codex" / "auth.json"
    _write_auth(auth_path, account_id="acct-old", email="old@example.com")
    manifest_path = tmp_path / "accounts" / "accounts.json"
    callback_result: list[bool] = []
    callback_event = threading.Event()

    class FakeProcess:
        def wait(self) -> int:
            return 1

    service = CodexAccountService(
        auth_path=auth_path,
        accounts_dir=tmp_path / "accounts",
        manifest_path=manifest_path,
        launch_login=lambda _command: FakeProcess(),
        now=lambda: 330,
    )

    started = service.start_device_login(
        None,
        on_complete=lambda success: (callback_result.append(success), callback_event.set()),
    )

    assert started is True
    assert callback_event.wait(1)
    assert callback_result == [False]
    assert json.loads(auth_path.read_text(encoding="utf-8"))["tokens"]["account_id"] == "acct-old"

    status = service.get_status()
    assert status.current_account_label == "old@example.com"
    assert len(status.accounts) == 1
    assert not any((tmp_path / "accounts").glob(".login-backup-*"))


def test_codex_account_service_login_timeout_restores_previous_auth(tmp_path: Path, monkeypatch) -> None:
    auth_path = tmp_path / ".codex" / "auth.json"
    _write_auth(auth_path, account_id="acct-old", email="old@example.com")
    manifest_path = tmp_path / "accounts" / "accounts.json"
    callback_result: list[bool] = []
    callback_event = threading.Event()

    class FakePollingProcess:
        def __init__(self) -> None:
            self._return_code = 0

        def poll(self) -> int | None:
            return self._return_code

    monkeypatch.setattr("linux_agent_island.codex_accounts._DEVICE_LOGIN_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr("linux_agent_island.codex_accounts._DEVICE_LOGIN_POLL_INTERVAL_SECONDS", 0.01)

    service = CodexAccountService(
        auth_path=auth_path,
        accounts_dir=tmp_path / "accounts",
        manifest_path=manifest_path,
        launch_login=lambda _command: FakePollingProcess(),
        now=lambda: 335,
    )

    started = service.start_device_login(
        "Timeout login",
        on_complete=lambda success: (callback_result.append(success), callback_event.set()),
    )

    assert started is True
    assert callback_event.wait(1)
    assert callback_result == [False]
    assert json.loads(auth_path.read_text(encoding="utf-8"))["tokens"]["account_id"] == "acct-old"
    assert service.get_status().current_account_label == "old@example.com"


def test_codex_account_service_login_succeeds_when_auth_arrives_after_nonzero_exit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    auth_path = tmp_path / ".codex" / "auth.json"
    manifest_path = tmp_path / "accounts" / "accounts.json"
    callback_result: list[bool] = []
    callback_event = threading.Event()

    class FakePollingProcess:
        def __init__(self) -> None:
            self._return_code: int | None = None
            threading.Thread(target=self._finish, daemon=True).start()

        def _finish(self) -> None:
            time.sleep(0.01)
            self._return_code = 1
            time.sleep(0.02)
            _write_auth(auth_path, account_id="acct-late", email="late@example.com")

        def poll(self) -> int | None:
            return self._return_code

    monkeypatch.setattr("linux_agent_island.codex_accounts._DEVICE_LOGIN_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr("linux_agent_island.codex_accounts._DEVICE_LOGIN_POLL_INTERVAL_SECONDS", 0.01)

    service = CodexAccountService(
        auth_path=auth_path,
        accounts_dir=tmp_path / "accounts",
        manifest_path=manifest_path,
        launch_login=lambda _command: FakePollingProcess(),
        now=lambda: 336,
    )

    started = service.start_device_login(
        "Late login",
        on_complete=lambda success: (callback_result.append(success), callback_event.set()),
    )

    assert started is True
    assert callback_event.wait(1)
    assert callback_result == [True]
    assert service.get_status().current_account_label == "late@example.com"


def test_codex_account_service_reports_local_login_in_progress_before_worker_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    auth_path = tmp_path / ".codex" / "auth.json"
    service = CodexAccountService(
        auth_path=auth_path,
        accounts_dir=tmp_path / "accounts",
        manifest_path=tmp_path / "accounts" / "accounts.json",
    )

    class DeferredThread:
        def __init__(self, *, target, args, daemon):  # type: ignore[no-untyped-def]
            assert daemon is True
            self.target = target
            self.args = args

        def start(self) -> None:
            return None

    monkeypatch.setattr("linux_agent_island.codex_accounts.threading.Thread", DeferredThread)

    assert service.start_device_login("Work") is True
    assert service.get_status().device_login_in_progress is True
    assert service.start_device_login("Other") is False


def test_codex_account_service_import_current_auth_adds_new_managed_account(tmp_path: Path) -> None:
    auth_path = tmp_path / ".codex" / "auth.json"
    _write_auth(auth_path, account_id="acct-import", email="import@example.com")
    service = CodexAccountService(
        auth_path=auth_path,
        accounts_dir=tmp_path / "accounts",
        manifest_path=tmp_path / "accounts" / "accounts.json",
        now=lambda: 340,
    )

    imported = service.import_current_auth("Imported")

    assert imported.label == "import@example.com"
    accounts = service.list_accounts()
    assert any(account.label == "import@example.com" for account in accounts)


def test_codex_account_service_load_deduplicates_same_identity_accounts(tmp_path: Path) -> None:
    auth_path = tmp_path / ".codex" / "auth.json"
    manifest_path = tmp_path / "accounts" / "accounts.json"
    accounts_dir = tmp_path / "accounts"
    _write_auth(auth_path, account_id="acct-dup", email="dup@example.com", last_refresh="2026-04-19T00:00:00Z")
    snapshot_a = accounts_dir / "acct-a.json"
    snapshot_b = accounts_dir / "acct-b.json"
    _write_auth(snapshot_a, account_id="acct-dup", email="dup@example.com", last_refresh="2026-04-17T00:00:00Z")
    _write_auth(snapshot_b, account_id="acct-dup", email="dup@example.com", last_refresh="2026-04-18T00:00:00Z")

    service = CodexAccountService(
        auth_path=auth_path,
        accounts_dir=accounts_dir,
        manifest_path=manifest_path,
        now=lambda: 340,
    )
    payload_a = service._read_payload_from_path(snapshot_a)  # type: ignore[attr-defined]
    payload_b = service._read_payload_from_path(snapshot_b)  # type: ignore[attr-defined]
    assert payload_a is not None
    assert payload_b is not None

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(
            {
                "accounts": [
                    {
                        "account_id": "acct-a",
                        "label": "dup@example.com",
                        "created_at": 100,
                        "updated_at": 100,
                        "auth_fingerprint": service._fingerprint_payload(payload_a),  # type: ignore[attr-defined]
                        "identity_key": "account_id:acct-dup",
                        "is_default": True,
                    },
                    {
                        "account_id": "acct-b",
                        "label": "dup@example.com",
                        "created_at": 101,
                        "updated_at": 101,
                        "auth_fingerprint": service._fingerprint_payload(payload_b),  # type: ignore[attr-defined]
                        "identity_key": "account_id:acct-dup",
                        "is_default": False,
                    },
                ]
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    accounts = service.list_accounts()

    assert len(accounts) == 1
    assert accounts[0].account_id == "acct-a"
    assert accounts[0].is_active is True
    assert snapshot_a.exists()
    assert not snapshot_b.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert len(manifest["accounts"]) == 1


def test_codex_account_service_sync_credentials_updates_openclaw_and_hermes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        CodexAccountService,
        "_reload_openclaw_runtime",
        lambda self: ("reloaded", '{"ok": true, "warningCount": 0}'),
    )
    auth_path = tmp_path / ".codex" / "auth.json"
    _write_auth(auth_path, account_id="acct-sync", email="sync@example.com")
    openclaw_main = tmp_path / ".openclaw" / "agents" / "main" / "agent" / "auth-profiles.json"
    openclaw_codex = tmp_path / ".openclaw" / "agents" / "codex" / "agent" / "auth-profiles.json"
    openclaw_main_state = openclaw_main.with_name("auth-state.json")
    openclaw_keep_profile = "other:default"
    hermes_auth = tmp_path / ".hermes" / "auth.json"
    openclaw_main.parent.mkdir(parents=True, exist_ok=True)
    openclaw_main.write_text(
        json.dumps({"profiles": {openclaw_keep_profile: {"provider": "other", "type": "token", "token": "keep"}}}),
        encoding="utf-8",
    )
    openclaw_main_state.write_text(
        json.dumps(
            {
                "version": 1,
                "lastGood": {"other": openclaw_keep_profile},
                "usageStats": {
                    _OPENCLAW_PROFILE_ID: {
                        "errorCount": 5,
                        "cooldownUntil": 9999999999999,
                    },
                    openclaw_keep_profile: {
                        "errorCount": 1,
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    hermes_auth.parent.mkdir(parents=True, exist_ok=True)
    hermes_auth.write_text(json.dumps({"providers": {"nous": {"token": "keep"}}}), encoding="utf-8")

    service = CodexAccountService(
        auth_path=auth_path,
        accounts_dir=tmp_path / "accounts",
        manifest_path=tmp_path / "accounts" / "accounts.json",
        openclaw_auth_profile_paths=(openclaw_main, openclaw_codex),
        hermes_auth_path=hermes_auth,
    )

    result = service.sync_credentials()

    assert result.account_email == "sync@example.com"
    for path in (openclaw_main, openclaw_codex):
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["defaults"]["openai-codex"] == "openai-codex:default"
        assert payload["profiles"]["openai-codex:default"]["provider"] == "openai-codex"
        assert payload["profiles"]["openai-codex:default"]["accountId"] == "acct-sync"
        assert payload["profiles"]["openai-codex:default"]["managedBy"] == "codex-cli"
        assert payload["profiles"]["openai-codex:default"]["refresh"] == "refresh-acct-sync"
        assert payload["version"] == 1
        state_payload = json.loads(path.with_name("auth-state.json").read_text(encoding="utf-8"))
        assert state_payload["lastGood"]["openai-codex"] == _OPENCLAW_PROFILE_ID
        assert _OPENCLAW_PROFILE_ID not in state_payload.get("usageStats", {})
    main_state_payload = json.loads(openclaw_main_state.read_text(encoding="utf-8"))
    assert main_state_payload["lastGood"]["other"] == openclaw_keep_profile
    assert main_state_payload["usageStats"][openclaw_keep_profile]["errorCount"] == 1
    hermes_payload = json.loads(hermes_auth.read_text(encoding="utf-8"))
    assert hermes_payload["active_provider"] == "openai-codex"
    assert hermes_payload["providers"]["nous"]["token"] == "keep"
    assert hermes_payload["providers"]["openai-codex"]["tokens"]["account_id"] == "acct-sync"
    assert hermes_payload["providers"]["openai-codex"]["auth_mode"] == "chatgpt"
    assert result.openclaw_reload_status == "reloaded"


def test_codex_account_service_sync_credentials_can_select_account_by_email(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(CodexAccountService, "_reload_openclaw_runtime", lambda self: ("skipped", None))
    auth_path = tmp_path / ".codex" / "auth.json"
    manifest_path = tmp_path / "accounts" / "accounts.json"
    openclaw_target = tmp_path / ".openclaw" / "agents" / "main" / "agent" / "auth-profiles.json"
    hermes_auth = tmp_path / ".hermes" / "auth.json"
    _write_auth(auth_path, account_id="acct-current", email="current@example.com")
    service = CodexAccountService(
        auth_path=auth_path,
        accounts_dir=tmp_path / "accounts",
        manifest_path=manifest_path,
        openclaw_auth_profile_paths=(openclaw_target,),
        hermes_auth_path=hermes_auth,
    )

    current_account = service.list_accounts()[0]
    _write_auth(auth_path, account_id="acct-other", email="other@example.com")
    imported = service.import_current_auth("Other")
    service.switch_account(current_account.account_id)

    result = service.sync_credentials("other@example.com")

    assert result.account_email == "other@example.com"
    payload = json.loads(openclaw_target.read_text(encoding="utf-8"))
    assert payload["profiles"]["openai-codex:default"]["accountId"] == "acct-other"
    hermes_payload = json.loads(hermes_auth.read_text(encoding="utf-8"))
    assert hermes_payload["providers"]["openai-codex"]["tokens"]["account_id"] == "acct-other"
    assert imported.account_id != current_account.account_id


def test_codex_account_service_sync_credentials_can_select_account_by_number(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(CodexAccountService, "_reload_openclaw_runtime", lambda self: ("skipped", None))
    auth_path = tmp_path / ".codex" / "auth.json"
    manifest_path = tmp_path / "accounts" / "accounts.json"
    openclaw_target = tmp_path / ".openclaw" / "agents" / "main" / "agent" / "auth-profiles.json"
    hermes_auth = tmp_path / ".hermes" / "auth.json"
    _write_auth(auth_path, account_id="acct-current", email="current@example.com")
    service = CodexAccountService(
        auth_path=auth_path,
        accounts_dir=tmp_path / "accounts",
        manifest_path=manifest_path,
        openclaw_auth_profile_paths=(openclaw_target,),
        hermes_auth_path=hermes_auth,
    )

    current_account = service.list_accounts()[0]
    _write_auth(auth_path, account_id="acct-other", email="other@example.com")
    imported = service.import_current_auth("Other")
    service.switch_account(current_account.account_id)

    result = service.sync_credentials("2")

    assert result.account_email == "other@example.com"
    payload = json.loads(openclaw_target.read_text(encoding="utf-8"))
    assert payload["profiles"]["openai-codex:default"]["accountId"] == "acct-other"
    hermes_payload = json.loads(hermes_auth.read_text(encoding="utf-8"))
    assert hermes_payload["providers"]["openai-codex"]["tokens"]["account_id"] == "acct-other"
    assert imported.account_id != current_account.account_id


def test_reload_openclaw_runtime_skips_when_cli_missing(tmp_path: Path, monkeypatch) -> None:
    service = CodexAccountService(
        auth_path=tmp_path / ".codex" / "auth.json",
        accounts_dir=tmp_path / "accounts",
        manifest_path=tmp_path / "accounts" / "accounts.json",
    )
    monkeypatch.setattr("linux_agent_island.codex_accounts.shutil.which", lambda name: None)

    status, message = service._reload_openclaw_runtime()  # type: ignore[attr-defined]

    assert status == "skipped"
    assert message is not None
    assert "openclaw secrets reload" in message


def test_reload_openclaw_runtime_reports_success(tmp_path: Path, monkeypatch) -> None:
    service = CodexAccountService(
        auth_path=tmp_path / ".codex" / "auth.json",
        accounts_dir=tmp_path / "accounts",
        manifest_path=tmp_path / "accounts" / "accounts.json",
    )

    class FakeCompletedProcess:
        returncode = 0
        stdout = '{"ok": true, "warningCount": 0}\n'
        stderr = ""

    monkeypatch.setattr("linux_agent_island.codex_accounts.shutil.which", lambda name: "/usr/bin/openclaw")
    monkeypatch.setattr(
        "linux_agent_island.codex_accounts.subprocess.run",
        lambda *args, **kwargs: FakeCompletedProcess(),
    )

    status, message = service._reload_openclaw_runtime()  # type: ignore[attr-defined]

    assert status == "reloaded"
    assert message == '{"ok": true, "warningCount": 0}'


def test_codex_account_service_get_usage_info_reads_current_auth(tmp_path: Path) -> None:
    auth_path = tmp_path / ".codex" / "auth.json"
    auth_path.parent.mkdir(parents=True, exist_ok=True)
    auth_path.write_text(
        json.dumps(
            {
                "auth_mode": "chatgpt",
                "OPENAI_API_KEY": None,
                "tokens": {
                    "access_token": _jwt(
                        {
                            "sub": "acct-current",
                            "https://api.openai.com/profile": {"email": "current@example.com"},
                        }
                    ),
                    "refresh_token": "refresh-current",
                    "id_token": _jwt(
                        {
                            "email": "current@example.com",
                            "https://api.openai.com/auth": {
                                "chatgpt_plan_type": "plus",
                                "chatgpt_subscription_active_start": "2026-04-01T00:00:00+00:00",
                                "chatgpt_subscription_active_until": "2099-05-01T00:00:00+00:00",
                                "chatgpt_subscription_last_checked": "2026-04-19T00:00:00+00:00",
                            },
                        }
                    ),
                    "account_id": "acct-current",
                },
            }
        ),
        encoding="utf-8",
    )
    service = CodexAccountService(
        auth_path=auth_path,
        accounts_dir=tmp_path / "accounts",
        manifest_path=tmp_path / "accounts" / "accounts.json",
    )

    service._fetch_backend_usage_payload = lambda _payload: {
        "email": "current@example.com",
        "plan_type": "plus",
        "rate_limit": {
            "primary_window": {
                "used_percent": 49,
                "limit_window_seconds": 18000,
                "reset_at": 1776579494,
            },
            "secondary_window": {
                "used_percent": 70,
                "limit_window_seconds": 604800,
                "reset_at": 1776997593,
            },
        },
        "credits": {
            "has_credits": False,
            "unlimited": False,
            "balance": "0",
        },
    }

    usage = service.get_usage_info()

    assert usage.email == "current@example.com"
    assert usage.plan_type == "plus"
    assert usage.subscription_active_until == "2099-05-01T00:00:00+00:00"
    assert usage.remaining_days is not None
    assert usage.five_hour_window_minutes == 300
    assert usage.weekly_window_minutes == 10080
    assert usage.credits_balance == "0"


def test_codex_account_service_get_usage_info_reads_snapshot_by_account_id(tmp_path: Path) -> None:
    auth_path = tmp_path / ".codex" / "auth.json"
    _write_auth(auth_path, account_id="acct-live", email="live@example.com")
    service = CodexAccountService(
        auth_path=auth_path,
        accounts_dir=tmp_path / "accounts",
        manifest_path=tmp_path / "accounts" / "accounts.json",
    )
    default_account = service.list_accounts()[0]

    snapshot_path = tmp_path / "accounts" / f"{default_account.account_id}.json"
    snapshot_path.write_text(
        json.dumps(
            {
                "auth_mode": "chatgpt",
                "OPENAI_API_KEY": None,
                "tokens": {
                    "access_token": _jwt(
                        {
                            "sub": "acct-default",
                            "https://api.openai.com/profile": {"email": "default@example.com"},
                        }
                    ),
                    "refresh_token": "refresh-default",
                    "id_token": _jwt(
                        {
                            "email": "default@example.com",
                            "https://api.openai.com/auth": {
                                "chatgpt_plan_type": "team",
                                "chatgpt_subscription_active_start": "2026-04-10T00:00:00+00:00",
                                "chatgpt_subscription_active_until": "2099-06-10T00:00:00+00:00",
                                "chatgpt_subscription_last_checked": "2026-04-19T00:00:00+00:00",
                            },
                        }
                    ),
                    "account_id": "acct-default",
                },
            }
        ),
        encoding="utf-8",
    )

    service._fetch_backend_usage_payload = lambda _payload: {
        "email": "default@example.com",
        "plan_type": "team",
        "rate_limit": {
            "primary_window": {
                "used_percent": 10,
                "limit_window_seconds": 18000,
                "reset_at": 1776579494,
            },
            "secondary_window": {
                "used_percent": 20,
                "limit_window_seconds": 604800,
                "reset_at": 1776997593,
            },
        },
        "credits": {
            "has_credits": True,
            "unlimited": True,
            "balance": None,
        },
    }

    usage = service.get_usage_info(default_account.account_id)

    assert usage.account_id == default_account.account_id
    assert usage.label == default_account.label
    assert usage.email == "default@example.com"
    assert usage.plan_type == "team"
    assert usage.five_hour_used_percent == 10.0
    assert usage.weekly_used_percent == 20.0
    assert usage.has_credits is True


def test_codex_account_service_get_usage_info_reads_snapshot_by_email(tmp_path: Path) -> None:
    auth_path = tmp_path / ".codex" / "auth.json"
    _write_auth(auth_path, account_id="acct-live", email="live@example.com")
    service = CodexAccountService(
        auth_path=auth_path,
        accounts_dir=tmp_path / "accounts",
        manifest_path=tmp_path / "accounts" / "accounts.json",
    )
    default_account = service.list_accounts()[0]

    snapshot_path = tmp_path / "accounts" / f"{default_account.account_id}.json"
    snapshot_path.write_text(
        json.dumps(
            {
                "auth_mode": "chatgpt",
                "OPENAI_API_KEY": None,
                "tokens": {
                    "access_token": _jwt(
                        {
                            "sub": "acct-default",
                            "https://api.openai.com/profile": {"email": "default@example.com"},
                        }
                    ),
                    "refresh_token": "***",
                    "id_token": _jwt(
                        {
                            "email": "default@example.com",
                            "https://api.openai.com/auth": {
                                "chatgpt_plan_type": "team",
                                "chatgpt_subscription_active_start": "2026-04-10T00:00:00+00:00",
                                "chatgpt_subscription_active_until": "2099-06-10T00:00:00+00:00",
                                "chatgpt_subscription_last_checked": "2026-04-19T00:00:00+00:00",
                            },
                        }
                    ),
                    "account_id": "acct-default",
                },
            }
        ),
        encoding="utf-8",
    )

    service._fetch_backend_usage_payload = lambda _payload: {
        "email": "default@example.com",
        "plan_type": "team",
        "rate_limit": {
            "primary_window": {"used_percent": 10, "limit_window_seconds": 18000, "reset_at": 1776579494},
            "secondary_window": {"used_percent": 20, "limit_window_seconds": 604800, "reset_at": 1776997593},
        },
        "credits": {"has_credits": True, "unlimited": True, "balance": None},
    }

    usage = service.get_usage_info("default@example.com")

    assert usage.account_id == default_account.account_id
    assert usage.email == "default@example.com"
    assert usage.plan_type == "team"


def test_codex_account_service_get_usage_info_reads_snapshot_by_number(tmp_path: Path) -> None:
    auth_path = tmp_path / ".codex" / "auth.json"
    _write_auth(auth_path, account_id="acct-live", email="live@example.com")
    service = CodexAccountService(
        auth_path=auth_path,
        accounts_dir=tmp_path / "accounts",
        manifest_path=tmp_path / "accounts" / "accounts.json",
    )
    default_account = service.list_accounts()[0]

    snapshot_path = tmp_path / "accounts" / f"{default_account.account_id}.json"
    snapshot_path.write_text(
        json.dumps(
            {
                "auth_mode": "chatgpt",
                "OPENAI_API_KEY": None,
                "tokens": {
                    "access_token": _jwt(
                        {
                            "sub": "acct-default",
                            "https://api.openai.com/profile": {"email": "default@example.com"},
                        }
                    ),
                    "refresh_token": "***",
                    "id_token": _jwt(
                        {
                            "email": "default@example.com",
                            "https://api.openai.com/auth": {
                                "chatgpt_plan_type": "team",
                                "chatgpt_subscription_active_until": "2099-06-10T00:00:00+00:00",
                            },
                        }
                    ),
                    "account_id": "acct-default",
                },
            }
        ),
        encoding="utf-8",
    )

    service._fetch_backend_usage_payload = lambda _payload: {
        "email": "default@example.com",
        "plan_type": "team",
        "rate_limit": {
            "primary_window": {"used_percent": 10, "limit_window_seconds": 18000, "reset_at": 1776579494},
            "secondary_window": {"used_percent": 20, "limit_window_seconds": 604800, "reset_at": 1776997593},
        },
    }

    usage = service.get_usage_info("1")

    assert usage.account_id == default_account.account_id
    assert usage.email == "default@example.com"
    assert usage.plan_type == "team"


def test_codex_account_service_caches_static_usage_fields_between_live_requests(tmp_path: Path) -> None:
    auth_path = tmp_path / ".codex" / "auth.json"
    _write_auth(auth_path, account_id="acct-live", email="live@example.com")
    service = CodexAccountService(
        auth_path=auth_path,
        accounts_dir=tmp_path / "accounts",
        manifest_path=tmp_path / "accounts" / "accounts.json",
    )
    default_account = service.list_accounts()[0]

    responses = [
        {
            "email": "default@example.com",
            "plan_type": "team",
            "rate_limit": {
                "primary_window": {"used_percent": 10, "limit_window_seconds": 18000, "reset_at": 1776579494},
                "secondary_window": {"used_percent": 20, "limit_window_seconds": 604800, "reset_at": 1776997593},
            },
            "credits": {"has_credits": True, "unlimited": True, "balance": "42"},
        },
        {
            "rate_limit": {
                "primary_window": {"used_percent": 35, "limit_window_seconds": 18000, "reset_at": 1776583094},
                "secondary_window": {"used_percent": 40, "limit_window_seconds": 604800, "reset_at": 1777001193},
            }
        },
    ]

    service._fetch_backend_usage_payload = lambda _payload: responses.pop(0)

    first_usage = service.get_usage_info(default_account.account_id)
    second_usage = service.get_usage_info(default_account.account_id)

    assert first_usage.plan_type == "team"
    assert second_usage.plan_type == "team"
    assert second_usage.email == "live@example.com"
    assert second_usage.has_credits is True
    assert second_usage.credits_unlimited is True
    assert second_usage.credits_balance == "42"
    assert second_usage.five_hour_used_percent == 35.0
    assert second_usage.weekly_used_percent == 40.0


def test_codex_account_service_writes_usage_cache_without_holding_live_request_results_only_in_memory(tmp_path: Path) -> None:
    auth_path = tmp_path / ".codex" / "auth.json"
    _write_auth(auth_path, account_id="acct-live", email="live@example.com")
    service = CodexAccountService(
        auth_path=auth_path,
        accounts_dir=tmp_path / "accounts",
        manifest_path=tmp_path / "accounts" / "accounts.json",
    )
    default_account = service.list_accounts()[0]
    service._fetch_backend_usage_payload = lambda _payload: {
        "email": "default@example.com",
        "plan_type": "team",
        "rate_limit": {
            "primary_window": {"used_percent": 10, "limit_window_seconds": 18000, "reset_at": 1776579494},
            "secondary_window": {"used_percent": 20, "limit_window_seconds": 604800, "reset_at": 1776997593},
        },
    }

    service.get_usage_info(default_account.account_id)

    cache_path = tmp_path / "accounts" / "usage-cache" / f"{default_account.account_id}.json"
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    assert payload["auth_fingerprint"] == service._fingerprint_payload(  # type: ignore[attr-defined]
        json.loads((tmp_path / "accounts" / f"{default_account.account_id}.json").read_text(encoding="utf-8"))
    )
    assert payload["usage"]["plan_type"] == "team"


def test_codex_account_service_switches_account_by_email(tmp_path: Path) -> None:
    auth_path = tmp_path / ".codex" / "auth.json"
    _write_auth(auth_path, account_id="acct-old", email="old@example.com")
    service = CodexAccountService(
        auth_path=auth_path,
        accounts_dir=tmp_path / "accounts",
        manifest_path=tmp_path / "accounts" / "accounts.json",
        now=lambda: 320,
    )
    old_account = service.list_accounts()[0]

    _write_auth(auth_path, account_id="acct-new", email="new@example.com")
    with service._locked_accounts_io():  # type: ignore[attr-defined]
        with service._lock:  # type: ignore[attr-defined]
            stored_accounts = service._load_accounts_locked()  # type: ignore[attr-defined]
            service._import_current_auth_locked(stored_accounts, "")  # type: ignore[attr-defined]

    switched_status = service.switch_account("old@example.com")

    assert switched_status.current_account_label == "old@example.com"
    assert switched_status.current_account_id == old_account.account_id
    assert json.loads(auth_path.read_text(encoding="utf-8"))["tokens"]["account_id"] == "acct-old"


def test_codex_account_service_switches_account_by_number(tmp_path: Path) -> None:
    auth_path = tmp_path / ".codex" / "auth.json"
    _write_auth(auth_path, account_id="acct-old", email="old@example.com")
    service = CodexAccountService(
        auth_path=auth_path,
        accounts_dir=tmp_path / "accounts",
        manifest_path=tmp_path / "accounts" / "accounts.json",
        now=lambda: 320,
    )
    old_account = service.list_accounts()[0]

    _write_auth(auth_path, account_id="acct-new", email="new@example.com")
    with service._locked_accounts_io():  # type: ignore[attr-defined]
        with service._lock:  # type: ignore[attr-defined]
            stored_accounts = service._load_accounts_locked()  # type: ignore[attr-defined]
            service._import_current_auth_locked(stored_accounts, "")  # type: ignore[attr-defined]

    switched_status = service.switch_account("1")

    assert switched_status.current_account_label == "old@example.com"
    assert switched_status.current_account_id == old_account.account_id
    assert json.loads(auth_path.read_text(encoding="utf-8"))["tokens"]["account_id"] == "acct-old"


def test_codex_account_service_import_current_auth_rejects_missing_login(tmp_path: Path) -> None:
    service = CodexAccountService(
        auth_path=tmp_path / ".codex" / "auth.json",
        accounts_dir=tmp_path / "accounts",
        manifest_path=tmp_path / "accounts" / "accounts.json",
    )

    try:
        service.import_current_auth("Imported")
    except ValueError as exc:
        assert "not logged in" in str(exc)
    else:
        raise AssertionError("expected import_current_auth to fail without auth")


def test_codex_account_service_uses_email_as_default_label(tmp_path: Path) -> None:
    auth_path = tmp_path / ".codex" / "auth.json"
    _write_auth(auth_path, account_id="acct-email", email="person@example.com")
    service = CodexAccountService(
        auth_path=auth_path,
        accounts_dir=tmp_path / "accounts",
        manifest_path=tmp_path / "accounts" / "accounts.json",
    )

    accounts = service.list_accounts()

    assert len(accounts) == 1
    assert accounts[0].label == "person@example.com"


def test_codex_account_service_falls_back_to_access_token_profile_email(tmp_path: Path) -> None:
    auth_path = tmp_path / ".codex" / "auth.json"
    _write_auth(
        auth_path,
        account_id="acct-access",
        email="access@example.com",
        include_id_token=False,
        include_access_token=True,
    )
    service = CodexAccountService(
        auth_path=auth_path,
        accounts_dir=tmp_path / "accounts",
        manifest_path=tmp_path / "accounts" / "accounts.json",
    )

    accounts = service.list_accounts()

    assert len(accounts) == 1
    assert accounts[0].label == "access@example.com"


def test_codex_account_service_falls_back_to_requested_label_without_email(tmp_path: Path) -> None:
    auth_path = tmp_path / ".codex" / "auth.json"
    _write_auth(
        auth_path,
        account_id="acct-no-email",
        email=None,
        include_id_token=False,
        include_access_token=False,
    )
    service = CodexAccountService(
        auth_path=auth_path,
        accounts_dir=tmp_path / "accounts",
        manifest_path=tmp_path / "accounts" / "accounts.json",
    )

    imported = service.import_current_auth("Manual label")

    assert imported.label == "Manual label"


def test_codex_account_service_falls_back_to_current_account_without_email(tmp_path: Path) -> None:
    auth_path = tmp_path / ".codex" / "auth.json"
    _write_auth(
        auth_path,
        account_id="acct-no-email",
        email=None,
        include_id_token=False,
        include_access_token=False,
    )
    service = CodexAccountService(
        auth_path=auth_path,
        accounts_dir=tmp_path / "accounts",
        manifest_path=tmp_path / "accounts" / "accounts.json",
    )

    accounts = service.list_accounts()

    assert len(accounts) == 1
    assert accounts[0].label == "Current account"


def test_codex_account_status_reports_active_shared_login_state(tmp_path: Path, monkeypatch) -> None:
    auth_path = tmp_path / ".codex" / "auth.json"
    service = CodexAccountService(
        auth_path=auth_path,
        accounts_dir=tmp_path / "accounts",
        manifest_path=tmp_path / "accounts" / "accounts.json",
    )

    state_path = tmp_path / "accounts" / ".login-active.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"pid": 4242}), encoding="utf-8")

    def _alive_kill(pid: int, sig: int) -> None:
        assert sig == 0
        if pid != 4242:
            raise ProcessLookupError()

    monkeypatch.setattr("linux_agent_island.codex_accounts.os.kill", _alive_kill)

    status = service.get_status()

    assert status.device_login_in_progress is True


def test_codex_account_status_cleans_stale_shared_login_state(tmp_path: Path, monkeypatch) -> None:
    auth_path = tmp_path / ".codex" / "auth.json"
    service = CodexAccountService(
        auth_path=auth_path,
        accounts_dir=tmp_path / "accounts",
        manifest_path=tmp_path / "accounts" / "accounts.json",
    )

    state_path = tmp_path / "accounts" / ".login-active.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"pid": 9999}), encoding="utf-8")

    def _dead_kill(_pid: int, _sig: int) -> None:
        raise ProcessLookupError()

    monkeypatch.setattr("linux_agent_island.codex_accounts.os.kill", _dead_kill)

    status = service.get_status()

    assert status.device_login_in_progress is False
    assert not state_path.exists()


def test_terminal_launch_command_prefers_user_login_shell(monkeypatch, tmp_path: Path) -> None:
    service = CodexAccountService(
        auth_path=tmp_path / ".codex" / "auth.json",
        accounts_dir=tmp_path / "accounts",
        manifest_path=tmp_path / "accounts" / "accounts.json",
    )

    monkeypatch.setenv("SHELL", "/bin/zsh")
    monkeypatch.setattr(
        "linux_agent_island.codex_accounts.pwd.getpwuid",
        lambda _uid: pwd_module.struct_passwd(("user", "x", 1000, 1000, "", "/tmp", "/bin/bash")),
    )
    monkeypatch.setattr(
        "linux_agent_island.codex_accounts.shutil.which",
        lambda name: {
            "zsh": "/bin/zsh",
            "gnome-terminal": "/usr/bin/gnome-terminal",
        }.get(name),
    )

    command = service._terminal_launch_command("codex login")  # type: ignore[attr-defined]

    assert command[:2] == ["gnome-terminal", "--"]
    assert command[2:6] == ["/bin/zsh", "-l", "-i", "-c"]
    assert "codex login" in command[6]


def test_terminal_launch_command_falls_back_to_sh_without_user_shell(monkeypatch, tmp_path: Path) -> None:
    service = CodexAccountService(
        auth_path=tmp_path / ".codex" / "auth.json",
        accounts_dir=tmp_path / "accounts",
        manifest_path=tmp_path / "accounts" / "accounts.json",
    )

    monkeypatch.delenv("SHELL", raising=False)
    monkeypatch.setattr(
        "linux_agent_island.codex_accounts.pwd.getpwuid",
        lambda _uid: pwd_module.struct_passwd(("user", "x", 1000, 1000, "", "/tmp", "/usr/sbin/nologin")),
    )
    monkeypatch.setattr(
        "linux_agent_island.codex_accounts.shutil.which",
        lambda name: {
            "xterm": "/usr/bin/xterm",
        }.get(name),
    )

    command = service._terminal_launch_command("codex login")  # type: ignore[attr-defined]

    assert command[:2] == ["xterm", "-e"]
    assert command[2:5] == ["sh", "-lc", command[4]]
    assert "codex login" in command[4]


def test_terminal_launch_command_prefers_terminator_without_dbus(monkeypatch, tmp_path: Path) -> None:
    service = CodexAccountService(
        auth_path=tmp_path / ".codex" / "auth.json",
        accounts_dir=tmp_path / "accounts",
        manifest_path=tmp_path / "accounts" / "accounts.json",
    )

    monkeypatch.setenv("SHELL", "/bin/zsh")
    monkeypatch.setattr(
        "linux_agent_island.codex_accounts.pwd.getpwuid",
        lambda _uid: pwd_module.struct_passwd(("user", "x", 1000, 1000, "", "/tmp", "/bin/zsh")),
    )
    monkeypatch.setattr(
        "linux_agent_island.codex_accounts.shutil.which",
        lambda name: {
            "zsh": "/bin/zsh",
            "terminator": "/usr/bin/terminator",
            "x-terminal-emulator": "/usr/bin/x-terminal-emulator",
        }.get(name),
    )

    command = service._terminal_launch_command("codex login")  # type: ignore[attr-defined]

    assert command[:3] == ["terminator", "--no-dbus", "-x"]
    assert command[3:7] == ["/bin/zsh", "-l", "-i", "-c"]
    assert "codex login" in command[7]


def test_terminal_launch_command_keeps_prompt_after_login_status_write(monkeypatch, tmp_path: Path) -> None:
    codex_bin = tmp_path / "bin" / "codex"
    codex_bin.parent.mkdir(parents=True, exist_ok=True)
    codex_bin.write_text("#!/bin/sh\n", encoding="utf-8")
    codex_bin.chmod(0o755)
    service = CodexAccountService(
        auth_path=tmp_path / ".codex" / "auth.json",
        accounts_dir=tmp_path / "accounts",
        manifest_path=tmp_path / "accounts" / "accounts.json",
        configured_codex_bin=str(codex_bin),
    )

    monkeypatch.setenv("SHELL", "/bin/zsh")
    monkeypatch.setattr(
        "linux_agent_island.codex_accounts.pwd.getpwuid",
        lambda _uid: pwd_module.struct_passwd(("user", "x", 1000, 1000, "", "/tmp", "/bin/zsh")),
    )
    monkeypatch.setattr(
        "linux_agent_island.codex_accounts.shutil.which",
        lambda name: {
            "zsh": "/bin/zsh",
            "gnome-terminal": "/usr/bin/gnome-terminal",
        }.get(name),
    )

    status_path = tmp_path / "accounts" / ".login-status.txt"
    command = service._terminal_launch_command(  # type: ignore[attr-defined]
        service._login_shell_command(status_path)  # type: ignore[attr-defined]
    )
    shell_command = command[6]

    assert f"> {status_path}" in shell_command
    assert "Press Enter to close..." in shell_command
    assert shell_command.index(f"> {status_path}") < shell_command.index("Press Enter to close...")
    assert "status=$?" not in shell_command
    assert shell_command.count("exit $login_rc") == 1


def test_resolve_codex_executable_prefers_configured_path(monkeypatch, tmp_path: Path) -> None:
    service = CodexAccountService(
        auth_path=tmp_path / ".codex" / "auth.json",
        accounts_dir=tmp_path / "accounts",
        manifest_path=tmp_path / "accounts" / "accounts.json",
        configured_codex_bin=str(tmp_path / "bin" / "codex"),
    )
    codex_bin = tmp_path / "bin" / "codex"
    codex_bin.parent.mkdir(parents=True, exist_ok=True)
    codex_bin.write_text("#!/bin/sh\n", encoding="utf-8")
    codex_bin.chmod(0o755)

    monkeypatch.setattr("linux_agent_island.codex_accounts.shutil.which", lambda _name: None)

    assert service._resolve_codex_executable() == str(codex_bin)


def test_resolve_codex_executable_falls_back_to_system_path(monkeypatch, tmp_path: Path) -> None:
    service = CodexAccountService(
        auth_path=tmp_path / ".codex" / "auth.json",
        accounts_dir=tmp_path / "accounts",
        manifest_path=tmp_path / "accounts" / "accounts.json",
    )
    codex_bin = tmp_path / "bin" / "codex"
    codex_bin.parent.mkdir(parents=True, exist_ok=True)
    codex_bin.write_text("#!/bin/sh\n", encoding="utf-8")
    codex_bin.chmod(0o755)

    monkeypatch.setattr("linux_agent_island.codex_accounts.shutil.which", lambda name: str(codex_bin) if name == "codex" else None)

    assert service._resolve_codex_executable() == str(codex_bin)


def test_resolve_codex_executable_raises_when_configured_path_invalid(monkeypatch, tmp_path: Path) -> None:
    service = CodexAccountService(
        auth_path=tmp_path / ".codex" / "auth.json",
        accounts_dir=tmp_path / "accounts",
        manifest_path=tmp_path / "accounts" / "accounts.json",
        configured_codex_bin=str(tmp_path / "missing" / "codex"),
    )

    monkeypatch.setattr("linux_agent_island.codex_accounts.shutil.which", lambda _name: "/usr/bin/codex")

    try:
        service._resolve_codex_executable()
    except RuntimeError as exc:
        assert "configured Codex executable" in str(exc)
    else:
        raise AssertionError("expected configured invalid codex path to fail")


def test_login_shell_command_uses_resolved_codex_executable(monkeypatch, tmp_path: Path) -> None:
    service = CodexAccountService(
        auth_path=tmp_path / ".codex" / "auth.json",
        accounts_dir=tmp_path / "accounts",
        manifest_path=tmp_path / "accounts" / "accounts.json",
    )
    status_path = tmp_path / "accounts" / ".login-status.txt"

    monkeypatch.setattr(
        service,
        "_resolve_codex_executable",
        lambda: "/home/lzn/.nvm/versions/node/v24.13.0/bin/codex",
    )

    command = service._login_shell_command(status_path)

    assert 'export PATH=/home/lzn/.nvm/versions/node/v24.13.0/bin:$PATH;' in command
    assert "/home/lzn/.nvm/versions/node/v24.13.0/bin/codex" in command
    assert " login" in command
    assert "--device-auth" not in command
    assert "codex login" not in command.replace(
        "/home/lzn/.nvm/versions/node/v24.13.0/bin/codex login",
        "",
    )


def test_launch_login_terminal_passes_gui_env(monkeypatch, tmp_path: Path) -> None:
    service = CodexAccountService(
        auth_path=tmp_path / ".codex" / "auth.json",
        accounts_dir=tmp_path / "accounts",
        manifest_path=tmp_path / "accounts" / "accounts.json",
    )
    captured: dict[str, object] = {}

    monkeypatch.setattr(service, "_terminal_launch_command", lambda command: ["fake-terminal", command])
    monkeypatch.setattr(
        service,
        "_gui_environment",
        lambda: {"DISPLAY": ":1", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus"},
    )
    monkeypatch.setattr(
        "linux_agent_island.codex_accounts.subprocess.Popen",
        lambda command, env=None: captured.update({"command": command, "env": env}) or object(),
    )

    service._launch_login_terminal("echo hi")

    assert captured["command"] == ["fake-terminal", "echo hi"]
    assert isinstance(captured["env"], dict)
    assert captured["env"]["DISPLAY"] == ":1"
    assert captured["env"]["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=/run/user/1000/bus"
