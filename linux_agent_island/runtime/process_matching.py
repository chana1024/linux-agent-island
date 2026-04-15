from __future__ import annotations

from dataclasses import dataclass, replace

from ..core.models import AgentSession


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
    args: str = ""


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
    desktop: int | None
    pid: int
    title: str


@dataclass(frozen=True, slots=True)
class TmuxClientCandidate:
    client: TmuxClientInfo
    window: WindowInfo | None
    session_matches_target: bool = False


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
        parts = line.split(maxsplit=4)
        if len(parts) < 4:
            continue
        try:
            desktop = int(parts[1])
            pid = int(parts[2])
        except ValueError:
            continue
        title = parts[4] if len(parts) > 4 else ""
        windows.append(WindowInfo(window_id=parts[0], desktop=desktop, pid=pid, title=title))
    return windows


def parse_process_tree(output: str) -> dict[int, ProcessInfo]:
    tree: dict[int, ProcessInfo] = {}
    for line in output.splitlines():
        parts = line.strip().split(maxsplit=4)
        if len(parts) < 4:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
        except ValueError:
            continue
        tty = None if parts[2] in {"??", "-"} else parts[2]
        args = parts[4] if len(parts) > 4 else ""
        tree[pid] = ProcessInfo(pid=pid, ppid=ppid, command=parts[3], tty=tty, args=args)
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


def is_guake_process(info: ProcessInfo) -> bool:
    args = info.args.strip()
    return info.command == "guake" or args.endswith("/usr/bin/guake") or "/usr/bin/guake " in args


def is_terminal_process(info: ProcessInfo) -> bool:
    return info.command in TERMINAL_NAMES or is_guake_process(info)


def match_session_process(
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
            process
            for process in provider_processes
            if process.pid not in claimed
            and (process.tty == session.tty.removeprefix("/dev/") or process.tty == session.tty)
        ]
        if tty_matches:
            return tty_matches[0]
    if session.cwd:
        cwd_matches = [
            process for process in provider_processes if process.pid not in claimed and process.cwd == session.cwd
        ]
        if cwd_matches:
            return cwd_matches[0]
    return None


def ancestor_pids(pid: int, tree: dict[int, ProcessInfo]) -> list[int]:
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


def find_terminal_pid(pid: int, tree: dict[int, ProcessInfo]) -> int | None:
    current = pid
    depth = 0
    while current > 1 and depth < 20:
        info = tree.get(current)
        if info is None:
            return None
        if is_terminal_process(info):
            return current
        current = info.ppid
        depth += 1
    return None


def find_tmux_pane(pid: int, tree: dict[int, ProcessInfo], panes: list[TmuxPaneInfo]) -> TmuxPaneInfo | None:
    ancestors = set(ancestor_pids(pid, tree))
    for pane in panes:
        if pane.pane_pid in ancestors:
            return pane
    return None


def find_window_for_pid_chain(pid: int, tree: dict[int, ProcessInfo], windows: list[WindowInfo]) -> WindowInfo | None:
    by_pid = {window.pid: window for window in windows}
    for ancestor_pid in ancestor_pids(pid, tree):
        window = by_pid.get(ancestor_pid)
        if window is not None:
            return window
    return None


def tmux_client_candidates(
    pane: TmuxPaneInfo,
    clients: list[TmuxClientInfo],
    tree: dict[int, ProcessInfo],
    windows: list[WindowInfo],
) -> list[TmuxClientCandidate]:
    return [
        TmuxClientCandidate(
            client=client,
            window=find_window_for_pid_chain(client.client_pid, tree, windows),
            session_matches_target=client.session_id == pane.session_id,
        )
        for client in clients
    ]


def find_tmux_client(candidates: list[TmuxClientCandidate]) -> TmuxClientInfo | None:
    if not candidates:
        return None
    matching_candidates = [candidate for candidate in candidates if candidate.session_matches_target]
    base_candidates = matching_candidates
    if not base_candidates:
        prioritized_groups = [
            [candidate for candidate in candidates if candidate.window is not None and candidate.client.is_focused],
            [candidate for candidate in candidates if candidate.window is not None and candidate.client.is_attached],
            [candidate for candidate in candidates if candidate.client.is_focused],
            [candidate for candidate in candidates if candidate.client.is_attached],
            [candidate for candidate in candidates if candidate.window is not None],
            candidates,
        ]
        for group in prioritized_groups:
            if len(group) == 1:
                return group[0].client
        return None
    windowed_candidates = [candidate for candidate in base_candidates if candidate.window is not None]
    preferred_candidates = windowed_candidates or base_candidates
    focused_client = next((candidate.client for candidate in preferred_candidates if candidate.client.is_focused), None)
    if focused_client is not None:
        return focused_client
    attached_client = next((candidate.client for candidate in preferred_candidates if candidate.client.is_attached), None)
    if attached_client is not None:
        return attached_client
    return preferred_candidates[0].client


def annotate_sessions(
    sessions: list[AgentSession],
    tree: dict[int, ProcessInfo],
    visible_window_pids: set[int],
    active_window_pid: int | None,
    tmux_panes: list[TmuxPaneInfo],
) -> list[AgentSession]:
    annotated: list[AgentSession] = []
    for session in sessions:
        terminal_pid = find_terminal_pid(session.pid, tree) if session.pid is not None else None
        tmux_pane = find_tmux_pane(session.pid, tree, tmux_panes) if session.pid is not None else None
        has_window = terminal_pid is not None and terminal_pid in visible_window_pids
        is_focused = terminal_pid is not None and active_window_pid is not None and terminal_pid == active_window_pid
        if not has_window and tmux_pane is not None:
            has_window = tmux_pane.session_attached
        if not is_focused and tmux_pane is not None:
            is_focused = tmux_pane.session_attached and tmux_pane.window_active and tmux_pane.pane_active
        annotated.append(replace(session, has_interactive_window=has_window, is_focused=is_focused))
    return annotated


def reconcile_sessions(
    sessions: list[AgentSession],
    processes: list[AgentProcessInfo],
    tree: dict[int, ProcessInfo],
    visible_window_pids: set[int],
    active_window_pid: int | None,
    tmux_panes: list[TmuxPaneInfo],
) -> tuple[list[AgentSession], set[tuple[str, str]]]:
    matched_sessions: list[AgentSession] = []
    alive_session_keys: set[tuple[str, str]] = set()
    claimed_pids: set[int] = set()

    for session in sorted(
        sessions,
        key=lambda item: (item.identity_confirmed_by_hook, item.updated_at),
        reverse=True,
    ):
        match = match_session_process(session, processes, claimed_pids)
        if match is not None:
            alive_session_keys.add((session.provider, session.session_id))
            claimed_pids.add(match.pid)
            session = replace(
                session,
                pid=match.pid,
                tty=session.tty or (f"/dev/{match.tty}" if match.tty and not match.tty.startswith("/dev/") else match.tty),
            )
        matched_sessions.append(session)

    return annotate_sessions(
        matched_sessions,
        tree=tree,
        visible_window_pids=visible_window_pids,
        active_window_pid=active_window_pid,
        tmux_panes=tmux_panes,
    ), alive_session_keys
