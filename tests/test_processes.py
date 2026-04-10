from linux_agent_shell.models import AgentSession, SessionOrigin, SessionPhase
from linux_agent_shell.runtime.processes import (
    AgentProcessInfo,
    ProcessInfo,
    SessionProcessInspector,
    TmuxClientCandidate,
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
        WindowInfo(window_id="0x03e00007", desktop=0, pid=321, title="Terminal"),
        WindowInfo(window_id="0x03e00008", desktop=0, pid=654, title="Firefox"),
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


def test_find_tmux_client_prefers_windowed_attached_candidate() -> None:
    inspector = SessionProcessInspector()
    candidates = [
        TmuxClientCandidate(
            client=TmuxClientInfo(
                client_pid=1,
                session_id="$1",
                client_tty="/dev/pts/1",
                is_attached=False,
                is_focused=True,
            ),
            window=None,
        ),
        TmuxClientCandidate(
            client=TmuxClientInfo(
                client_pid=2,
                session_id="$1",
                client_tty="/dev/pts/2",
                is_attached=True,
                is_focused=False,
            ),
            window=WindowInfo(window_id="0x03e00007", desktop=0, pid=200, title="Terminal"),
        ),
    ]

    selected = inspector.find_tmux_client(candidates)

    assert selected == candidates[1].client


def test_find_tmux_client_falls_back_to_non_target_session_candidate() -> None:
    inspector = SessionProcessInspector()
    candidates = [
        TmuxClientCandidate(
            client=TmuxClientInfo(
                client_pid=1,
                session_id="$1",
                client_tty="/dev/pts/1",
                is_attached=True,
                is_focused=True,
            ),
            window=WindowInfo(window_id="0x03e00007", desktop=0, pid=200, title="Guake!"),
            session_matches_target=False,
        ),
    ]

    selected = inspector.find_tmux_client(candidates)

    assert selected == candidates[0].client


def test_build_process_tree_does_not_log_commands_by_default(monkeypatch, caplog) -> None:
    inspector = SessionProcessInspector()

    def fake_run(args, **kwargs):  # type: ignore[no-untyped-def]
        class Result:
            stdout = "100 1 pts/7 codex codex --yolo\n"
            returncode = 0

        return Result()

    monkeypatch.setattr("linux_agent_shell.runtime.processes.subprocess.run", fake_run)

    with caplog.at_level("DEBUG"):
        tree = inspector.build_process_tree()

    assert tree[100] == ProcessInfo(pid=100, ppid=1, command="codex", tty="pts/7", args="codex --yolo")
    assert "command finished" not in caplog.text


def test_activate_window_logs_commands_when_enabled(monkeypatch, caplog) -> None:
    inspector = SessionProcessInspector()

    def fake_run(args, **kwargs):  # type: ignore[no-untyped-def]
        class Result:
            returncode = 0

        return Result()

    monkeypatch.setattr("linux_agent_shell.runtime.processes.subprocess.run", fake_run)

    with caplog.at_level("DEBUG"):
        activated = inspector.activate_window("0x03e00007", log_commands=True)

    assert activated is True
    assert "command finished context=activate_window" in caplog.text


def test_activate_window_for_guake_shows_window_before_activation(monkeypatch) -> None:
    inspector = SessionProcessInspector()
    tree = {
        188951: ProcessInfo(
            pid=188951,
            ppid=1,
            command="python3",
            tty=None,
            args="/usr/bin/python3 /usr/bin/guake",
        )
    }
    window = WindowInfo(window_id="0x03e00007", desktop=0, pid=188951, title="Guake!")
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(args)
        class Result:
            returncode = 0

        return Result()

    monkeypatch.setattr("linux_agent_shell.runtime.processes.subprocess.run", fake_run)

    activated = inspector.activate_window_for_pid(window, window.pid, tree, log_commands=True)

    assert activated is True
    assert calls == [
        ["guake", "--show"],
        ["wmctrl", "-i", "-a", "0x03e00007"],
    ]


def test_jump_to_session_reveals_hidden_guake_tmux_window(monkeypatch) -> None:
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
        188951: ProcessInfo(
            pid=188951,
            ppid=1,
            command="python3",
            tty=None,
            args="/usr/bin/python3 /usr/bin/guake",
        ),
    }
    calls: list[list[str]] = []
    window = WindowInfo(window_id="0x03e00007", desktop=0, pid=188951, title="Guake!")
    windows_by_call = [
        [],
        [window],
    ]

    monkeypatch.setattr(inspector, "build_process_tree", lambda **_kwargs: tree)
    monkeypatch.setattr(
        inspector,
        "list_tmux_panes",
        lambda **_kwargs: [TmuxPaneInfo(session_id="$1", window_id="@1", pane_id="%3", pane_pid=222, pane_active=True, window_active=True, session_attached=True)],
    )
    monkeypatch.setattr(
        inspector,
        "list_tmux_clients",
        lambda **_kwargs: [TmuxClientInfo(client_pid=190751, session_id="$1", client_tty="/dev/pts/5", is_attached=True, is_focused=True)],
    )
    monkeypatch.setattr(inspector, "list_windows", lambda **_kwargs: windows_by_call.pop(0) if windows_by_call else [window])

    def fake_run(args, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(args)
        class Result:
            returncode = 0

        return Result()

    monkeypatch.setattr("linux_agent_shell.runtime.processes.subprocess.run", fake_run)

    assert inspector.jump_to_session(session) is True
    assert ["guake", "--show"] in calls
    assert ["wmctrl", "-i", "-a", "0x03e00007"] in calls


def test_jump_to_session_uses_external_tmux_client_when_target_session_has_no_client(monkeypatch) -> None:
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
        333: ProcessInfo(pid=333, ppid=222, command="codex", tty="/dev/pts/8"),
        222: ProcessInfo(pid=222, ppid=700, command="zsh", tty="/dev/pts/8"),
        700: ProcessInfo(pid=700, ppid=1, command="tmux: server", tty=None),
        190751: ProcessInfo(pid=190751, ppid=189012, command="tmux: client", tty="/dev/pts/5"),
        189012: ProcessInfo(pid=189012, ppid=188951, command="zsh", tty="/dev/pts/5"),
        188951: ProcessInfo(
            pid=188951,
            ppid=1,
            command="python3",
            tty=None,
            args="/usr/bin/python3 /usr/bin/guake",
        ),
    }
    calls: list[list[str]] = []
    window = WindowInfo(window_id="0x03e00007", desktop=0, pid=188951, title="Guake!")

    monkeypatch.setattr(inspector, "build_process_tree", lambda **_kwargs: tree)
    monkeypatch.setattr(
        inspector,
        "list_tmux_panes",
        lambda **_kwargs: [TmuxPaneInfo(session_id="$2", window_id="@2", pane_id="%9", pane_pid=222, pane_active=True, window_active=True, session_attached=False)],
    )
    monkeypatch.setattr(
        inspector,
        "list_tmux_clients",
        lambda **_kwargs: [TmuxClientInfo(client_pid=190751, session_id="$1", client_tty="/dev/pts/5", is_attached=True, is_focused=True)],
    )
    monkeypatch.setattr(inspector, "list_windows", lambda **_kwargs: [window])

    def fake_run(args, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(args)
        class Result:
            returncode = 0

        return Result()

    monkeypatch.setattr("linux_agent_shell.runtime.processes.subprocess.run", fake_run)

    assert inspector.jump_to_session(session) is True
    assert ["tmux", "switch-client", "-c", "/dev/pts/5", "-t", "$2"] in calls
    assert ["wmctrl", "-i", "-a", "0x03e00007"] in calls


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

    monkeypatch.setattr(inspector, "build_process_tree", lambda **_kwargs: tree)
    monkeypatch.setattr(
        inspector,
        "list_windows",
        lambda **_kwargs: [WindowInfo(window_id="0x03e00007", desktop=0, pid=50, title="Terminal")],
    )
    monkeypatch.setattr(inspector, "list_tmux_panes", lambda **_kwargs: [])

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

    monkeypatch.setattr(inspector, "build_process_tree", lambda **_kwargs: tree)
    monkeypatch.setattr(
        inspector,
        "list_tmux_panes",
        lambda **_kwargs: [TmuxPaneInfo(session_id="$1", window_id="@1", pane_id="%3", pane_pid=222, pane_active=True, window_active=True, session_attached=True)],
    )
    monkeypatch.setattr(
        inspector,
        "list_tmux_clients",
        lambda **_kwargs: [TmuxClientInfo(client_pid=190751, session_id="$1", client_tty="/dev/pts/5", is_attached=True, is_focused=True)],
    )
    monkeypatch.setattr(
        inspector,
        "list_windows",
        lambda **_kwargs: [WindowInfo(window_id="0x03e00007", desktop=0, pid=188951, title="Terminal")],
    )

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

    monkeypatch.setattr(inspector, "build_process_tree", lambda **_kwargs: tree)
    monkeypatch.setattr(inspector, "list_windows", lambda **_kwargs: [])
    monkeypatch.setattr(inspector, "list_tmux_panes", lambda **_kwargs: [])

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

    monkeypatch.setattr(inspector, "build_process_tree", lambda **_kwargs: tree)
    monkeypatch.setattr(
        inspector,
        "list_windows",
        lambda **_kwargs: [WindowInfo(window_id="0x03e00007", desktop=0, pid=50, title="Terminal")],
    )
    monkeypatch.setattr(inspector, "list_tmux_panes", lambda **_kwargs: [])
    monkeypatch.setattr(inspector, "activate_window", lambda _window_id, **_kwargs: False)

    with caplog.at_level("WARNING"):
        assert inspector.jump_to_session(session) is False

    assert "failed to activate window" in caplog.text
