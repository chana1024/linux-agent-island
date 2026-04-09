import json
import sqlite3
from pathlib import Path

from linux_agent_shell.models import SessionOrigin, SessionPhase
from linux_agent_shell.providers.codex import CodexProvider


def _write_thread_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE threads (
            id TEXT PRIMARY KEY,
            rollout_path TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            source TEXT NOT NULL,
            model_provider TEXT NOT NULL,
            cwd TEXT NOT NULL,
            title TEXT NOT NULL,
            sandbox_policy TEXT NOT NULL,
            approval_mode TEXT NOT NULL,
            tokens_used INTEGER NOT NULL DEFAULT 0,
            has_user_event INTEGER NOT NULL DEFAULT 0,
            archived INTEGER NOT NULL DEFAULT 0,
            archived_at INTEGER,
            git_sha TEXT,
            git_branch TEXT,
            git_origin_url TEXT,
            cli_version TEXT NOT NULL DEFAULT '',
            first_user_message TEXT NOT NULL DEFAULT '',
            agent_nickname TEXT,
            agent_role TEXT,
            memory_mode TEXT NOT NULL DEFAULT 'enabled',
            model TEXT,
            reasoning_effort TEXT,
            agent_path TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO threads (
            id, rollout_path, created_at, updated_at, source, model_provider, cwd, title,
            sandbox_policy, approval_mode, model
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "thread-1",
            "rollout",
            100,
            250,
            "interactive",
            "openai",
            "/tmp/project",
            "Build the thing",
            '{"type":"workspace-write"}',
            "never",
            "gpt-5.4",
        ),
    )
    conn.commit()
    conn.close()


def _managed_hook(command: str) -> dict[str, object]:
    return {"type": "command", "command": command, "timeout": 10}


def test_codex_provider_loads_sessions_from_sqlite_and_history(tmp_path: Path) -> None:
    db_path = tmp_path / "state.sqlite"
    history_path = tmp_path / "history.jsonl"
    _write_thread_db(db_path)
    history_path.write_text(
        "\n".join(
            [
                json.dumps({"session_id": "thread-1", "ts": 111, "text": "first prompt"}),
                json.dumps({"session_id": "thread-1", "ts": 222, "text": "latest prompt"}),
            ]
        ),
        encoding="utf-8",
    )

    provider = CodexProvider(
        state_db_path=db_path,
        history_path=history_path,
        hooks_config_path=tmp_path / "hooks.json",
        hook_script_path=tmp_path / "codex-hook.py",
        recent_window_seconds=60,
    )

    sessions = provider.load_sessions(now=260)

    assert len(sessions) == 1
    session = sessions[0]
    assert session.provider == "codex"
    assert session.session_id == "thread-1"
    assert session.cwd == "/tmp/project"
    assert session.title == "Build the thing"
    assert session.model == "gpt-5.4"
    assert session.approval_mode == "never"
    assert session.last_message_preview == "latest prompt"
    assert session.phase is SessionPhase.COMPLETED
    assert session.origin is SessionOrigin.RESTORED
    assert session.is_process_alive is True


def test_codex_provider_filters_out_inactive_threads(tmp_path: Path) -> None:
    db_path = tmp_path / "state.sqlite"
    history_path = tmp_path / "history.jsonl"
    _write_thread_db(db_path)

    provider = CodexProvider(
        state_db_path=db_path,
        history_path=history_path,
        hooks_config_path=tmp_path / "hooks.json",
        hook_script_path=tmp_path / "codex-hook.py",
        recent_window_seconds=10,
    )

    sessions = provider.load_sessions(now=300)

    assert sessions == []


def test_codex_provider_ignores_subagent_threads_from_sqlite(tmp_path: Path) -> None:
    db_path = tmp_path / "state.sqlite"
    history_path = tmp_path / "history.jsonl"
    _write_thread_db(db_path)
    history_path.write_text(
        "\n".join(
            [
                json.dumps({"session_id": "thread-1", "ts": 111, "text": "main prompt"}),
                json.dumps({"session_id": "thread-2", "ts": 222, "text": "subagent prompt"}),
            ]
        ),
        encoding="utf-8",
    )

    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO threads (
            id, rollout_path, created_at, updated_at, source, model_provider, cwd, title,
            sandbox_policy, approval_mode, agent_nickname, agent_role, model
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "thread-2",
            "rollout-2",
            110,
            255,
            json.dumps(
                {
                    "subagent": {
                        "thread_spawn": {
                            "parent_thread_id": "thread-1",
                            "depth": 1,
                            "agent_nickname": "Kierkegaard",
                            "agent_role": "explorer",
                        }
                    }
                }
            ),
            "openai",
            "/tmp/project",
            "Subagent thread",
            '{"type":"workspace-write"}',
            "never",
            "Kierkegaard",
            "explorer",
            "gpt-5.4",
        ),
    )
    conn.commit()
    conn.close()

    provider = CodexProvider(
        state_db_path=db_path,
        history_path=history_path,
        hooks_config_path=tmp_path / "hooks.json",
        hook_script_path=tmp_path / "codex-hook.py",
        recent_window_seconds=60,
    )

    sessions = provider.load_sessions(now=260)

    assert [session.session_id for session in sessions] == ["thread-1"]


def test_codex_provider_filters_cached_subagent_sessions(tmp_path: Path) -> None:
    db_path = tmp_path / "state.sqlite"
    _write_thread_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO threads (
            id, rollout_path, created_at, updated_at, source, model_provider, cwd, title,
            sandbox_policy, approval_mode, agent_nickname, agent_role, model
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "thread-2",
            "rollout-2",
            110,
            255,
            json.dumps(
                {
                    "subagent": {
                        "thread_spawn": {
                            "parent_thread_id": "thread-1",
                            "depth": 1,
                            "agent_nickname": "Kierkegaard",
                            "agent_role": "explorer",
                        }
                    }
                }
            ),
            "openai",
            "/tmp/project",
            "Subagent thread",
            '{"type":"workspace-write"}',
            "never",
            "Kierkegaard",
            "explorer",
            "gpt-5.4",
        ),
    )
    conn.commit()
    conn.close()

    provider = CodexProvider(
        state_db_path=db_path,
        history_path=tmp_path / "history.jsonl",
        hooks_config_path=tmp_path / "hooks.json",
        hook_script_path=tmp_path / "codex-hook.py",
    )

    cached_sessions = [
        provider.load_sessions(now=260)[0],
        provider.load_sessions(now=260)[0].__class__(
            provider="codex",
            session_id="thread-2",
            cwd="/tmp/project",
            title="Subagent thread",
            phase=SessionPhase.RUNNING,
            model="gpt-5.4",
            sandbox='{"type":"workspace-write"}',
            approval_mode="never",
            updated_at=255,
            origin=SessionOrigin.RESTORED,
            is_process_alive=True,
        ),
    ]

    filtered = provider.filter_cached_sessions(cached_sessions)

    assert [session.session_id for session in filtered] == ["thread-1"]


def test_codex_provider_merges_required_hooks(tmp_path: Path) -> None:
    hooks_path = tmp_path / "hooks.json"
    hooks_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "Stop": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "/existing/stop.sh",
                                    "timeout": 10,
                                }
                            ]
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    provider = CodexProvider(
        state_db_path=tmp_path / "state.sqlite",
        history_path=tmp_path / "history.jsonl",
        hooks_config_path=hooks_path,
        hook_script_path=Path("/opt/linux-agent-shell/codex-hook.py"),
    )

    provider.install_hooks()
    payload = json.loads(hooks_path.read_text(encoding="utf-8"))

    session_start_commands = [
        hook["command"]
        for entry in payload["hooks"]["SessionStart"]
        if isinstance(entry, dict) and isinstance(entry.get("hooks"), list)
        for hook in entry["hooks"]
        if isinstance(hook, dict)
    ]
    user_prompt_submit_commands = [
        hook["command"]
        for entry in payload["hooks"]["UserPromptSubmit"]
        if isinstance(entry, dict) and isinstance(entry.get("hooks"), list)
        for hook in entry["hooks"]
        if isinstance(hook, dict)
    ]
    stop_commands = [
        hook["command"]
        for entry in payload["hooks"]["Stop"]
        for hook in entry["hooks"]
    ]

    assert "/usr/bin/python3 /opt/linux-agent-shell/codex-hook.py SessionStart" in session_start_commands
    assert (
        "/usr/bin/python3 /opt/linux-agent-shell/codex-hook.py UserPromptSubmit"
        in user_prompt_submit_commands
    )
    assert "/existing/stop.sh" in stop_commands
    assert "/usr/bin/python3 /opt/linux-agent-shell/codex-hook.py Stop" in stop_commands
    assert "PreToolUse" not in payload["hooks"]
    assert "PostToolUse" not in payload["hooks"]


def test_codex_provider_deduplicates_existing_managed_required_hooks(tmp_path: Path) -> None:
    hooks_path = tmp_path / "hooks.json"
    hooks_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "/usr/bin/python3 /opt/linux-agent-shell/codex-hook.py SessionStart",
                                    "timeout": 10,
                                },
                                {
                                    "type": "command",
                                    "command": "/usr/bin/python3 /opt/linux-agent-shell/codex-hook.py SessionStart",
                                    "timeout": 10,
                                },
                                {
                                    "type": "command",
                                    "command": "/custom/start.sh",
                                    "timeout": 10,
                                },
                            ]
                        }
                    ],
                    "Stop": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "/usr/bin/python3 /opt/linux-agent-shell/codex-hook.py Stop",
                                    "timeout": 10,
                                },
                                {
                                    "type": "command",
                                    "command": "/usr/bin/python3 /opt/linux-agent-shell/codex-hook.py Stop",
                                    "timeout": 10,
                                },
                            ]
                        }
                    ],
                }
            }
        ),
        encoding="utf-8",
    )

    provider = CodexProvider(
        state_db_path=tmp_path / "state.sqlite",
        history_path=tmp_path / "history.jsonl",
        hooks_config_path=hooks_path,
        hook_script_path=Path("/opt/linux-agent-shell/codex-hook.py"),
    )

    provider.install_hooks()
    payload = json.loads(hooks_path.read_text(encoding="utf-8"))

    session_start_commands = [
        hook["command"]
        for entry in payload["hooks"]["SessionStart"]
        for hook in entry["hooks"]
    ]
    stop_commands = [
        hook["command"]
        for entry in payload["hooks"]["Stop"]
        for hook in entry["hooks"]
    ]

    assert session_start_commands.count("/usr/bin/python3 /opt/linux-agent-shell/codex-hook.py SessionStart") == 1
    assert "/custom/start.sh" in session_start_commands
    assert stop_commands.count("/usr/bin/python3 /opt/linux-agent-shell/codex-hook.py Stop") == 1


def test_codex_provider_moves_scoped_managed_required_hook_to_canonical_entry(tmp_path: Path) -> None:
    hooks_path = tmp_path / "hooks.json"
    hooks_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {
                            "matcher": "scoped",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "/usr/bin/python3 /opt/linux-agent-shell/codex-hook.py SessionStart",
                                    "timeout": 10,
                                },
                                {
                                    "type": "command",
                                    "command": "/custom/start.sh",
                                    "timeout": 10,
                                },
                            ],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    provider = CodexProvider(
        state_db_path=tmp_path / "state.sqlite",
        history_path=tmp_path / "history.jsonl",
        hooks_config_path=hooks_path,
        hook_script_path=Path("/opt/linux-agent-shell/codex-hook.py"),
    )

    provider.install_hooks()
    payload = json.loads(hooks_path.read_text(encoding="utf-8"))

    assert payload["hooks"]["SessionStart"] == [
        {
            "matcher": "scoped",
            "hooks": [
                {
                    "type": "command",
                    "command": "/custom/start.sh",
                    "timeout": 10,
                }
            ],
        },
        {
            "hooks": [
                _managed_hook("/usr/bin/python3 /opt/linux-agent-shell/codex-hook.py SessionStart")
            ]
        },
    ]


def test_codex_provider_canonicalizes_malformed_managed_required_hooks(tmp_path: Path) -> None:
    hooks_path = tmp_path / "hooks.json"
    hooks_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "UserPromptSubmit": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "/usr/bin/python3 /opt/linux-agent-shell/codex-hook.py UserPromptSubmit",
                                    "timeout": 1,
                                    "unexpected": True,
                                },
                                {
                                    "type": "command",
                                    "command": "/usr/bin/python3 /opt/linux-agent-shell/codex-hook.py UserPromptSubmit",
                                    "timeout": 10,
                                },
                                {
                                    "type": "command",
                                    "command": "/custom/prompt.sh",
                                    "timeout": 10,
                                },
                            ]
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    provider = CodexProvider(
        state_db_path=tmp_path / "state.sqlite",
        history_path=tmp_path / "history.jsonl",
        hooks_config_path=hooks_path,
        hook_script_path=Path("/opt/linux-agent-shell/codex-hook.py"),
    )

    provider.install_hooks()
    payload = json.loads(hooks_path.read_text(encoding="utf-8"))

    user_prompt_submit_hooks = [
        hook
        for entry in payload["hooks"]["UserPromptSubmit"]
        if isinstance(entry, dict) and isinstance(entry.get("hooks"), list)
        for hook in entry["hooks"]
        if isinstance(hook, dict)
    ]
    canonical_hook = _managed_hook(
        "/usr/bin/python3 /opt/linux-agent-shell/codex-hook.py UserPromptSubmit"
    )

    assert user_prompt_submit_hooks.count(canonical_hook) == 1
    assert {"type": "command", "command": "/custom/prompt.sh", "timeout": 10} in user_prompt_submit_hooks
    assert all(hook.get("unexpected") is not True for hook in user_prompt_submit_hooks)


def test_codex_provider_handles_non_dict_hooks_section_without_crashing(tmp_path: Path) -> None:
    hooks_path = tmp_path / "hooks.json"
    hooks_path.write_text(json.dumps({"hooks": []}), encoding="utf-8")

    provider = CodexProvider(
        state_db_path=tmp_path / "state.sqlite",
        history_path=tmp_path / "history.jsonl",
        hooks_config_path=hooks_path,
        hook_script_path=Path("/opt/linux-agent-shell/codex-hook.py"),
    )

    provider.install_hooks()
    payload = json.loads(hooks_path.read_text(encoding="utf-8"))

    assert set(payload["hooks"]) == {"SessionStart", "UserPromptSubmit", "Stop"}


def test_codex_provider_recovers_from_invalid_json_hooks_file(tmp_path: Path) -> None:
    hooks_path = tmp_path / "hooks.json"
    hooks_path.write_text("{not valid json", encoding="utf-8")

    provider = CodexProvider(
        state_db_path=tmp_path / "state.sqlite",
        history_path=tmp_path / "history.jsonl",
        hooks_config_path=hooks_path,
        hook_script_path=Path("/opt/linux-agent-shell/codex-hook.py"),
    )

    provider.install_hooks()
    payload = json.loads(hooks_path.read_text(encoding="utf-8"))

    assert set(payload["hooks"]) == {"SessionStart", "UserPromptSubmit", "Stop"}
    assert payload["hooks"]["SessionStart"] == [
        {"hooks": [_managed_hook("/usr/bin/python3 /opt/linux-agent-shell/codex-hook.py SessionStart")]}
    ]
    assert payload["hooks"]["UserPromptSubmit"] == [
        {"hooks": [_managed_hook("/usr/bin/python3 /opt/linux-agent-shell/codex-hook.py UserPromptSubmit")]}
    ]
    assert payload["hooks"]["Stop"] == [
        {"hooks": [_managed_hook("/usr/bin/python3 /opt/linux-agent-shell/codex-hook.py Stop")]}
    ]


def test_codex_provider_handles_malformed_event_entries_without_crashing(tmp_path: Path) -> None:
    hooks_path = tmp_path / "hooks.json"
    hooks_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": "broken",
                    "UserPromptSubmit": [
                        "broken",
                        {
                            "hooks": "broken",
                        },
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "/custom/prompt.sh",
                                    "timeout": 10,
                                }
                            ]
                        },
                    ],
                    "PreToolUse": [
                        "broken-entry",
                        {
                            "hooks": "broken",
                        }
                    ],
                }
            }
        ),
        encoding="utf-8",
    )

    provider = CodexProvider(
        state_db_path=tmp_path / "state.sqlite",
        history_path=tmp_path / "history.jsonl",
        hooks_config_path=hooks_path,
        hook_script_path=Path("/opt/linux-agent-shell/codex-hook.py"),
    )

    provider.install_hooks()
    payload = json.loads(hooks_path.read_text(encoding="utf-8"))

    session_start_commands = [
        hook["command"]
        for entry in payload["hooks"]["SessionStart"]
        for hook in entry["hooks"]
    ]
    user_prompt_submit_commands = [
        hook["command"]
        for entry in payload["hooks"]["UserPromptSubmit"]
        if isinstance(entry, dict) and isinstance(entry.get("hooks"), list)
        for hook in entry["hooks"]
        if isinstance(hook, dict)
    ]

    assert session_start_commands.count("/usr/bin/python3 /opt/linux-agent-shell/codex-hook.py SessionStart") == 1
    assert payload["hooks"]["UserPromptSubmit"][0] == "broken"
    assert payload["hooks"]["UserPromptSubmit"][1] == {"hooks": "broken"}
    assert "/custom/prompt.sh" in user_prompt_submit_commands
    assert user_prompt_submit_commands.count("/usr/bin/python3 /opt/linux-agent-shell/codex-hook.py UserPromptSubmit") == 1
    assert payload["hooks"]["PreToolUse"] == ["broken-entry", {"hooks": "broken"}]


def test_codex_provider_removes_managed_pre_and_post_hooks_but_keeps_unrelated_hooks(
    tmp_path: Path,
) -> None:
    hooks_path = tmp_path / "hooks.json"
    managed_pre = "/usr/bin/python3 /opt/linux-agent-shell/codex-hook.py PreToolUse"
    managed_post = "/usr/bin/python3 /opt/linux-agent-shell/codex-hook.py PostToolUse"
    hooks_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": managed_pre,
                                    "timeout": 10,
                                },
                                {
                                    "type": "command",
                                    "command": "/custom/pre.sh",
                                    "timeout": 10,
                                },
                            ]
                        }
                    ],
                    "PostToolUse": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": managed_post,
                                    "timeout": 10,
                                },
                                {
                                    "type": "command",
                                    "command": "/custom/post.sh",
                                    "timeout": 10,
                                }
                            ]
                        }
                    ],
                    "CustomHook": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "/custom/notify.sh",
                                    "timeout": 10,
                                }
                            ]
                        }
                    ],
                }
            }
        ),
        encoding="utf-8",
    )

    provider = CodexProvider(
        state_db_path=tmp_path / "state.sqlite",
        history_path=tmp_path / "history.jsonl",
        hooks_config_path=hooks_path,
        hook_script_path=Path("/opt/linux-agent-shell/codex-hook.py"),
    )

    provider.install_hooks()
    payload = json.loads(hooks_path.read_text(encoding="utf-8"))

    pre_commands = [
        hook["command"]
        for entry in payload["hooks"]["PreToolUse"]
        for hook in entry["hooks"]
    ]
    post_commands = [
        hook["command"]
        for entry in payload["hooks"]["PostToolUse"]
        for hook in entry["hooks"]
    ]
    custom_commands = [
        hook["command"]
        for entry in payload["hooks"]["CustomHook"]
        for hook in entry["hooks"]
    ]

    assert managed_pre not in pre_commands
    assert "/custom/pre.sh" in pre_commands
    assert managed_post not in post_commands
    assert "/custom/post.sh" in post_commands
    assert "/custom/notify.sh" in custom_commands


def test_codex_provider_removes_empty_legacy_hook_sections_when_only_managed_hook_present(
    tmp_path: Path,
) -> None:
    hooks_path = tmp_path / "hooks.json"
    hooks_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "/usr/bin/python3 /opt/linux-agent-shell/codex-hook.py PreToolUse",
                                    "timeout": 10,
                                }
                            ]
                        }
                    ],
                    "PostToolUse": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "/usr/bin/python3 /opt/linux-agent-shell/codex-hook.py PostToolUse",
                                    "timeout": 10,
                                }
                            ]
                        }
                    ],
                }
            }
        ),
        encoding="utf-8",
    )

    provider = CodexProvider(
        state_db_path=tmp_path / "state.sqlite",
        history_path=tmp_path / "history.jsonl",
        hooks_config_path=hooks_path,
        hook_script_path=Path("/opt/linux-agent-shell/codex-hook.py"),
    )

    provider.install_hooks()
    payload = json.loads(hooks_path.read_text(encoding="utf-8"))

    assert "PreToolUse" not in payload["hooks"]
    assert "PostToolUse" not in payload["hooks"]
