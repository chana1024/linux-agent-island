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
from ..core.models import AgentSession
from ..core.logging import configure_logging
from ..core.store import SessionStore
from ..providers import get_all_providers, get_provider
from ..runtime.agent_events import AgentEvent
from ..runtime.events import EventSocketServer
from ..runtime.processes import SessionProcessInspector
from ..runtime.restore import build_sessions_from_processes, filter_cached_sessions_for_restore
from ..runtime.session_cache import SessionCache


INTROSPECTION_XML = """
<node>
  <interface name="com.lzn.LinuxAgentIsland">
    <method name="ListSessions">
      <arg name="sessions" direction="out" type="s"/>
    </method>
    <method name="GetSessionTranscript">
      <arg name="provider" direction="in" type="s"/>
      <arg name="session_id" direction="in" type="s"/>
      <arg name="turns" direction="out" type="s"/>
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
        self.providers = get_all_providers(self.config)
        self.store = SessionStore()
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
        self._fast_load_cache()
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
        # Defer heavy provider and process loading to after the loop starts
        GLib.idle_add(self._deferred_reload_state)
        
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
        for provider in self.providers:
            provider.install_hooks()

    def _fast_load_cache(self) -> None:
        cached_sessions = self.session_cache.load()
        filtered_cached_sessions = filter_cached_sessions_for_restore(cached_sessions, self.providers)
        self.store = SessionStore()
        if filtered_cached_sessions:
            self.store.restore_sessions(filtered_cached_sessions)

    def _deferred_reload_state(self) -> bool:
        logger.info("deferred state reload starting")
        cached_sessions = self.session_cache.load()
        filtered_cached_sessions = filter_cached_sessions_for_restore(cached_sessions, self.providers)
        
        provider_sessions: list[AgentSession] = []
        for provider in self.providers:
            try:
                live_sessions = provider.load_sessions()
                provider_sessions.extend(live_sessions)
            except Exception as exc:
                logger.error("failed to load sessions for provider %s: %s", provider.name, exc)

        process_tree = self.process_inspector.build_process_tree()
        processes = self.process_inspector.list_agent_processes(process_tree)
        restored_sessions = build_sessions_from_processes(
            processes,
            cached_sessions=filtered_cached_sessions,
            provider_sessions=provider_sessions,
        )
        
        if restored_sessions:
            self.store.restore_sessions(restored_sessions)
            self._persist_sessions()
            self._emit_sessions_changed()
            
        logger.info("deferred state reload finished")
        return False  # Run only once

    def _reload_provider_state(self) -> None:
        # Keeping this for backward compatibility or direct calls if needed, 
        # though start() now uses the split phases.
        self._fast_load_cache()
        self._deferred_reload_state()

    def _on_runtime_event(self, payload: dict[str, object]) -> None:
        event = AgentEvent.from_payload(payload)
        previous = self.store.get(event.provider, event.session_id)
        logger.info(
            (
                "runtime event received provider=%s session_id=%s event_type=%s "
                "phase=%s updated_at=%s pid=%s tty=%s is_session_end=%s previous_phase=%s"
            ),
            event.provider,
            event.session_id,
            event.type.value,
            None if event.phase is None else event.phase.value,
            event.updated_at,
            event.pid,
            event.tty,
            event.is_session_end,
            None if previous is None else previous.phase.value,
        )
        session = self.store.apply(event)
        if event.is_hook_managed and (event.pid is not None or event.tty is not None):
            self.store.reassign_runtime_identity(
                session.provider,
                session.session_id,
                session.pid,
                session.tty,
            )
        logger.info(
            "runtime event applied provider=%s session_id=%s current_phase=%s completed_at=%s",
            session.provider,
            session.session_id,
            session.phase.value,
            session.completed_at,
        )
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
        if method_name == "GetSessionTranscript":
            provider, session_id = parameters.unpack()
            invocation.return_value(GLib.Variant("(s)", (self._serialize_session_transcript(provider, session_id),)))
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
            "com.lzn.LinuxAgentIsland.Error",
            f"Unknown method: {method_name}",
        )

    def _serialize_sessions(self) -> str:
        sessions = self.store.list_sessions(visible_only=True)
        sessions = self.process_inspector.annotate_sessions(sessions)
        return json.dumps([session.to_dict() for session in sessions])

    def _serialize_session_transcript(self, provider_name: str, session_id: str) -> str:
        session = self.store.get(provider_name, session_id)
        provider = get_provider(provider_name, self.config)
        if not provider:
            return "[]"

        kwargs = {}
        if provider_name == "claude" and session:
            kwargs["cwd"] = session.cwd

        return json.dumps(provider.load_transcript(session_id, **kwargs))

    def _reconcile_sessions(self) -> bool:
        sessions = self.store.list_sessions()
        provider_events = []
        for provider in self.providers:
            provider_events.extend(provider.poll_events(sessions))
        changed = False
        for event in provider_events:
            self.store.apply(event)
            changed = True
        if not sessions:
            if changed:
                self._persist_sessions()
                self._emit_sessions_changed()
            return True
        matched_sessions, alive_session_keys = self.process_inspector.reconcile_sessions(sessions)
        changed = self.store.reconcile_process_matches(matched_sessions) or changed
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
