from __future__ import annotations

import argparse
import logging
import time
from typing import Any

from ..cli import _run_application_action
from ..core.config import AppConfig
from ..core.logging import configure_logging


logger = logging.getLogger(__name__)
RETRY_DELAY_SECONDS = 2.0


class GlobalHotkeyListener:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._display: Any | None = None
        self._root: Any | None = None
        self._X: Any | None = None
        self._keycode: int | None = None
        self._grab_masks: tuple[int, ...] = ()

    def connect(self) -> None:
        from Xlib import X, XK, display

        self._display = display.Display()
        self._root = self._display.screen().root
        self._X = X
        keycode = self._display.keysym_to_keycode(XK.string_to_keysym("i"))
        if keycode == 0:
            raise RuntimeError("failed to resolve X11 keycode for Ctrl+I")
        self._keycode = keycode
        self._grab_masks = self._build_grab_masks()
        self._grab_hotkey()
        logger.info("global hotkey listener connected keycode=%s", self._keycode)

    def _build_grab_masks(self) -> tuple[int, ...]:
        assert self._display is not None
        assert self._X is not None

        numlock_mask = 0
        modifier_map = self._display.get_modifier_mapping()
        from Xlib import XK

        numlock_keycode = self._display.keysym_to_keycode(XK.string_to_keysym("Num_Lock"))
        for index, keycodes in enumerate(modifier_map):
            if numlock_keycode and numlock_keycode in keycodes:
                numlock_mask = 1 << index
                break
        masks = {
            self._X.ControlMask,
            self._X.ControlMask | self._X.LockMask,
        }
        if numlock_mask:
            masks.add(self._X.ControlMask | numlock_mask)
            masks.add(self._X.ControlMask | self._X.LockMask | numlock_mask)
        return tuple(sorted(masks))

    def _grab_hotkey(self) -> None:
        assert self._root is not None
        assert self._X is not None
        assert self._display is not None
        assert self._keycode is not None

        for mask in self._grab_masks:
            self._root.grab_key(
                self._keycode,
                mask,
                True,
                self._X.GrabModeAsync,
                self._X.GrabModeAsync,
            )
        self._display.sync()

    def _handle_keypress(self, event: Any) -> None:
        assert self._X is not None
        assert self._keycode is not None

        if event.type != self._X.KeyPress or event.detail != self._keycode:
            return
        if event.state not in self._grab_masks:
            return
        result = _run_application_action(self.config, "toggle-island-focus")
        logger.info("global Ctrl+I triggered toggle-island-focus result=%s", result)

    def run_forever(self) -> None:
        if self._display is None:
            self.connect()
        assert self._display is not None
        while True:
            event = self._display.next_event()
            self._handle_keypress(event)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)
    config = AppConfig.default()
    log_file_path = config.runtime_dir / "logs" / "hotkeys.log"
    level_name = configure_logging(args.log_level, log_file_path=log_file_path)
    logger.info("hotkey logging initialized level=%s", level_name)
    logger.info("hotkey log file=%s", log_file_path)

    while True:
        try:
            GlobalHotkeyListener(config).run_forever()
        except KeyboardInterrupt:
            return 0
        except Exception as exc:
            logger.warning("global hotkey listener unavailable error=%s", exc)
            time.sleep(RETRY_DELAY_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())
