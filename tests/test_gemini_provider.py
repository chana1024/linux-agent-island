import json
from pathlib import Path

from linux_agent_island.providers.gemini import GeminiProvider


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
