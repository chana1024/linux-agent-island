import json
from pathlib import Path

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
