from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from ..core.models import AgentSession, SessionPhase
from ..providers.base import BaseProvider
from ..providers.utils import current_timestamp
from .process_matching import AgentProcessInfo


def filter_cached_sessions_for_restore(
    cached_sessions: list[AgentSession],
    providers: list[BaseProvider],
) -> list[AgentSession]:
    provider_map = {provider.name: provider for provider in providers}
    restored: list[AgentSession] = []

    for session in cached_sessions:
        provider = provider_map.get(session.provider)
        if provider is None:
            restored.append(session)

    for provider_name, provider in provider_map.items():
        provider_sessions = [session for session in cached_sessions if session.provider == provider_name]
        restored.extend(provider.filter_cached_sessions(provider_sessions))

    return restored


def build_sessions_from_processes(
    processes: list[AgentProcessInfo],
    cached_sessions: list[AgentSession],
    provider_sessions: list[AgentSession],
) -> list[AgentSession]:
    now_ts = current_timestamp()
    cached_by_provider: dict[str, list[AgentSession]] = {}
    provider_by_provider: dict[str, list[AgentSession]] = {}
    provider_by_id: dict[tuple[str, str], AgentSession] = {}

    for session in cached_sessions:
        cached_by_provider.setdefault(session.provider, []).append(session)
    for session in provider_sessions:
        provider_by_provider.setdefault(session.provider, []).append(session)
        key = (session.provider, session.session_id)
        previous = provider_by_id.get(key)
        if previous is None or _is_newer(session, previous):
            provider_by_id[key] = session

    claimed_cache: set[tuple[str, str]] = set()
    claimed_provider: set[tuple[str, str]] = set()
    restored: list[AgentSession] = []
    ordered_processes = sorted(processes, key=lambda item: (item.provider, item.pid))

    for process in ordered_processes:
        anchor = _synthetic_session(process, now_ts)
        cache_match = _match_cached_session(process, cached_by_provider.get(process.provider, []), claimed_cache)
        selected = anchor

        if cache_match is not None:
            selected = _apply_process(cache_match, process, process_anchor=True, synthetic=False, provider_stale=False)
            claimed_cache.add((cache_match.provider, cache_match.session_id))

        if cache_match is not None:
            provider_match = provider_by_id.get((cache_match.provider, cache_match.session_id))
            if provider_match is not None:
                claimed_provider.add((provider_match.provider, provider_match.session_id))
                selected = _merge_with_provider(
                    base=selected,
                    provider_session=provider_match,
                    process=process,
                    provider_stale=False,
                )
            else:
                selected = replace(selected, provider_stale=True)
        else:
            provider_match = _match_provider_by_cwd(process, provider_by_provider.get(process.provider, []), claimed_provider)
            if provider_match is not None:
                claimed_provider.add((provider_match.provider, provider_match.session_id))
                selected = _merge_with_provider(
                    base=anchor,
                    provider_session=provider_match,
                    process=process,
                    provider_stale=False,
                )

        restored.append(selected)

    return restored


def _is_newer(left: AgentSession, right: AgentSession) -> bool:
    if left.updated_at != right.updated_at:
        return left.updated_at > right.updated_at
    return left.session_id < right.session_id


def _session_sort_key(session: AgentSession) -> tuple[int, str]:
    return (-session.updated_at, session.session_id)


def _tty_variants(tty: str | None) -> set[str]:
    if not tty:
        return set()
    tty_str = tty.strip()
    if not tty_str:
        return set()
    if tty_str.startswith("/dev/"):
        return {tty_str, tty_str.removeprefix("/dev/")}
    return {tty_str, f"/dev/{tty_str}"}


def _match_cached_session(
    process: AgentProcessInfo,
    candidates: list[AgentSession],
    claimed: set[tuple[str, str]],
) -> AgentSession | None:
    unclaimed = [s for s in candidates if (s.provider, s.session_id) not in claimed]

    pid_matches = [s for s in unclaimed if s.pid is not None and s.pid == process.pid]
    if pid_matches:
        return sorted(pid_matches, key=_session_sort_key)[0]

    if not process.cwd:
        return None

    process_ttys = _tty_variants(process.tty)
    tty_cwd_matches = [
        s
        for s in unclaimed
        if s.cwd == process.cwd and process_ttys and _tty_variants(s.tty).intersection(process_ttys)
    ]
    if tty_cwd_matches:
        return sorted(tty_cwd_matches, key=_session_sort_key)[0]
    return None


def _match_provider_by_cwd(
    process: AgentProcessInfo,
    candidates: list[AgentSession],
    claimed: set[tuple[str, str]],
) -> AgentSession | None:
    if not process.cwd:
        return None
    cwd_matches = [
        s
        for s in candidates
        if (s.provider, s.session_id) not in claimed and s.cwd == process.cwd
    ]
    if not cwd_matches:
        return None
    return sorted(cwd_matches, key=_session_sort_key)[0]


def _synthetic_session(process: AgentProcessInfo, now_ts: int) -> AgentSession:
    cwd = process.cwd or ""
    synthetic_id = f"{process.provider}:pid:{process.pid}"
    title = Path(cwd).name if cwd else synthetic_id
    tty = process.tty
    if tty and not tty.startswith("/dev/"):
        tty = f"/dev/{tty}"
    return AgentSession(
        provider=process.provider,
        session_id=synthetic_id,
        cwd=cwd,
        title=title,
        phase=SessionPhase.COMPLETED,
        model=None,
        sandbox=None,
        approval_mode=None,
        updated_at=now_ts,
        pid=process.pid,
        tty=tty,
        process_anchor=True,
        synthetic_session=True,
        provider_stale=False,
        is_process_alive=True,
        process_not_seen_count=0,
    )


def _apply_process(
    session: AgentSession,
    process: AgentProcessInfo,
    *,
    process_anchor: bool,
    synthetic: bool,
    provider_stale: bool,
) -> AgentSession:
    tty = process.tty
    if tty and not tty.startswith("/dev/"):
        tty = f"/dev/{tty}"
    return replace(
        session,
        cwd=process.cwd or session.cwd,
        pid=process.pid,
        tty=tty or session.tty,
        process_anchor=process_anchor,
        synthetic_session=synthetic,
        provider_stale=provider_stale,
        is_process_alive=True,
        process_not_seen_count=0,
        is_session_ended=False,
    )


def _merge_with_provider(
    base: AgentSession,
    provider_session: AgentSession,
    process: AgentProcessInfo,
    *,
    provider_stale: bool,
) -> AgentSession:
    merged = replace(
        provider_session,
        title=provider_session.title or base.title,
        last_message_preview=provider_session.last_message_preview or base.last_message_preview,
        summary=provider_session.summary or base.summary,
    )
    return _apply_process(
        merged,
        process,
        process_anchor=True,
        synthetic=False,
        provider_stale=provider_stale,
    )
