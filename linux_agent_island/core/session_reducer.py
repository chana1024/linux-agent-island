from __future__ import annotations

from dataclasses import replace

from .models import AgentSession, SessionOrigin, SessionPhase
from ..runtime.agent_events import AgentEvent, AgentEventType


def restore_session(current: AgentSession | None, event: AgentEvent) -> AgentSession:
    return AgentSession(
        provider=event.provider,
        session_id=event.session_id,
        cwd=event.cwd or (current.cwd if current else ""),
        title=event.title or (current.title if current else event.session_id),
        phase=event.phase or (current.phase if current else SessionPhase.COMPLETED),
        model=event.model if event.model is not None else (current.model if current else None),
        sandbox=event.sandbox if event.sandbox is not None else (current.sandbox if current else None),
        approval_mode=(
            event.approval_mode if event.approval_mode is not None else (current.approval_mode if current else None)
        ),
        updated_at=event.updated_at,
        started_at=event.started_at if event.started_at is not None else (current.started_at if current else event.updated_at),
        completed_at=event.completed_at if event.completed_at is not None else (current.completed_at if current else None),
        origin=SessionOrigin.RESTORED,
        summary=event.summary or (current.summary if current else ""),
        pid=event.pid if event.pid is not None else (current.pid if current else None),
        tty=event.tty if event.tty is not None else (current.tty if current else None),
        has_interactive_window=current.has_interactive_window if current else False,
        is_focused=current.is_focused if current else False,
        is_hook_managed=event.is_hook_managed if event.is_hook_managed is not None else (current.is_hook_managed if current else False),
        identity_confirmed_by_hook=(
            event.identity_confirmed_by_hook
            if event.identity_confirmed_by_hook is not None
            else (current.identity_confirmed_by_hook if current else False)
        ),
        process_anchor=current.process_anchor if current else False,
        synthetic_session=current.synthetic_session if current else False,
        provider_stale=current.provider_stale if current else False,
        is_session_ended=event.is_session_end if event.is_session_end is not None else (current.is_session_ended if current else False),
        is_process_alive=event.is_process_alive if event.is_process_alive is not None else (current.is_process_alive if current else False),
        process_not_seen_count=(
            event.process_not_seen_count
            if event.process_not_seen_count is not None
            else (current.process_not_seen_count if current else 0)
        ),
        last_message_preview=event.last_message_preview or (current.last_message_preview if current else ""),
        permission_request=event.permission_request if event.permission_request is not None else (current.permission_request if current else None),
        question_prompt=event.question_prompt if event.question_prompt is not None else (current.question_prompt if current else None),
        codex_metadata=event.codex_metadata if event.codex_metadata is not None else (current.codex_metadata if current else None),
        claude_metadata=event.claude_metadata if event.claude_metadata is not None else (current.claude_metadata if current else None),
    )


def build_base_session(current: AgentSession | None, event: AgentEvent) -> AgentSession:
    if current is not None:
        return current
    return AgentSession(
        provider=event.provider,
        session_id=event.session_id,
        cwd=event.cwd,
        title=event.title or event.session_id,
        phase=event.phase or SessionPhase.COMPLETED,
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
        is_hook_managed=bool(event.is_hook_managed),
        identity_confirmed_by_hook=bool(event.identity_confirmed_by_hook),
        process_anchor=False,
        synthetic_session=False,
        provider_stale=False,
        is_process_alive=True,
        process_not_seen_count=0,
        last_message_preview=event.last_message_preview,
        permission_request=event.permission_request,
        question_prompt=event.question_prompt,
        codex_metadata=event.codex_metadata,
        claude_metadata=event.claude_metadata,
    )


def should_preserve_phase(base: AgentSession, event: AgentEvent, phase: SessionPhase) -> bool:
    return (
        base.is_session_ended
        and event.type is AgentEventType.ACTIVITY_UPDATED
        and phase is not SessionPhase.COMPLETED
    )


def resolve_phase(base: AgentSession, event: AgentEvent) -> SessionPhase:
    if event.type is AgentEventType.PERMISSION_REQUESTED:
        return SessionPhase.WAITING_APPROVAL
    if event.type is AgentEventType.QUESTION_ASKED:
        return SessionPhase.WAITING_ANSWER
    if event.type is AgentEventType.METADATA_UPDATED:
        return base.phase
    if event.type is AgentEventType.ACTIONABLE_STATE_RESOLVED:
        return SessionPhase.RUNNING
    if event.type is AgentEventType.SESSION_COMPLETED:
        return SessionPhase.COMPLETED
    phase = event.phase if event.phase is not None else base.phase
    if should_preserve_phase(base, event, phase):
        return base.phase
    return phase


def resolve_summary(base: AgentSession, event: AgentEvent) -> str:
    if event.type is AgentEventType.PERMISSION_REQUESTED and event.permission_request is not None:
        return event.permission_request.summary or event.permission_request.title or base.summary
    if event.type is AgentEventType.QUESTION_ASKED and event.question_prompt is not None:
        return event.question_prompt.title or base.summary
    summary = event.summary or base.summary
    if not summary and event.last_message_preview:
        summary = event.last_message_preview
    return summary


def resolve_is_session_ended(base: AgentSession, event: AgentEvent) -> bool:
    if event.type is AgentEventType.SESSION_STARTED:
        return False
    return base.is_session_ended or event.is_session_end


def resolve_completed_at(base: AgentSession, event: AgentEvent, phase: SessionPhase) -> int | None:
    if event.type is AgentEventType.SESSION_COMPLETED:
        return event.updated_at
    if event.type is AgentEventType.SESSION_STARTED:
        return None
    if event.type is AgentEventType.METADATA_UPDATED:
        return base.completed_at
    if event.type is AgentEventType.ACTIVITY_UPDATED and phase is not SessionPhase.COMPLETED:
        return None
    return base.completed_at


def resolve_permission_request(base: AgentSession, event: AgentEvent, phase: SessionPhase):
    if event.type is AgentEventType.PERMISSION_REQUESTED:
        return event.permission_request
    if event.type is AgentEventType.ACTIONABLE_STATE_RESOLVED:
        return None
    if phase is not SessionPhase.WAITING_APPROVAL:
        return None
    return base.permission_request


def resolve_question_prompt(base: AgentSession, event: AgentEvent, phase: SessionPhase):
    if event.type is AgentEventType.QUESTION_ASKED:
        return event.question_prompt
    if event.type is AgentEventType.ACTIONABLE_STATE_RESOLVED:
        return None
    if phase is not SessionPhase.WAITING_ANSWER:
        return None
    return base.question_prompt


def resolve_identity_confirmed_by_hook(base: AgentSession, event: AgentEvent) -> bool:
    return (
        bool(event.identity_confirmed_by_hook)
        or base.identity_confirmed_by_hook
        or (bool(event.is_hook_managed) and (event.pid is not None or event.tty is not None))
    )


def resolve_codex_metadata(base: AgentSession, event: AgentEvent):
    if event.type is AgentEventType.METADATA_UPDATED and event.metadata_kind == "codex":
        return event.codex_metadata
    return base.codex_metadata


def resolve_claude_metadata(base: AgentSession, event: AgentEvent):
    if event.type is AgentEventType.METADATA_UPDATED and event.metadata_kind == "claude":
        return event.claude_metadata
    return base.claude_metadata


def apply_live_event(current: AgentSession | None, event: AgentEvent) -> AgentSession:
    base = build_base_session(current, event)
    phase = resolve_phase(base, event)
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
        completed_at=resolve_completed_at(base, event, phase),
        origin=event.origin if event.type is AgentEventType.SESSION_STARTED else base.origin,
        summary=resolve_summary(base, event),
        pid=event.pid if event.pid is not None else base.pid,
        tty=event.tty if event.tty is not None else base.tty,
        is_hook_managed=base.is_hook_managed or event.is_hook_managed,
        identity_confirmed_by_hook=resolve_identity_confirmed_by_hook(base, event),
        is_session_ended=resolve_is_session_ended(base, event),
        is_process_alive=True,
        process_not_seen_count=0,
        last_message_preview=event.last_message_preview or base.last_message_preview,
        permission_request=resolve_permission_request(base, event, phase),
        question_prompt=resolve_question_prompt(base, event, phase),
        codex_metadata=resolve_codex_metadata(base, event),
        claude_metadata=resolve_claude_metadata(base, event),
    )
