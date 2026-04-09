from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, replace

from ..models import AgentSession


logger = logging.getLogger(__name__)

PROVIDER_COMMANDS = {
    "codex": {"codex"},
    "claude": {"claude", "claude-code"},
}


TERMINAL_NAMES = {
    "gnome-terminal-server",
    "gnome-terminal",
    "kgx",
    "ptyxis",
    "alacritty",
    "kitty",
    "wezterm-gui",
    "tilix",
    "konsole",
    "xterm",
    "zellij",
}


@dataclass(frozen=True, slots=True)
class ProcessInfo:
    pid: int
    ppid: int
    command: str
    tty: str | None


@dataclass(frozen=True, slots=True)
class TmuxPaneInfo:
    session_id: str
    window_id: str
    pane_id: str
    pane_pid: int
    pane_active: bool
    window_active: bool
    session_attached: bool


@dataclass(frozen=True, slots=True)
class TmuxClientInfo:
    client_pid: int
    session_id: str
    client_tty: str | None
    is_attached: bool
    is_focused: bool


@dataclass(frozen=True, slots=True)
class AgentProcessInfo:
    provider: str
    pid: int
    tty: str | None
    cwd: str | None


@dataclass(frozen=True, slots=True)
class WindowInfo:
    window_id: str
    pid: int


def parse_visible_window_pids(output: str) -> set[int]:
    pids: set[int] = set()
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        try:
            pids.add(int(parts[2]))
        except ValueError:
            continue
    return pids


def parse_windows(output: str) -> list[WindowInfo]:
    windows: list[WindowInfo] = []
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        try:
            pid = int(parts[2])
        except ValueError:
            continue
        windows.append(WindowInfo(window_id=parts[0], pid=pid))
    return windows


def parse_process_tree(output: str) -> dict[int, ProcessInfo]:
    tree: dict[int, ProcessInfo] = {}
    for line in output.splitlines():
        parts = line.strip().split(maxsplit=3)
        if len(parts) < 4:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
        except ValueError:
            continue
        tty = None if parts[2] in {"??", "-"} else parts[2]
        tree[pid] = ProcessInfo(pid=pid, ppid=ppid, command=parts[3], tty=tty)
    return tree


def parse_tmux_panes(output: str) -> list[TmuxPaneInfo]:
    panes: list[TmuxPaneInfo] = []
    for line in output.splitlines():
        parts = line.strip().split("\t")
        if len(parts) != 7:
            continue
        try:
            pane_pid = int(parts[3])
        except ValueError:
            continue
        panes.append(
            TmuxPaneInfo(
                session_id=parts[0],
                window_id=parts[1],
                pane_id=parts[2],
                pane_pid=pane_pid,
                pane_active=parts[4] == "1",
                window_active=parts[5] == "1",
                session_attached=parts[6] == "1",
            )
        )
    return panes


def parse_tmux_clients(output: str) -> list[TmuxClientInfo]:
    clients: list[TmuxClientInfo] = []
    for line in output.splitlines():
        parts = line.strip().split("\t")
        if len(parts) != 4:
            continue
        try:
            client_pid = int(parts[0])
        except ValueError:
            continue
        flags = {flag.strip() for flag in parts[3].split(",") if flag.strip()}
        clients.append(
            TmuxClientInfo(
                client_pid=client_pid,
                session_id=parts[1],
                client_tty=parts[2] or None,
                is_attached="attached" in flags,
                is_focused="focused" in flags,
            )
        )
    return clients


class SessionProcessInspector:
    def _run_command(self, args: list[str], timeout: int = 2) -> subprocess.CompletedProcess[str] | None:
        try:
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except OSError as exc:
            logger.warning("command failed to start args=%s error=%s", args, exc)
            return None
        logger.debug("command finished args=%s returncode=%s", args, result.returncode)
        return result

    def build_process_tree(self) -> dict[int, ProcessInfo]:
        result = self._run_command(["ps", "-eo", "pid,ppid,tty,comm"])
        if result is None:
            return {}
        return parse_process_tree(result.stdout)

    def visible_window_pids(self) -> set[int]:
        result = self._run_command(["wmctrl", "-lp"])
        if result is None:
            return set()
        return parse_visible_window_pids(result.stdout)

    def list_windows(self) -> list[WindowInfo]:
        result = self._run_command(["wmctrl", "-lp"])
        if result is None:
            return []
        return parse_windows(result.stdout)

    def active_window_pid(self) -> int | None:
        result = self._run_command(["xdotool", "getactivewindow", "getwindowpid"])
        if result is None:
            return None
        try:
            return int(result.stdout.strip())
        except ValueError:
            return None

    def list_tmux_panes(self) -> list[TmuxPaneInfo]:
        result = self._run_command(
            [
                "tmux",
                "list-panes",
                "-a",
                "-F",
                "#{session_id}\t#{window_id}\t#{pane_id}\t#{pane_pid}\t#{pane_active}\t#{window_active}\t#{session_attached}",
            ]
        )
        if result is None:
            return []
        return parse_tmux_panes(result.stdout)

    def list_tmux_clients(self) -> list[TmuxClientInfo]:
        result = self._run_command(
            [
                "tmux",
                "list-clients",
                "-F",
                "#{client_pid}\t#{session_id}\t#{client_tty}\t#{client_flags}",
            ]
        )
        if result is None:
            return []
        return parse_tmux_clients(result.stdout)

    def process_cwd(self, pid: int) -> str | None:
        result = self._run_command(["pwdx", str(pid)])
        if result is None:
            return None
        if result.returncode != 0 or ":" not in result.stdout:
            return None
        return result.stdout.split(":", 1)[1].strip() or None

    def list_agent_processes(self, tree: dict[int, ProcessInfo]) -> list[AgentProcessInfo]:
        agent_processes: list[AgentProcessInfo] = []
        for info in tree.values():
            provider = next(
                (name for name, commands in PROVIDER_COMMANDS.items() if info.command in commands),
                None,
            )
            if provider is None:
                continue
            agent_processes.append(
                AgentProcessInfo(
                    provider=provider,
                    pid=info.pid,
                    tty=info.tty,
                    cwd=self.process_cwd(info.pid),
                )
            )
        return agent_processes

    def match_session_process(
        self,
        session: AgentSession,
        processes: list[AgentProcessInfo],
        claimed_pids: set[int] | None = None,
    ) -> AgentProcessInfo | None:
        claimed = claimed_pids or set()
        provider_processes = [process for process in processes if process.provider == session.provider]
        if session.pid is not None:
            for process in provider_processes:
                if process.pid not in claimed and process.pid == session.pid:
                    return process
        if session.tty:
            tty_matches = [
                process for process in provider_processes
                if process.pid not in claimed
                and (process.tty == session.tty.removeprefix("/dev/") or process.tty == session.tty)
            ]
            if len(tty_matches) == 1:
                return tty_matches[0]
        if session.cwd:
            cwd_matches = [
                process for process in provider_processes
                if process.pid not in claimed and process.cwd == session.cwd
            ]
            if len(cwd_matches) == 1:
                return cwd_matches[0]
        return None

    def find_terminal_pid(self, pid: int, tree: dict[int, ProcessInfo]) -> int | None:
        current = pid
        depth = 0
        while current > 1 and depth < 20:
            info = tree.get(current)
            if info is None:
                return None
            if info.command in TERMINAL_NAMES:
                return current
            current = info.ppid
            depth += 1
        return None

    def ancestor_pids(self, pid: int, tree: dict[int, ProcessInfo]) -> list[int]:
        current = pid
        depth = 0
        ancestors: list[int] = []
        while current > 1 and depth < 20:
            ancestors.append(current)
            info = tree.get(current)
            if info is None:
                break
            current = info.ppid
            depth += 1
        return ancestors

    def find_tmux_pane(self, pid: int, tree: dict[int, ProcessInfo], panes: list[TmuxPaneInfo]) -> TmuxPaneInfo | None:
        ancestors = set(self.ancestor_pids(pid, tree))
        for pane in panes:
            if pane.pane_pid in ancestors:
                return pane
        return None

    def find_window_for_pid_chain(
        self,
        pid: int,
        tree: dict[int, ProcessInfo],
        windows: list[WindowInfo],
    ) -> WindowInfo | None:
        by_pid = {window.pid: window for window in windows}
        for ancestor_pid in self.ancestor_pids(pid, tree):
            window = by_pid.get(ancestor_pid)
            if window is not None:
                return window
        return None

    def find_tmux_client(
        self,
        pane: TmuxPaneInfo,
        clients: list[TmuxClientInfo],
    ) -> TmuxClientInfo | None:
        session_clients = [client for client in clients if client.session_id == pane.session_id]
        if not session_clients:
            return None
        focused_client = next((client for client in session_clients if client.is_focused), None)
        if focused_client is not None:
            return focused_client
        attached_client = next((client for client in session_clients if client.is_attached), None)
        if attached_client is not None:
            return attached_client
        return session_clients[0]

    def activate_window(self, window_id: str) -> bool:
        result = self._run_command(["wmctrl", "-i", "-a", window_id])
        if result is None:
            return False
        return result.returncode == 0

    def select_tmux_pane(self, pane: TmuxPaneInfo, client: TmuxClientInfo | None) -> bool:
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
            result = self._run_command(args)
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
        annotated: list[AgentSession] = []
        for session in sessions:
            terminal_pid = self.find_terminal_pid(session.pid, tree) if session.pid is not None else None
            tmux_pane = self.find_tmux_pane(session.pid, tree, panes) if session.pid is not None else None
            has_window = terminal_pid is not None and terminal_pid in visible
            is_focused = terminal_pid is not None and active_pid is not None and terminal_pid == active_pid
            if not has_window and tmux_pane is not None:
                has_window = tmux_pane.session_attached
            if not is_focused and tmux_pane is not None:
                is_focused = tmux_pane.session_attached and tmux_pane.window_active and tmux_pane.pane_active
            annotated.append(replace(session, has_interactive_window=has_window, is_focused=is_focused))
        return annotated

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
        matched_sessions: list[AgentSession] = []
        alive_session_keys: set[tuple[str, str]] = set()
        claimed_pids: set[int] = set()

        for session in sorted(sessions, key=lambda item: item.updated_at, reverse=True):
            match = self.match_session_process(session, processes, claimed_pids)
            if match is not None:
                alive_session_keys.add((session.provider, session.session_id))
                claimed_pids.add(match.pid)
                session = replace(
                    session,
                    pid=match.pid,
                    tty=session.tty or (f"/dev/{match.tty}" if match.tty and not match.tty.startswith("/dev/") else match.tty),
                )
            matched_sessions.append(session)

        return (
            self.annotate_sessions(
                matched_sessions,
                process_tree=tree,
                visible_window_pids=visible_window_pids,
                active_window_pid=active_window_pid,
                tmux_panes=tmux_panes,
            ),
            alive_session_keys,
        )

    def jump_to_session(self, session: AgentSession) -> bool:
        logger.info(
            "jump_to_session start provider=%s session_id=%s pid=%s",
            session.provider,
            session.session_id,
            session.pid,
        )
        tree = self.build_process_tree()
        if session.pid is None:
            logger.warning(
                "jump_to_session aborted because session has no pid provider=%s session_id=%s",
                session.provider,
                session.session_id,
            )
            return False
        windows = self.list_windows()
        panes = self.list_tmux_panes()
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
            client = self.find_tmux_client(pane, self.list_tmux_clients())
            logger.debug(
                "jump_to_session tmux client provider=%s session_id=%s client_pid=%s client_tty=%s",
                session.provider,
                session.session_id,
                None if client is None else client.client_pid,
                None if client is None else client.client_tty,
            )
            selected = self.select_tmux_pane(pane, client)
            if client is not None:
                window = self.find_window_for_pid_chain(client.client_pid, tree, windows)
                if window is not None and self.activate_window(window.window_id):
                    logger.info(
                        "jump_to_session succeeded via tmux client window provider=%s session_id=%s window_id=%s",
                        session.provider,
                        session.session_id,
                        window.window_id,
                    )
                    return True
            window = self.find_window_for_pid_chain(session.pid, tree, windows)
            if window is not None and self.activate_window(window.window_id):
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
            "jump_to_session using direct window path provider=%s session_id=%s window_id=%s",
            session.provider,
            session.session_id,
            window.window_id,
        )
        activated = self.activate_window(window.window_id)
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
