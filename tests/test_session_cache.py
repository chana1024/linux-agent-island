from pathlib import Path

from linux_agent_island.core.models import AgentSession, PermissionRequest, QuestionOption, QuestionPrompt, SessionOrigin, SessionPhase
from linux_agent_island.runtime.session_cache import SessionCache


def test_session_cache_round_trips_session_metadata(tmp_path: Path) -> None:
    cache = SessionCache(tmp_path / "sessions.json")
    session = AgentSession(
        provider="codex",
        session_id="thread-1",
        cwd="/tmp/project",
        title="Build the thing",
        phase=SessionPhase.COMPLETED,
        model="gpt-5.4",
        sandbox='{"type":"workspace-write"}',
        approval_mode="never",
        updated_at=123,
        origin=SessionOrigin.RESTORED,
        summary="latest summary",
        pid=4321,
        tty="/dev/pts/7",
        is_hook_managed=True,
        is_session_ended=False,
        is_process_alive=True,
        process_not_seen_count=1,
        last_message_preview="done",
        permission_request=PermissionRequest(
            title="Permission required",
            summary="allow write",
            affected_path="/tmp/project",
        ),
        question_prompt=QuestionPrompt(
            title="Need input",
            options=[QuestionOption(label="Yes"), QuestionOption(label="No")],
        ),
    )

    cache.save([session])
    restored = cache.load()

    assert len(restored) == 1
    restored_session = restored[0]
    assert restored_session.session_id == "thread-1"
    assert restored_session.pid == 4321
    assert restored_session.tty == "/dev/pts/7"
    assert restored_session.is_hook_managed is True
    assert restored_session.is_process_alive is True
    assert restored_session.process_not_seen_count == 1
    assert restored_session.last_message_preview == "done"
    assert restored_session.permission_request is not None
    assert restored_session.permission_request.affected_path == "/tmp/project"
    assert restored_session.question_prompt is not None
    assert [option.label for option in restored_session.question_prompt.options] == ["Yes", "No"]


def test_session_cache_returns_empty_list_for_missing_file(tmp_path: Path) -> None:
    cache = SessionCache(tmp_path / "missing.json")
    assert cache.load() == []
