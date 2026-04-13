import json
from pathlib import Path

from linux_agent_island.core.models import SessionPhase
from linux_agent_island.providers.claude import ClaudeProvider
from linux_agent_island.providers.utils import current_timestamp


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


def test_claude_provider_replaces_old_module_hook_prefix(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    old_command = "PYTHONPATH=/checkout /usr/bin/python3 -m linux_agent_island.hooks claude Stop"
    new_command = "/venv/bin/python -m linux_agent_island.hooks claude Stop"
    settings_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "Stop": [
                        {
                            "hooks": [
                                {"type": "command", "command": old_command},
                                {"type": "command", "command": "echo existing"},
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
    assert stop_commands.count(new_command) == 1
    assert "echo existing" in stop_commands


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


def test_claude_provider_loads_transcript_from_project_file(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    session_dir = projects_dir / "-tmp-workspace-demo"
    session_dir.mkdir(parents=True)
    (session_dir / "claude-1.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"type": "file-history-snapshot"}),
                json.dumps(
                    {
                        "type": "user",
                        "sessionId": "claude-1",
                        "timestamp": "2026-04-10T00:00:00Z",
                        "message": {"role": "user", "content": "hello"},
                    }
                ),
                json.dumps(
                    {
                        "type": "assistant",
                        "sessionId": "claude-1",
                        "timestamp": "2026-04-10T00:00:01Z",
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": "hi"}],
                        },
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )
    provider = ClaudeProvider(
        settings_path=tmp_path / "settings.json",
        hook_command_prefix="/venv/bin/python -m linux_agent_island.hooks",
        socket_path=tmp_path / "events.sock",
        projects_dir=projects_dir,
    )

    assert provider.load_transcript("claude-1", "/tmp/workspace/demo") == [
        {"role": "user", "text": "hello", "timestamp": "2026-04-10T00:00:00Z"},
        {"role": "assistant", "text": "hi", "timestamp": "2026-04-10T00:00:01Z"},
    ]


def test_claude_provider_loads_sessions(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    session_dir = projects_dir / "-tmp-workspace-demo"
    session_dir.mkdir(parents=True)
    
    # ms timestamp
    now_ms = int(current_timestamp() * 1000)
    
    (session_dir / "claude-1.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "user",
                        "timestamp": now_ms - 1000,
                        "message": {"role": "user", "content": "hello world"},
                    }
                ),
                json.dumps(
                    {
                        "type": "assistant",
                        "status": "waiting_for_input",
                        "timestamp": now_ms,
                        "cwd": "/tmp/workspace/demo",
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )
    
    provider = ClaudeProvider(
        settings_path=tmp_path / "settings.json",
        hook_command_prefix="/venv/bin/python -m linux_agent_island.hooks",
        socket_path=tmp_path / "events.sock",
        projects_dir=projects_dir,
    )

    sessions = provider.load_sessions()
    assert len(sessions) == 1
    session = sessions[0]
    assert session.session_id == "claude-1"
    assert session.cwd == "/tmp/workspace/demo"
    assert session.title == "hello world"
    assert session.is_process_alive is True


def test_claude_provider_loads_sessions_with_iso_timestamp(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    session_dir = projects_dir / "-tmp-workspace-demo"
    session_dir.mkdir(parents=True)

    (session_dir / "claude-iso.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "user",
                        "timestamp": "2026-04-13T05:55:03Z",
                        "message": {"role": "user", "content": "hello iso"},
                    }
                ),
                json.dumps(
                    {
                        "type": "assistant",
                        "status": "waiting_for_input",
                        "timestamp": "2026-04-13T05:55:04Z",
                        "cwd": "/tmp/workspace/demo",
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    provider = ClaudeProvider(
        settings_path=tmp_path / "settings.json",
        hook_command_prefix="/venv/bin/python -m linux_agent_island.hooks",
        socket_path=tmp_path / "events.sock",
        projects_dir=projects_dir,
        recent_window_seconds=10_000_000,
    )

    sessions = provider.load_sessions()
    assert len(sessions) == 1
    assert sessions[0].session_id == "claude-iso"
