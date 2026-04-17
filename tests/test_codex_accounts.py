import json
import threading
from pathlib import Path
import pwd as pwd_module

from linux_agent_island.codex_accounts import CodexAccountService
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
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    token_payload: dict[str, object] = {"sub": account_id}
    if email is not None:
        token_payload["email"] = email
    access_token_payload: dict[str, object] = {
        "sub": account_id,
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
                "last_refresh": "2026-04-17T00:00:00Z",
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
        if account.label == "Personal"
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
    assert status.current_account_label == "Work login"
    assert status.has_running_codex_sessions is True
    assert len(status.accounts) == 1


def test_codex_account_service_login_preserves_old_account_and_imports_new_one(tmp_path: Path) -> None:
    auth_path = tmp_path / ".codex" / "auth.json"
    _write_auth(auth_path, account_id="acct-old", email="old@example.com")
    manifest_path = tmp_path / "accounts" / "accounts.json"
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
    assert status.current_account_label == "New login"
    assert len(status.accounts) == 2
    assert json.loads(auth_path.read_text(encoding="utf-8"))["tokens"]["account_id"] == "acct-new"

    switched_status = service.switch_account(old_account.account_id)
    assert switched_status.current_account_id == old_account.account_id
    assert json.loads(auth_path.read_text(encoding="utf-8"))["tokens"]["account_id"] == "acct-old"
    assert not any((tmp_path / "accounts").glob(".login-backup-*"))


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
    assert shell_command.count("exit $status") == 1
