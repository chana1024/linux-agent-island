from __future__ import annotations

from ..core.models import AgentSession
from ..providers.base import BaseProvider


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
