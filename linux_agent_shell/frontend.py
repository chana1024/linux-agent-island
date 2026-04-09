from __future__ import annotations

import argparse
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

from .config import AppConfig, FrontendSettings, load_frontend_settings
from .logging_utils import configure_logging
from .models import AgentSession, SessionPhase


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

""".strip()


COLLAPSED_WIDTH = 220
EXPANDED_WIDTH = 720
APP_TITLE = "Linux Agent Island"
HIGHLIGHT_DURATION_SECONDS = 5 * 60

SessionKey = tuple[str, str]

logger = logging.getLogger(__name__)


def session_key(session: AgentSession) -> SessionKey:
    return (session.provider, session.session_id)


def parse_workarea_top_offset(output: str) -> int:
    values = [int(match) for match in re.findall(r"-?\d+", output)]
    if len(values) < 2:
        return 0
    return max(0, values[1])


def compute_window_position(
    monitor_x: int,
    monitor_y: int,
    monitor_width: int,
    top_bar_offset: int,
    top_bar_gap: int,
    expanded: bool,
) -> tuple[int, int, int]:
    width = EXPANDED_WIDTH if expanded else COLLAPSED_WIDTH
    x = monitor_x + (monitor_width - width) // 2
    y = monitor_y + max(0, top_bar_offset) + max(0, top_bar_gap)
    return width, x, y


def summarize_visible_sessions(sessions: list[AgentSession]) -> str:
    visible_count = sum(1 for session in sessions if session.is_visible_in_island)
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


def format_session_minutes(session: AgentSession, now_ts: int | None = None) -> str:
    current_ts = now_ts if now_ts is not None else int(time.time())
    if session.phase is SessionPhase.COMPLETED and session.completed_at is not None:
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


def detect_completed_sessions(
    previous_phases: dict[SessionKey, SessionPhase],
    sessions: list[AgentSession],
) -> list[AgentSession]:
    return [
        session
        for session in sessions
        if previous_phases.get(session_key(session)) is SessionPhase.RUNNING
        and session.phase is SessionPhase.COMPLETED
    ]


def refresh_completion_highlights(
    highlighted_until: dict[SessionKey, int],
    completed_sessions: list[AgentSession],
    now_ts: int,
) -> tuple[dict[SessionKey, int], SessionKey | None]:
    updated = dict(highlighted_until)
    latest_session: AgentSession | None = None

    for session in completed_sessions:
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


def compute_expanded_window_height(session_count: int) -> int:
    header_height = 120
    per_session_height = 88
    max_scroll_height = 352
    scroll_height = min(max_scroll_height, per_session_height * max(1, session_count))
    return header_height + scroll_height


class FrontendApp(Gtk.Application):
    def __init__(self) -> None:
        super().__init__(application_id="com.openclaw.LinuxAgentShell.Frontend")
        self.config = AppConfig.default()
        self.window: Gtk.ApplicationWindow | None = None
        self.box: Gtk.Box | None = None
        self.scroller: Gtk.ScrolledWindow | None = None
        self.viewport: Gtk.Box | None = None
        self.expanded = False
        self.expanded_session_ids: set[SessionKey] = set()
        self.sessions: list[AgentSession] = []
        self.panel_session_keys: list[SessionKey] = []
        self.session_row_widgets: dict[SessionKey, Gtk.Widget] = {}
        self.previous_session_phases: dict[SessionKey, SessionPhase] = {}
        self.highlighted_until: dict[SessionKey, int] = {}
        self.pending_scroll_session: SessionKey | None = None
        self.highlight_timeout_id: int | None = None
        self.proxy: Gio.DBusProxy | None = None
        self.settings = FrontendSettings()

    def do_activate(self) -> None:
        self._install_css()
        self.settings = load_frontend_settings(self.config.frontend_settings_path)
        if self.window is None:
            self.window = Gtk.ApplicationWindow(application=self)
            self.window.set_decorated(False)
            self.window.set_resizable(False)
            self.window.set_default_size(COLLAPSED_WIDTH, 60)
            self.window.set_title(APP_TITLE)
            self.window.connect("close-request", self._on_close_request)

            self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            self.box.add_css_class("island-root")
            self.window.set_child(self.box)

            self._connect_dbus()
            self._render()
        self.window.present()
        self._schedule_position_window()
        self._schedule_apply_window_state()

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
        self.expanded = not self.expanded
        self._render()
        self._schedule_position_window()

    def _toggle_session_details(self, key: SessionKey) -> None:
        if key in self.expanded_session_ids:
            self.expanded_session_ids.remove(key)
        else:
            self.expanded_session_ids.add(key)
        self._render()
        self._schedule_position_window()

    def _clear_session_highlight(self, key: SessionKey) -> None:
        if key not in self.highlighted_until:
            return
        self.highlighted_until.pop(key, None)
        self._schedule_highlight_cleanup()
        self._render()

    def _on_session_summary_clicked(self, session: AgentSession) -> None:
        key = session_key(session)
        self._clear_session_highlight(key)
        self._toggle_session_details(key)

    def _on_session_jump_clicked(self, session: AgentSession) -> None:
        self._clear_session_highlight(session_key(session))
        logger.info(
            "jump button clicked provider=%s session_id=%s pid=%s",
            session.provider,
            session.session_id,
            session.pid,
        )
        self._jump_to_session(session.provider, session.session_id)

    def _jump_to_session(self, provider: str, session_id: str) -> bool:
        if self.proxy is None:
            logger.warning("JumpToSession skipped because D-Bus proxy is unavailable")
            return False
        logger.debug("calling JumpToSession provider=%s session_id=%s", provider, session_id)
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
        logger.info(
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

        self.sessions = sessions
        self.previous_session_phases = {
            session_key(session): session.phase
            for session in sessions
        }
        self._schedule_highlight_cleanup()

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

    def _scroll_to_pending_session(self) -> bool:
        if self.pending_scroll_session is None or self.scroller is None or self.viewport is None:
            return False

        row = self.session_row_widgets.get(self.pending_scroll_session)
        if row is None:
            self.pending_scroll_session = None
            return False

        adjustment = self.scroller.get_vadjustment()
        if adjustment is None:
            self.pending_scroll_session = None
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
        self.pending_scroll_session = None
        return False

    def _render(self) -> None:
        assert self.box is not None
        self.scroller = None
        self.viewport = None
        self.panel_session_keys = []
        self.session_row_widgets = {}
        while child := self.box.get_first_child():
            self.box.remove(child)

        if not self.expanded:
            pill = Gtk.Button()
            pill.add_css_class("session-summary")
            pill.connect("clicked", self._toggle_expand)
            pill_content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            pill_content.add_css_class("pill")
            pill.add_css_class("pill")
            icon = Gtk.Label(label="●")
            title = Gtk.Label(label=summarize_visible_sessions(self.sessions))
            title.add_css_class("title")
            pill_content.append(icon)
            pill_content.append(title)
            pill.set_child(pill_content)
            self.box.append(pill)
            if self.window is not None:
                self.window.set_default_size(COLLAPSED_WIDTH, 60)
            return

        header = Gtk.Button(label=expanded_header_title(self.sessions))
        header.add_css_class("session-summary")
        header.add_css_class("title")
        header.connect("clicked", self._toggle_expand)
        self.box.append(header)

        if not self.sessions:
            empty = Gtk.Label(label="No visible sessions")
            empty.add_css_class("meta")
            self.box.append(empty)
        else:
            viewport = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            self.viewport = viewport
            ordered_sessions = panel_sessions(self.sessions)
            self.panel_session_keys = [session_key(session) for session in ordered_sessions]
            for session in ordered_sessions:
                card = self._session_card(session)
                self.session_row_widgets[session_key(session)] = card
                viewport.append(card)
            scroller = Gtk.ScrolledWindow()
            scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scroller.set_min_content_height(120)
            scroller.set_max_content_height(352)
            scroller.set_vexpand(True)
            scroller.set_child(viewport)
            self.scroller = scroller
            self.box.append(scroller)

        if self.window is not None:
            self.window.set_default_size(EXPANDED_WIDTH, compute_expanded_window_height(len(self.sessions)))

    def _session_card(self, session: AgentSession) -> Gtk.Widget:
        key = session_key(session)
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        outer.add_css_class("session-card")
        if key in self.highlighted_until:
            outer.add_css_class("session-card-highlight")

        summary_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        toggle = Gtk.Button()
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
        time_label = Gtk.Label(label=format_session_minutes(session))
        time_label.add_css_class("meta")
        side.append(time_label)

        jump_button = Gtk.Button()
        jump_button.add_css_class("jump-button")
        jump_button.set_tooltip_text("Open terminal")
        jump_button.set_child(Gtk.Image.new_from_icon_name("go-jump-symbolic"))
        jump_button.set_sensitive(session.pid is not None)
        jump_button.connect("clicked", lambda *_args: self._on_session_jump_clicked(session))
        side.append(jump_button)
        summary_row.append(side)
        outer.append(summary_row)

        if key in self.expanded_session_ids:
            details = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            provider_phase = Gtk.Label(label=f"{session.provider.upper()}  {session.phase.value.upper()}")
            provider_phase.set_xalign(0)
            provider_phase.add_css_class("provider")
            details.append(provider_phase)

            flags: list[str] = []
            if session.is_focused:
                flags.append("focused")
            elif session.has_interactive_window:
                flags.append("window")
            detail_parts = [session.model or "unknown model"]
            if flags:
                detail_parts.extend(flags)
            if session.summary:
                detail_parts.append(session.summary)
            meta = Gtk.Label(label=" • ".join(detail_parts))
            meta.set_xalign(0)
            meta.set_wrap(True)
            meta.add_css_class("meta")
            details.append(meta)

            if session.last_message_preview:
                preview = Gtk.Label(label=session.last_message_preview[:160])
                preview.set_xalign(0)
                preview.set_wrap(True)
                preview.add_css_class("preview")
                details.append(preview)

            detail_click = Gtk.GestureClick()
            detail_click.connect("released", lambda *_args: self._clear_session_highlight(key))
            details.add_controller(detail_click)
            outer.append(details)

        return outer

    def _schedule_position_window(self) -> None:
        GLib.idle_add(self._position_window)

    def _position_window(self) -> bool:
        if self.window is None:
            return False
        display = Gdk.Display.get_default()
        if display is None:
            return False
        monitor = display.get_monitors().get_item(0)
        if monitor is None:
            return False
        geometry = monitor.get_geometry()
        width, x, y = compute_window_position(
            monitor_x=geometry.x,
            monitor_y=geometry.y,
            monitor_width=geometry.width,
            top_bar_offset=self._top_bar_offset(),
            top_bar_gap=self.settings.top_bar_gap,
            expanded=self.expanded,
        )
        if self._move_surface_x11(x, y):
            return False
        self._move_surface_fallback(width, x, y)
        return False

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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log-level", default="INFO")
    args, remaining = parser.parse_known_args(argv)
    level_name = configure_logging(args.log_level)
    logger.info("frontend logging initialized level=%s", level_name)
    app = FrontendApp()
    return app.run(remaining)


if __name__ == "__main__":
    raise SystemExit(main())
