from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..core.models import AgentSession
    from ..runtime.agent_events import AgentEvent


class BaseProvider:
    @property
    def name(self) -> str:
        raise NotImplementedError

    def install_hooks(self) -> None:
        raise NotImplementedError

    def uninstall_hooks(self) -> None:
        raise NotImplementedError

    def load_transcript(
        self,
        session_id: str,
        cwd: str = "",
        **kwargs: Any,
    ) -> list[dict[str, str]]:
        raise NotImplementedError

    def load_sessions(self) -> list[AgentSession]:
        """Loads sessions from the provider's persistent storage."""
        return []

    def filter_cached_sessions(self, cached_sessions: list[AgentSession]) -> list[AgentSession]:
        """Filters out cached sessions that are no longer valid for this provider."""
        return cached_sessions

    def get_process_signatures(self) -> dict[str, list[str]]:
        """
        Returns a dictionary with identification signatures.
        Supported keys:
        - 'commands': list of executable names (e.g., ['claude', 'claude-code'])
        - 'arg_patterns': list of regex or substrings for command line arguments.
        """
        return {"commands": [], "arg_patterns": []}

    def build_event(
        self,
        hook_name: str,
        payload: dict[str, Any],
        pid: int | None = None,
        tty: str | None = None,
    ) -> dict[str, Any]:
        """
        Translates raw hook payload from the agent tool into a standardized
        Island event dictionary.
        """
        raise NotImplementedError

    def poll_events(self, sessions: list[AgentSession]) -> list[AgentEvent]:
        """Returns provider-specific incremental events from local state sources."""
        return []
