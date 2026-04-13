from __future__ import annotations

import argparse
from collections.abc import Callable
import json
import logging
import re
import subprocess
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
try:
    gi.require_version("GdkX11", "4.0")
except ValueError:
    GdkX11 = None
else:
    from gi.repository import GdkX11

from gi.repository import Gdk, Gio, GLib, Gtk

from ..core.config import LOG_LEVELS, AppConfig, FrontendSettings, load_frontend_settings, save_frontend_settings
from ..core.logging import configure_logging
from ..core.models import AgentSession, SessionPhase


CSS = """
window {
  background: transparent;
}

.island-root {
  background: rgba(10, 10, 12, 0.94);
  border-radius: 22px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  padding: 12px 14px;
}

.pill {
  min-width: 180px;
  min-height: 34px;
}

.session-card {
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid transparent;
  border-radius: 16px;
  padding: 10px 12px;
}

.session-card-highlight {
  background: rgba(127, 182, 255, 0.12);
  border: 1px solid rgba(127, 182, 255, 0.6);
}

.session-card-selected {
  border: 1px solid rgba(61, 220, 132, 0.8);
}

.session-summary {
  background: transparent;
  border: none;
  padding: 0;
}

.provider, .phase {
  color: #8aa2ff;
  font-size: 11px;
  font-weight: 700;
}

.session-tag {
  background: rgba(255, 255, 255, 0.08);
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 8px;
  color: rgba(255, 255, 255, 0.78);
  font-size: 11px;
  font-weight: 700;
  padding: 2px 7px;
}

.title {
  color: #ffffff;
  font-weight: 700;
}

.meta, .preview {
  color: rgba(255, 255, 255, 0.72);
  font-size: 12px;
}

.status-dot {
  font-size: 14px;
}

.status-running {
  color: #3ddc84;
}

.status-attention {
  color: #ffb020;
}

.status-waiting {
  color: #7fb6ff;
}

.status-completed {
  color: rgba(255, 255, 255, 0.42);
}

.status-error {
  color: #ff6b6b;
}

.jump-button {
  min-width: 32px;
  min-height: 28px;
  padding: 0 6px;
}

.transcript-title {
  color: #8aa2ff;
  font-size: 11px;
  font-weight: 700;
}

.transcript-role {
  color: rgba(255, 255, 255, 0.56);
  font-size: 10px;
  font-weight: 700;
}

.transcript-text {
  color: rgba(255, 255, 255, 0.82);
  font-size: 12px;
}

.transcript-empty {
  color: rgba(255, 255, 255, 0.56);
  font-size: 12px;
}

""".strip()


COLLAPSED_WIDTH = 220
COLLAPSED_HEIGHT = 60
EXPANDED_WIDTH = 720
DETAIL_EXPANDED_WIDTH = EXPANDED_WIDTH * 2
APP_TITLE = "Linux Agent Island"
HIGHLIGHT_DURATION_SECONDS = 5 * 60
REVEAL_TRANSITION_MS = 260
REVEAL_START_DELAY_MS = 48
TRANSCRIPT_REFRESH_MS = 1000
BASE_EXPANDED_MAX_SCROLL_HEIGHT = 352
BASE_DETAIL_MAX_SCROLL_HEIGHT = 620
EXPANDED_HEIGHT_NUMERATOR = 3
EXPANDED_HEIGHT_DENOMINATOR = 2
DETAIL_HEIGHT_MULTIPLIER = 2

SessionKey = tuple[str, str]

logger = logging.getLogger(__name__)


def session_key(session: AgentSession) -> SessionKey:
    return (session.provider, session.session_id)


def parse_workarea_top_offset(output: str) -> int:
    values = [int(match) for match in re.findall(r"-?\d+", output)]
    if len(values) < 2:
        return 0
    return max(0, values[1])


def compute_window_position_for_width(
    monitor_x: int,
    monitor_y: int,
    monitor_width: int,
    top_bar_offset: int,
    top_bar_gap: int,
    width: int,
) -> tuple[int, int, int]:
    x = monitor_x + (monitor_width - width) // 2
    y = monitor_y + max(0, top_bar_offset) + max(0, top_bar_gap)
    return width, x, y


def compute_window_position(
    monitor_x: int,
    monitor_y: int,
    monitor_width: int,
    top_bar_offset: int,
    top_bar_gap: int,
    expanded: bool,
) -> tuple[int, int, int]:
    width = EXPANDED_WIDTH if expanded else COLLAPSED_WIDTH
    return compute_window_position_for_width(
        monitor_x=monitor_x,
        monitor_y=monitor_y,
        monitor_width=monitor_width,
        top_bar_offset=top_bar_offset,
        top_bar_gap=top_bar_gap,
        width=width,
    )


def visible_sessions(sessions: list[AgentSession]) -> list[AgentSession]:
    return [session for session in sessions if session.is_visible_in_island]


def summarize_visible_sessions(sessions: list[AgentSession]) -> str:
    visible_count = len(visible_sessions(sessions))
    return f"{visible_count} session" if visible_count == 1 else f"{visible_count} sessions"


def expanded_header_title(sessions: list[AgentSession]) -> str:
    return f"{APP_TITLE} · {summarize_visible_sessions(sessions)}"


def status_dot_css_class(phase: SessionPhase) -> str:
    mapping = {
        SessionPhase.WAITING_APPROVAL: "status-dot status-attention",
        SessionPhase.RUNNING: "status-dot status-running",
        SessionPhase.WAITING: "status-dot status-waiting",
        SessionPhase.COMPLETED: "status-dot status-completed",
        SessionPhase.ERROR: "status-dot status-error",
        SessionPhase.IDLE: "status-dot status-waiting",
    }
    return mapping[phase]


def collapsed_status_phase(sessions: list[AgentSession]) -> SessionPhase:
    phases = {session.phase for session in visible_sessions(sessions)}
    if SessionPhase.RUNNING in phases:
        return SessionPhase.RUNNING
    if SessionPhase.WAITING_APPROVAL in phases:
        return SessionPhase.WAITING_APPROVAL
    if SessionPhase.ERROR in phases:
        return SessionPhase.ERROR
    if SessionPhase.WAITING in phases:
        return SessionPhase.WAITING
    if SessionPhase.IDLE in phases:
        return SessionPhase.IDLE
    return SessionPhase.COMPLETED


def collapsed_status_css_class(sessions: list[AgentSession]) -> str:
    return status_dot_css_class(collapsed_status_phase(sessions))


def has_done_time_label(session: AgentSession) -> bool:
    return session.phase is SessionPhase.COMPLETED and session.completed_at is not None


def format_session_minutes(session: AgentSession, now_ts: int | None = None) -> str:
    current_ts = now_ts if now_ts is not None else int(time.time())
    if has_done_time_label(session):
        assert session.completed_at is not None
        minutes = max(1, (current_ts - session.completed_at + 59) // 60)
        return f"done {minutes}m"
    reference = session.started_at or session.updated_at
    minutes = max(1, (current_ts - reference + 59) // 60)
    return f"{minutes}m"


def _session_sort_key(session: AgentSession) -> tuple[int, int]:
    if session.requires_attention:
        return (0, -session.updated_at)
    if session.phase is SessionPhase.RUNNING:
        return (1, -session.updated_at)
    if session.phase is SessionPhase.COMPLETED:
        return (2, -(session.completed_at or session.updated_at))
    return (3, -session.updated_at)


def panel_sessions(sessions: list[AgentSession]) -> list[AgentSession]:
    return sorted(sessions, key=_session_sort_key)


def session_provider_label(provider: str) -> str:
    mapping = {
        "claude": "Claude Code",
        "codex": "Codex",
        "gemini": "Gemini",
    }
    return mapping.get(provider.lower(), provider)


def session_metadata_tags(session: AgentSession) -> list[str]:
    return [
        session_provider_label(session.provider),
        session.model or "Unknown model",
    ]


def detect_completed_sessions(
    previous_phases: dict[SessionKey, SessionPhase],
    sessions: list[AgentSession],
) -> list[AgentSession]:
    return [
        session
        for session in sessions
        if previous_phases.get(session_key(session)) is SessionPhase.RUNNING
        and has_done_time_label(session)
    ]


def refresh_completion_highlights(
    highlighted_until: dict[SessionKey, int],
    completed_sessions: list[AgentSession],
    now_ts: int,
) -> tuple[dict[SessionKey, int], SessionKey | None]:
    updated = dict(highlighted_until)
    latest_session: AgentSession | None = None

    for session in completed_sessions:
        if not has_done_time_label(session):
            continue
        updated[session_key(session)] = now_ts + HIGHLIGHT_DURATION_SECONDS
        if latest_session is None:
            latest_session = session
            continue
        latest_completed_at = latest_session.completed_at or latest_session.updated_at
        session_completed_at = session.completed_at or session.updated_at
        if session_completed_at >= latest_completed_at:
            latest_session = session

    return updated, (session_key(latest_session) if latest_session is not None else None)


def prune_expired_highlights(
    highlighted_until: dict[SessionKey, int],
    now_ts: int,
) -> dict[SessionKey, int]:
    return {
        key: expires_at
        for key, expires_at in highlighted_until.items()
        if expires_at > now_ts
    }


def compute_expanded_window_height(session_count: int, has_expanded_session: bool = False) -> int:
    header_height = 120
    per_session_height = 88
    max_scroll_height = BASE_DETAIL_MAX_SCROLL_HEIGHT if has_expanded_session else BASE_EXPANDED_MAX_SCROLL_HEIGHT
    scroll_height = min(max_scroll_height, per_session_height * max(1, session_count))
    if has_expanded_session:
        scroll_height = max(scroll_height, 500)
        return (header_height + scroll_height) * DETAIL_HEIGHT_MULTIPLIER
    return (header_height + scroll_height) * EXPANDED_HEIGHT_NUMERATOR // EXPANDED_HEIGHT_DENOMINATOR


def window_width_for_state(expanded: bool, has_expanded_session: bool) -> int:
    if not expanded:
        return COLLAPSED_WIDTH
    return DETAIL_EXPANDED_WIDTH if has_expanded_session else EXPANDED_WIDTH


def navigation_delta_for_key(keyval: int) -> int | None:
    if keyval == Gdk.KEY_Down:
        return 1
    if keyval == Gdk.KEY_Up:
        return -1
    return None


def should_activate_selected_for_key(keyval: int) -> bool:
    return keyval in {Gdk.KEY_Return, Gdk.KEY_KP_Enter}


def should_collapse_layer_for_key(keyval: int) -> bool:
    return keyval == Gdk.KEY_Escape


def key_state_has_shift(state: Gdk.ModifierType) -> bool:
    return bool(state & Gdk.ModifierType.SHIFT_MASK)


def moved_selection_key(
    current_key: SessionKey | None,
    ordered_keys: list[SessionKey],
    delta: int,
) -> SessionKey | None:
    if not ordered_keys:
        return None
    if current_key not in ordered_keys:
        return ordered_keys[0 if delta >= 0 else -1]
    index = ordered_keys.index(current_key)
    return ordered_keys[max(0, min(len(ordered_keys) - 1, index + delta))]


class FrontendApp(Gtk.Application):
    def __init__(self) -> None:
        self.config = AppConfig.default()
        super().__init__(application_id=self.config.frontend_application_id)
        self.window: Gtk.ApplicationWindow | None = None
        self.settings_window: Gtk.ApplicationWindow | None = None
        self.box: Gtk.Box | None = None
        self.scroller: Gtk.ScrolledWindow | None = None
        self.viewport: Gtk.Box | None = None
        self.expanded = False
        self.expanded_session_ids: set[SessionKey] = set()
        self.session_transcripts: dict[SessionKey, list[dict[str, str]]] = {}
        self.transcript_refresh_id: int | None = None
        self.pending_panel_reveal = False
        self.pending_detail_reveal_keys: set[SessionKey] = set()
        self.collapsing_detail_keys: set[SessionKey] = set()
        self.panel_revealer: Gtk.Revealer | None = None
        self.detail_revealers: dict[SessionKey, Gtk.Revealer] = {}
        self.panel_collapse_timeout_id: int | None = None
        self.current_window_size: tuple[int, int] = (COLLAPSED_WIDTH, COLLAPSED_HEIGHT)
        self.sessions: list[AgentSession] = []
        self.panel_session_keys: list[SessionKey] = []
        self.selected_session_key: SessionKey | None = None
        self.session_row_widgets: dict[SessionKey, Gtk.Widget] = {}
        self.previous_session_phases: dict[SessionKey, SessionPhase] = {}
        self.highlighted_until: dict[SessionKey, int] = {}
        self.pending_scroll_session: SessionKey | None = None
        self.highlight_timeout_id: int | None = None
        self.proxy: Gio.DBusProxy | None = None
        self.settings = FrontendSettings()

    def do_startup(self) -> None:
        Gtk.Application.do_startup(self)
        for name, callback in [
            ("show-island", self._action_show_island),
            ("hide-island", self._action_hide_island),
            ("open-settings", self._action_open_settings),
            ("quit-service", self._action_quit_service),
        ]:
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)

    def do_activate(self) -> None:
        self._install_css()
        self.settings = load_frontend_settings(self.config.frontend_settings_path)
        if self.window is None:
            self.window = Gtk.ApplicationWindow(application=self)
            self.window.set_decorated(False)
            self.window.set_resizable(False)
            self.window.set_default_size(COLLAPSED_WIDTH, COLLAPSED_HEIGHT)
            self.window.set_title(APP_TITLE)
            self.window.connect("close-request", self._on_close_request)
            key_controller = Gtk.EventControllerKey()
            key_controller.connect("key-pressed", self._on_key_pressed)
            self.window.add_controller(key_controller)

            self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            self.box.set_halign(Gtk.Align.FILL)
            self.box.set_valign(Gtk.Align.START)
            self.box.set_hexpand(True)
            self.box.set_vexpand(False)
            self.box.add_css_class("island-root")
            self.window.set_child(self.box)

            self._connect_dbus()
            self._render()
        self.window.present()
        self._schedule_position_window()
        self._schedule_apply_window_state()

    def _action_show_island(self, *_args: object) -> None:
        self.activate()
        if self.window is not None:
            self.window.present()

    def _action_hide_island(self, *_args: object) -> None:
        if self.window is not None:
            self.window.hide()

    def _action_open_settings(self, *_args: object) -> None:
        self._open_settings_window()

    def _action_quit_service(self, *_args: object) -> None:
        subprocess.run(
            ["systemctl", "--user", "stop", self.config.service_name],
            capture_output=True,
            text=True,
            check=False,
        )

    def _install_css(self) -> None:
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS.encode("utf-8"))
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def _connect_dbus(self) -> None:
        self.proxy = Gio.DBusProxy.new_for_bus_sync(
            Gio.BusType.SESSION,
            Gio.DBusProxyFlags.NONE,
            None,
            self.config.dbus_name,
            self.config.dbus_path,
            self.config.dbus_name,
            None,
        )
        self.proxy.connect("g-signal", self._on_signal)
        result = self.proxy.call_sync("ListSessions", None, Gio.DBusCallFlags.NONE, -1, None)
        sessions_json = result.unpack()[0]
        self.sessions = [AgentSession.from_dict(item) for item in json.loads(sessions_json)]
        self.previous_session_phases = {
            session_key(session): session.phase
            for session in self.sessions
        }

    def _on_signal(self, _proxy: Gio.DBusProxy, _sender: str, signal_name: str, parameters: GLib.Variant) -> None:
        if signal_name != "SessionsChanged":
            return
        sessions_json = parameters.unpack()[0]
        self._apply_session_update([AgentSession.from_dict(item) for item in json.loads(sessions_json)])
        self._render()
        self._schedule_position_window()
        self._schedule_scroll_to_pending_session()

    def _toggle_expand(self, *_args: object) -> None:
        if self.expanded:
            self._collapse_panel_with_animation()
            return
        self.expanded = True
        self.pending_panel_reveal = True
        self._render()

    def _on_key_pressed(
        self,
        _controller: Gtk.EventControllerKey,
        keyval: int,
        _keycode: int,
        _state: Gdk.ModifierType,
    ) -> bool:
        delta = navigation_delta_for_key(keyval)
        if delta is not None:
            return self._move_selected_session(delta)

        if should_activate_selected_for_key(keyval):
            if key_state_has_shift(_state):
                return self._jump_selected_session()
            return self._expand_one_layer()

        if should_collapse_layer_for_key(keyval):
            return self._collapse_one_layer()

        return False

    def _move_selected_session(self, delta: int) -> bool:
        if not self.expanded:
            self.expanded = True
            self.pending_panel_reveal = True
            self._render()
            self._schedule_scroll_to_selected_session()
            return self.selected_session_key is not None

        self.selected_session_key = moved_selection_key(
            self.selected_session_key,
            self.panel_session_keys,
            delta,
        )
        self._render()
        self._schedule_scroll_to_selected_session()
        return self.selected_session_key is not None

    def _expand_one_layer(self) -> bool:
        if not self.expanded:
            self.expanded = True
            self.pending_panel_reveal = True
            self._render()
            self._schedule_scroll_to_selected_session()
            return self.selected_session_key is not None

        if self.selected_session_key is None:
            return False
        if self.selected_session_key in self.expanded_session_ids:
            return True

        self.expanded_session_ids.add(self.selected_session_key)
        self.pending_detail_reveal_keys.add(self.selected_session_key)
        self._refresh_session_transcript(self.selected_session_key)
        self._schedule_transcript_refresh()
        self._render()
        self._schedule_scroll_to_selected_session()
        return True

    def _collapse_one_layer(self) -> bool:
        if not self.expanded:
            self._action_hide_island()
            return True

        if self.selected_session_key is None and self.expanded_session_ids:
            self._collapse_all_session_details_with_animation()
            return True

        if self.selected_session_key is None:
            self._collapse_panel_with_animation()
            return True

        if self.selected_session_key in self.expanded_session_ids:
            self._collapse_session_details_with_animation(self.selected_session_key)
            return True

        if self.expanded_session_ids:
            self._collapse_all_session_details_with_animation()
            return True

        self._collapse_panel_with_animation()
        return True

    def _jump_selected_session(self) -> bool:
        if self.selected_session_key is None:
            if not self.expanded:
                self.expanded = True
                self.pending_panel_reveal = True
                self._render()
                self._schedule_scroll_to_selected_session()
            return False

        session = self._session_for_key(self.selected_session_key)
        if session is None:
            return False
        self._on_session_jump_clicked(session)
        return True

    def _session_for_key(self, key: SessionKey) -> AgentSession | None:
        for session in self.sessions:
            if session_key(session) == key:
                return session
        return None

    def _toggle_session_details(self, key: SessionKey) -> None:
        if key in self.expanded_session_ids:
            self._collapse_session_details_with_animation(key)
            return
        self.expanded_session_ids.add(key)
        self.pending_detail_reveal_keys.add(key)
        self._refresh_session_transcript(key)
        self._schedule_transcript_refresh()
        self._render()

    def _collapse_session_details_with_animation(self, key: SessionKey) -> None:
        if key in self.collapsing_detail_keys:
            return
        revealer = self.detail_revealers.get(key)
        if revealer is None:
            self._finish_session_details_collapse(key)
            return
        self.collapsing_detail_keys.add(key)
        revealer.set_reveal_child(False)
        GLib.timeout_add(REVEAL_TRANSITION_MS, self._finish_session_details_collapse, key)

    def _collapse_all_session_details_with_animation(self) -> None:
        keys = set(self.expanded_session_ids)
        animated = False
        for key in keys:
            revealer = self.detail_revealers.get(key)
            if revealer is None:
                continue
            self.collapsing_detail_keys.add(key)
            revealer.set_reveal_child(False)
            animated = True
        if animated:
            GLib.timeout_add(REVEAL_TRANSITION_MS, self._finish_all_session_details_collapse, keys)
            return
        self._finish_all_session_details_collapse(keys)

    def _finish_session_details_collapse(self, key: SessionKey) -> bool:
        self.expanded_session_ids.discard(key)
        self.pending_detail_reveal_keys.discard(key)
        self.collapsing_detail_keys.discard(key)
        self._schedule_transcript_refresh()
        self._render()
        self._schedule_scroll_to_selected_session()
        return False

    def _finish_all_session_details_collapse(self, keys: set[SessionKey]) -> bool:
        self.expanded_session_ids.difference_update(keys)
        self.pending_detail_reveal_keys.difference_update(keys)
        self.collapsing_detail_keys.difference_update(keys)
        self._schedule_transcript_refresh()
        self._render()
        self._schedule_scroll_to_selected_session()
        return False

    def _collapse_panel_with_animation(self) -> None:
        if self.panel_collapse_timeout_id is not None:
            return
        self.pending_panel_reveal = False
        self._set_panel_fills_window(False)
        if self.panel_revealer is None:
            self._finish_panel_collapse()
            return
        self.panel_revealer.set_reveal_child(False)
        self.panel_collapse_timeout_id = GLib.timeout_add(REVEAL_TRANSITION_MS, self._finish_panel_collapse)

    def _finish_panel_collapse(self) -> bool:
        self.panel_collapse_timeout_id = None
        self.expanded = False
        self._render()
        return False

    def _clear_session_highlight(self, key: SessionKey) -> None:
        if key not in self.highlighted_until:
            return
        self.highlighted_until.pop(key, None)
        self._schedule_highlight_cleanup()
        self._render()

    def _on_session_summary_clicked(self, session: AgentSession) -> None:
        key = session_key(session)
        self.selected_session_key = key
        self._clear_session_highlight(key)
        self._toggle_session_details(key)

    def _on_session_jump_clicked(self, session: AgentSession) -> None:
        key = session_key(session)
        self.selected_session_key = key
        self._clear_session_highlight(key)
        logger.info(
            "jump button clicked provider=%s session_id=%s pid=%s tty=%s cwd=%s has_window=%s focused=%s",
            session.provider,
            session.session_id,
            session.pid,
            session.tty,
            session.cwd,
            session.has_interactive_window,
            session.is_focused,
        )
        self._jump_to_session(session.provider, session.session_id)

    def _schedule_transcript_refresh(self) -> None:
        if not self.expanded_session_ids:
            if self.transcript_refresh_id is not None:
                GLib.source_remove(self.transcript_refresh_id)
                self.transcript_refresh_id = None
            return
        if self.transcript_refresh_id is None:
            self.transcript_refresh_id = GLib.timeout_add(TRANSCRIPT_REFRESH_MS, self._refresh_expanded_transcripts)

    def _refresh_expanded_transcripts(self) -> bool:
        self.transcript_refresh_id = None
        if not self.expanded_session_ids:
            return False

        changed = False
        for key in list(self.expanded_session_ids):
            changed = self._refresh_session_transcript(key) or changed
        if changed:
            self._render()
        self._schedule_transcript_refresh()
        return False

    def _refresh_session_transcript(self, key: SessionKey) -> bool:
        transcript = self._fetch_session_transcript(*key)
        if transcript == self.session_transcripts.get(key):
            return False
        self.session_transcripts[key] = transcript
        return True

    def _fetch_session_transcript(self, provider: str, session_id: str) -> list[dict[str, str]]:
        if self.proxy is None:
            return []
        try:
            result = self.proxy.call_sync(
                "GetSessionTranscript",
                GLib.Variant("(ss)", (provider, session_id)),
                Gio.DBusCallFlags.NONE,
                -1,
                None,
            )
        except GLib.Error as exc:
            logger.warning(
                "GetSessionTranscript D-Bus call failed provider=%s session_id=%s error=%s",
                provider,
                session_id,
                exc,
            )
            return []
        try:
            payload = json.loads(result.unpack()[0])
        except (json.JSONDecodeError, TypeError):
            return []
        if not isinstance(payload, list):
            return []
        turns: list[dict[str, str]] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role", ""))
            text = str(item.get("text", ""))
            timestamp = str(item.get("timestamp", ""))
            if role and text:
                turns.append({"role": role, "text": text, "timestamp": timestamp})
        return turns

    def _jump_to_session(self, provider: str, session_id: str) -> bool:
        if self.proxy is None:
            logger.warning("JumpToSession skipped because D-Bus proxy is unavailable")
            return False
        logger.info("calling JumpToSession provider=%s session_id=%s", provider, session_id)
        try:
            result = self.proxy.call_sync(
                "JumpToSession",
                GLib.Variant("(ss)", (provider, session_id)),
                Gio.DBusCallFlags.NONE,
                -1,
                None,
            )
        except GLib.Error as exc:
            logger.warning(
                "JumpToSession D-Bus call failed provider=%s session_id=%s error=%s",
                provider,
                session_id,
                exc,
            )
            return False
        jumped = bool(result.unpack()[0])
        if jumped:
            logger.info(
                "JumpToSession returned provider=%s session_id=%s jumped=%s",
                provider,
                session_id,
                jumped,
            )
        else:
            logger.warning(
                "JumpToSession returned provider=%s session_id=%s jumped=%s",
                provider,
                session_id,
                jumped,
            )
        return jumped

    def _apply_session_update(self, sessions: list[AgentSession]) -> None:
        now_ts = int(time.time())
        self.highlighted_until = prune_expired_highlights(self.highlighted_until, now_ts)

        completed_sessions = detect_completed_sessions(self.previous_session_phases, sessions)
        if completed_sessions:
            self.highlighted_until, self.pending_scroll_session = refresh_completion_highlights(
                highlighted_until=self.highlighted_until,
                completed_sessions=completed_sessions,
                now_ts=now_ts,
            )
            if not self.expanded:
                self.expanded = True
                self.pending_panel_reveal = True

        active_keys = {session_key(session) for session in sessions}
        self.expanded_session_ids.intersection_update(active_keys)
        if self.selected_session_key not in active_keys:
            self.selected_session_key = None
        self.session_transcripts = {
            key: transcript
            for key, transcript in self.session_transcripts.items()
            if key in active_keys
        }
        self.sessions = sessions
        self.previous_session_phases = {
            session_key(session): session.phase
            for session in sessions
        }
        self._schedule_highlight_cleanup()
        self._schedule_transcript_refresh()

    def _schedule_highlight_cleanup(self) -> None:
        if self.highlight_timeout_id is not None:
            GLib.source_remove(self.highlight_timeout_id)
            self.highlight_timeout_id = None

        if not self.highlighted_until:
            return

        now_ts = int(time.time())
        next_expiry = min(self.highlighted_until.values())
        delay_ms = max(1000, (next_expiry - now_ts) * 1000)
        self.highlight_timeout_id = GLib.timeout_add(delay_ms, self._expire_highlights)

    def _expire_highlights(self) -> bool:
        self.highlight_timeout_id = None
        now_ts = int(time.time())
        updated = prune_expired_highlights(self.highlighted_until, now_ts)
        if updated != self.highlighted_until:
            self.highlighted_until = updated
            self._render()
        self._schedule_highlight_cleanup()
        return False

    def _schedule_scroll_to_pending_session(self) -> None:
        if self.pending_scroll_session is None or not self.expanded:
            return
        GLib.idle_add(self._scroll_to_pending_session)

    def _schedule_scroll_to_selected_session(self) -> None:
        if self.selected_session_key is None or not self.expanded:
            return
        GLib.idle_add(self._scroll_to_selected_session)

    def _scroll_to_pending_session(self) -> bool:
        if self.pending_scroll_session is None:
            return False
        scrolled = self._scroll_to_session(self.pending_scroll_session)
        self.pending_scroll_session = None
        return scrolled

    def _scroll_to_selected_session(self) -> bool:
        if self.selected_session_key is None:
            return False
        return self._scroll_to_session(self.selected_session_key)

    def _scroll_to_session(self, key: SessionKey) -> bool:
        if self.scroller is None or self.viewport is None:
            return False

        row = self.session_row_widgets.get(key)
        if row is None:
            return False

        adjustment = self.scroller.get_vadjustment()
        if adjustment is None:
            return False

        target = 0.0
        spacing = float(self.viewport.get_spacing())
        child = self.viewport.get_first_child()
        while child is not None and child is not row:
            target += float(child.get_allocated_height()) + spacing
            child = child.get_next_sibling()

        target = max(0.0, target - 12.0)
        max_value = max(0.0, adjustment.get_upper() - adjustment.get_page_size())
        adjustment.set_value(min(target, max_value))
        return False

    def _render(self) -> None:
        assert self.box is not None
        self.scroller = None
        self.viewport = None
        self.panel_revealer = None
        self.detail_revealers = {}
        self.panel_session_keys = []
        self.session_row_widgets = {}
        self._apply_target_window_size()
        while child := self.box.get_first_child():
            self.box.remove(child)

        if not self.expanded:
            self._set_panel_fills_window(True)
            pill = Gtk.Button()
            pill.set_can_focus(False)
            pill.add_css_class("session-summary")
            pill.connect("clicked", self._toggle_expand)
            pill_content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            pill_content.add_css_class("pill")
            pill.add_css_class("pill")
            icon = Gtk.Label(label="●")
            for css_class in collapsed_status_css_class(self.sessions).split():
                icon.add_css_class(css_class)
            title = Gtk.Label(label=summarize_visible_sessions(self.sessions))
            title.add_css_class("title")
            pill_content.append(icon)
            pill_content.append(title)
            pill.set_child(pill_content)
            self.box.append(pill)
            return

        animate_panel = self.pending_panel_reveal
        self._set_panel_fills_window(not animate_panel)

        header = Gtk.Button(label=expanded_header_title(self.sessions))
        header.set_can_focus(False)
        header.add_css_class("session-summary")
        header.add_css_class("title")
        header.connect("clicked", self._toggle_expand)
        self.box.append(header)

        if not self.sessions:
            empty = Gtk.Label(label="No visible sessions")
            empty.add_css_class("meta")
            content_area: Gtk.Widget = empty
        else:
            viewport = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            self.viewport = viewport
            ordered_sessions = panel_sessions(self.sessions)
            self.panel_session_keys = [session_key(session) for session in ordered_sessions]
            if self.selected_session_key not in self.panel_session_keys:
                self.selected_session_key = self.panel_session_keys[0] if self.panel_session_keys else None
            for session in ordered_sessions:
                card = self._session_card(session)
                self.session_row_widgets[session_key(session)] = card
                viewport.append(card)
            scroller = Gtk.ScrolledWindow()
            scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scroller.set_min_content_height(120)
            scroller.set_max_content_height(self._max_content_height())
            scroller.set_vexpand(True)
            scroller.set_child(viewport)
            self.scroller = scroller
            content_area = scroller

        self.panel_revealer = self._content_revealer(
            content_area,
            animate=animate_panel,
            vexpand=True,
            on_revealed=self._finish_panel_reveal,
        )
        self.box.append(self.panel_revealer)
        self.pending_panel_reveal = False

    def _session_card(self, session: AgentSession) -> Gtk.Widget:
        key = session_key(session)
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        outer.add_css_class("session-card")
        if key in self.highlighted_until and has_done_time_label(session):
            outer.add_css_class("session-card-highlight")
        if key == self.selected_session_key:
            outer.add_css_class("session-card-selected")

        summary_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        toggle = Gtk.Button()
        toggle.set_can_focus(False)
        toggle.add_css_class("session-summary")
        toggle.set_hexpand(True)
        toggle.set_halign(Gtk.Align.FILL)
        toggle.connect("clicked", lambda *_args: self._on_session_summary_clicked(session))

        toggle_content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        dot = Gtk.Label(label="●")
        for css_class in status_dot_css_class(session.phase).split():
            dot.add_css_class(css_class)
        toggle_content.append(dot)

        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        title = Gtk.Label(label=session.title)
        title.set_xalign(0)
        title.set_ellipsize(3)
        title.add_css_class("title")
        text_box.append(title)

        cwd = Gtk.Label(label=session.cwd)
        cwd.set_xalign(0)
        cwd.set_ellipsize(3)
        cwd.add_css_class("meta")
        text_box.append(cwd)
        toggle_content.append(text_box)
        toggle.set_child(toggle_content)
        summary_row.append(toggle)

        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        side.set_halign(Gtk.Align.END)
        time_label = Gtk.Label(label=format_session_minutes(session))
        time_label.set_halign(Gtk.Align.END)
        time_label.add_css_class("meta")
        side.append(time_label)

        action_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        action_row.set_halign(Gtk.Align.END)
        for tag_text in session_metadata_tags(session):
            tag = Gtk.Label(label=tag_text)
            tag.set_ellipsize(3)
            tag.set_max_width_chars(18)
            tag.add_css_class("session-tag")
            action_row.append(tag)

        jump_button = Gtk.Button()
        jump_button.set_can_focus(False)
        jump_button.add_css_class("jump-button")
        jump_button.set_tooltip_text("Open terminal")
        jump_button.set_child(Gtk.Image.new_from_icon_name("go-jump-symbolic"))
        jump_button.set_sensitive(session.pid is not None)
        jump_button.connect("clicked", lambda *_args: self._on_session_jump_clicked(session))
        action_row.append(jump_button)
        side.append(action_row)
        summary_row.append(side)
        outer.append(summary_row)

        if key in self.expanded_session_ids:
            details = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            details.append(self._transcript_view(key))

            detail_click = Gtk.GestureClick()
            detail_click.connect("released", lambda *_args: self._clear_session_highlight(key))
            details.add_controller(detail_click)
            animate_details = key in self.pending_detail_reveal_keys
            detail_revealer = self._content_revealer(details, animate=animate_details)
            self.detail_revealers[key] = detail_revealer
            outer.append(detail_revealer)
            self.pending_detail_reveal_keys.discard(key)

        return outer

    def _content_revealer(
        self,
        child: Gtk.Widget,
        *,
        animate: bool,
        vexpand: bool = False,
        on_revealed: Callable[[], None] | None = None,
    ) -> Gtk.Revealer:
        revealer = Gtk.Revealer()
        revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        revealer.set_transition_duration(REVEAL_TRANSITION_MS)
        revealer.set_hexpand(True)
        revealer.set_vexpand(vexpand)
        revealer.set_child(child)
        if animate:
            revealer.set_reveal_child(False)
            revealer.connect("map", self._queue_content_reveal)
            if on_revealed is not None:
                revealer.connect("notify::child-revealed", self._on_revealer_child_revealed, on_revealed)
        else:
            revealer.set_reveal_child(True)
        return revealer

    def _queue_content_reveal(self, revealer: Gtk.Revealer) -> None:
        GLib.timeout_add(REVEAL_START_DELAY_MS, self._start_content_reveal, revealer)

    def _start_content_reveal(self, revealer: Gtk.Revealer) -> bool:
        revealer.set_reveal_child(True)
        return False

    def _on_revealer_child_revealed(
        self,
        revealer: Gtk.Revealer,
        _pspec: object,
        callback: Callable[[], None],
    ) -> None:
        if revealer.get_child_revealed():
            callback()

    def _finish_panel_reveal(self) -> None:
        if self.expanded:
            self._set_panel_fills_window(True)

    def _set_panel_fills_window(self, fill: bool) -> None:
        if self.box is None:
            return
        width, height = self.current_window_size
        self.box.set_halign(Gtk.Align.FILL)
        self.box.set_hexpand(True)
        if fill:
            self.box.set_valign(Gtk.Align.FILL)
            self.box.set_vexpand(True)
            self.box.set_size_request(width, height)
            return
        self.box.set_valign(Gtk.Align.START)
        self.box.set_vexpand(False)
        self.box.set_size_request(width, -1)

    def _transcript_view(self, key: SessionKey) -> Gtk.Widget:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        title = Gtk.Label(label="Conversation")
        title.set_xalign(0)
        title.add_css_class("transcript-title")
        root.append(title)

        turns_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        turns = self.session_transcripts.get(key, [])
        if not turns:
            empty = Gtk.Label(label="No conversation records yet")
            empty.set_xalign(0)
            empty.add_css_class("transcript-empty")
            turns_box.append(empty)
        else:
            for turn in turns:
                turn_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                role = Gtk.Label(label=turn["role"].upper())
                role.set_xalign(0)
                role.add_css_class("transcript-role")
                text = Gtk.Label(label=turn["text"])
                text.set_xalign(0)
                text.set_wrap(True)
                text.set_selectable(True)
                text.add_css_class("transcript-text")
                turn_box.append(role)
                turn_box.append(text)
                turns_box.append(turn_box)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_min_content_height(220)
        scroller.set_max_content_height(420)
        scroller.set_child(turns_box)
        root.append(scroller)
        return root

    def _max_content_height(self) -> int:
        header_height = 120
        return max(
            120,
            compute_expanded_window_height(len(self.sessions), bool(self.expanded_session_ids)) - header_height,
        )

    def _target_window_size(self) -> tuple[int, int]:
        if not self.expanded:
            return COLLAPSED_WIDTH, COLLAPSED_HEIGHT
        has_expanded_session = bool(self.expanded_session_ids)
        return (
            window_width_for_state(self.expanded, has_expanded_session),
            compute_expanded_window_height(len(self.sessions), has_expanded_session),
        )

    def _apply_target_window_size(self) -> None:
        target = self._target_window_size()
        if target == self.current_window_size:
            return
        self.current_window_size = target
        width, height = target
        if self.window is not None:
            self.window.set_default_size(width, height)
            self.window.set_size_request(width, height)
        self._schedule_position_window()

    def _schedule_position_window(self) -> None:
        GLib.idle_add(self._position_window)

    def _position_window(self) -> bool:
        width, _height = self.current_window_size
        position = self._window_position_for_width(width)
        if position is None:
            return False
        _, x, y = position
        if self._move_surface_x11(x, y):
            return False
        self._move_surface_fallback(width, x, y)
        return False

    def _window_position_for_width(self, width: int) -> tuple[int, int, int] | None:
        display = Gdk.Display.get_default()
        if display is None:
            return None
        monitor = display.get_monitors().get_item(0)
        if monitor is None:
            return None
        geometry = monitor.get_geometry()
        return compute_window_position_for_width(
            monitor_x=geometry.x,
            monitor_y=geometry.y,
            monitor_width=geometry.width,
            top_bar_offset=self._top_bar_offset(),
            top_bar_gap=self.settings.top_bar_gap,
            width=width,
        )

    def _schedule_apply_window_state(self) -> None:
        GLib.idle_add(self._apply_window_state)

    def _apply_window_state(self) -> bool:
        if self.window is None:
            return False

        surface = self.window.get_surface()
        if surface is None:
            return False

        if GdkX11 is not None and isinstance(surface, GdkX11.X11Surface):
            surface.set_skip_taskbar_hint(True)
            surface.set_skip_pager_hint(True)
            self._set_x11_above_state(surface.get_xid())
        return False

    def _set_x11_above_state(self, xid: int) -> None:
        try:
            subprocess.run(
                ["wmctrl", "-i", "-r", hex(xid), "-b", "add,above,skip_taskbar,skip_pager"],
                capture_output=True,
                text=True,
                timeout=1,
                check=False,
            )
        except OSError:
            pass

    def _move_surface_x11(self, x: int, y: int) -> bool:
        if self.window is None or GdkX11 is None:
            return False

        surface = self.window.get_surface()
        if surface is None or not isinstance(surface, GdkX11.X11Surface):
            return False

        try:
            result = subprocess.run(
                ["wmctrl", "-i", "-r", hex(surface.get_xid()), "-e", f"0,{x},{y},-1,-1"],
                capture_output=True,
                text=True,
                timeout=1,
                check=False,
            )
        except OSError:
            return False
        return result.returncode == 0

    def _move_surface_fallback(self, width: int, x: int, y: int) -> None:
        if self.window is None:
            return

        surface = self.window.get_surface()
        if surface is None or not hasattr(surface, "move_to_rect"):
            return

        try:
            surface.move_to_rect(
                Gdk.Rectangle(x=x, y=y, width=width, height=1),
                Gdk.Gravity.NORTH,
                Gdk.Gravity.NORTH,
                Gdk.AnchorHints.SLIDE_X,
                0,
                0,
            )
        except Exception:
            pass

    def _top_bar_offset(self) -> int:
        try:
            result = subprocess.run(
                ["xprop", "-root", "_NET_WORKAREA"],
                capture_output=True,
                text=True,
                timeout=1,
                check=False,
            )
        except OSError:
            return 0
        output = result.stdout.strip() or result.stderr.strip()
        return parse_workarea_top_offset(output)

    def _on_close_request(self, *_args: object) -> bool:
        if self.window is not None:
            self.window.hide()
        return True

    def _open_settings_window(self) -> None:
        self._install_css()
        self.settings = load_frontend_settings(self.config.frontend_settings_path)
        if self.settings_window is None:
            self.settings_window = Gtk.ApplicationWindow(application=self)
            self.settings_window.set_title("Linux Agent Island Settings")
            self.settings_window.set_default_size(360, 220)
            self.settings_window.connect("close-request", self._on_settings_close_request)

            root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
            root.set_margin_top(18)
            root.set_margin_bottom(18)
            root.set_margin_start(18)
            root.set_margin_end(18)
            self.settings_window.set_child(root)

            title = Gtk.Label(label="Settings")
            title.set_xalign(0)
            title.add_css_class("title")
            root.append(title)

            gap_adjustment = Gtk.Adjustment(
                value=float(self.settings.top_bar_gap),
                lower=0,
                upper=128,
                step_increment=1,
                page_increment=8,
                page_size=0,
            )
            gap = Gtk.SpinButton(adjustment=gap_adjustment, climb_rate=1, digits=0)
            gap.set_value(float(self.settings.top_bar_gap))
            gap.set_hexpand(True)

            log_level = Gtk.ComboBoxText()
            for level in LOG_LEVELS:
                log_level.append_text(level)
            log_level.set_active(LOG_LEVELS.index(self.settings.log_level))
            log_level.set_hexpand(True)

            autostart = Gtk.Switch()
            autostart.set_active(self.settings.start_on_login)

            root.append(self._settings_row("Top bar gap", gap))
            root.append(self._settings_row("Log level", log_level))
            root.append(self._settings_row("Start on login", autostart))

            note = Gtk.Label(label="Log level changes apply after restart.")
            note.set_xalign(0)
            note.add_css_class("meta")
            root.append(note)

            save = Gtk.Button(label="Save")
            save.connect("clicked", lambda *_args: self._save_settings(gap, log_level, autostart))
            root.append(save)
        else:
            self._refresh_settings_window()
        self.settings_window.present()

    def _settings_row(self, label_text: str, control: Gtk.Widget) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        label = Gtk.Label(label=label_text)
        label.set_xalign(0)
        label.set_hexpand(True)
        row.append(label)
        row.append(control)
        return row

    def _refresh_settings_window(self) -> None:
        self.settings = load_frontend_settings(self.config.frontend_settings_path)

    def _save_settings(self, gap: Gtk.SpinButton, log_level: Gtk.ComboBoxText, autostart: Gtk.Switch) -> None:
        active_text = log_level.get_active_text()
        updated = FrontendSettings(
            top_bar_gap=int(gap.get_value()),
            log_level=str(active_text) if active_text is not None else self.settings.log_level,
            start_on_login=autostart.get_active(),
        )
        save_frontend_settings(self.config.frontend_settings_path, updated)
        self.settings = updated
        self._apply_start_on_login(updated.start_on_login)
        self._schedule_position_window()

    def _apply_start_on_login(self, enabled: bool) -> None:
        action = "enable" if enabled else "disable"
        subprocess.run(
            ["systemctl", "--user", action, self.config.service_name],
            capture_output=True,
            text=True,
            check=False,
        )

    def _on_settings_close_request(self, *_args: object) -> bool:
        if self.settings_window is not None:
            self.settings_window.hide()
        return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log-level", default="INFO")
    args, remaining = parser.parse_known_args(argv)
    config = AppConfig.default()
    log_file_path = config.runtime_dir / "logs" / "frontend.log"
    level_name = configure_logging(args.log_level, log_file_path=log_file_path)
    logger.info("frontend logging initialized level=%s", level_name)
    logger.info("frontend log file=%s", log_file_path)
    app = FrontendApp()
    return app.run(remaining)


if __name__ == "__main__":
    raise SystemExit(main())
