import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path

from linux_agent_island.core.models import AgentSession, SessionOrigin, SessionPhase
from linux_agent_island.providers.gemini import GeminiProvider
from linux_agent_island.runtime.agent_events import AgentEventType


def _managed_hook(command: str) -> dict[str, object]:
    return {
        "type": "command",
        "command": command,
        "timeout": 10000,
        "name": "linux-agent-island",
    }


def test_gemini_provider_merges_hooks_into_settings(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        json.dumps(
            {
                "ui": {"theme": "Dracula"},
                "hooks": {
                    "AfterAgent": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "/custom/after.sh",
                                    "timeout": 1000,
                                }
                            ]
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    provider = GeminiProvider(
        settings_path=settings_path,
        tmp_dir=tmp_path / "tmp",
        hook_command_prefix="/venv/bin/python -m linux_agent_island.hooks",
    )

    provider.install_hooks()
    payload = json.loads(settings_path.read_text(encoding="utf-8"))

    assert payload["ui"] == {"theme": "Dracula"}
    assert "SessionStart" in payload["hooks"]
    assert "BeforeAgent" in payload["hooks"]
    assert "AfterAgent" in payload["hooks"]
    assert "SessionEnd" in payload["hooks"]
    assert "Notification" in payload["hooks"]
    after_commands = [
        hook["command"]
        for entry in payload["hooks"]["AfterAgent"]
        for hook in entry["hooks"]
    ]
    assert "/custom/after.sh" in after_commands
    assert "/venv/bin/python -m linux_agent_island.hooks gemini AfterAgent" in after_commands


def test_gemini_provider_deduplicates_existing_managed_hooks(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    command = "/venv/bin/python -m linux_agent_island.hooks gemini BeforeAgent"
    settings_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "BeforeAgent": [
                        {
                            "hooks": [
                                _managed_hook(command),
                                _managed_hook(command),
                                {"type": "command", "command": "/custom/before.sh"},
                            ]
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    provider = GeminiProvider(
        settings_path=settings_path,
        tmp_dir=tmp_path / "tmp",
        hook_command_prefix="/venv/bin/python -m linux_agent_island.hooks",
    )

    provider.install_hooks()
    payload = json.loads(settings_path.read_text(encoding="utf-8"))
    commands = [hook["command"] for entry in payload["hooks"]["BeforeAgent"] for hook in entry["hooks"]]

    assert commands.count(command) == 1
    assert "/custom/before.sh" in commands


def test_gemini_provider_replaces_old_module_hook_prefix(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    old_command = "PYTHONPATH=/checkout /usr/bin/python3 -m linux_agent_island.hooks gemini SessionEnd"
    new_command = "/venv/bin/python -m linux_agent_island.hooks gemini SessionEnd"
    settings_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionEnd": [
                        {
                            "hooks": [
                                {"type": "command", "command": old_command, "timeout": 10000},
                                {"type": "command", "command": "/custom/end.sh"},
                            ]
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    provider = GeminiProvider(
        settings_path=settings_path,
        tmp_dir=tmp_path / "tmp",
        hook_command_prefix="/venv/bin/python -m linux_agent_island.hooks",
    )

    provider.install_hooks()
    payload = json.loads(settings_path.read_text(encoding="utf-8"))
    commands = [hook["command"] for entry in payload["hooks"]["SessionEnd"] for hook in entry["hooks"]]

    assert old_command not in commands
    assert new_command in commands
    assert "/custom/end.sh" in commands


def test_gemini_provider_uninstalls_only_managed_hooks(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    managed = "/venv/bin/python -m linux_agent_island.hooks gemini Notification"
    settings_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "Notification": [
                        {
                            "hooks": [
                                _managed_hook(managed),
                                {"type": "command", "command": "/custom/notify.sh"},
                            ]
                        }
                    ],
                    "BeforeTool": [{"hooks": [{"type": "command", "command": "/custom/tool.sh"}]}],
                }
            }
        ),
        encoding="utf-8",
    )
    provider = GeminiProvider(
        settings_path=settings_path,
        tmp_dir=tmp_path / "tmp",
        hook_command_prefix="/venv/bin/python -m linux_agent_island.hooks",
    )

    provider.uninstall_hooks()
    payload = json.loads(settings_path.read_text(encoding="utf-8"))

    assert payload["hooks"]["Notification"] == [{"hooks": [{"type": "command", "command": "/custom/notify.sh"}]}]
    assert payload["hooks"]["BeforeTool"] == [{"hooks": [{"type": "command", "command": "/custom/tool.sh"}]}]


def test_gemini_provider_loads_transcript_from_chat_file(tmp_path: Path) -> None:
    chat_dir = tmp_path / "tmp" / "project" / "chats"
    chat_dir.mkdir(parents=True)
    (chat_dir / "session-2026-04-10T01-02-abcd1234.json").write_text(
        json.dumps(
            {
                "sessionId": "abcd1234-0000-4000-9000-abcdefabcdef",
                "messages": [
                    {
                        "type": "user",
                        "timestamp": "2026-04-10T01:02:00Z",
                        "content": [{"text": "hello"}],
                    },
                    {
                        "type": "gemini",
                        "timestamp": "2026-04-10T01:02:01Z",
                        "content": "hi",
                    },
                    {"type": "tool", "content": "ignored"},
                ],
            }
        ),
        encoding="utf-8",
    )
    provider = GeminiProvider(
        settings_path=tmp_path / "settings.json",
        tmp_dir=tmp_path / "tmp",
        hook_command_prefix="/venv/bin/python -m linux_agent_island.hooks",
    )

    assert provider.load_transcript("abcd1234-0000-4000-9000-abcdefabcdef") == [
        {"role": "user", "text": "hello", "timestamp": "2026-04-10T01:02:00Z"},
        {"role": "assistant", "text": "hi", "timestamp": "2026-04-10T01:02:01Z"},
    ]


def test_gemini_provider_loads_sessions_from_tmp_dir(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    projects_path = tmp_path / "projects.json"
    tmp_dir = tmp_path / "tmp"

    projects_path.write_text(
        json.dumps(
            {
                "projects": {
                    "/home/user/project1": "nickname1",
                }
            }
        ),
        encoding="utf-8",
    )

    chat_dir = tmp_dir / "nickname1" / "chats"
    chat_dir.mkdir(parents=True)
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    (chat_dir / "session-2026-04-10T01-02-abcd1234.json").write_text(
        json.dumps(
            {
                "sessionId": "abcd1234",
                "projectHash": "nickname1",
                "startTime": now_iso,
                "lastUpdated": now_iso,
                "messages": [
                    {
                        "type": "user",
                        "content": "hello",
                    },
                    {
                        "type": "user",
                        "content": "latest prompt",
                    },
                    {
                        "type": "gemini",
                        "model": "gemini-2.5-pro",
                        "content": "hi",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    provider = GeminiProvider(
        settings_path=settings_path,
        tmp_dir=tmp_dir,
        hook_command_prefix="/venv/bin/python -m linux_agent_island.hooks",
    )

    sessions = provider.load_sessions()
    assert len(sessions) == 1
    session = sessions[0]
    assert session.session_id == "abcd1234"
    assert session.cwd == "/home/user/project1"
    assert session.title == "latest prompt"
    assert session.provider == "gemini"
    assert session.model == "gemini-2.5-pro"
    assert session.is_hook_managed is True
    assert session.is_process_alive is True


def test_gemini_provider_loads_sessions_with_sha256_project_hash(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    tmp_dir = tmp_path / "tmp"
    project_cwd = "/home/user/project-hashed"
    project_hash = hashlib.sha256(project_cwd.encode("utf-8")).hexdigest()
    project_nickname = "project-hashed"
    (tmp_path / "projects.json").write_text(
        json.dumps({"projects": {project_cwd: project_nickname}}),
        encoding="utf-8",
    )

    chat_dir = tmp_dir / project_nickname / "chats"
    chat_dir.mkdir(parents=True)
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    (chat_dir / "session-2026-04-10T01-02-hashed.json").write_text(
        json.dumps(
            {
                "sessionId": "hashed-session",
                "projectHash": project_hash,
                "startTime": now_iso,
                "lastUpdated": now_iso,
                "messages": [
                    {"type": "user", "content": "hello"},
                    {"type": "gemini", "model": "gemini-3-flash-preview", "content": "hi"},
                ],
            }
        ),
        encoding="utf-8",
    )
    provider = GeminiProvider(
        settings_path=settings_path,
        tmp_dir=tmp_dir,
        hook_command_prefix="/venv/bin/python -m linux_agent_island.hooks",
    )

    sessions = provider.load_sessions()
    assert len(sessions) == 1
    assert sessions[0].cwd == project_cwd
    assert sessions[0].model == "gemini-3-flash-preview"


def test_gemini_provider_poll_events_backfills_model_from_session_file(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    projects_path = tmp_path / "projects.json"
    tmp_dir = tmp_path / "tmp"
    projects_path.write_text(
        json.dumps({"projects": {"/home/user/project1": "nickname1"}}),
        encoding="utf-8",
    )
    chat_dir = tmp_dir / "nickname1" / "chats"
    chat_dir.mkdir(parents=True)
    (chat_dir / "session-2026-04-10T01-02-abcd1234.json").write_text(
        json.dumps(
            {
                "sessionId": "abcd1234",
                "projectHash": "nickname1",
                "lastUpdated": "2026-04-10T01:02:10Z",
                "messages": [
                    {"type": "user", "content": "hello"},
                    {"type": "gemini", "model": "gemini-2.5-pro", "content": "hi"},
                ],
            }
        ),
        encoding="utf-8",
    )
    provider = GeminiProvider(
        settings_path=settings_path,
        tmp_dir=tmp_dir,
        hook_command_prefix="/venv/bin/python -m linux_agent_island.hooks",
    )
    sessions = [
        AgentSession(
            provider="gemini",
            session_id="abcd1234",
            cwd="/home/user/project1",
            title="latest prompt",
            phase=SessionPhase.RUNNING,
            model=None,
            sandbox=None,
            approval_mode=None,
            updated_at=1,
            origin=SessionOrigin.LIVE,
            is_hook_managed=True,
            is_process_alive=True,
        )
    ]

    events = provider.poll_events(sessions)
    assert len(events) == 1
    event = events[0]
    assert event.type is AgentEventType.METADATA_UPDATED
    assert event.provider == "gemini"
    assert event.session_id == "abcd1234"
    assert event.model == "gemini-2.5-pro"
    assert event.source == "gemini_session_poll"


def test_gemini_provider_extracts_model_from_nested_alias_fields(tmp_path: Path) -> None:
    provider = GeminiProvider(
        settings_path=tmp_path / "settings.json",
        tmp_dir=tmp_path / "tmp",
        hook_command_prefix="/venv/bin/python -m linux_agent_island.hooks",
    )

    event = provider.build_event(
        "BeforeAgent",
        {
            "session_id": "gemini-1",
            "cwd": "/tmp/demo",
            "prompt": "latest prompt",
            "metadata": {
                "selectedModel": "gemini-2.5-flash",
            },
        },
        pid=123,
        tty="/dev/pts/7",
    )

    assert event["model"] == "gemini-2.5-flash"


def test_gemini_provider_session_start_is_initially_completed_not_running(tmp_path: Path) -> None:
    provider = GeminiProvider(
        settings_path=tmp_path / "settings.json",
        tmp_dir=tmp_path / "tmp",
        hook_command_prefix="/venv/bin/python -m linux_agent_island.hooks",
    )

    # 1. SessionStart should be "completed" (idle)
    start_event = provider.build_event(
        "SessionStart",
        {"session_id": "gemini-1", "cwd": "/tmp/demo"},
        pid=123,
        tty="/dev/pts/7",
    )
    assert start_event["phase"] == "completed"

    # 2. BeforeAgent should be "running"
    before_event = provider.build_event(
        "BeforeAgent",
        {"session_id": "gemini-1", "cwd": "/tmp/demo", "prompt": "think about it"},
        pid=123,
        tty="/dev/pts/7",
    )
    assert before_event["phase"] == "running"
