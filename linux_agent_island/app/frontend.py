from __future__ import annotations

import argparse
import json
import logging
import subprocess

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")

from gi.repository import Gdk, Gio, GLib, Gtk

from ..core.config import AppConfig, FrontendSettings, load_frontend_settings
from ..core.logging import configure_logging
from ..core.models import AgentSession, SessionPhase
from .frontend_client import connect_proxy, list_sessions
from .frontend_controls import (
    REVEAL_START_DELAY_MS,
    REVEAL_TRANSITION_MS,
    TRANSCRIPT_REFRESH_MS,
    key_state_has_shift,
    moved_selection_key,
    navigation_delta_for_key,
    should_activate_selected_for_key,
    should_collapse_layer_for_key,
)
from .frontend_interactions import FrontendInteractionsMixin
from .frontend_panel import FrontendPanelMixin
from .frontend_presenter import (
    APP_TITLE,
    COLLAPSED_HEIGHT,
    COLLAPSED_WIDTH,
    HIGHLIGHT_DURATION_SECONDS,
    SessionKey,
    collapsed_status_css_class,
    collapsed_status_phase,
    compute_expanded_window_height,
    compute_window_position,
    compute_window_position_for_width,
    detect_completed_sessions,
    expanded_header_title,
    format_session_minutes,
    has_done_time_label,
    panel_sessions,
    parse_workarea_top_offset,
    prune_expired_highlights,
    refresh_completion_highlights,
    session_key,
    session_metadata_tags,
    session_provider_label,
    status_dot_css_class,
    status_dot_glyph,
    summarize_visible_sessions,
    window_width_for_state,
)
from .frontend_settings import FrontendSettingsMixin


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

.tag-provider-gemini {
  background: rgba(138, 162, 255, 0.15);
  border: 1px solid rgba(138, 162, 255, 0.3);
  color: #8aa2ff;
}

.tag-provider-claude {
  background: rgba(217, 119, 87, 0.15);
  border: 1px solid rgba(217, 119, 87, 0.3);
  color: #d97757;
}

.tag-provider-codex {
  background: rgba(61, 220, 132, 0.15);
  border: 1px solid rgba(61, 220, 132, 0.3);
  color: #3ddc84;
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

.status-completed {
  color: rgba(255, 255, 255, 0.42);
}

.jump-button {
  min-width: 32px;
  min-height: 28px;
  padding: 0 6px;
}

.actionable-card {
  background: rgba(255, 176, 32, 0.08);
  border: 1px solid rgba(255, 176, 32, 0.24);
  border-radius: 12px;
  padding: 10px;
}

.actionable-option {
  background: rgba(255, 255, 255, 0.03);
  border-radius: 8px;
  padding: 6px 8px;
}

.actionable-title {
  color: #ffcf70;
  font-size: 12px;
  font-weight: 700;
}

.actionable-text {
  color: rgba(255, 255, 255, 0.86);
  font-size: 12px;
}

.actionable-meta {
  color: rgba(255, 255, 255, 0.6);
  font-size: 11px;
}

.metadata-card {
  background: rgba(127, 182, 255, 0.08);
  border: 1px solid rgba(127, 182, 255, 0.2);
  border-radius: 12px;
  padding: 10px;
}

.metadata-title {
  color: #9ec4ff;
  font-size: 12px;
  font-weight: 700;
}

.metadata-label {
  color: rgba(255, 255, 255, 0.56);
  font-size: 10px;
  font-weight: 700;
}

.metadata-value {
  color: rgba(255, 255, 255, 0.84);
  font-size: 12px;
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


logger = logging.getLogger(__name__)


class FrontendApp(FrontendInteractionsMixin, FrontendPanelMixin, FrontendSettingsMixin, Gtk.Application):
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
        self.proxy = connect_proxy(self.config)
        self.proxy.connect("g-signal", self._on_signal)
        self.sessions = list_sessions(self.proxy)
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

    def _on_key_pressed(
        self,
        _controller: Gtk.EventControllerKey,
        keyval: int,
        _keycode: int,
        state: Gdk.ModifierType,
    ) -> bool:
        delta = navigation_delta_for_key(keyval)
        if delta is not None:
            return self._move_selected_session(delta)
        if should_activate_selected_for_key(keyval):
            if key_state_has_shift(state):
                return self._jump_selected_session()
            return self._expand_one_layer()
        if should_collapse_layer_for_key(keyval):
            return self._collapse_one_layer()
        return False

    def _on_close_request(self, *_args: object) -> bool:
        if self.window is not None:
            self.window.hide()
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
