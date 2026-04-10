from __future__ import annotations

import argparse
import logging
import subprocess

import gi

try:
    gi.require_version("Gtk", "3.0")
    gi.require_version("AyatanaAppIndicator3", "0.1")
except ValueError as exc:
    raise SystemExit(f"tray unavailable: {exc}")

from gi.repository import AyatanaAppIndicator3, Gtk

from ..core.config import AppConfig
from ..core.logging import configure_logging


logger = logging.getLogger(__name__)


def _run_application_action(config: AppConfig, action_name: str) -> None:
    subprocess.run(
        ["gapplication", "action", config.frontend_application_id, action_name],
        capture_output=True,
        text=True,
        check=False,
    )


class TrayApp:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.indicator = AyatanaAppIndicator3.Indicator.new(
            "linux-agent-island",
            "linux-agent-island",
            AyatanaAppIndicator3.IndicatorCategory.APPLICATION_STATUS,
        )
        self.indicator.set_status(AyatanaAppIndicator3.IndicatorStatus.ACTIVE)
        self.indicator.set_title("Linux Agent Island")
        self.indicator.set_menu(self._build_menu())

    def _build_menu(self) -> Gtk.Menu:
        menu = Gtk.Menu()
        menu.append(self._menu_item("Show Island", "show-island"))
        menu.append(self._menu_item("Hide Island", "hide-island"))
        menu.append(self._menu_item("Settings", "open-settings"))
        menu.append(Gtk.SeparatorMenuItem())

        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", self._quit_service)
        menu.append(quit_item)

        menu.show_all()
        return menu

    def _menu_item(self, label: str, action_name: str) -> Gtk.MenuItem:
        item = Gtk.MenuItem(label=label)
        item.connect("activate", lambda *_args: _run_application_action(self.config, action_name))
        return item

    def _quit_service(self, *_args: object) -> None:
        subprocess.run(
            ["systemctl", "--user", "stop", self.config.service_name],
            capture_output=True,
            text=True,
            check=False,
        )
        Gtk.main_quit()

    def run(self) -> int:
        Gtk.main()
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)
    config = AppConfig.default()
    log_file_path = config.runtime_dir / "logs" / "tray.log"
    level_name = configure_logging(args.log_level, log_file_path=log_file_path)
    logger.info("tray logging initialized level=%s", level_name)
    logger.info("tray log file=%s", log_file_path)
    return TrayApp(config).run()


if __name__ == "__main__":
    raise SystemExit(main())
