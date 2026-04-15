from dataclasses import replace

from linux_agent_island.core.models import (
    AgentSession,
    ClaudeSessionMetadata,
    CodexSessionMetadata,
    PermissionRequest,
    QuestionOption,
    QuestionPrompt,
    SessionOrigin,
    SessionPhase,
)
from linux_agent_island.runtime.agent_events import AgentEvent, AgentEventType
from linux_agent_island.core.store import SessionStore


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
            phase=SessionPhase.WAITING_ANSWER,
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


def test_store_resets_started_at_from_user_prompt_activity() -> None:
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
        )
    )
    store.apply(
        AgentEvent(
            type=AgentEventType.ACTIVITY_UPDATED,
            provider="codex",
            session_id="thread-1",
            title="latest prompt",
            phase=SessionPhase.RUNNING,
            updated_at=250,
            started_at=250,
            origin=SessionOrigin.LIVE,
            is_hook_managed=True,
        )
    )

    session = store.get("codex", "thread-1")

    assert session is not None
    assert session.started_at == 250
    assert session.title == "latest prompt"


def test_store_restore_preserves_hook_managed_state() -> None:
    store = SessionStore()
    store.restore_sessions(
        [
            AgentSession(
                provider="codex",
                session_id="hooked",
                cwd="/tmp/hooked",
                title="Hooked",
                phase=SessionPhase.COMPLETED,
                model=None,
                sandbox=None,
                approval_mode=None,
                updated_at=10,
                origin=SessionOrigin.RESTORED,
                is_hook_managed=True,
                is_session_ended=True,
                is_process_alive=True,
            )
        ]
    )

    session = store.get("codex", "hooked")

    assert session is not None
    assert session.is_hook_managed is True
    assert session.is_session_ended is True


def test_store_hook_runtime_identity_reassigns_pid_and_tty_to_real_session() -> None:
    store = SessionStore()
    store.restore_sessions(
        [
            AgentSession(
                provider="codex",
                session_id="guessed",
                cwd="/tmp/demo",
                title="Guessed",
                phase=SessionPhase.COMPLETED,
                model=None,
                sandbox=None,
                approval_mode=None,
                updated_at=10,
                origin=SessionOrigin.RESTORED,
                pid=500,
                tty="/dev/pts/7",
                is_process_alive=True,
            ),
            AgentSession(
                provider="codex",
                session_id="real",
                cwd="/tmp/demo",
                title="Real",
                phase=SessionPhase.RUNNING,
                model=None,
                sandbox=None,
                approval_mode=None,
                updated_at=20,
                origin=SessionOrigin.RESTORED,
                is_process_alive=True,
            ),
        ]
    )

    changed = store.reassign_runtime_identity("codex", "real", 500, "/dev/pts/7")

    guessed = store.get("codex", "guessed")
    real = store.get("codex", "real")

    assert changed is True
    assert guessed is not None
    assert guessed.pid is None
    assert guessed.tty is None
    assert real is not None
    assert real.identity_confirmed_by_hook is True


def test_reconcile_process_matches_does_not_regress_phase_with_stale_snapshot() -> None:
    store = SessionStore()
    store.apply(
        AgentEvent(
            type=AgentEventType.SESSION_STARTED,
            provider="gemini",
            session_id="thread-1",
            cwd="/tmp/demo",
            title="Demo",
            phase=SessionPhase.RUNNING,
            updated_at=100,
            origin=SessionOrigin.LIVE,
            is_hook_managed=True,
            pid=1000,
            tty="/dev/pts/1",
        )
    )
    stale = store.get("gemini", "thread-1")
    assert stale is not None

    store.apply(
        AgentEvent(
            type=AgentEventType.SESSION_COMPLETED,
            provider="gemini",
            session_id="thread-1",
            phase=SessionPhase.COMPLETED,
            updated_at=200,
            origin=SessionOrigin.LIVE,
            is_hook_managed=True,
        )
    )

    changed = store.reconcile_process_matches(
        [replace(stale, pid=2000, tty="/dev/pts/2", has_interactive_window=True, is_focused=True)]
    )
    session = store.get("gemini", "thread-1")

    assert changed is True
    assert session is not None
    assert session.phase is SessionPhase.COMPLETED
    assert session.updated_at == 200
    assert session.completed_at == 200
    assert session.pid == 2000
    assert session.tty == "/dev/pts/2"
    assert session.has_interactive_window is True
    assert session.is_focused is True


def test_store_hook_activity_with_pid_marks_session_as_hook_confirmed() -> None:
    store = SessionStore()

    session = store.apply(
        AgentEvent(
            type=AgentEventType.ACTIVITY_UPDATED,
            provider="codex",
            session_id="thread-1",
            cwd="/tmp/demo",
            title="Demo",
            phase=SessionPhase.RUNNING,
            updated_at=100,
            origin=SessionOrigin.LIVE,
            is_hook_managed=True,
            pid=1234,
            tty="/dev/pts/9",
        )
    )

    assert session.identity_confirmed_by_hook is True


def test_store_activity_reopens_completed_turn() -> None:
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
        )
    )
    store.apply(
        AgentEvent(
            type=AgentEventType.SESSION_COMPLETED,
            provider="codex",
            session_id="thread-1",
            phase=SessionPhase.COMPLETED,
            updated_at=200,
            origin=SessionOrigin.LIVE,
            is_hook_managed=True,
            summary="done",
        )
    )
    store.apply(
        AgentEvent(
            type=AgentEventType.ACTIVITY_UPDATED,
            provider="codex",
            session_id="thread-1",
            phase=SessionPhase.RUNNING,
            updated_at=250,
            origin=SessionOrigin.LIVE,
            is_hook_managed=True,
            title="late activity",
        )
    )

    session = store.get("codex", "thread-1")

    assert session is not None
    assert session.phase is SessionPhase.RUNNING
    assert session.completed_at is None
    assert session.title == "late activity"


def test_store_does_not_reopen_session_after_session_end() -> None:
    store = SessionStore()
    store.apply(
        AgentEvent(
            type=AgentEventType.SESSION_STARTED,
            provider="claude",
            session_id="thread-1",
            cwd="/tmp/demo",
            title="Demo",
            phase=SessionPhase.RUNNING,
            updated_at=100,
            origin=SessionOrigin.LIVE,
            is_hook_managed=True,
        )
    )
    store.apply(
        AgentEvent(
            type=AgentEventType.SESSION_COMPLETED,
            provider="claude",
            session_id="thread-1",
            phase=SessionPhase.COMPLETED,
            updated_at=200,
            origin=SessionOrigin.LIVE,
            is_hook_managed=True,
            is_session_end=True,
        )
    )
    store.apply(
        AgentEvent(
            type=AgentEventType.ACTIVITY_UPDATED,
            provider="claude",
            session_id="thread-1",
            phase=SessionPhase.WAITING_ANSWER,
            updated_at=250,
            origin=SessionOrigin.LIVE,
            is_hook_managed=True,
        )
    )

    session = store.get("claude", "thread-1")

    assert session is not None
    assert session.phase is SessionPhase.COMPLETED
    assert session.completed_at == 200
    assert session.is_session_ended is True


def test_store_session_started_reopens_completed_session() -> None:
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
        )
    )
    store.apply(
        AgentEvent(
            type=AgentEventType.SESSION_COMPLETED,
            provider="codex",
            session_id="thread-1",
            phase=SessionPhase.COMPLETED,
            updated_at=200,
            origin=SessionOrigin.LIVE,
            is_hook_managed=True,
        )
    )
    store.apply(
        AgentEvent(
            type=AgentEventType.SESSION_STARTED,
            provider="codex",
            session_id="thread-1",
            cwd="/tmp/demo",
            title="Demo again",
            phase=SessionPhase.RUNNING,
            updated_at=300,
            origin=SessionOrigin.LIVE,
            is_hook_managed=True,
        )
    )

    session = store.get("codex", "thread-1")

    assert session is not None
    assert session.phase is SessionPhase.RUNNING
    assert session.completed_at is None
    assert session.title == "Demo again"


def test_store_permission_requested_sets_structured_request() -> None:
    store = SessionStore()
    store.apply(
        AgentEvent(
            type=AgentEventType.SESSION_STARTED,
            provider="claude",
            session_id="thread-1",
            cwd="/tmp/demo",
            title="Demo",
            phase=SessionPhase.RUNNING,
            updated_at=100,
            origin=SessionOrigin.LIVE,
            is_hook_managed=True,
        )
    )
    store.apply(
        AgentEvent(
            type=AgentEventType.PERMISSION_REQUESTED,
            provider="claude",
            session_id="thread-1",
            updated_at=200,
            origin=SessionOrigin.LIVE,
            is_hook_managed=True,
            permission_request=PermissionRequest(
                title="Permission required",
                summary="Allow write access",
                affected_path="/tmp/demo",
            ),
        )
    )

    session = store.get("claude", "thread-1")
    assert session is not None
    assert session.phase is SessionPhase.WAITING_APPROVAL
    assert session.permission_request is not None
    assert session.permission_request.affected_path == "/tmp/demo"


def test_store_question_asked_sets_structured_prompt() -> None:
    store = SessionStore()
    store.apply(
        AgentEvent(
            type=AgentEventType.SESSION_STARTED,
            provider="gemini",
            session_id="thread-1",
            cwd="/tmp/demo",
            title="Demo",
            phase=SessionPhase.RUNNING,
            updated_at=100,
            origin=SessionOrigin.LIVE,
            is_hook_managed=True,
        )
    )
    store.apply(
        AgentEvent(
            type=AgentEventType.QUESTION_ASKED,
            provider="gemini",
            session_id="thread-1",
            updated_at=200,
            origin=SessionOrigin.LIVE,
            is_hook_managed=True,
            question_prompt=QuestionPrompt(
                title="Need input",
                options=[QuestionOption(label="A"), QuestionOption(label="B", description="second")],
            ),
        )
    )

    session = store.get("gemini", "thread-1")
    assert session is not None
    assert session.phase is SessionPhase.WAITING_ANSWER
    assert session.question_prompt is not None
    assert [option.label for option in session.question_prompt.options] == ["A", "B"]


def test_store_metadata_updated_sets_codex_metadata_without_changing_phase() -> None:
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
        )
    )

    store.apply(
        AgentEvent(
            type=AgentEventType.METADATA_UPDATED,
            provider="codex",
            session_id="thread-1",
            updated_at=200,
            metadata_kind="codex",
            codex_metadata=CodexSessionMetadata(
                transcript_path="/tmp/rollout.jsonl",
                last_user_prompt="hello",
                last_assistant_message="hi",
            ),
        )
    )

    session = store.get("codex", "thread-1")
    assert session is not None
    assert session.phase is SessionPhase.RUNNING
    assert session.codex_metadata is not None
    assert session.codex_metadata.last_user_prompt == "hello"


def test_store_metadata_updated_sets_claude_metadata_without_changing_phase() -> None:
    store = SessionStore()
    store.apply(
        AgentEvent(
            type=AgentEventType.SESSION_STARTED,
            provider="claude",
            session_id="thread-1",
            cwd="/tmp/demo",
            title="Demo",
            phase=SessionPhase.RUNNING,
            updated_at=100,
            origin=SessionOrigin.LIVE,
            is_hook_managed=True,
        )
    )

    store.apply(
        AgentEvent(
            type=AgentEventType.METADATA_UPDATED,
            provider="claude",
            session_id="thread-1",
            updated_at=200,
            metadata_kind="claude",
            claude_metadata=ClaudeSessionMetadata(
                transcript_path="/tmp/claude.jsonl",
                current_tool="Write",
                last_assistant_message="done",
            ),
        )
    )

    session = store.get("claude", "thread-1")
    assert session is not None
    assert session.phase is SessionPhase.RUNNING
    assert session.claude_metadata is not None
    assert session.claude_metadata.current_tool == "Write"
