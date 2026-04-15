from __future__ import annotations

from collections.abc import Callable

from gi.repository import GLib, Gtk

from .frontend_controls import REVEAL_START_DELAY_MS, REVEAL_TRANSITION_MS
from .frontend_presenter import (
    COLLAPSED_HEIGHT,
    COLLAPSED_WIDTH,
    SessionKey,
    collapsed_status_phase,
    compute_expanded_window_height,
    expanded_header_title,
    format_session_minutes,
    has_done_time_label,
    panel_sessions,
    session_key,
    session_metadata_tags,
    status_dot_css_class,
    status_dot_glyph,
    summarize_visible_sessions,
    window_width_for_state,
)
from .frontend_windowing import (
    apply_window_state,
    max_window_height_for_monitor,
    move_surface_fallback,
    move_surface_x11,
    top_bar_offset,
    window_position_for_width,
)


class FrontendPanelMixin:
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
            collapsed_phase = collapsed_status_phase(self.sessions)
            icon = Gtk.Label(label=status_dot_glyph(collapsed_phase))
            for css_class in status_dot_css_class(collapsed_phase).split():
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

    def _session_card(self, session) -> Gtk.Widget:
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
        dot = Gtk.Label(label=status_dot_glyph(session.phase))
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
            actionable = self._actionable_state_view(session)
            if actionable is not None:
                details.append(actionable)
            metadata = self._metadata_state_view(session)
            if metadata is not None:
                details.append(metadata)
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

    def _actionable_state_view(self, session) -> Gtk.Widget | None:
        if session.permission_request is not None:
            request = session.permission_request
            root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            root.add_css_class("actionable-card")

            title = Gtk.Label(label=request.title)
            title.set_xalign(0)
            title.add_css_class("actionable-title")
            root.append(title)

            summary = Gtk.Label(label=request.summary)
            summary.set_xalign(0)
            summary.set_wrap(True)
            summary.add_css_class("actionable-text")
            root.append(summary)

            if request.affected_path:
                path = Gtk.Label(label=request.affected_path)
                path.set_xalign(0)
                path.set_wrap(True)
                path.add_css_class("actionable-meta")
                root.append(path)
            return root

        if session.question_prompt is not None:
            prompt = session.question_prompt
            root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            root.add_css_class("actionable-card")

            title = Gtk.Label(label=prompt.title)
            title.set_xalign(0)
            title.set_wrap(True)
            title.add_css_class("actionable-title")
            root.append(title)

            for option in prompt.options:
                option_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                option_box.add_css_class("actionable-option")

                label = Gtk.Label(label=option.label)
                label.set_xalign(0)
                label.add_css_class("actionable-text")
                option_box.append(label)

                if option.description:
                    description = Gtk.Label(label=option.description)
                    description.set_xalign(0)
                    description.set_wrap(True)
                    description.add_css_class("actionable-meta")
                    option_box.append(description)
                root.append(option_box)
            return root

        return None

    def _metadata_state_view(self, session) -> Gtk.Widget | None:
        rows: list[tuple[str, str]] = []

        if session.codex_metadata is not None:
            metadata = session.codex_metadata
            rows.extend(
                [
                    ("Current tool", metadata.current_tool or ""),
                    ("Command preview", metadata.current_command_preview or ""),
                    ("Last prompt", metadata.last_user_prompt or ""),
                    ("Last reply", metadata.last_assistant_message or ""),
                    ("Transcript", metadata.transcript_path or ""),
                ]
            )

        if session.claude_metadata is not None:
            metadata = session.claude_metadata
            rows.extend(
                [
                    ("Current tool", metadata.current_tool or ""),
                    ("Tool input", metadata.current_tool_input_preview or ""),
                    ("Last prompt", metadata.last_user_prompt or ""),
                    ("Last reply", metadata.last_assistant_message or ""),
                    ("Transcript", metadata.transcript_path or ""),
                ]
            )

        visible_rows = [(label, value) for label, value in rows if value]
        if not visible_rows:
            return None

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        root.add_css_class("metadata-card")

        title = Gtk.Label(label="Session context")
        title.set_xalign(0)
        title.add_css_class("metadata-title")
        root.append(title)

        for label_text, value_text in visible_rows:
            row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            label = Gtk.Label(label=label_text.upper())
            label.set_xalign(0)
            label.add_css_class("metadata-label")
            value = Gtk.Label(label=value_text)
            value.set_xalign(0)
            value.set_wrap(True)
            value.set_selectable(True)
            value.add_css_class("metadata-value")
            row.append(label)
            row.append(value)
            root.append(row)

        return root

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
        max_available = max_window_height_for_monitor(self.window, self.settings.top_bar_gap)
        return max(
            120,
            compute_expanded_window_height(len(self.sessions), bool(self.expanded_session_ids), max_available) - header_height,
        )

    def _target_window_size(self) -> tuple[int, int]:
        if not self.expanded:
            return COLLAPSED_WIDTH, COLLAPSED_HEIGHT
        has_expanded_session = bool(self.expanded_session_ids)
        max_available = max_window_height_for_monitor(self.window, self.settings.top_bar_gap)
        return (
            window_width_for_state(self.expanded, has_expanded_session),
            compute_expanded_window_height(len(self.sessions), has_expanded_session, max_available),
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
        # Use a short timeout instead of idle_add to ensure the size request
        # is processed by the window manager before we try to move it back.
        GLib.timeout_add(100, self._position_window)

    def _schedule_position_window(self) -> None:
        # For general repositioning (not size changes), idle_add is fine
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
        return window_position_for_width(self.window, width, self.settings.top_bar_gap)

    def _schedule_apply_window_state(self) -> None:
        GLib.idle_add(self._apply_window_state)

    def _apply_window_state(self) -> bool:
        return apply_window_state(self.window)

    def _move_surface_x11(self, x: int, y: int) -> bool:
        return move_surface_x11(self.window, x, y)

    def _move_surface_fallback(self, width: int, x: int, y: int) -> None:
        move_surface_fallback(self.window, width, x, y)

    def _top_bar_offset(self) -> int:
        return top_bar_offset()
