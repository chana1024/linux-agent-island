from __future__ import annotations

import logging
import subprocess
from dataclasses import replace

from ..core.config import AppConfig
from ..core.models import AgentSession
from ..providers import get_all_providers
from .process_matching import (
    AgentProcessInfo,
    ProcessInfo,
    TmuxClientCandidate,
    TmuxClientInfo,
    TmuxPaneInfo,
    WindowInfo,
    annotate_sessions as annotate_session_presence,
    ancestor_pids,
    find_terminal_pid,
    find_tmux_client,
    find_tmux_pane,
    find_window_for_pid_chain,
    is_guake_process,
    is_terminal_process,
    match_session_process,
    parse_process_tree,
    parse_tmux_clients,
    parse_tmux_panes,
    parse_visible_window_pids,
    parse_windows,
    reconcile_sessions as reconcile_matched_sessions,
    tmux_client_candidates,
)


logger = logging.getLogger(__name__)


def process_provider(info: ProcessInfo) -> str | None:
    provider, _confidence = process_provider_with_confidence(info)
    return provider


def process_provider_with_confidence(info: ProcessInfo) -> tuple[str | None, int]:
    config = AppConfig.default()
    for provider in get_all_providers(config):
        sigs = provider.get_process_signatures()
        if info.command in sigs.get("commands", []):
            return provider.name, 2
        for pattern in sigs.get("arg_patterns", []):
            if pattern in info.args:
                return provider.name, 1
    return None, 0


class SessionProcessInspector:
    def is_terminal_process(self, info: ProcessInfo) -> bool:
        return is_terminal_process(info)

    def _run_command(
        self,
        args: list[str],
        timeout: int = 2,
        *,
        log_commands: bool = False,
        command_context: str | None = None,
    ) -> subprocess.CompletedProcess[str] | None:
        try:
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except OSError as exc:
            if command_context is None:
                logger.warning("command failed to start args=%s error=%s", args, exc)
            else:
                logger.warning(
                    "command failed to start context=%s args=%s error=%s",
                    command_context,
                    args,
                    exc,
                )
            return None
        if log_commands:
            if command_context is None:
                logger.debug("command finished args=%s returncode=%s", args, result.returncode)
            else:
                logger.debug(
                    "command finished context=%s args=%s returncode=%s",
                    command_context,
                    args,
                    result.returncode,
                )
        return result

    def build_process_tree(self, *, log_commands: bool = False) -> dict[int, ProcessInfo]:
        result = self._run_command(
            ["ps", "-eo", "pid,ppid,tty,comm,args"],
            log_commands=log_commands,
            command_context="build_process_tree",
        )
        if result is None:
            return {}
        return parse_process_tree(result.stdout)

    def visible_window_pids(self, *, log_commands: bool = False) -> set[int]:
        result = self._run_command(
            ["wmctrl", "-lp"],
            log_commands=log_commands,
            command_context="visible_window_pids",
        )
        if result is None:
            return set()
        return parse_visible_window_pids(result.stdout)

    def list_windows(self, *, log_commands: bool = False) -> list[WindowInfo]:
        result = self._run_command(
            ["wmctrl", "-lp"],
            log_commands=log_commands,
            command_context="list_windows",
        )
        if result is None:
            return []
        return parse_windows(result.stdout)

    def active_window_pid(self, *, log_commands: bool = False) -> int | None:
        result = self._run_command(
            ["xdotool", "getactivewindow", "getwindowpid"],
            log_commands=log_commands,
            command_context="active_window_pid",
        )
        if result is None:
            return None
        try:
            return int(result.stdout.strip())
        except ValueError:
            return None

    def list_tmux_panes(self, *, log_commands: bool = False) -> list[TmuxPaneInfo]:
        result = self._run_command(
            [
                "tmux",
                "list-panes",
                "-a",
                "-F",
                "#{session_id}\t#{window_id}\t#{pane_id}\t#{pane_pid}\t#{pane_active}\t#{window_active}\t#{session_attached}",
            ],
            log_commands=log_commands,
            command_context="list_tmux_panes",
        )
        if result is None:
            return []
        return parse_tmux_panes(result.stdout)

    def list_tmux_clients(self, *, log_commands: bool = False) -> list[TmuxClientInfo]:
        result = self._run_command(
            [
                "tmux",
                "list-clients",
                "-F",
                "#{client_pid}\t#{session_id}\t#{client_tty}\t#{client_flags}",
            ],
            log_commands=log_commands,
            command_context="list_tmux_clients",
        )
        if result is None:
            return []
        return parse_tmux_clients(result.stdout)

    def process_cwd(self, pid: int, *, log_commands: bool = False) -> str | None:
        result = self._run_command(
            ["pwdx", str(pid)],
            log_commands=log_commands,
            command_context="process_cwd",
        )
        if result is None:
            return None
        if result.returncode != 0 or ":" not in result.stdout:
            return None
        return result.stdout.split(":", 1)[1].strip() or None

    def list_agent_processes(
        self,
        tree: dict[int, ProcessInfo],
        *,
        log_commands: bool = False,
    ) -> list[AgentProcessInfo]:
        agent_processes: list[AgentProcessInfo] = []
        dedup_index: dict[tuple[str, str | None, str | None], tuple[int, int]] = {}
        for info in tree.values():
            provider, confidence = process_provider_with_confidence(info)
            if provider is None:
                continue
            cwd = self.process_cwd(info.pid, log_commands=log_commands)
            dedup_key = (provider, info.tty, cwd)
            existing = dedup_index.get(dedup_key)
            if existing is not None:
                existing_confidence, existing_index = existing
                # Prefer direct command matches over broad arg pattern matches.
                # When confidence ties, keep the older PID for stability.
                if confidence < existing_confidence:
                    continue
                if confidence == existing_confidence and info.pid >= agent_processes[existing_index].pid:
                    continue
                agent_processes[existing_index] = AgentProcessInfo(
                    provider=provider,
                    pid=info.pid,
                    tty=info.tty,
                    cwd=cwd,
                )
                dedup_index[dedup_key] = (confidence, existing_index)
                continue
            dedup_index[dedup_key] = (confidence, len(agent_processes))
            agent_processes.append(
                AgentProcessInfo(
                    provider=provider,
                    pid=info.pid,
                    tty=info.tty,
                    cwd=cwd,
                )
            )
        return agent_processes

    def match_session_process(
        self,
        session: AgentSession,
        processes: list[AgentProcessInfo],
        claimed_pids: set[int] | None = None,
    ) -> AgentProcessInfo | None:
        return match_session_process(session, processes, claimed_pids)

    def find_terminal_pid(self, pid: int, tree: dict[int, ProcessInfo]) -> int | None:
        return find_terminal_pid(pid, tree)

    def is_guake_pid(self, pid: int, tree: dict[int, ProcessInfo]) -> bool:
        info = tree.get(pid)
        return info is not None and is_guake_process(info)

    def reveal_terminal_for_pid(
        self,
        pid: int,
        tree: dict[int, ProcessInfo],
        *,
        log_commands: bool = False,
    ) -> bool:
        terminal_pid = self.find_terminal_pid(pid, tree)
        if terminal_pid is None or not self.is_guake_pid(terminal_pid, tree):
            return False
        shown = self.show_guake(log_commands=log_commands)
        if shown:
            logger.info("reveal_terminal_for_pid showed guake terminal_pid=%s pid=%s", terminal_pid, pid)
        else:
            logger.warning("reveal_terminal_for_pid failed to show guake terminal_pid=%s pid=%s", terminal_pid, pid)
        return shown

    def show_guake(self, *, log_commands: bool = False) -> bool:
        result = self._run_command(
            ["guake", "--show"],
            timeout=3,
            log_commands=log_commands,
            command_context="show_guake",
        )
        if result is None:
            return False
        return result.returncode == 0

    def ancestor_pids(self, pid: int, tree: dict[int, ProcessInfo]) -> list[int]:
        return ancestor_pids(pid, tree)

    def find_tmux_pane(self, pid: int, tree: dict[int, ProcessInfo], panes: list[TmuxPaneInfo]) -> TmuxPaneInfo | None:
        return find_tmux_pane(pid, tree, panes)

    def find_window_for_pid_chain(
        self,
        pid: int,
        tree: dict[int, ProcessInfo],
        windows: list[WindowInfo],
    ) -> WindowInfo | None:
        return find_window_for_pid_chain(pid, tree, windows)

    def tmux_client_candidates(
        self,
        pane: TmuxPaneInfo,
        clients: list[TmuxClientInfo],
        tree: dict[int, ProcessInfo],
        windows: list[WindowInfo],
    ) -> list[TmuxClientCandidate]:
        return tmux_client_candidates(pane, clients, tree, windows)

    def find_tmux_client(self, candidates: list[TmuxClientCandidate]) -> TmuxClientInfo | None:
        return find_tmux_client(candidates)

    def activate_window(self, window_id: str, *, log_commands: bool = False) -> bool:
        result = self._run_command(
            ["wmctrl", "-i", "-a", window_id],
            log_commands=log_commands,
            command_context="activate_window",
        )
        if result is None:
            return False
        return result.returncode == 0

    def activate_window_for_pid(
        self,
        window: WindowInfo,
        pid: int,
        tree: dict[int, ProcessInfo],
        *,
        log_commands: bool = False,
    ) -> bool:
        self.reveal_terminal_for_pid(pid, tree, log_commands=log_commands)
        return self.activate_window(window.window_id, log_commands=log_commands)

    def select_tmux_pane(
        self,
        pane: TmuxPaneInfo,
        client: TmuxClientInfo | None,
        *,
        log_commands: bool = False,
    ) -> bool:
        commands: list[list[str]] = []
        if client is not None and client.client_tty:
            commands.append(["tmux", "switch-client", "-c", client.client_tty, "-t", pane.session_id])
        commands.extend(
            [
                ["tmux", "select-window", "-t", pane.window_id],
                ["tmux", "select-pane", "-t", pane.pane_id],
            ]
        )
        succeeded = False
        for args in commands:
            result = self._run_command(
                args,
                log_commands=log_commands,
                command_context="select_tmux_pane",
            )
            if result is None:
                continue
            succeeded = succeeded or result.returncode == 0
        return succeeded

    def annotate_sessions(
        self,
        sessions: list[AgentSession],
        process_tree: dict[int, ProcessInfo] | None = None,
        visible_window_pids: set[int] | None = None,
        active_window_pid: int | None = None,
        tmux_panes: list[TmuxPaneInfo] | None = None,
    ) -> list[AgentSession]:
        tree = process_tree if process_tree is not None else self.build_process_tree()
        visible = visible_window_pids if visible_window_pids is not None else self.visible_window_pids()
        active_pid = active_window_pid if active_window_pid is not None else self.active_window_pid()
        panes = tmux_panes if tmux_panes is not None else self.list_tmux_panes()
        return annotate_session_presence(sessions, tree, visible, active_pid, panes)

    def reconcile_sessions(
        self,
        sessions: list[AgentSession],
        process_tree: dict[int, ProcessInfo] | None = None,
        visible_window_pids: set[int] | None = None,
        active_window_pid: int | None = None,
        tmux_panes: list[TmuxPaneInfo] | None = None,
    ) -> tuple[list[AgentSession], set[tuple[str, str]]]:
        tree = process_tree if process_tree is not None else self.build_process_tree()
        processes = self.list_agent_processes(tree)
        visible = visible_window_pids if visible_window_pids is not None else self.visible_window_pids()
        active_pid = active_window_pid if active_window_pid is not None else self.active_window_pid()
        panes = tmux_panes if tmux_panes is not None else self.list_tmux_panes()
        return reconcile_matched_sessions(sessions, processes, tree, visible, active_pid, panes)

    def jump_to_session(self, session: AgentSession) -> bool:
        logger.info(
            "jump_to_session start provider=%s session_id=%s pid=%s",
            session.provider,
            session.session_id,
            session.pid,
        )
        tree = self.build_process_tree(log_commands=True)
        if session.pid is None:
            logger.warning(
                "jump_to_session aborted because session has no pid provider=%s session_id=%s",
                session.provider,
                session.session_id,
            )
            return False
        windows = self.list_windows(log_commands=True)
        panes = self.list_tmux_panes(log_commands=True)
        logger.debug(
            "jump_to_session context provider=%s session_id=%s process_count=%s window_count=%s tmux_pane_count=%s",
            session.provider,
            session.session_id,
            len(tree),
            len(windows),
            len(panes),
        )
        pane = self.find_tmux_pane(session.pid, tree, panes)
        if pane is not None:
            logger.debug(
                "jump_to_session using tmux path provider=%s session_id=%s pane_id=%s window_id=%s",
                session.provider,
                session.session_id,
                pane.pane_id,
                pane.window_id,
            )
            tmux_clients = self.list_tmux_clients(log_commands=True)
            client_candidates = self.tmux_client_candidates(
                pane,
                tmux_clients,
                tree,
                windows,
            )
            logger.debug(
                "jump_to_session tmux candidates provider=%s session_id=%s candidates=%s",
                session.provider,
                session.session_id,
                [
                    {
                        "client_pid": candidate.client.client_pid,
                        "client_session_id": candidate.client.session_id,
                        "client_tty": candidate.client.client_tty,
                        "attached": candidate.client.is_attached,
                        "focused": candidate.client.is_focused,
                        "session_matches_target": candidate.session_matches_target,
                        "window_id": None if candidate.window is None else candidate.window.window_id,
                        "window_pid": None if candidate.window is None else candidate.window.pid,
                        "window_desktop": None if candidate.window is None else candidate.window.desktop,
                        "window_title": None if candidate.window is None else candidate.window.title,
                    }
                    for candidate in client_candidates
                ],
            )
            client = self.find_tmux_client(client_candidates)
            if client is not None and client.session_id != pane.session_id:
                logger.info(
                    "jump_to_session falling back to external tmux client provider=%s session_id=%s client_pid=%s client_session_id=%s target_session_id=%s",
                    session.provider,
                    session.session_id,
                    client.client_pid,
                    client.session_id,
                    pane.session_id,
                )
            logger.debug(
                "jump_to_session tmux client provider=%s session_id=%s client_pid=%s client_tty=%s",
                session.provider,
                session.session_id,
                None if client is None else client.client_pid,
                None if client is None else client.client_tty,
            )
            selected = self.select_tmux_pane(pane, client, log_commands=True)
            if client is not None:
                if self.reveal_terminal_for_pid(client.client_pid, tree, log_commands=True):
                    windows = self.list_windows(log_commands=True)
                window = self.find_window_for_pid_chain(client.client_pid, tree, windows)
                logger.debug(
                    "jump_to_session tmux selected window provider=%s session_id=%s client_pid=%s window_id=%s window_pid=%s window_desktop=%s window_title=%s",
                    session.provider,
                    session.session_id,
                    client.client_pid,
                    None if window is None else window.window_id,
                    None if window is None else window.pid,
                    None if window is None else window.desktop,
                    None if window is None else window.title,
                )
                if window is not None and self.activate_window_for_pid(
                    window,
                    client.client_pid,
                    tree,
                    log_commands=True,
                ):
                    logger.info(
                        "jump_to_session succeeded via tmux client window provider=%s session_id=%s window_id=%s",
                        session.provider,
                        session.session_id,
                        window.window_id,
                    )
                    return True
            if self.reveal_terminal_for_pid(session.pid, tree, log_commands=True):
                windows = self.list_windows(log_commands=True)
            window = self.find_window_for_pid_chain(session.pid, tree, windows)
            logger.debug(
                "jump_to_session session fallback window provider=%s session_id=%s window_id=%s window_pid=%s window_desktop=%s window_title=%s",
                session.provider,
                session.session_id,
                None if window is None else window.window_id,
                None if window is None else window.pid,
                None if window is None else window.desktop,
                None if window is None else window.title,
            )
            if window is not None and self.activate_window_for_pid(
                window,
                session.pid,
                tree,
                log_commands=True,
            ):
                logger.info(
                    "jump_to_session succeeded via session window after tmux select provider=%s session_id=%s window_id=%s",
                    session.provider,
                    session.session_id,
                    window.window_id,
                )
                return True
            logger.info(
                "jump_to_session finished tmux path provider=%s session_id=%s selected=%s",
                session.provider,
                session.session_id,
                selected,
            )
            return selected

        if self.reveal_terminal_for_pid(session.pid, tree, log_commands=True):
            windows = self.list_windows(log_commands=True)
        window = self.find_window_for_pid_chain(session.pid, tree, windows)
        if window is None:
            logger.warning(
                "jump_to_session failed because no window matched provider=%s session_id=%s pid=%s",
                session.provider,
                session.session_id,
                session.pid,
            )
            return False
        logger.debug(
            "jump_to_session using direct window path provider=%s session_id=%s window_id=%s window_pid=%s window_desktop=%s window_title=%s",
            session.provider,
            session.session_id,
            window.window_id,
            window.pid,
            window.desktop,
            window.title,
        )
        activated = self.activate_window_for_pid(window, session.pid, tree, log_commands=True)
        if not activated:
            logger.warning(
                "jump_to_session failed to activate window provider=%s session_id=%s window_id=%s",
                session.provider,
                session.session_id,
                window.window_id,
            )
        else:
            logger.info(
                "jump_to_session succeeded via direct window provider=%s session_id=%s window_id=%s",
                session.provider,
                session.session_id,
                window.window_id,
            )
        return activated
