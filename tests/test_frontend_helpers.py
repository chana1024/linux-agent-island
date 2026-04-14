from linux_agent_island.frontend import (
    FrontendApp,
    Gdk,
    HIGHLIGHT_DURATION_SECONDS,
    collapsed_status_css_class,
    collapsed_status_phase,
    compute_expanded_window_height,
    compute_window_position,
    compute_window_position_for_width,
    detect_completed_sessions,
    expanded_header_title,
    format_session_minutes,
    has_done_time_label,
    moved_selection_key,
    key_state_has_shift,
    navigation_delta_for_key,
    panel_sessions,
    parse_workarea_top_offset,
    prune_expired_highlights,
    refresh_completion_highlights,
    session_metadata_tags,
    session_provider_label,
    status_dot_css_class,
    status_dot_glyph,
    should_activate_selected_for_key,
    should_collapse_layer_for_key,
    summarize_visible_sessions,
    window_width_for_state,
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


def test_session_provider_label_formats_agent_cli_names() -> None:
    assert session_provider_label("codex") == "Codex"
    assert session_provider_label("claude") == "Claude Code"
    assert session_provider_label("gemini") == "Gemini"


def test_session_metadata_tags_include_model_and_window_state() -> None:
    windowed = AgentSession(
        provider="claude",
        session_id="windowed",
        cwd="/tmp/windowed",
        title="Windowed",
        phase=SessionPhase.RUNNING,
        model="sonnet",
        sandbox=None,
        approval_mode=None,
        updated_at=1,
        is_process_alive=True,
        has_interactive_window=True,
    )
    focused = AgentSession(
        provider="codex",
        session_id="focused",
        cwd="/tmp/focused",
        title="Focused",
        phase=SessionPhase.RUNNING,
        model=None,
        sandbox=None,
        approval_mode=None,
        updated_at=1,
        is_process_alive=True,
        has_interactive_window=True,
        is_focused=True,
    )

    assert session_metadata_tags(windowed) == ["Claude Code", "sonnet"]
    assert session_metadata_tags(focused) == ["Codex", "Unknown model"]


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


def test_detect_completed_sessions_requires_done_time_label() -> None:
    previous = {("codex", "missing-completed-at"): SessionPhase.RUNNING}
    sessions = [
        build_session("missing-completed-at", phase=SessionPhase.COMPLETED, updated_at=50),
    ]

    assert detect_completed_sessions(previous, sessions) == []


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


def test_refresh_completion_highlights_skips_sessions_without_done_time_label() -> None:
    missing_done_label = build_session("missing", phase=SessionPhase.COMPLETED, updated_at=150)

    highlighted, target = refresh_completion_highlights(
        highlighted_until={},
        completed_sessions=[missing_done_label],
        now_ts=200,
    )

    assert highlighted == {}
    assert target is None


def test_prune_expired_highlights_drops_expired_entries() -> None:
    highlighted = {
        ("codex", "keep"): 501,
        ("codex", "expire"): 500,
    }

    pruned = prune_expired_highlights(highlighted, now_ts=500)

    assert pruned == {("codex", "keep"): 501}


def test_compute_expanded_window_height_tracks_scroll_area_height() -> None:
    assert compute_expanded_window_height(session_count=1) == 312
    assert compute_expanded_window_height(session_count=7) == 708


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


def test_done_time_label_requires_completed_at() -> None:
    done = build_session("done", phase=SessionPhase.COMPLETED, updated_at=100, completed_at=100)
    completed_without_time = build_session("completed", phase=SessionPhase.COMPLETED, updated_at=100)
    running = build_session("running", phase=SessionPhase.RUNNING, updated_at=100)

    assert has_done_time_label(done) is True
    assert has_done_time_label(completed_without_time) is False
    assert has_done_time_label(running) is False


def test_status_dot_css_class_maps_phase_to_css_class() -> None:
    assert status_dot_css_class(SessionPhase.WAITING_APPROVAL) == "status-dot status-attention"
    assert status_dot_css_class(SessionPhase.RUNNING) == "status-dot status-running"
    assert status_dot_css_class(SessionPhase.IDLE) == "status-dot status-idle"


def test_status_dot_glyph_distinguishes_idle_from_waiting() -> None:
    assert status_dot_glyph(SessionPhase.IDLE) == "○"
    assert status_dot_glyph(SessionPhase.WAITING) == "●"


def test_compute_window_position_for_expanded_detail_width() -> None:
    assert compute_window_position_for_width(
        monitor_x=0,
        monitor_y=0,
        monitor_width=1920,
        top_bar_offset=36,
        top_bar_gap=8,
        width=1440,
    ) == (1440, 240, 44)


def test_collapsed_status_prefers_running_session() -> None:
    sessions = [
        build_session("done", phase=SessionPhase.COMPLETED, updated_at=1, completed_at=1),
        build_session("run", phase=SessionPhase.RUNNING, updated_at=2),
    ]

    assert collapsed_status_phase(sessions) is SessionPhase.RUNNING
    assert collapsed_status_css_class(sessions) == "status-dot status-running"


def test_window_width_for_detail_state_doubles_expanded_width() -> None:
    assert window_width_for_state(expanded=False, has_expanded_session=True) == 220
    assert window_width_for_state(expanded=True, has_expanded_session=False) == 720
    assert window_width_for_state(expanded=True, has_expanded_session=True) == 1440


def test_arrow_keys_map_to_selection_delta() -> None:
    assert navigation_delta_for_key(Gdk.KEY_Down) == 1
    assert navigation_delta_for_key(Gdk.KEY_Up) == -1
    assert navigation_delta_for_key(ord("a")) is None


def test_enter_keys_activate_selected_session() -> None:
    assert should_activate_selected_for_key(Gdk.KEY_Return) is True
    assert should_activate_selected_for_key(Gdk.KEY_KP_Enter) is True
    assert should_activate_selected_for_key(ord("a")) is False


def test_escape_key_collapses_one_layer() -> None:
    assert should_collapse_layer_for_key(Gdk.KEY_Escape) is True
    assert should_collapse_layer_for_key(Gdk.KEY_space) is False


def test_collapse_one_layer_hides_island_when_panel_already_collapsed(monkeypatch) -> None:
    app = FrontendApp()
    app.expanded = False

    hidden: list[bool] = []

    def fake_hide(*_args: object) -> None:
        hidden.append(True)

    monkeypatch.setattr(app, "_action_hide_island", fake_hide)

    assert app._collapse_one_layer() is True
    assert hidden == [True]


def test_shift_state_controls_jump_shortcut() -> None:
    assert key_state_has_shift(Gdk.ModifierType.SHIFT_MASK) is True
    assert key_state_has_shift(Gdk.ModifierType.CONTROL_MASK) is False


def test_moved_selection_key_clamps_and_initializes_selection() -> None:
    keys = [("codex", "one"), ("codex", "two"), ("codex", "three")]

    assert moved_selection_key(None, keys, 1) == ("codex", "one")
    assert moved_selection_key(None, keys, -1) == ("codex", "three")
    assert moved_selection_key(("codex", "one"), keys, 1) == ("codex", "two")
    assert moved_selection_key(("codex", "three"), keys, 1) == ("codex", "three")
    assert moved_selection_key(("codex", "one"), keys, -1) == ("codex", "one")
    assert moved_selection_key(("codex", "missing"), keys, 1) == ("codex", "one")
    assert moved_selection_key(("codex", "one"), [], 1) is None


def test_compute_expanded_window_height_grows_for_expanded_session() -> None:
    assert compute_expanded_window_height(session_count=1, has_expanded_session=True) == 1240


def test_format_running_session_minutes_uses_started_at() -> None:
    session = AgentSession(
        provider="codex",
        session_id="run",
        cwd="/tmp/run",
        title="Run",
        phase=SessionPhase.RUNNING,
        model=None,
        sandbox=None,
        approval_mode=None,
        updated_at=10,
        started_at=100,
        is_process_alive=True,
    )

    assert format_session_minutes(session, now_ts=220) == "2m"
