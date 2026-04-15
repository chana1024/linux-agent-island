from __future__ import annotations

import logging
import threading
from dataclasses import replace
from typing import Iterable

from .models import AgentSession, SessionOrigin, SessionPhase
from .session_reducer import apply_live_event, restore_session
from ..runtime.agent_events import AgentEvent, AgentEventType


logger = logging.getLogger(__name__)


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[tuple[str, str], AgentSession] = {}
        self._lock = threading.Lock()

    def upsert(self, session: AgentSession) -> None:
        key = (session.provider, session.session_id)
        with self._lock:
            current = self._sessions.get(key)
            if current and current.updated_at > session.updated_at:
                return
            self._sessions[key] = session

    def restore_sessions(self, sessions: Iterable[AgentSession]) -> None:
        for session in sessions:
            self.apply(
                AgentEvent(
                    type=AgentEventType.SESSION_RESTORED,
                    provider=session.provider,
                    session_id=session.session_id,
                    updated_at=session.updated_at,
                    cwd=session.cwd,
                    title=session.title,
                    phase=session.phase,
                    model=session.model,
                    sandbox=session.sandbox,
                    approval_mode=session.approval_mode,
                    source=AgentEventType.SESSION_RESTORED.value,
                    origin=session.origin,
                    started_at=session.started_at,
                    completed_at=session.completed_at,
                    summary=session.summary,
                    pid=session.pid,
                    tty=session.tty,
                    last_message_preview=session.last_message_preview,
                    is_hook_managed=session.is_hook_managed,
                    identity_confirmed_by_hook=session.identity_confirmed_by_hook,
                    is_session_end=session.is_session_ended,
                    is_process_alive=session.is_process_alive,
                    process_not_seen_count=session.process_not_seen_count,
                )
            )

    def list_sessions(self, visible_only: bool = False) -> list[AgentSession]:
        with self._lock:
            sessions = self._sessions.values()
            if visible_only:
                sessions = [session for session in sessions if session.is_visible_in_island]
            return sorted(
                sessions,
                key=lambda session: session.updated_at,
                reverse=True,
            )

    def get(self, provider: str, session_id: str) -> AgentSession | None:
        with self._lock:
            return self._sessions.get((provider, session_id))

    def archive(self, provider: str, session_id: str) -> None:
        with self._lock:
            self._sessions.pop((provider, session_id), None)

    def apply(self, event: AgentEvent) -> AgentSession:
        key = (event.provider, event.session_id)
        with self._lock:
            current = self._sessions.get(key)
            session = self._apply_locked(current, event)
            self._sessions[key] = session
            self._log_transition(current, event, session)
            return session

    def reconcile_process_matches(self, sessions: Iterable[AgentSession]) -> bool:
        changed = False
        with self._lock:
            for session in sessions:
                key = (session.provider, session.session_id)
                current = self._sessions.get(key)
                if current is None:
                    self._sessions[key] = session
                    changed = True
                    continue
                # Process reconciliation must only refresh runtime identity/presence.
                # Overwriting the whole object can regress phase/updated_at when
                # runtime events are applied concurrently from the socket thread.
                merged = replace(
                    current,
                    pid=session.pid,
                    tty=session.tty,
                    has_interactive_window=session.has_interactive_window,
                    is_focused=session.is_focused,
                )
                if merged != current:
                    self._sessions[key] = merged
                    changed = True
        return changed

    def reassign_runtime_identity(
        self,
        provider: str,
        session_id: str,
        pid: int | None,
        tty: str | None,
    ) -> bool:
        changed = False
        with self._lock:
            for key, session in list(self._sessions.items()):
                if session.provider != provider:
                    continue
                if session.session_id == session_id:
                    if not session.identity_confirmed_by_hook:
                        self._sessions[key] = replace(session, identity_confirmed_by_hook=True)
                        changed = True
                    continue

                same_pid = pid is not None and session.pid == pid
                same_tty = tty is not None and session.tty == tty
                if not same_pid and not same_tty:
                    continue

                updated = replace(
                    session,
                    pid=None if same_pid else session.pid,
                    tty=None if same_tty else session.tty,
                    identity_confirmed_by_hook=False,
                )
                if updated != session:
                    self._sessions[key] = updated
                    changed = True
        return changed

    def mark_process_liveness(self, alive_session_keys: set[tuple[str, str]]) -> bool:
        changed = False
        with self._lock:
            for key, session in list(self._sessions.items()):
                updated = session
                if session.is_hook_managed:
                    if session.is_session_ended:
                        continue
                    if key in alive_session_keys:
                        updated = replace(session, is_process_alive=True, process_not_seen_count=0)
                    else:
                        missed = session.process_not_seen_count + 1
                        updated = replace(
                            session,
                            is_process_alive=False,
                            process_not_seen_count=missed,
                            is_session_ended=missed >= 2,
                            phase=SessionPhase.COMPLETED if missed >= 2 else session.phase,
                        )
                else:
                    if key in alive_session_keys:
                        updated = replace(session, is_process_alive=True, process_not_seen_count=0)
                    else:
                        missed = session.process_not_seen_count + 1
                        updated = replace(
                            session,
                            is_process_alive=missed < 2,
                            process_not_seen_count=missed,
                        )
                if updated != session:
                    self._sessions[key] = updated
                    changed = True
        return changed

    def remove_invisible_sessions(self) -> bool:
        with self._lock:
            before = len(self._sessions)
            self._sessions = {
                key: session for key, session in self._sessions.items()
                if session.is_visible_in_island
            }
            return len(self._sessions) != before

    def _apply_locked(self, current: AgentSession | None, event: AgentEvent) -> AgentSession:
        if event.type is AgentEventType.SESSION_RESTORED:
            return restore_session(current, event)
        return apply_live_event(current, event)

    def _log_transition(
        self,
        previous: AgentSession | None,
        event: AgentEvent,
        current: AgentSession,
    ) -> None:
        previous_phase = previous.phase.value if previous is not None else None
        current_phase = current.phase.value
        event_phase = event.phase.value if event.phase is not None else None

        suspicious_regression = (
            previous is not None
            and previous.phase is SessionPhase.COMPLETED
            and current.phase is not SessionPhase.COMPLETED
        )
        phase_changed = previous_phase != current_phase
        suspicious_event = (
            previous is not None
            and previous.phase is SessionPhase.COMPLETED
            and event.type is AgentEventType.ACTIVITY_UPDATED
            and event_phase not in {None, SessionPhase.COMPLETED.value}
        )

        if not (phase_changed or suspicious_regression or suspicious_event):
            return

        level = logging.WARNING if suspicious_regression else logging.INFO
        logger.log(
            level,
            (
                "session transition provider=%s session_id=%s event_type=%s "
                "event_phase=%s previous_phase=%s current_phase=%s updated_at=%s "
                "pid=%s tty=%s is_session_end=%s"
            ),
            event.provider,
            event.session_id,
            event.type.value,
            event_phase,
            previous_phase,
            current_phase,
            event.updated_at,
            current.pid,
            current.tty,
            event.is_session_end,
        )
