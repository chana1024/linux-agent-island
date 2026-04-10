from __future__ import annotations

import argparse
import json
import logging
import signal

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")

from gi.repository import Gio, GLib

from ..core.config import AppConfig
from ..core.logging import configure_logging
from ..core.store import SessionStore
from ..providers import ClaudeProvider, CodexProvider
from ..runtime.agent_events import AgentEvent
from ..runtime.events import EventSocketServer
from ..runtime.processes import SessionProcessInspector
from ..runtime.session_cache import SessionCache


INTROSPECTION_XML = """
<node>
  <interface name="com.openclaw.LinuxAgentIsland">
    <method name="ListSessions">
      <arg name="sessions" direction="out" type="s"/>
    </method>
    <method name="JumpToSession">
      <arg name="provider" direction="in" type="s"/>
      <arg name="session_id" direction="in" type="s"/>
      <arg name="jumped" direction="out" type="b"/>
    </method>
    <method name="ArchiveSession">
      <arg name="provider" direction="in" type="s"/>
      <arg name="session_id" direction="in" type="s"/>
    </method>
    <signal name="SessionsChanged">
      <arg name="sessions" type="s"/>
    </signal>
  </interface>
</node>
""".strip()


logger = logging.getLogger(__name__)


class BackendService:
    def __init__(self, config: AppConfig | None = None) -> None:
        self.config = config or AppConfig.default()
        self.store = SessionStore()
        self.claude = ClaudeProvider(
            settings_path=self.config.claude_settings_path,
            hook_script_path=self.config.claude_hook_script_path,
            socket_path=self.config.event_socket_path,
        )
        self.codex = CodexProvider(
            state_db_path=self.config.codex_state_db_path,
            history_path=self.config.codex_history_path,
            hooks_config_path=self.config.codex_hooks_path,
            hook_script_path=self.config.codex_hook_script_path,
        )
        self.session_cache = SessionCache(self.config.session_cache_path)
        self.process_inspector = SessionProcessInspector()
        self.socket_server = EventSocketServer(self.config.event_socket_path, self._on_runtime_event)
        self.loop = GLib.MainLoop()
        self.node_info = Gio.DBusNodeInfo.new_for_xml(INTROSPECTION_XML)
        self.interface = self.node_info.interfaces[0]
        self.connection: Gio.DBusConnection | None = None
        self.registration_id: int | None = None
        self.owner_id: int | None = None

    def start(self) -> None:
        self.config.runtime_dir.mkdir(parents=True, exist_ok=True)
        self._install_hooks()
        self._reload_provider_state()
        self.socket_server.start()
        GLib.timeout_add_seconds(2, self._reconcile_sessions)
        self.owner_id = Gio.bus_own_name(
            Gio.BusType.SESSION,
            self.config.dbus_name,
            Gio.BusNameOwnerFlags.NONE,
            self._on_bus_acquired,
            None,
            None,
        )
        signal.signal(signal.SIGINT, self._stop_signal)
        signal.signal(signal.SIGTERM, self._stop_signal)
        self.loop.run()

    def stop(self) -> None:
        self._persist_sessions()
        self.socket_server.stop()
        if self.connection is not None and self.registration_id is not None:
            self.connection.unregister_object(self.registration_id)
        if self.owner_id is not None:
            Gio.bus_unown_name(self.owner_id)
        if self.loop.is_running():
            self.loop.quit()

    def _stop_signal(self, *_args: object) -> None:
        self.stop()

    def _install_hooks(self) -> None:
        self.claude.install_hooks()
        self.codex.install_hooks()

    def _reload_provider_state(self) -> None:
        cached_sessions = self.session_cache.load()
        cached_codex_sessions = [
            session for session in cached_sessions
            if session.provider == "codex"
        ]
        cached_non_codex_sessions = [
            session for session in cached_sessions
            if session.provider != "codex"
        ]
        self.store.restore_sessions(cached_non_codex_sessions)
        self.store.restore_sessions(self.codex.filter_cached_sessions(cached_codex_sessions))
        sessions = self.codex.load_sessions()
        self.store.restore_sessions(sessions)
        self._persist_sessions()

    def _on_runtime_event(self, payload: dict[str, object]) -> None:
        self.store.apply(AgentEvent.from_payload(payload))
        self._persist_sessions()
        self._emit_sessions_changed()

    def _on_bus_acquired(self, connection: Gio.DBusConnection, _name: str) -> None:
        self.connection = connection
        self.registration_id = connection.register_object(
            self.config.dbus_path,
            self.interface,
            self._handle_method_call,
            None,
            None,
        )
        self._emit_sessions_changed()

    def _handle_method_call(
        self,
        connection: Gio.DBusConnection,
        _sender: str,
        _object_path: str,
        _interface_name: str,
        method_name: str,
        parameters: GLib.Variant,
        invocation: Gio.DBusMethodInvocation,
        *_user_data: object,
    ) -> None:
        if method_name == "ListSessions":
            invocation.return_value(GLib.Variant("(s)", (self._serialize_sessions(),)))
            return
        if method_name == "JumpToSession":
            provider, session_id = parameters.unpack()
            logger.info("JumpToSession requested provider=%s session_id=%s", provider, session_id)
            session = self.store.get(provider, session_id)
            jumped = False
            if session is None:
                logger.warning("JumpToSession session not found provider=%s session_id=%s", provider, session_id)
            else:
                logger.debug(
                    "JumpToSession session context provider=%s session_id=%s pid=%s tty=%s cwd=%s",
                    session.provider,
                    session.session_id,
                    session.pid,
                    session.tty,
                    session.cwd,
                )
                jumped = self.process_inspector.jump_to_session(session)
            if jumped:
                logger.info(
                    "JumpToSession finished provider=%s session_id=%s jumped=%s",
                    provider,
                    session_id,
                    jumped,
                )
            else:
                logger.warning(
                    "JumpToSession finished provider=%s session_id=%s jumped=%s",
                    provider,
                    session_id,
                    jumped,
                )
            invocation.return_value(GLib.Variant("(b)", (jumped,)))
            return
        if method_name == "ArchiveSession":
            provider, session_id = parameters.unpack()
            self.store.archive(provider, session_id)
            self._persist_sessions()
            self._emit_sessions_changed()
            invocation.return_value(None)
            return
        invocation.return_dbus_error(
            "com.openclaw.LinuxAgentIsland.Error",
            f"Unknown method: {method_name}",
        )

    def _serialize_sessions(self) -> str:
        sessions = self.store.list_sessions(visible_only=True)
        sessions = self.process_inspector.annotate_sessions(sessions)
        return json.dumps([session.to_dict() for session in sessions])

    def _reconcile_sessions(self) -> bool:
        sessions = self.store.list_sessions()
        if not sessions:
            return True
        matched_sessions, alive_session_keys = self.process_inspector.reconcile_sessions(sessions)
        changed = self.store.reconcile_process_matches(matched_sessions)
        changed = self.store.mark_process_liveness(alive_session_keys) or changed
        changed = self.store.remove_invisible_sessions() or changed
        if changed:
            self._persist_sessions()
            self._emit_sessions_changed()
        return True

    def _persist_sessions(self) -> None:
        self.session_cache.save(self.store.list_sessions())

    def _emit_sessions_changed(self) -> None:
        if self.connection is None:
            return
        self.connection.emit_signal(
            None,
            self.config.dbus_path,
            self.config.dbus_name,
            "SessionsChanged",
            GLib.Variant("(s)", (self._serialize_sessions(),)),
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log-level", default="INFO")
    args, _unknown = parser.parse_known_args(argv)
    config = AppConfig.default()
    log_file_path = config.runtime_dir / "logs" / "backend.log"
    level_name = configure_logging(args.log_level, log_file_path=log_file_path)
    logger.info("backend logging initialized level=%s", level_name)
    logger.info("backend log file=%s", log_file_path)
    BackendService(config=config).start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
