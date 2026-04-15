from __future__ import annotations

import re
import time

from ..core.models import AgentSession, SessionPhase


APP_TITLE = "Linux Agent Island"
COLLAPSED_WIDTH = 220
COLLAPSED_HEIGHT = 60
EXPANDED_WIDTH = 720
DETAIL_EXPANDED_WIDTH = EXPANDED_WIDTH * 2
HIGHLIGHT_DURATION_SECONDS = 0
BASE_EXPANDED_MAX_SCROLL_HEIGHT = 352
BASE_DETAIL_MAX_SCROLL_HEIGHT = 620
EXPANDED_HEIGHT_NUMERATOR = 3
EXPANDED_HEIGHT_DENOMINATOR = 2
DETAIL_HEIGHT_MULTIPLIER = 2

SessionKey = tuple[str, str]


def session_key(session: AgentSession) -> SessionKey:
    return (session.provider, session.session_id)


def parse_workarea_top_offset(output: str) -> int:
    values = [int(match) for match in re.findall(r"-?\d+", output)]
    if len(values) < 2:
        return 0
    return max(0, values[1])


def compute_window_position_for_width(
    monitor_x: int,
    monitor_y: int,
    monitor_width: int,
    top_bar_offset: int,
    top_bar_gap: int,
    width: int,
) -> tuple[int, int, int]:
    x = monitor_x + (monitor_width - width) // 2
    y = monitor_y + max(0, top_bar_offset) + max(0, top_bar_gap)
    return width, x, y


def compute_window_position(
    monitor_x: int,
    monitor_y: int,
    monitor_width: int,
    top_bar_offset: int,
    top_bar_gap: int,
    expanded: bool,
) -> tuple[int, int, int]:
    width = EXPANDED_WIDTH if expanded else COLLAPSED_WIDTH
    return compute_window_position_for_width(
        monitor_x=monitor_x,
        monitor_y=monitor_y,
        monitor_width=monitor_width,
        top_bar_offset=top_bar_offset,
        top_bar_gap=top_bar_gap,
        width=width,
    )


def visible_sessions(sessions: list[AgentSession]) -> list[AgentSession]:
    return [session for session in sessions if session.is_visible_in_island]


def summarize_visible_sessions(sessions: list[AgentSession]) -> str:
    visible_count = len(visible_sessions(sessions))
    return f"{visible_count} session" if visible_count == 1 else f"{visible_count} sessions"


def expanded_header_title(sessions: list[AgentSession]) -> str:
    return f"{APP_TITLE} · {summarize_visible_sessions(sessions)}"


def status_dot_css_class(phase: SessionPhase) -> str:
    mapping = {
        SessionPhase.WAITING_APPROVAL: "status-dot status-attention",
        SessionPhase.WAITING_ANSWER: "status-dot status-attention",
        SessionPhase.RUNNING: "status-dot status-running",
        SessionPhase.COMPLETED: "status-dot status-completed",
    }
    return mapping[phase]


def status_dot_glyph(phase: SessionPhase) -> str:
    return "●" if phase is not SessionPhase.COMPLETED else "○"


def collapsed_status_phase(sessions: list[AgentSession]) -> SessionPhase:
    phases = {session.phase for session in visible_sessions(sessions)}
    if SessionPhase.RUNNING in phases:
        return SessionPhase.RUNNING
    if SessionPhase.WAITING_APPROVAL in phases:
        return SessionPhase.WAITING_APPROVAL
    if SessionPhase.WAITING_ANSWER in phases:
        return SessionPhase.WAITING_ANSWER
    return SessionPhase.COMPLETED


def collapsed_status_css_class(sessions: list[AgentSession]) -> str:
    return status_dot_css_class(collapsed_status_phase(sessions))


def has_done_time_label(session: AgentSession) -> bool:
    return session.phase is SessionPhase.COMPLETED and session.completed_at is not None


def format_session_minutes(session: AgentSession, now_ts: int | None = None) -> str:
    current_ts = now_ts if now_ts is not None else int(time.time())
    if has_done_time_label(session):
        assert session.completed_at is not None
        minutes = max(1, (current_ts - session.completed_at + 59) // 60)
        return f"done {minutes}m"
    reference = session.started_at or session.updated_at
    minutes = max(1, (current_ts - reference + 59) // 60)
    return f"{minutes}m"


def session_sort_key(session: AgentSession) -> tuple[int, int]:
    if session.requires_attention:
        return (0, -session.updated_at)
    if session.phase is SessionPhase.RUNNING:
        return (1, -session.updated_at)
    if session.phase is SessionPhase.COMPLETED:
        return (2, -(session.completed_at or session.updated_at))
    return (3, -session.updated_at)


def panel_sessions(sessions: list[AgentSession]) -> list[AgentSession]:
    return sorted(sessions, key=session_sort_key)


def session_provider_label(provider: str) -> str:
    mapping = {
        "claude": "Claude Code",
        "codex": "Codex",
        "gemini": "Gemini",
    }
    return mapping.get(provider.lower(), provider)


def session_metadata_tags(session: AgentSession) -> list[tuple[str, str]]:
    return [
        (session_provider_label(session.provider), f"tag-provider-{session.provider.lower()}"),
        (session.model or "Unknown model", "tag-model"),
    ]


def detect_completed_sessions(
    previous_phases: dict[SessionKey, SessionPhase],
    sessions: list[AgentSession],
) -> list[AgentSession]:
    return [
        session
        for session in sessions
        if previous_phases.get(session_key(session)) is SessionPhase.RUNNING and has_done_time_label(session)
    ]


def refresh_completion_highlights(
    highlighted_until: dict[SessionKey, int],
    completed_sessions: list[AgentSession],
    now_ts: int,
) -> tuple[dict[SessionKey, int], SessionKey | None]:
    updated = dict(highlighted_until)
    latest_session: AgentSession | None = None

    for session in completed_sessions:
        if not has_done_time_label(session):
            continue
        
        # Use 0 to indicate the highlight never expires automatically
        updated[session_key(session)] = 0
        
        if latest_session is None:
            latest_session = session
            continue
        latest_completed_at = latest_session.completed_at or latest_session.updated_at
        session_completed_at = session.completed_at or session.updated_at
        if session_completed_at >= latest_completed_at:
            latest_session = session

    return updated, (session_key(latest_session) if latest_session is not None else None)


def prune_expired_highlights(
    highlighted_until: dict[SessionKey, int],
    now_ts: int,
) -> dict[SessionKey, int]:
    return {
        key: expires_at
        for key, expires_at in highlighted_until.items()
        if expires_at == 0 or expires_at > now_ts
    }


def compute_expanded_window_height(session_count: int, has_expanded_session: bool = False, max_available_height: int | None = None) -> int:
    header_height = 120
    per_session_height = 88
    max_scroll_height = BASE_DETAIL_MAX_SCROLL_HEIGHT if has_expanded_session else BASE_EXPANDED_MAX_SCROLL_HEIGHT
    scroll_height = min(max_scroll_height, per_session_height * max(1, session_count))
    if has_expanded_session:
        scroll_height = max(scroll_height, 500)
        target_height = (header_height + scroll_height) * DETAIL_HEIGHT_MULTIPLIER
    else:
        target_height = (header_height + scroll_height) * EXPANDED_HEIGHT_NUMERATOR // EXPANDED_HEIGHT_DENOMINATOR
    
    if max_available_height is not None:
        # Leave a moderate margin (64px) at the bottom to account for invisible CSD shadows or WM paddings
        target_height = min(target_height, max_available_height - 64)
        
    return max(120, target_height)


def window_width_for_state(expanded: bool, has_expanded_session: bool) -> int:
    if not expanded:
        return COLLAPSED_WIDTH
    return DETAIL_EXPANDED_WIDTH if has_expanded_session else EXPANDED_WIDTH
