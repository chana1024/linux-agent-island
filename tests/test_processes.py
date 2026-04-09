from linux_agent_shell.models import AgentSession, SessionOrigin, SessionPhase
from linux_agent_shell.runtime.processes import (
    AgentProcessInfo,
    ProcessInfo,
    SessionProcessInspector,
    TmuxClientInfo,
    TmuxPaneInfo,
    WindowInfo,
    parse_tmux_clients,
    parse_tmux_panes,
    parse_visible_window_pids,
    parse_windows,
)


def test_parse_visible_window_pids_reads_pid_column() -> None:
    output = """
0x03e00007  0  321 host Terminal
0x03e00008  0  654 host Firefox
""".strip()

    assert parse_visible_window_pids(output) == {321, 654}


def test_parse_windows_reads_window_id_and_pid() -> None:
    output = """
0x03e00007  0  321 host Terminal
0x03e00008  0  654 host Firefox
""".strip()

    windows = parse_windows(output)

    assert windows == [
        WindowInfo(window_id="0x03e00007", pid=321),
        WindowInfo(window_id="0x03e00008", pid=654),
    ]


def test_process_inspector_marks_window_and_focus_from_terminal_ancestor() -> None:
    inspector = SessionProcessInspector()
    sessions = [
        AgentSession(
            provider="codex",
            session_id="s1",
            cwd="/tmp/demo",
            title="Demo",
            phase=SessionPhase.RUNNING,
            model=None,
            sandbox=None,
            approval_mode=None,
            updated_at=1,
            pid=222,
        )
    ]
    tree = {
        222: ProcessInfo(pid=222, ppid=111, command="codex", tty="/dev/pts/7"),
        111: ProcessInfo(pid=111, ppid=50, command="zsh", tty="/dev/pts/7"),
        50: ProcessInfo(pid=50, ppid=1, command="gnome-terminal-server", tty=None),
    }

    annotated = inspector.annotate_sessions(
        sessions,
        process_tree=tree,
        visible_window_pids={50},
        active_window_pid=50,
    )

    assert annotated[0].has_interactive_window is True
    assert annotated[0].is_focused is True


def test_parse_tmux_panes_reads_pid_and_focus_flags() -> None:
    output = """
%1\t@1\t%3\t700\t1\t1\t1
%2\t@2\t%4\t701\t0\t0\t1
""".strip()

    panes = parse_tmux_panes(output)

    assert len(panes) == 2
    assert panes[0].pane_pid == 700
    assert panes[0].pane_active is True
    assert panes[0].window_active is True
    assert panes[0].session_attached is True


def test_parse_tmux_clients_reads_session_and_flags() -> None:
    output = "190751\t$1\t/dev/pts/5\tattached,focused,UTF-8"

    clients = parse_tmux_clients(output)

    assert clients == [
        TmuxClientInfo(
            client_pid=190751,
            session_id="$1",
            client_tty="/dev/pts/5",
            is_attached=True,
            is_focused=True,
        )
    ]


def test_process_inspector_marks_tmux_session_as_windowed_and_focused() -> None:
    inspector = SessionProcessInspector()
    sessions = [
        AgentSession(
            provider="codex",
            session_id="s1",
            cwd="/tmp/demo",
            title="Demo",
            phase=SessionPhase.RUNNING,
            model=None,
            sandbox=None,
            approval_mode=None,
            updated_at=1,
            pid=333,
        )
    ]
    tree = {
        333: ProcessInfo(pid=333, ppid=222, command="codex", tty="/dev/pts/8"),
        222: ProcessInfo(pid=222, ppid=700, command="zsh", tty="/dev/pts/8"),
        700: ProcessInfo(pid=700, ppid=1, command="tmux: server", tty=None),
    }

    annotated = inspector.annotate_sessions(
        sessions,
        process_tree=tree,
        visible_window_pids=set(),
        active_window_pid=None,
        tmux_panes=parse_tmux_panes("%1\t@1\t%3\t222\t1\t1\t1"),
    )

    assert annotated[0].has_interactive_window is True
    assert annotated[0].is_focused is True


def test_reconcile_sessions_does_not_match_multiple_restored_sessions_to_one_cwd_process() -> None:
    inspector = SessionProcessInspector()
    sessions = [
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
            is_process_alive=True,
        ),
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
            is_process_alive=True,
        ),
    ]
    tree = {
        900: ProcessInfo(pid=900, ppid=1, command="codex", tty="pts/7"),
    }

    original_list_agent_processes = inspector.list_agent_processes
    inspector.list_agent_processes = lambda _tree: [  # type: ignore[method-assign]
        AgentProcessInfo(provider="codex", pid=900, tty="pts/7", cwd="/tmp/project")
    ]
    try:
        reconciled, alive_keys = inspector.reconcile_sessions(
            sessions,
            process_tree=tree,
            visible_window_pids=set(),
            active_window_pid=None,
            tmux_panes=[],
        )
    finally:
        inspector.list_agent_processes = original_list_agent_processes  # type: ignore[method-assign]

    assert alive_keys == {("codex", "newer")}
    by_id = {session.session_id: session for session in reconciled}
    assert by_id["newer"].pid == 900
    assert by_id["older"].pid is None


def test_jump_to_session_activates_matching_terminal_window(monkeypatch) -> None:
    inspector = SessionProcessInspector()
    session = AgentSession(
        provider="codex",
        session_id="jump",
        cwd="/tmp/demo",
        title="Jump",
        phase=SessionPhase.RUNNING,
        model=None,
        sandbox=None,
        approval_mode=None,
        updated_at=1,
        pid=222,
    )
    tree = {
        222: ProcessInfo(pid=222, ppid=111, command="codex", tty="pts/7"),
        111: ProcessInfo(pid=111, ppid=50, command="zsh", tty="pts/7"),
        50: ProcessInfo(pid=50, ppid=1, command="gnome-terminal-server", tty=None),
    }

    calls: list[list[str]] = []

    monkeypatch.setattr(inspector, "build_process_tree", lambda: tree)
    monkeypatch.setattr(inspector, "list_windows", lambda: [WindowInfo(window_id="0x03e00007", pid=50)])
    monkeypatch.setattr(inspector, "list_tmux_panes", lambda: [])

    def fake_run(args, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(args)
        class Result:
            returncode = 0
        return Result()

    monkeypatch.setattr("linux_agent_shell.runtime.processes.subprocess.run", fake_run)

    assert inspector.jump_to_session(session) is True
    assert calls == [["wmctrl", "-i", "-a", "0x03e00007"]]


def test_jump_to_session_selects_tmux_pane_and_activates_client_window(monkeypatch) -> None:
    inspector = SessionProcessInspector()
    session = AgentSession(
        provider="codex",
        session_id="jump",
        cwd="/tmp/demo",
        title="Jump",
        phase=SessionPhase.RUNNING,
        model=None,
        sandbox=None,
        approval_mode=None,
        updated_at=1,
        pid=333,
    )
    tree = {
        333: ProcessInfo(pid=333, ppid=222, command="codex", tty="pts/8"),
        222: ProcessInfo(pid=222, ppid=700, command="zsh", tty="pts/8"),
        700: ProcessInfo(pid=700, ppid=1, command="tmux: server", tty=None),
        190751: ProcessInfo(pid=190751, ppid=189012, command="tmux: client", tty="pts/5"),
        189012: ProcessInfo(pid=189012, ppid=188951, command="zsh", tty="pts/5"),
        188951: ProcessInfo(pid=188951, ppid=1, command="python3", tty=None),
    }

    calls: list[list[str]] = []

    monkeypatch.setattr(inspector, "build_process_tree", lambda: tree)
    monkeypatch.setattr(
        inspector,
        "list_tmux_panes",
        lambda: [TmuxPaneInfo(session_id="$1", window_id="@1", pane_id="%3", pane_pid=222, pane_active=True, window_active=True, session_attached=True)],
    )
    monkeypatch.setattr(
        inspector,
        "list_tmux_clients",
        lambda: [TmuxClientInfo(client_pid=190751, session_id="$1", client_tty="/dev/pts/5", is_attached=True, is_focused=True)],
    )
    monkeypatch.setattr(inspector, "list_windows", lambda: [WindowInfo(window_id="0x03e00007", pid=188951)])

    def fake_run(args, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(args)
        class Result:
            returncode = 0
        return Result()

    monkeypatch.setattr("linux_agent_shell.runtime.processes.subprocess.run", fake_run)

    assert inspector.jump_to_session(session) is True
    assert calls == [
        ["tmux", "switch-client", "-c", "/dev/pts/5", "-t", "$1"],
        ["tmux", "select-window", "-t", "@1"],
        ["tmux", "select-pane", "-t", "%3"],
        ["wmctrl", "-i", "-a", "0x03e00007"],
    ]


def test_jump_to_session_logs_missing_pid(caplog) -> None:
    inspector = SessionProcessInspector()
    session = AgentSession(
        provider="codex",
        session_id="jump",
        cwd="/tmp/demo",
        title="Jump",
        phase=SessionPhase.RUNNING,
        model=None,
        sandbox=None,
        approval_mode=None,
        updated_at=1,
        pid=None,
    )

    with caplog.at_level("WARNING"):
        assert inspector.jump_to_session(session) is False

    assert "session has no pid" in caplog.text


def test_jump_to_session_logs_missing_window_match(monkeypatch, caplog) -> None:
    inspector = SessionProcessInspector()
    session = AgentSession(
        provider="codex",
        session_id="jump",
        cwd="/tmp/demo",
        title="Jump",
        phase=SessionPhase.RUNNING,
        model=None,
        sandbox=None,
        approval_mode=None,
        updated_at=1,
        pid=222,
    )
    tree = {
        222: ProcessInfo(pid=222, ppid=111, command="codex", tty="pts/7"),
        111: ProcessInfo(pid=111, ppid=50, command="zsh", tty="pts/7"),
        50: ProcessInfo(pid=50, ppid=1, command="gnome-terminal-server", tty=None),
    }

    monkeypatch.setattr(inspector, "build_process_tree", lambda: tree)
    monkeypatch.setattr(inspector, "list_windows", lambda: [])
    monkeypatch.setattr(inspector, "list_tmux_panes", lambda: [])

    with caplog.at_level("WARNING"):
        assert inspector.jump_to_session(session) is False

    assert "no window matched" in caplog.text


def test_jump_to_session_logs_failed_window_activation(monkeypatch, caplog) -> None:
    inspector = SessionProcessInspector()
    session = AgentSession(
        provider="codex",
        session_id="jump",
        cwd="/tmp/demo",
        title="Jump",
        phase=SessionPhase.RUNNING,
        model=None,
        sandbox=None,
        approval_mode=None,
        updated_at=1,
        pid=222,
    )
    tree = {
        222: ProcessInfo(pid=222, ppid=111, command="codex", tty="pts/7"),
        111: ProcessInfo(pid=111, ppid=50, command="zsh", tty="pts/7"),
        50: ProcessInfo(pid=50, ppid=1, command="gnome-terminal-server", tty=None),
    }

    monkeypatch.setattr(inspector, "build_process_tree", lambda: tree)
    monkeypatch.setattr(inspector, "list_windows", lambda: [WindowInfo(window_id="0x03e00007", pid=50)])
    monkeypatch.setattr(inspector, "list_tmux_panes", lambda: [])
    monkeypatch.setattr(inspector, "activate_window", lambda _window_id: False)

    with caplog.at_level("WARNING"):
        assert inspector.jump_to_session(session) is False

    assert "failed to activate window" in caplog.text
