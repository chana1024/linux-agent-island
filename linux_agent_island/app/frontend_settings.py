from __future__ import annotations

import subprocess

from gi.repository import Gtk

from ..core.config import LOG_LEVELS, FrontendSettings, load_frontend_settings, save_frontend_settings


class FrontendSettingsMixin:
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
