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
        hook_command_prefix="/opt/linux-agent-island/venv/bin/python -m linux_agent_island.hooks",
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
    assert (
        "/opt/linux-agent-island/venv/bin/python -m linux_agent_island.hooks claude Stop"
        in stop_commands
    )


def test_claude_provider_removes_old_managed_hook_paths(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    old_command = "/usr/bin/python3 /home/me/linux-agent-island/bin/claude-hook.py Stop"
    settings_path.write_text(
        json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": old_command}]}]}}),
        encoding="utf-8",
    )

    provider = ClaudeProvider(
        settings_path=settings_path,
        hook_command_prefix="/venv/bin/python -m linux_agent_island.hooks",
        socket_path=tmp_path / "events.sock",
    )

    provider.install_hooks()
    payload = json.loads(settings_path.read_text(encoding="utf-8"))
    stop_commands = [
        hook["command"]
        for entry in payload["hooks"]["Stop"]
        for hook in entry["hooks"]
    ]

    assert old_command not in stop_commands
    assert "/venv/bin/python -m linux_agent_island.hooks claude Stop" in stop_commands


def test_claude_provider_maps_hook_event_to_session_update(tmp_path: Path) -> None:
    provider = ClaudeProvider(
        settings_path=tmp_path / "settings.json",
        hook_command_prefix="/venv/bin/python -m linux_agent_island.hooks",
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
        hook_command_prefix="/venv/bin/python -m linux_agent_island.hooks",
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
