from __future__ import annotations

import threading
from dataclasses import replace
from typing import Iterable

from .models import AgentSession, SessionOrigin, SessionPhase
from ..runtime.agent_events import AgentEvent, AgentEventType


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
                    origin=session.origin,
                    started_at=session.started_at,
                    completed_at=session.completed_at,
                    summary=session.summary,
                    pid=session.pid,
                    tty=session.tty,
                    last_message_preview=session.last_message_preview,
                    is_hook_managed=session.is_hook_managed,
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
            return session

    def reconcile_process_matches(self, sessions: Iterable[AgentSession]) -> bool:
        changed = False
        with self._lock:
            for session in sessions:
                key = (session.provider, session.session_id)
                current = self._sessions.get(key)
                if current is None or current == session:
                    continue
                self._sessions[key] = session
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
            return AgentSession(
                provider=event.provider,
                session_id=event.session_id,
                cwd=event.cwd or (current.cwd if current else ""),
                title=event.title or (current.title if current else event.session_id),
                phase=event.phase or (current.phase if current else SessionPhase.COMPLETED),
                model=event.model if event.model is not None else (current.model if current else None),
                sandbox=event.sandbox if event.sandbox is not None else (current.sandbox if current else None),
                approval_mode=event.approval_mode if event.approval_mode is not None else (current.approval_mode if current else None),
                updated_at=event.updated_at,
                started_at=event.started_at if event.started_at is not None else (current.started_at if current else event.updated_at),
                completed_at=event.completed_at if event.completed_at is not None else (current.completed_at if current else None),
                origin=SessionOrigin.RESTORED,
                summary=event.summary or (current.summary if current else ""),
                pid=event.pid if event.pid is not None else (current.pid if current else None),
                tty=event.tty if event.tty is not None else (current.tty if current else None),
                has_interactive_window=current.has_interactive_window if current else False,
                is_focused=current.is_focused if current else False,
                is_hook_managed=False,
                is_session_ended=False,
                is_process_alive=event.is_process_alive or (current.is_process_alive if current else False),
                process_not_seen_count=(
                    event.process_not_seen_count
                    if event.process_not_seen_count
                    else (current.process_not_seen_count if current else 0)
                ),
                last_message_preview=event.last_message_preview or (current.last_message_preview if current else ""),
            )

        base = current or AgentSession(
            provider=event.provider,
            session_id=event.session_id,
            cwd=event.cwd,
            title=event.title or event.session_id,
            phase=event.phase or SessionPhase.IDLE,
            model=event.model,
            sandbox=event.sandbox,
            approval_mode=event.approval_mode,
            updated_at=event.updated_at,
            started_at=event.started_at or event.updated_at,
            completed_at=event.completed_at,
            origin=event.origin,
            summary=event.summary,
            pid=event.pid,
            tty=event.tty,
            is_hook_managed=event.is_hook_managed,
            is_process_alive=True,
            process_not_seen_count=0,
            last_message_preview=event.last_message_preview,
        )

        phase = event.phase if event.phase is not None else base.phase
        summary = event.summary or base.summary
        if not summary and event.last_message_preview:
            summary = event.last_message_preview

        is_session_ended = base.is_session_ended or event.is_session_end
        if event.type is AgentEventType.SESSION_STARTED:
            is_session_ended = False

        return replace(
            base,
            cwd=event.cwd or base.cwd,
            title=event.title or base.title,
            phase=phase,
            model=event.model if event.model is not None else base.model,
            sandbox=event.sandbox if event.sandbox is not None else base.sandbox,
            approval_mode=event.approval_mode if event.approval_mode is not None else base.approval_mode,
            updated_at=event.updated_at,
            started_at=(
                event.started_at
                if event.started_at is not None
                else (
                    event.updated_at
                    if event.type is AgentEventType.SESSION_STARTED
                    else (base.started_at or event.updated_at)
                )
            ),
            completed_at=(
                event.updated_at
                if event.type is AgentEventType.SESSION_COMPLETED
                else (None if event.type is AgentEventType.SESSION_STARTED else base.completed_at)
            ),
            origin=event.origin if event.type is AgentEventType.SESSION_STARTED else base.origin,
            summary=summary,
            pid=event.pid if event.pid is not None else base.pid,
            tty=event.tty if event.tty is not None else base.tty,
            is_hook_managed=base.is_hook_managed or event.is_hook_managed,
            is_session_ended=is_session_ended,
            is_process_alive=True,
            process_not_seen_count=0,
            last_message_preview=event.last_message_preview or base.last_message_preview,
        )
