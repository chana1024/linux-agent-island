from linux_agent_island.core.models import AgentSession, SessionOrigin, SessionPhase
from linux_agent_island.providers.base import BaseProvider
from linux_agent_island.runtime.restore import filter_cached_sessions_for_restore


class _Provider(BaseProvider):
    def __init__(self, name: str, kept_session_ids: set[str]) -> None:
        self._name = name
        self._kept_session_ids = kept_session_ids

    @property
    def name(self) -> str:
        return self._name

    def install_hooks(self) -> None:
        raise NotImplementedError

    def uninstall_hooks(self) -> None:
        raise NotImplementedError

    def load_transcript(self, session_id: str, cwd: str = "", **kwargs: object) -> list[dict[str, str]]:
        raise NotImplementedError

    def filter_cached_sessions(self, cached_sessions: list[AgentSession]) -> list[AgentSession]:
        return [session for session in cached_sessions if session.session_id in self._kept_session_ids]


def test_filter_cached_sessions_for_restore_keeps_only_provider_approved_sessions() -> None:
    cached_sessions = [
        AgentSession(
            provider="codex",
            session_id="keep",
            cwd="/tmp/a",
            title="Keep",
            phase=SessionPhase.COMPLETED,
            model=None,
            sandbox=None,
            approval_mode=None,
            updated_at=2,
            origin=SessionOrigin.RESTORED,
        ),
        AgentSession(
            provider="codex",
            session_id="drop",
            cwd="/tmp/b",
            title="Drop",
            phase=SessionPhase.COMPLETED,
            model=None,
            sandbox=None,
            approval_mode=None,
            updated_at=1,
            origin=SessionOrigin.RESTORED,
        ),
        AgentSession(
            provider="custom",
            session_id="custom-1",
            cwd="/tmp/c",
            title="Custom",
            phase=SessionPhase.COMPLETED,
            model=None,
            sandbox=None,
            approval_mode=None,
            updated_at=3,
            origin=SessionOrigin.RESTORED,
        ),
    ]

    restored = filter_cached_sessions_for_restore(cached_sessions, [_Provider("codex", {"keep"})])

    assert [(session.provider, session.session_id) for session in restored] == [
        ("custom", "custom-1"),
        ("codex", "keep"),
    ]
