from __future__ import annotations

import logging
import time

from gi.repository import GLib

from ..core.models import AgentSession, SessionPhase
from .frontend_controls import REVEAL_TRANSITION_MS, TRANSCRIPT_REFRESH_MS, moved_selection_key
from .frontend_client import fetch_session_transcript, jump_to_session
from .frontend_presenter import (
    HIGHLIGHT_DURATION_SECONDS,
    SessionKey,
    detect_completed_sessions,
    prune_expired_highlights,
    refresh_completion_highlights,
    session_key,
)


logger = logging.getLogger(__name__)


class FrontendInteractionsMixin:
    def _toggle_expand(self, *_args: object) -> None:
        if self.expanded:
            self._collapse_panel_with_animation()
            return
        self.expanded = True
        self.pending_panel_reveal = True
        self._render()

    def _move_selected_session(self, delta: int) -> bool:
        if not self.expanded:
            self.expanded = True
            self.pending_panel_reveal = True
            self._render()
            self._schedule_scroll_to_selected_session()
            return self.selected_session_key is not None

        previous_selected_key = self.selected_session_key
        self.selected_session_key = moved_selection_key(
            self.selected_session_key,
            self.panel_session_keys,
            delta,
        )
        if self.selected_session_key != previous_selected_key:
            self._update_selected_row_css(previous_selected_key, self.selected_session_key)
        self._schedule_scroll_to_selected_session()
        return self.selected_session_key is not None

    def _update_selected_row_css(self, previous: SessionKey | None, current: SessionKey | None) -> None:
        previous_row = self.session_row_widgets.get(previous) if previous is not None else None
        if previous_row is not None:
            previous_row.remove_css_class("session-card-selected")

        current_row = self.session_row_widgets.get(current) if current is not None else None
        if current is not None and current_row is None:
            # Fallback for edge cases where the list was rebuilt between key events.
            self._render()
            return
        if current_row is not None:
            current_row.add_css_class("session-card-selected")

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
        return fetch_session_transcript(self.proxy, provider, session_id)

    def _jump_to_session(self, provider: str, session_id: str) -> bool:
        return jump_to_session(self.proxy, provider, session_id)

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
            if self.window is not None:
                self.window.present()
                self._schedule_apply_window_state()
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

        expiring_values = [v for v in self.highlighted_until.values() if v > 0]
        if not expiring_values:
            return

        now_ts = int(time.time())
        next_expiry = min(expiring_values)
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

        row_top = target
        row_bottom = row_top + float(row.get_allocated_height())
        viewport_top = adjustment.get_value()
        viewport_bottom = viewport_top + adjustment.get_page_size()
        margin = 12.0

        if row_top >= viewport_top + margin and row_bottom <= viewport_bottom - margin:
            return False

        if row_top < viewport_top + margin:
            target = row_top - margin
        else:
            target = row_bottom - adjustment.get_page_size() + margin

        target = max(0.0, target)
        max_value = max(0.0, adjustment.get_upper() - adjustment.get_page_size())
        adjustment.set_value(min(target, max_value))
        return False
