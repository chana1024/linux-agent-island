from __future__ import annotations

import subprocess
import time

import gi

gi.require_version("Gdk", "4.0")
try:
    gi.require_version("GdkX11", "4.0")
except ValueError:
    GdkX11 = None
else:
    from gi.repository import GdkX11

from gi.repository import Gdk, Gtk

from .frontend_presenter import compute_window_position_for_width, parse_workarea_top_offset

_TOP_BAR_OFFSET_CACHE_TTL_SECONDS = 1.0
_top_bar_offset_cache_value = 0
_top_bar_offset_cache_ts = 0.0


def top_bar_offset() -> int:
    global _top_bar_offset_cache_value, _top_bar_offset_cache_ts
    now = time.monotonic()
    if now - _top_bar_offset_cache_ts < _TOP_BAR_OFFSET_CACHE_TTL_SECONDS:
        return _top_bar_offset_cache_value
    try:
        result = subprocess.run(
            ["xprop", "-root", "_NET_WORKAREA"],
            capture_output=True,
            text=True,
            timeout=1,
            check=False,
        )
    except OSError:
        _top_bar_offset_cache_value = 0
        _top_bar_offset_cache_ts = now
        return 0
    output = result.stdout.strip() or result.stderr.strip()
    _top_bar_offset_cache_value = parse_workarea_top_offset(output)
    _top_bar_offset_cache_ts = now
    return _top_bar_offset_cache_value


def max_window_height_for_monitor(window: Gtk.ApplicationWindow | None, top_bar_gap: int) -> int:
    display = Gdk.Display.get_default()
    if display is None:
        return 1080 # Fallback

    monitor = None
    if window is not None:
        surface = window.get_surface()
        if surface is not None:
            monitor = display.get_monitor_at_surface(surface)

    if monitor is None:
        monitors = display.get_monitors()
        if monitors.get_n_items() > 0:
            monitor = monitors.get_item(0)

    if monitor is None:
        return 1080 # Fallback

    geometry = monitor.get_geometry()
    scale = window.get_scale_factor() if window is not None else 1
    logical_top_bar_offset = top_bar_offset() / scale

    return geometry.height - int(max(0, logical_top_bar_offset) + max(0, top_bar_gap))


def window_position_for_width(window: Gtk.ApplicationWindow | None, width: int, top_bar_gap: int) -> tuple[int, int, int] | None:
    display = Gdk.Display.get_default()
    if display is None:
        return None

    # 1. Identify active monitor (where the window is or fallback to first)
    monitor = None
    if window is not None:
        surface = window.get_surface()
        if surface is not None:
            monitor = display.get_monitor_at_surface(surface)

    if monitor is None:
        monitors = display.get_monitors()
        if monitors.get_n_items() > 0:
            monitor = monitors.get_item(0)

    if monitor is None:
        return None

    # 2. Get geometry (already in logical pixels in GTK4)
    geometry = monitor.get_geometry()

    # 3. Handle HiDPI scaling
    # xprop returns physical pixels, but GTK4 expects logical pixels.
    # We must normalize the top bar offset by the current scale factor.
    scale = window.get_scale_factor() if window is not None else 1
    logical_top_bar_offset = top_bar_offset() / scale

    return compute_window_position_for_width(
        monitor_x=geometry.x,
        monitor_y=geometry.y,
        monitor_width=geometry.width,
        top_bar_offset=int(logical_top_bar_offset),
        top_bar_gap=top_bar_gap,
        width=width,
    )


def set_x11_above_state(xid: int) -> None:
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


def apply_window_state(window: Gtk.ApplicationWindow | None) -> bool:
    if window is None:
        return False
    surface = window.get_surface()
    if surface is None:
        return False
    if GdkX11 is not None and isinstance(surface, GdkX11.X11Surface):
        surface.set_skip_taskbar_hint(True)
        surface.set_skip_pager_hint(True)
        set_x11_above_state(surface.get_xid())
    return False


def move_surface_x11(window: Gtk.ApplicationWindow | None, x: int, y: int) -> bool:
    if window is None or GdkX11 is None:
        return False
    surface = window.get_surface()
    if surface is None or not isinstance(surface, GdkX11.X11Surface):
        return False

    # wmctrl and X11 tools use physical pixels.
    # We must scale GTK's logical coordinates back to physical.
    scale = window.get_scale_factor()
    px = int(x * scale)
    py = int(y * scale)

    try:
        result = subprocess.run(
            ["wmctrl", "-i", "-r", hex(surface.get_xid()), "-e", f"0,{px},{py},-1,-1"],
            capture_output=True,
            text=True,
            timeout=1,
            check=False,
        )
    except OSError:
        return False
    return result.returncode == 0


def move_surface_fallback(window: Gtk.ApplicationWindow | None, width: int, x: int, y: int) -> None:
    if window is None:
        return
    surface = window.get_surface()
    if surface is None or not hasattr(surface, "move_to_rect"):
        return
    try:
        surface.move_to_rect(
            Gdk.Rectangle(x=x, y=y, width=width, height=1),
            Gdk.Gravity.NORTH,
            Gdk.Gravity.NORTH,
            Gdk.AnchorHints.SLIDE_X | Gdk.AnchorHints.SLIDE_Y,
            0,
            0,
        )
    except Exception:
        pass
