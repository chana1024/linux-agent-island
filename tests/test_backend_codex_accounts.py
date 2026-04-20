import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("gi")

from linux_agent_island.app import backend as backend_module
from linux_agent_island.app.backend import BackendService
from linux_agent_island.core.config import AppConfig


def test_backend_serializes_codex_account_status(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    auth_path = home / ".codex" / "auth.json"
    auth_path.parent.mkdir(parents=True, exist_ok=True)
    auth_path.write_text(
        json.dumps(
            {
                "auth_mode": "chatgpt",
                "tokens": {
                    "access_token": "eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0.eyJodHRwczovL2FwaS5vcGVuYWkuY29tL3Byb2ZpbGUiOnsiZW1haWwiOiJiYWNrZW5kQGV4YW1wbGUuY29tIn19.",
                    "refresh_token": "refresh-token",
                    "id_token": "eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0.eyJlbWFpbCI6ImJhY2tlbmRAZXhhbXBsZS5jb20ifQ.",
                    "account_id": "acct-1",
                },
            }
        ),
        encoding="utf-8",
    )

    service = BackendService(config=AppConfig.default(root=tmp_path))

    accounts = json.loads(service._serialize_codex_accounts())
    status = json.loads(service._serialize_codex_account_status())

    assert len(accounts) == 1
    assert accounts[0]["label"] == "backend@example.com"
    assert status["logged_in"] is True
    assert status["current_account_managed"] is True
    assert status["switch_affects_new_sessions_only"] is True



def test_backend_starts_codex_login_cli_asynchronously(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    service = BackendService(config=AppConfig.default(root=tmp_path))
    started_threads: list[tuple[object, tuple[object, ...]]] = []
    popen_calls: list[tuple[list[str], dict[str, str] | None]] = []

    class FakeProcess:
        pid = 4321

    class FakeThread:
        def __init__(self, *, target, args, daemon):
            started_threads.append((target, args))
            assert daemon is True

        def start(self) -> None:
            return None

    monkeypatch.setattr(service, "_gui_environment", lambda: {"DISPLAY": ":1", "XDG_RUNTIME_DIR": "/run/user/1000"})
    monkeypatch.setattr(
        backend_module.subprocess,
        "Popen",
        lambda command, env=None: popen_calls.append((command, env)) or FakeProcess(),
    )
    monkeypatch.setattr(backend_module.threading, "Thread", FakeThread)

    started = service._start_codex_login_process("Work")

    assert started is True
    assert popen_calls[0][0] == [sys.executable, "-m", "linux_agent_island.cli", "codex", "login", "--label", "Work"]
    assert popen_calls[0][1]["DISPLAY"] == ":1"
    assert popen_calls[0][1]["XDG_RUNTIME_DIR"] == "/run/user/1000"
    assert len(started_threads) == 1
    assert started_threads[0][1][0].pid == 4321
