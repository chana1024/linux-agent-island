import json
import subprocess
from pathlib import Path

from linux_agent_shell import hooks


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
    assert event["phase"] == "running"
    assert event["event_type"] == "session_started"


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
    monkeypatch.setattr(
        hooks,
        "_is_codex_subagent_session",
        lambda _db_path, session_id: session_id == "subagent-1",
    )
    monkeypatch.setattr(hooks.sys, "argv", ["codex-hook.py", "codex", "UserPromptSubmit"])

    assert hooks.main() == 0
    assert emitted == []
    assert capsys.readouterr().out == ""


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
