import json
from pathlib import Path

from linux_agent_island.core.models import SessionPhase
from linux_agent_island.providers.claude import ClaudeProvider


def test_claude_provider_merges_hooks_into_settings(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "Stop": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "echo existing",
                                }
                            ]
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    provider = ClaudeProvider(
        settings_path=settings_path,
        hook_script_path=Path("/opt/linux-agent-island/claude-hook.py"),
        socket_path=tmp_path / "events.sock",
    )

    provider.install_hooks()
    payload = json.loads(settings_path.read_text(encoding="utf-8"))

    assert "SessionStart" in payload["hooks"]
    assert "PermissionRequest" in payload["hooks"]
    stop_commands = [
        hook["command"]
        for entry in payload["hooks"]["Stop"]
        for hook in entry["hooks"]
    ]
    assert "echo existing" in stop_commands
    assert any("claude-hook.py Stop" in command for command in stop_commands)


def test_claude_provider_maps_hook_event_to_session_update(tmp_path: Path) -> None:
    provider = ClaudeProvider(
        settings_path=tmp_path / "settings.json",
        hook_script_path=tmp_path / "claude-hook.py",
        socket_path=tmp_path / "events.sock",
    )

    session = provider.session_from_event(
        {
            "provider": "claude",
            "session_id": "claude-1",
            "cwd": "/tmp/workspace/demo",
            "event": "Notification",
            "status": "waiting_for_input",
            "model": "sonnet",
            "updated_at": 123,
        }
    )

    assert session.provider == "claude"
    assert session.session_id == "claude-1"
    assert session.title == "demo"
    assert session.phase is SessionPhase.WAITING
    assert session.model == "sonnet"


def test_claude_provider_maps_permission_requests_to_attention_phase(tmp_path: Path) -> None:
    provider = ClaudeProvider(
        settings_path=tmp_path / "settings.json",
        hook_script_path=tmp_path / "claude-hook.py",
        socket_path=tmp_path / "events.sock",
    )

    session = provider.session_from_event(
        {
            "provider": "claude",
            "session_id": "claude-2",
            "cwd": "/tmp/workspace/demo",
            "event": "PermissionRequest",
            "status": "waiting_for_approval",
            "model": "sonnet",
            "updated_at": 123,
        }
    )

    assert session.phase is SessionPhase.WAITING_APPROVAL
