from linux_agent_island.frontend import (
    HIGHLIGHT_DURATION_SECONDS,
    compute_expanded_window_height,
    compute_window_position,
    detect_completed_sessions,
    expanded_header_title,
    format_session_minutes,
    panel_sessions,
    parse_workarea_top_offset,
    prune_expired_highlights,
    refresh_completion_highlights,
    status_dot_css_class,
    summarize_visible_sessions,
)
from linux_agent_island.core.models import AgentSession, SessionOrigin, SessionPhase


def build_session(
    session_id: str,
    *,
    phase: SessionPhase,
    updated_at: int,
    completed_at: int | None = None,
) -> AgentSession:
    return AgentSession(
        provider="codex",
        session_id=session_id,
        cwd=f"/tmp/{session_id}",
        title=session_id,
        phase=phase,
        model=None,
        sandbox=None,
        approval_mode=None,
        updated_at=updated_at,
        completed_at=completed_at,
        origin=SessionOrigin.RESTORED,
        is_process_alive=True,
    )


def test_parse_workarea_top_offset_reads_first_workarea_y_value() -> None:
    sample = "_NET_WORKAREA(CARDINAL) = 0, 36, 1920, 1044"
    assert parse_workarea_top_offset(sample) == 36


def test_parse_workarea_top_offset_defaults_to_zero_for_invalid_input() -> None:
    assert parse_workarea_top_offset("garbage") == 0


def test_compute_window_position_returns_top_centered_collapsed_bounds() -> None:
    assert compute_window_position(
        monitor_x=0,
        monitor_y=0,
        monitor_width=1920,
        top_bar_offset=36,
        top_bar_gap=0,
        expanded=False,
    ) == (220, 850, 36)


def test_compute_window_position_returns_top_centered_expanded_bounds() -> None:
    assert compute_window_position(
        monitor_x=100,
        monitor_y=24,
        monitor_width=2560,
        top_bar_offset=48,
        top_bar_gap=0,
        expanded=True,
    ) == (720, 1020, 72)


def test_compute_window_position_applies_top_bar_gap() -> None:
    assert compute_window_position(
        monitor_x=0,
        monitor_y=0,
        monitor_width=1920,
        top_bar_offset=36,
        top_bar_gap=8,
        expanded=False,
    ) == (220, 850, 44)


def test_summarize_visible_sessions_uses_visible_count() -> None:
    sessions = [
        AgentSession(
            provider="codex",
            session_id="visible",
            cwd="/tmp/a",
            title="A",
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
            title="B",
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

    assert summarize_visible_sessions(sessions) == "1 session"


def test_expanded_header_title_includes_app_name_and_visible_session_count() -> None:
    sessions = [
        AgentSession(
            provider="codex",
            session_id="visible",
            cwd="/tmp/a",
            title="A",
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
            title="B",
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

    assert expanded_header_title(sessions) == "Linux Agent Island · 1 session"


def test_panel_sessions_returns_all_visible_sessions_without_truncation() -> None:
    sessions = [
        AgentSession(
            provider="codex",
            session_id=f"session-{index}",
            cwd=f"/tmp/{index}",
            title=f"Session {index}",
            phase=SessionPhase.COMPLETED,
            model=None,
            sandbox=None,
            approval_mode=None,
            updated_at=index,
            origin=SessionOrigin.RESTORED,
            is_process_alive=True,
        )
        for index in range(7)
    ]

    assert [session.session_id for session in panel_sessions(sessions)] == [
        "session-6",
        "session-5",
        "session-4",
        "session-3",
        "session-2",
        "session-1",
        "session-0",
    ]


def test_panel_sessions_prioritizes_attention_running_then_completed() -> None:
    sessions = [
        AgentSession(
            provider="claude",
            session_id="waiting",
            cwd="/tmp/waiting",
            title="Waiting",
            phase=SessionPhase.WAITING,
            model=None,
            sandbox=None,
            approval_mode=None,
            updated_at=5,
            is_process_alive=True,
        ),
        AgentSession(
            provider="claude",
            session_id="completed-older",
            cwd="/tmp/completed-older",
            title="Completed Older",
            phase=SessionPhase.COMPLETED,
            model=None,
            sandbox=None,
            approval_mode=None,
            updated_at=10,
            completed_at=10,
            is_process_alive=True,
        ),
        AgentSession(
            provider="claude",
            session_id="running",
            cwd="/tmp/running",
            title="Running",
            phase=SessionPhase.RUNNING,
            model=None,
            sandbox=None,
            approval_mode=None,
            updated_at=20,
            is_process_alive=True,
        ),
        AgentSession(
            provider="claude",
            session_id="approval",
            cwd="/tmp/approval",
            title="Approval",
            phase=SessionPhase.WAITING_APPROVAL,
            model=None,
            sandbox=None,
            approval_mode=None,
            updated_at=15,
            is_process_alive=True,
        ),
        AgentSession(
            provider="claude",
            session_id="completed-newer",
            cwd="/tmp/completed-newer",
            title="Completed Newer",
            phase=SessionPhase.COMPLETED,
            model=None,
            sandbox=None,
            approval_mode=None,
            updated_at=30,
            completed_at=30,
            is_process_alive=True,
        ),
    ]

    assert [session.session_id for session in panel_sessions(sessions)] == [
        "approval",
        "running",
        "completed-newer",
        "completed-older",
        "waiting",
    ]


def test_detect_completed_sessions_tracks_running_to_completed_transitions() -> None:
    previous = {
        ("codex", "done"): SessionPhase.RUNNING,
        ("codex", "still-running"): SessionPhase.RUNNING,
    }
    sessions = [
        build_session("done", phase=SessionPhase.COMPLETED, updated_at=50, completed_at=50),
        build_session("still-running", phase=SessionPhase.RUNNING, updated_at=55),
    ]

    completed = detect_completed_sessions(previous, sessions)

    assert [session.session_id for session in completed] == ["done"]


def test_detect_completed_sessions_ignores_sessions_without_running_predecessor() -> None:
    previous = {}
    sessions = [
        build_session("restored", phase=SessionPhase.COMPLETED, updated_at=50, completed_at=50),
    ]

    completed = detect_completed_sessions(previous, sessions)

    assert completed == []


def test_refresh_completion_highlights_uses_latest_completed_session_as_target() -> None:
    older = build_session("older", phase=SessionPhase.COMPLETED, updated_at=120, completed_at=120)
    newer = build_session("newer", phase=SessionPhase.COMPLETED, updated_at=150, completed_at=150)

    highlighted, target = refresh_completion_highlights(
        highlighted_until={},
        completed_sessions=[older, newer],
        now_ts=200,
    )

    assert target == ("codex", "newer")
    assert highlighted[("codex", "older")] == 200 + HIGHLIGHT_DURATION_SECONDS
    assert highlighted[("codex", "newer")] == 200 + HIGHLIGHT_DURATION_SECONDS


def test_prune_expired_highlights_drops_expired_entries() -> None:
    highlighted = {
        ("codex", "keep"): 501,
        ("codex", "expire"): 500,
    }

    pruned = prune_expired_highlights(highlighted, now_ts=500)

    assert pruned == {("codex", "keep"): 501}


def test_compute_expanded_window_height_tracks_scroll_area_height() -> None:
    assert compute_expanded_window_height(session_count=1) == 208
    assert compute_expanded_window_height(session_count=7) == 472


def test_format_session_minutes_uses_completed_age_for_completed_sessions() -> None:
    session = AgentSession(
        provider="codex",
        session_id="done",
        cwd="/tmp/done",
        title="Done",
        phase=SessionPhase.COMPLETED,
        model=None,
        sandbox=None,
        approval_mode=None,
        updated_at=100,
        completed_at=100,
        is_process_alive=True,
    )

    assert format_session_minutes(session, now_ts=220) == "done 2m"


def test_status_dot_css_class_maps_phase_to_css_class() -> None:
    assert status_dot_css_class(SessionPhase.WAITING_APPROVAL) == "status-dot status-attention"
    assert status_dot_css_class(SessionPhase.RUNNING) == "status-dot status-running"
