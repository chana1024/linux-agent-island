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
            self.settings_window.set_default_size(560, 420)
            self.settings_window.connect("close-request", self._on_settings_close_request)
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
        if self.settings_window is None:
            return
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        root.set_margin_top(18)
        root.set_margin_bottom(18)
        root.set_margin_start(18)
        root.set_margin_end(18)

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

        root.append(self._codex_accounts_section())
        self.settings_window.set_child(root)

    def _codex_accounts_section(self) -> Gtk.Widget:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)

        title = Gtk.Label(label="Codex accounts")
        title.set_xalign(0)
        title.add_css_class("title")
        root.append(title)

        status_text = self.codex_account_status.current_account_label or "No Codex login"
        status = Gtk.Label(label=f"Current account: {status_text}")
        status.set_xalign(0)
        status.add_css_class("meta")
        root.append(status)

        if self.codex_account_status.has_running_codex_sessions:
            notice = Gtk.Label(label="Account switches affect new Codex sessions only.")
            notice.set_xalign(0)
            notice.add_css_class("meta")
            root.append(notice)

        login_button = Gtk.Button(label="Log in new account")
        login_button.set_sensitive(not self.codex_account_status.device_login_in_progress)
        login_button.connect("clicked", lambda *_args: self._start_codex_device_login())
        root.append(login_button)

        if not self.codex_account_status.accounts:
            empty = Gtk.Label(label="No saved Codex accounts yet.")
            empty.set_xalign(0)
            empty.add_css_class("meta")
            root.append(empty)
            return root

        for account in self.codex_account_status.accounts:
            root.append(self._codex_account_row(account))
        return root

    def _codex_account_row(self, account) -> Gtk.Widget:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        root.add_css_class("account-row")

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        entry = Gtk.Entry()
        entry.set_text(account.label)
        entry.set_hexpand(True)
        entry.add_css_class("account-entry")
        top.append(entry)

        if account.is_active:
            active = Gtk.Label(label="Active")
            active.add_css_class("session-tag")
            active.add_css_class("tag-provider-codex")
            top.append(active)
        if account.is_default:
            default = Gtk.Label(label="Default")
            default.add_css_class("session-tag")
            top.append(default)
        root.append(top)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        rename_button = Gtk.Button(label="Rename")
        rename_button.connect(
            "clicked",
            lambda *_args, account_id=account.account_id, label_entry=entry: self._rename_codex_account(
                account_id,
                label_entry.get_text(),
            ),
        )
        actions.append(rename_button)

        use_button = Gtk.Button(label="Use")
        use_button.set_sensitive(not account.is_active)
        use_button.connect("clicked", lambda *_args, account_id=account.account_id: self._switch_codex_account(account_id))
        actions.append(use_button)

        default_button = Gtk.Button(label="Set default")
        default_button.set_sensitive(not account.is_default)
        default_button.connect(
            "clicked",
            lambda *_args, account_id=account.account_id: self._set_default_codex_account(account_id),
        )
        actions.append(default_button)

        delete_button = Gtk.Button(label="Delete")
        delete_button.set_sensitive(not account.is_active)
        delete_button.connect("clicked", lambda *_args, account_id=account.account_id: self._delete_codex_account(account_id))
        actions.append(delete_button)
        root.append(actions)
        return root

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
