from __future__ import annotations

import gi

gi.require_version("Gdk", "4.0")

from gi.repository import Gdk


REVEAL_TRANSITION_MS = 260
REVEAL_START_DELAY_MS = 48
TRANSCRIPT_REFRESH_MS = 1000


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
    current_key: tuple[str, str] | None,
    ordered_keys: list[tuple[str, str]],
    delta: int,
) -> tuple[str, str] | None:
    if not ordered_keys:
        return None
    if current_key not in ordered_keys:
        return ordered_keys[0 if delta >= 0 else -1]
    index = ordered_keys.index(current_key)
    return ordered_keys[max(0, min(len(ordered_keys) - 1, index + delta))]
