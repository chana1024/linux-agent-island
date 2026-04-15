import json
import subprocess
from pathlib import Path
import logging

from linux_agent_island import hooks


def test_codex_hook_event_captures_pid_and_tty(monkeypatch) -> None:
    class Completed:
        stdout = "pts/7\n"
        stderr = ""

    monkeypatch.setattr(hooks.os, "getppid", lambda: 4321)
    monkeypatch.setattr(hooks.subprocess, "run", lambda *args, **kwargs: Completed())

    event = hooks._build_codex_event(  # noqa: SLF001
        "SessionStart",
        {
            "session_id": "session-1",
            "cwd": "/tmp/demo",
            "model": "gpt-5.4",
        },
    )

    assert event["pid"] == 4321
    assert event["tty"] == "/dev/pts/7"
    assert event["phase"] == "completed"
    assert event["event_type"] == "session_started"
    assert event["event_source"] == "SessionStart"


def test_codex_stop_hook_maps_to_waiting_with_message(monkeypatch) -> None:
    monkeypatch.setattr(hooks.os, "getppid", lambda: 4321)

    def raise_subprocess(*_args, **_kwargs):
        raise OSError("no ps")

    monkeypatch.setattr(hooks.subprocess, "run", raise_subprocess)
    monkeypatch.setattr(hooks, "_detect_tty_from_streams", lambda: "/dev/pts/9")

    event = hooks._build_codex_event(  # noqa: SLF001
        "Stop",
        {
            "session_id": "session-1",
            "cwd": "/tmp/demo",
            "last_assistant_message": "done",
        },
    )

    assert event["phase"] == "completed"
    assert event["tty"] == "/dev/pts/9"
    assert event["title"] == ""
    assert event["last_message_preview"] == "done"
    assert event["event_type"] == "session_completed"
    assert event["event_source"] == "Stop"


def test_codex_user_prompt_hook_uses_latest_prompt_as_title(monkeypatch) -> None:
    class Completed:
        stdout = "pts/7\n"
        stderr = ""

    monkeypatch.setattr(hooks.os, "getppid", lambda: 4321)
    monkeypatch.setattr(hooks.subprocess, "run", lambda *args, **kwargs: Completed())

    event = hooks._build_codex_event(  # noqa: SLF001
        "UserPromptSubmit",
        {
            "session_id": "session-1",
            "cwd": "/tmp/demo",
            "text": "latest prompt",
        },
    )

    assert event["title"] == "latest prompt"
    assert event["phase"] == "running"
    assert event["event_type"] == "activity_updated"
    assert event["event_source"] == "UserPromptSubmit"
    assert event["started_at"] == event["updated_at"]


def test_codex_main_skips_subagent_sessions(monkeypatch, tmp_path: Path, capsys) -> None:
    emitted: list[dict[str, object]] = []

    monkeypatch.setattr(
        hooks,
        "_load_stdin_json",
        lambda: {
            "session_id": "subagent-1",
            "cwd": "/tmp/demo",
            "text": "latest prompt",
        },
    )
    monkeypatch.setattr(
        hooks.AppConfig,
        "default",
        classmethod(
            lambda cls: hooks.AppConfig(
                root=tmp_path,
                runtime_dir=tmp_path,
                session_cache_path=tmp_path / "sessions.json",
                frontend_settings_path=tmp_path / "settings.json",
                claude_settings_path=tmp_path / "claude-settings.json",
                codex_state_db_path=tmp_path / "state.sqlite",
                codex_history_path=tmp_path / "history.jsonl",
                codex_hooks_path=tmp_path / "hooks.json",
                claude_hook_script_path=tmp_path / "claude-hook.py",
                codex_hook_script_path=tmp_path / "codex-hook.py",
                event_socket_path=tmp_path / "events.sock",
            )
        ),
    )
    monkeypatch.setattr(hooks, "emit_runtime_event", lambda _path, payload: emitted.append(payload))
    monkeypatch.setattr(hooks, "_configure_hook_logging", lambda _config: "INFO")
    monkeypatch.setattr(
        hooks,
        "_is_codex_subagent_session",
        lambda _db_path, session_id: session_id == "subagent-1",
    )
    monkeypatch.setattr(hooks.sys, "argv", ["codex-hook.py", "codex", "UserPromptSubmit"])

    assert hooks.main() == 0
    assert emitted == []
    assert capsys.readouterr().out == ""


def test_main_logs_hook_trigger_and_emitted_event(monkeypatch, tmp_path: Path, caplog) -> None:
    emitted: list[dict[str, object]] = []
    config = hooks.AppConfig.default(tmp_path)

    monkeypatch.setattr(
        hooks,
        "_load_stdin_json",
        lambda: {
            "session_id": "session-1",
            "cwd": "/tmp/demo",
            "text": "latest prompt",
        },
    )
    monkeypatch.setattr(hooks.AppConfig, "default", classmethod(lambda cls: config))
    monkeypatch.setattr(hooks, "_configure_hook_logging", lambda _config: "INFO")
    monkeypatch.setattr(hooks, "emit_runtime_event", lambda _path, payload: emitted.append(payload))
    monkeypatch.setattr(hooks, "_is_codex_subagent_session", lambda *_args: False)
    monkeypatch.setattr(
        hooks.sys,
        "argv",
        ["codex-hook.py", "codex", "UserPromptSubmit"],
    )

    with caplog.at_level(logging.INFO):
        assert hooks.main() == 0

    assert emitted
    assert "hook triggered provider=codex hook=UserPromptSubmit session_id=session-1 cwd=/tmp/demo" in caplog.text
    assert "hook emitted runtime event provider=codex hook=UserPromptSubmit session_id=session-1" in caplog.text


def test_main_logs_subagent_skip(monkeypatch, tmp_path: Path, caplog) -> None:
    config = hooks.AppConfig.default(tmp_path)

    monkeypatch.setattr(
        hooks,
        "_load_stdin_json",
        lambda: {
            "session_id": "subagent-1",
            "cwd": "/tmp/demo",
        },
    )
    monkeypatch.setattr(hooks.AppConfig, "default", classmethod(lambda cls: config))
    monkeypatch.setattr(hooks, "_configure_hook_logging", lambda _config: "INFO")
    monkeypatch.setattr(hooks, "emit_runtime_event", lambda *_args: None)
    monkeypatch.setattr(hooks, "_is_codex_subagent_session", lambda *_args: True)
    monkeypatch.setattr(hooks.sys, "argv", ["codex-hook.py", "codex", "Stop"])

    with caplog.at_level(logging.INFO):
        assert hooks.main() == 0

    assert "hook skipped for codex subagent provider=codex hook=Stop session_id=subagent-1" in caplog.text


def test_claude_notification_hook_does_not_reset_title(monkeypatch) -> None:
    monkeypatch.setattr(hooks.time, "time", lambda: 123)

    event = hooks._build_claude_event(  # noqa: SLF001
        "Notification",
        {
            "session_id": "claude-1",
            "cwd": "/tmp/demo",
            "message": "assistant message",
        },
    )

    assert event["title"] == ""
    assert event["last_message_preview"] == "assistant message"
    assert event["event_type"] == "question_asked"
    assert event["question_prompt"]["title"] == "assistant message"


def test_claude_user_prompt_hook_uses_latest_prompt_as_title(monkeypatch) -> None:
    monkeypatch.setattr(hooks.time, "time", lambda: 123)

    event = hooks._build_claude_event(  # noqa: SLF001
        "UserPromptSubmit",
        {
            "session_id": "claude-1",
            "cwd": "/tmp/demo",
            "message": "latest prompt",
        },
    )

    assert event["title"] == "latest prompt"
    assert event["phase"] == "running"
    assert event["event_type"] == "activity_updated"
    assert event["started_at"] == 123


def test_gemini_before_agent_hook_uses_prompt_as_title(monkeypatch) -> None:
    class Completed:
        stdout = "pts/7\n"
        stderr = ""

    monkeypatch.setattr(hooks.os, "getppid", lambda: 4321)
    monkeypatch.setattr(hooks.subprocess, "run", lambda *args, **kwargs: Completed())
    monkeypatch.setattr(hooks.time, "time", lambda: 123)

    event = hooks._build_gemini_event(  # noqa: SLF001
        "BeforeAgent",
        {
            "session_id": "gemini-1",
            "cwd": "/tmp/demo",
            "prompt": "latest prompt",
            "llm_request": {"model": "gemini-3-flash-preview"},
        },
    )

    assert event["provider"] == "gemini"
    assert event["title"] == "latest prompt"
    assert event["phase"] == "running"
    assert event["model"] == "gemini-3-flash-preview"
    assert event["started_at"] == 123
    assert event["pid"] == 4321
    assert event["tty"] == "/dev/pts/7"


def test_gemini_after_agent_hook_marks_completed(monkeypatch) -> None:
    monkeypatch.setattr(hooks.os, "getppid", lambda: 4321)
    monkeypatch.setattr(hooks.subprocess, "run", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("no ps")))
    monkeypatch.setattr(hooks, "_detect_tty_from_streams", lambda: "/dev/pts/9")

    event = hooks._build_gemini_event(  # noqa: SLF001
        "AfterAgent",
        {
            "session_id": "gemini-1",
            "cwd": "/tmp/demo",
            "prompt_response": "done",
        },
    )

    assert event["event_type"] == "session_completed"
    assert event["phase"] == "completed"
    assert event["last_message_preview"] == "done"
    assert event["tty"] == "/dev/pts/9"


def test_gemini_tool_permission_notification_requires_attention(monkeypatch) -> None:
    monkeypatch.setattr(hooks.os, "getppid", lambda: 4321)
    monkeypatch.setattr(hooks.subprocess, "run", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("no ps")))
    monkeypatch.setattr(hooks, "_detect_tty_from_streams", lambda: None)

    event = hooks._build_gemini_event(  # noqa: SLF001
        "Notification",
        {
            "session_id": "gemini-1",
            "cwd": "/tmp/demo",
            "notification_type": "ToolPermission",
            "message": "permission needed",
        },
    )

    assert event["phase"] == "waiting_approval"
    assert event["last_message_preview"] == "permission needed"
    assert event["event_type"] == "permission_requested"
    assert event["permission_request"]["summary"] == "permission needed"


def test_gemini_notification_hook_maps_non_permission_to_waiting_answer(monkeypatch) -> None:
    monkeypatch.setattr(hooks.os, "getppid", lambda: 4321)
    monkeypatch.setattr(hooks.subprocess, "run", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("no ps")))
    monkeypatch.setattr(hooks, "_detect_tty_from_streams", lambda: None)

    event = hooks._build_gemini_event(  # noqa: SLF001
        "Notification",
        {
            "session_id": "gemini-1",
            "cwd": "/tmp/demo",
            "notification_type": "Question",
            "message": "need input",
        },
    )

    assert event["phase"] == "waiting_answer"
    assert event["event_type"] == "question_asked"
    assert event["question_prompt"]["title"] == "need input"


def test_gemini_session_end_hook_marks_session_end(monkeypatch) -> None:
    monkeypatch.setattr(hooks.os, "getppid", lambda: 4321)
    monkeypatch.setattr(hooks.subprocess, "run", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("no ps")))
    monkeypatch.setattr(hooks, "_detect_tty_from_streams", lambda: None)

    event = hooks._build_gemini_event(  # noqa: SLF001
        "SessionEnd",
        {
            "session_id": "gemini-1",
            "cwd": "/tmp/demo",
        },
    )

    assert event["event_type"] == "session_completed"
    assert event["phase"] == "completed"
    assert event["is_session_end"] is True


def test_gemini_main_emits_event_and_prints_json(monkeypatch, tmp_path: Path, capsys) -> None:
    emitted: list[dict[str, object]] = []

    monkeypatch.setattr(
        hooks,
        "_load_stdin_json",
        lambda: {
            "session_id": "gemini-1",
            "cwd": "/tmp/demo",
            "prompt": "latest prompt",
        },
    )
    monkeypatch.setattr(
        hooks.AppConfig,
        "default",
        classmethod(
            lambda cls: hooks.AppConfig(
                root=tmp_path,
                runtime_dir=tmp_path,
                session_cache_path=tmp_path / "sessions.json",
                frontend_settings_path=tmp_path / "settings.json",
                claude_settings_path=tmp_path / "claude-settings.json",
                codex_state_db_path=tmp_path / "state.sqlite",
                codex_history_path=tmp_path / "history.jsonl",
                codex_hooks_path=tmp_path / "hooks.json",
                claude_hook_script_path=tmp_path / "claude-hook.py",
                codex_hook_script_path=tmp_path / "codex-hook.py",
                event_socket_path=tmp_path / "events.sock",
            )
        ),
    )
    monkeypatch.setattr(hooks, "emit_runtime_event", lambda _path, payload: emitted.append(payload))
    monkeypatch.setattr(hooks.sys, "argv", ["hooks.py", "gemini", "BeforeAgent"])
    monkeypatch.setattr(hooks.os, "getppid", lambda: 4321)
    monkeypatch.setattr(hooks.subprocess, "run", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("no ps")))
    monkeypatch.setattr(hooks, "_detect_tty_from_streams", lambda: None)

    assert hooks.main() == 0

    assert emitted[0]["provider"] == "gemini"
    assert json.loads(capsys.readouterr().out) == {"suppressOutput": True}
