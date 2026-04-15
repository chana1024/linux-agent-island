from __future__ import annotations

import json
import logging
import time

from gi.repository import Gio, GLib

from ..core.config import AppConfig
from ..core.models import AgentSession


logger = logging.getLogger(__name__)


def connect_proxy(config: AppConfig) -> Gio.DBusProxy:
    # During simultaneous startup, the backend might not have claimed the D-Bus name yet.
    # We retry for a short period to be resilient.
    max_attempts = 10
    last_error = None
    
    for attempt in range(max_attempts):
        try:
            return Gio.DBusProxy.new_for_bus_sync(
                Gio.BusType.SESSION,
                Gio.DBusProxyFlags.NONE,
                None,
                config.dbus_name,
                config.dbus_path,
                config.dbus_name,
                None,
            )
        except GLib.Error as exc:
            last_error = exc
            if attempt < max_attempts - 1:
                time.sleep(0.1)
                continue
            raise

    # Should not reach here if max_attempts > 0
    if last_error:
        raise last_error
    raise RuntimeError("Failed to connect to D-Bus proxy")


def list_sessions(proxy: Gio.DBusProxy) -> list[AgentSession]:
    result = proxy.call_sync("ListSessions", None, Gio.DBusCallFlags.NONE, -1, None)
    sessions_json = result.unpack()[0]
    return [AgentSession.from_dict(item) for item in json.loads(sessions_json)]


def fetch_session_transcript(proxy: Gio.DBusProxy | None, provider: str, session_id: str) -> list[dict[str, str]]:
    if proxy is None:
        return []
    try:
        result = proxy.call_sync(
            "GetSessionTranscript",
            GLib.Variant("(ss)", (provider, session_id)),
            Gio.DBusCallFlags.NONE,
            -1,
            None,
        )
    except GLib.Error as exc:
        logger.warning(
            "GetSessionTranscript D-Bus call failed provider=%s session_id=%s error=%s",
            provider,
            session_id,
            exc,
        )
        return []
    try:
        payload = json.loads(result.unpack()[0])
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(payload, list):
        return []
    turns: list[dict[str, str]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role", ""))
        text = str(item.get("text", ""))
        timestamp = str(item.get("timestamp", ""))
        if role and text:
            turns.append({"role": role, "text": text, "timestamp": timestamp})
    return turns


def jump_to_session(proxy: Gio.DBusProxy | None, provider: str, session_id: str) -> bool:
    if proxy is None:
        logger.warning("JumpToSession skipped because D-Bus proxy is unavailable")
        return False
    logger.info("calling JumpToSession provider=%s session_id=%s", provider, session_id)
    try:
        result = proxy.call_sync(
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
    if jumped:
        logger.info(
            "JumpToSession returned provider=%s session_id=%s jumped=%s",
            provider,
            session_id,
            jumped,
        )
    else:
        logger.warning(
            "JumpToSession returned provider=%s session_id=%s jumped=%s",
            provider,
            session_id,
            jumped,
        )
    return jumped

