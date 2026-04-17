from __future__ import annotations

import json
import logging
import time
from typing import Callable

from gi.repository import Gio, GLib

from ..core.config import AppConfig
from ..core.models import AgentSession, CodexAccountStatus, CodexAccountSummary


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


def list_codex_accounts(proxy: Gio.DBusProxy | None) -> list[CodexAccountSummary]:
    if proxy is None:
        return []
    try:
        result = proxy.call_sync("ListCodexAccounts", None, Gio.DBusCallFlags.NONE, -1, None)
    except GLib.Error as exc:
        logger.warning("ListCodexAccounts D-Bus call failed error=%s", exc)
        return []
    try:
        payload = json.loads(result.unpack()[0])
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(payload, list):
        return []
    return [
        CodexAccountSummary.from_dict(item)
        for item in payload
        if isinstance(item, dict)
    ]


def get_codex_account_status(proxy: Gio.DBusProxy | None) -> CodexAccountStatus:
    empty_status = CodexAccountStatus(logged_in=False)
    if proxy is None:
        return empty_status
    try:
        result = proxy.call_sync("GetCodexAccountStatus", None, Gio.DBusCallFlags.NONE, -1, None)
    except GLib.Error as exc:
        logger.warning("GetCodexAccountStatus D-Bus call failed error=%s", exc)
        return empty_status
    return _decode_codex_account_status(result)


def start_codex_device_login(proxy: Gio.DBusProxy | None, label: str = "") -> bool:
    if proxy is None:
        return False
    try:
        result = proxy.call_sync(
            "StartCodexDeviceLogin",
            GLib.Variant("(s)", (label,)),
            Gio.DBusCallFlags.NONE,
            -1,
            None,
        )
    except GLib.Error as exc:
        logger.warning("StartCodexDeviceLogin D-Bus call failed error=%s", exc)
        return False
    return bool(result.unpack()[0])


def start_codex_device_login_async(
    proxy: Gio.DBusProxy | None,
    label: str,
    on_complete: Callable[[bool], None],
) -> None:
    if proxy is None:
        on_complete(False)
        return
    proxy.call(
        "StartCodexDeviceLogin",
        GLib.Variant("(s)", (label,)),
        Gio.DBusCallFlags.NONE,
        -1,
        None,
        lambda source, result, *_args: _finish_bool_call(
            source,
            result,
            "StartCodexDeviceLogin",
            on_complete,
        ),
        None,
    )


def switch_codex_account(proxy: Gio.DBusProxy | None, account_id: str) -> CodexAccountStatus:
    if proxy is None:
        return CodexAccountStatus(logged_in=False)
    try:
        result = proxy.call_sync(
            "SwitchCodexAccount",
            GLib.Variant("(s)", (account_id,)),
            Gio.DBusCallFlags.NONE,
            -1,
            None,
        )
    except GLib.Error as exc:
        logger.warning("SwitchCodexAccount D-Bus call failed account_id=%s error=%s", account_id, exc)
        return get_codex_account_status(proxy)
    return _decode_codex_account_status(result)


def switch_codex_account_async(
    proxy: Gio.DBusProxy | None,
    account_id: str,
    on_complete: Callable[[CodexAccountStatus], None],
) -> None:
    _call_codex_account_status_async(
        proxy,
        "SwitchCodexAccount",
        GLib.Variant("(s)", (account_id,)),
        on_complete,
        log_context=f"account_id={account_id}",
    )


def rename_codex_account(proxy: Gio.DBusProxy | None, account_id: str, label: str) -> CodexAccountStatus:
    if proxy is None:
        return CodexAccountStatus(logged_in=False)
    try:
        result = proxy.call_sync(
            "RenameCodexAccount",
            GLib.Variant("(ss)", (account_id, label)),
            Gio.DBusCallFlags.NONE,
            -1,
            None,
        )
    except GLib.Error as exc:
        logger.warning("RenameCodexAccount D-Bus call failed account_id=%s error=%s", account_id, exc)
        return get_codex_account_status(proxy)
    return _decode_codex_account_status(result)


def rename_codex_account_async(
    proxy: Gio.DBusProxy | None,
    account_id: str,
    label: str,
    on_complete: Callable[[CodexAccountStatus], None],
) -> None:
    _call_codex_account_status_async(
        proxy,
        "RenameCodexAccount",
        GLib.Variant("(ss)", (account_id, label)),
        on_complete,
        log_context=f"account_id={account_id}",
    )


def delete_codex_account(proxy: Gio.DBusProxy | None, account_id: str) -> CodexAccountStatus:
    if proxy is None:
        return CodexAccountStatus(logged_in=False)
    try:
        result = proxy.call_sync(
            "DeleteCodexAccount",
            GLib.Variant("(s)", (account_id,)),
            Gio.DBusCallFlags.NONE,
            -1,
            None,
        )
    except GLib.Error as exc:
        logger.warning("DeleteCodexAccount D-Bus call failed account_id=%s error=%s", account_id, exc)
        return get_codex_account_status(proxy)
    return _decode_codex_account_status(result)


def delete_codex_account_async(
    proxy: Gio.DBusProxy | None,
    account_id: str,
    on_complete: Callable[[CodexAccountStatus], None],
) -> None:
    _call_codex_account_status_async(
        proxy,
        "DeleteCodexAccount",
        GLib.Variant("(s)", (account_id,)),
        on_complete,
        log_context=f"account_id={account_id}",
    )


def set_default_codex_account(proxy: Gio.DBusProxy | None, account_id: str) -> CodexAccountStatus:
    if proxy is None:
        return CodexAccountStatus(logged_in=False)
    try:
        result = proxy.call_sync(
            "SetDefaultCodexAccount",
            GLib.Variant("(s)", (account_id,)),
            Gio.DBusCallFlags.NONE,
            -1,
            None,
        )
    except GLib.Error as exc:
        logger.warning("SetDefaultCodexAccount D-Bus call failed account_id=%s error=%s", account_id, exc)
        return get_codex_account_status(proxy)
    return _decode_codex_account_status(result)


def set_default_codex_account_async(
    proxy: Gio.DBusProxy | None,
    account_id: str,
    on_complete: Callable[[CodexAccountStatus], None],
) -> None:
    _call_codex_account_status_async(
        proxy,
        "SetDefaultCodexAccount",
        GLib.Variant("(s)", (account_id,)),
        on_complete,
        log_context=f"account_id={account_id}",
    )


def _decode_codex_account_status(result: GLib.Variant) -> CodexAccountStatus:
    try:
        payload = json.loads(result.unpack()[0])
    except (json.JSONDecodeError, TypeError):
        return CodexAccountStatus(logged_in=False)
    if not isinstance(payload, dict):
        return CodexAccountStatus(logged_in=False)
    return CodexAccountStatus.from_dict(payload)


def _call_codex_account_status_async(
    proxy: Gio.DBusProxy | None,
    method_name: str,
    parameters: GLib.Variant | None,
    on_complete: Callable[[CodexAccountStatus], None],
    *,
    log_context: str = "",
) -> None:
    if proxy is None:
        on_complete(CodexAccountStatus(logged_in=False))
        return
    proxy.call(
        method_name,
        parameters,
        Gio.DBusCallFlags.NONE,
        -1,
        None,
        lambda source, result, *_args: _finish_codex_account_status_call(
            source,
            result,
            method_name,
            on_complete,
            log_context=log_context,
        ),
        None,
    )


def _finish_bool_call(
    source: Gio.DBusProxy,
    result: Gio.AsyncResult,
    method_name: str,
    on_complete: Callable[[bool], None],
) -> None:
    try:
        variant = source.call_finish(result)
    except GLib.Error as exc:
        logger.warning("%s D-Bus call failed error=%s", method_name, exc)
        on_complete(False)
        return
    on_complete(bool(variant.unpack()[0]))


def _finish_codex_account_status_call(
    source: Gio.DBusProxy,
    result: Gio.AsyncResult,
    method_name: str,
    on_complete: Callable[[CodexAccountStatus], None],
    *,
    log_context: str = "",
) -> None:
    try:
        variant = source.call_finish(result)
    except GLib.Error as exc:
        if log_context:
            logger.warning("%s D-Bus call failed %s error=%s", method_name, log_context, exc)
        else:
            logger.warning("%s D-Bus call failed error=%s", method_name, exc)
        on_complete(CodexAccountStatus(logged_in=False))
        return
    on_complete(_decode_codex_account_status(variant))
