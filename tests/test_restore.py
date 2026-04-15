from linux_agent_island.core.models import AgentSession, SessionOrigin, SessionPhase
from linux_agent_island.providers.base import BaseProvider
from linux_agent_island.runtime.process_matching import AgentProcessInfo
from linux_agent_island.runtime.restore import build_sessions_from_processes, filter_cached_sessions_for_restore


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


def test_build_sessions_from_processes_prefers_pid_then_refreshes_from_provider() -> None:
    processes = [
        AgentProcessInfo(provider="codex", pid=900, tty="pts/7", cwd="/tmp/project"),
    ]
    cached = [
        AgentSession(
            provider="codex",
            session_id="cache-1",
            cwd="/tmp/project",
            title="Cached",
            phase=SessionPhase.RUNNING,
            model=None,
            sandbox=None,
            approval_mode=None,
            updated_at=10,
            origin=SessionOrigin.RESTORED,
            pid=900,
            tty="/dev/pts/7",
        )
    ]
    provider = [
        AgentSession(
            provider="codex",
            session_id="cache-1",
            cwd="/tmp/project",
            title="Provider Newer",
            phase=SessionPhase.COMPLETED,
            model=None,
            sandbox=None,
            approval_mode=None,
            updated_at=20,
            origin=SessionOrigin.RESTORED,
        )
    ]

    restored = build_sessions_from_processes(processes, cached, provider)

    assert len(restored) == 1
    session = restored[0]
    assert session.session_id == "cache-1"
    assert session.title == "Provider Newer"
    assert session.pid == 900
    assert session.tty == "/dev/pts/7"
    assert session.process_anchor is True
    assert session.synthetic_session is False
    assert session.provider_stale is False


def test_build_sessions_from_processes_enforces_single_session_claim_per_process_group() -> None:
    processes = [
        AgentProcessInfo(provider="codex", pid=900, tty="pts/7", cwd="/tmp/project"),
        AgentProcessInfo(provider="codex", pid=901, tty="pts/7", cwd="/tmp/project"),
    ]
    cached = [
        AgentSession(
            provider="codex",
            session_id="cache-1",
            cwd="/tmp/project",
            title="Cache",
            phase=SessionPhase.RUNNING,
            model=None,
            sandbox=None,
            approval_mode=None,
            updated_at=10,
            origin=SessionOrigin.RESTORED,
            tty="/dev/pts/7",
        )
    ]

    restored = build_sessions_from_processes(processes, cached, [])
    by_pid = {session.pid: session for session in restored}

    assert by_pid[900].session_id == "cache-1"
    assert by_pid[900].synthetic_session is False
    assert by_pid[900].provider_stale is True
    assert by_pid[901].session_id == "codex:pid:901"
    assert by_pid[901].synthetic_session is True


def test_build_sessions_from_processes_provider_cwd_greedy_uses_latest_candidate() -> None:
    processes = [
        AgentProcessInfo(provider="codex", pid=900, tty="pts/8", cwd="/tmp/project"),
    ]
    provider = [
        AgentSession(
            provider="codex",
            session_id="older",
            cwd="/tmp/project",
            title="Older",
            phase=SessionPhase.COMPLETED,
            model=None,
            sandbox=None,
            approval_mode=None,
            updated_at=10,
            origin=SessionOrigin.RESTORED,
        ),
        AgentSession(
            provider="codex",
            session_id="newer",
            cwd="/tmp/project",
            title="Newer",
            phase=SessionPhase.COMPLETED,
            model=None,
            sandbox=None,
            approval_mode=None,
            updated_at=20,
            origin=SessionOrigin.RESTORED,
        ),
    ]

    restored = build_sessions_from_processes(processes, [], provider)

    assert len(restored) == 1
    session = restored[0]
    assert session.session_id == "newer"
    assert session.synthetic_session is False
    assert session.provider_stale is False
