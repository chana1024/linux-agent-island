from types import SimpleNamespace

from linux_agent_island.app.hotkeys import GlobalHotkeyListener


def test_hotkey_listener_handles_matching_ctrl_i(monkeypatch) -> None:
    config = SimpleNamespace()
    listener = GlobalHotkeyListener(config)
    listener._X = SimpleNamespace(KeyPress=2)
    listener._keycode = 31
    listener._grab_masks = (4, 6)

    calls: list[tuple[object, str]] = []
    monkeypatch.setattr(
        "linux_agent_island.app.hotkeys._run_application_action",
        lambda cfg, action: calls.append((cfg, action)) or 0,
    )

    listener._handle_keypress(SimpleNamespace(type=2, detail=31, state=4))

    assert calls == [(config, "toggle-island-focus")]


def test_hotkey_listener_ignores_non_matching_events(monkeypatch) -> None:
    listener = GlobalHotkeyListener(SimpleNamespace())
    listener._X = SimpleNamespace(KeyPress=2)
    listener._keycode = 31
    listener._grab_masks = (4,)

    calls: list[str] = []
    monkeypatch.setattr(
        "linux_agent_island.app.hotkeys._run_application_action",
        lambda _cfg, action: calls.append(action) or 0,
    )

    listener._handle_keypress(SimpleNamespace(type=3, detail=31, state=4))
    listener._handle_keypress(SimpleNamespace(type=2, detail=32, state=4))
    listener._handle_keypress(SimpleNamespace(type=2, detail=31, state=0))

    assert calls == []
