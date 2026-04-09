from linux_agent_shell.models import AgentSession, SessionOrigin, SessionPhase
from linux_agent_shell.runtime.agent_events import AgentEvent, AgentEventType
from linux_agent_shell.store import SessionStore


def test_store_upserts_and_archives_sessions() -> None:
    store = SessionStore()
    session = AgentSession(
            provider="codex",
            session_id="abc",
            cwd="/tmp/demo",
            title="Demo",
            phase=SessionPhase.RUNNING,
            model="gpt-5.4",
            sandbox='{"type":"workspace-write"}',
            approval_mode="never",
            updated_at=100,
            origin=SessionOrigin.RESTORED,
            is_process_alive=True,
            last_message_preview="hello",
        )

    store.upsert(session)
    listed = store.list_sessions()

    assert [item.session_id for item in listed] == ["abc"]

    store.archive("codex", "abc")
    assert store.list_sessions() == []


def test_store_keeps_completed_hook_managed_session_visible_until_process_timeout() -> None:
    store = SessionStore()
    store.apply(
        AgentEvent(
            type=AgentEventType.SESSION_STARTED,
            provider="codex",
            session_id="thread-1",
            cwd="/tmp/demo",
            title="Demo",
            phase=SessionPhase.RUNNING,
            updated_at=100,
            origin=SessionOrigin.LIVE,
            is_hook_managed=True,
            pid=123,
        )
    )
    store.apply(
        AgentEvent(
            type=AgentEventType.SESSION_COMPLETED,
            provider="codex",
            session_id="thread-1",
            phase=SessionPhase.COMPLETED,
            updated_at=200,
            last_message_preview="done",
            summary="done",
            origin=SessionOrigin.LIVE,
            is_hook_managed=True,
        )
    )

    session = store.get("codex", "thread-1")
    assert session is not None
    assert session.phase is SessionPhase.COMPLETED
    assert session.completed_at == 200
    assert session.last_message_preview == "done"
    assert session.is_visible_in_island is True

    store.mark_process_liveness(set())
    store.remove_invisible_sessions()

    assert store.get("codex", "thread-1") is not None

    store.mark_process_liveness(set())
    store.remove_invisible_sessions()

    assert store.get("codex", "thread-1") is None


def test_store_restored_sessions_need_two_missed_polls_before_removal() -> None:
    store = SessionStore()
    store.restore_sessions(
        [
            AgentSession(
                provider="codex",
                session_id="dead",
                cwd="/tmp/dead",
                title="Dead",
                phase=SessionPhase.COMPLETED,
                model=None,
                sandbox=None,
                approval_mode=None,
                updated_at=1,
                origin=SessionOrigin.RESTORED,
                is_process_alive=True,
            )
        ]
    )

    store.mark_process_liveness(set())

    session = store.get("codex", "dead")
    assert session is not None
    assert session.is_process_alive is True
    assert session.process_not_seen_count == 1
    assert session.is_visible_in_island is True

    store.mark_process_liveness(set())
    store.remove_invisible_sessions()

    assert store.get("codex", "dead") is None


def test_store_session_end_marks_hook_session_invisible_immediately() -> None:
    store = SessionStore()
    store.apply(
        AgentEvent(
            type=AgentEventType.SESSION_STARTED,
            provider="claude",
            session_id="ended",
            cwd="/tmp/ended",
            title="Ended",
            phase=SessionPhase.WAITING,
            updated_at=1,
            origin=SessionOrigin.LIVE,
            is_hook_managed=True,
            pid=555,
        )
    )

    store.apply(
        AgentEvent(
            type=AgentEventType.SESSION_COMPLETED,
            provider="claude",
            session_id="ended",
            phase=SessionPhase.COMPLETED,
            updated_at=2,
            origin=SessionOrigin.LIVE,
            is_hook_managed=True,
            is_session_end=True,
        )
    )

    session = store.get("claude", "ended")
    assert session is not None
    assert session.is_visible_in_island is False

    store.remove_invisible_sessions()
    assert store.get("claude", "ended") is None


def test_store_lists_visible_sessions_only_when_requested() -> None:
    store = SessionStore()
    store.restore_sessions(
        [
            AgentSession(
                provider="codex",
                session_id="visible",
                cwd="/tmp/a",
                title="Visible",
                phase=SessionPhase.COMPLETED,
                model=None,
                sandbox=None,
                approval_mode=None,
                updated_at=2,
                origin=SessionOrigin.RESTORED,
                is_process_alive=True,
            ),
            AgentSession(
                provider="codex",
                session_id="hidden",
                cwd="/tmp/b",
                title="Hidden",
                phase=SessionPhase.COMPLETED,
                model=None,
                sandbox=None,
                approval_mode=None,
                updated_at=1,
                origin=SessionOrigin.RESTORED,
                is_process_alive=False,
                process_not_seen_count=2,
            ),
        ]
    )

    sessions = store.list_sessions(visible_only=True)

    assert [item.session_id for item in sessions] == ["visible"]


def test_store_adopts_alive_set_and_resets_process_counter() -> None:
    store = SessionStore()
    store.restore_sessions(
        [
            AgentSession(
                provider="codex",
                session_id="alive",
                cwd="/tmp/alive",
                title="Alive",
                phase=SessionPhase.COMPLETED,
                model=None,
                sandbox=None,
                approval_mode=None,
                updated_at=2,
                origin=SessionOrigin.RESTORED,
                is_process_alive=True,
                process_not_seen_count=1,
            )
        ]
    )

    store.mark_process_liveness({("codex", "alive")})

    session = store.get("codex", "alive")
    assert session is not None
    assert session.is_process_alive is True
    assert session.process_not_seen_count == 0
