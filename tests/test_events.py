import socket
from pathlib import Path

import pytest

from linux_agent_island.runtime.events import EventSocketServer


def test_event_socket_server_does_not_unlink_active_socket(tmp_path: Path) -> None:
    socket_path = tmp_path / "events.sock"
    owner = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    owner.bind(str(socket_path))
    server = EventSocketServer(socket_path, lambda _payload: None)

    try:
        with pytest.raises(RuntimeError, match="event socket already active"):
            server.start()

        assert socket_path.exists()
    finally:
        owner.close()
        socket_path.unlink(missing_ok=True)


def test_event_socket_server_replaces_stale_socket(tmp_path: Path) -> None:
    socket_path = tmp_path / "events.sock"
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    stale.bind(str(socket_path))
    stale.close()
    server = EventSocketServer(socket_path, lambda _payload: None)

    try:
        server.start()

        assert socket_path.exists()
    finally:
        server.stop()

    assert not socket_path.exists()


def test_event_socket_server_stop_does_not_unlink_replacement_socket(tmp_path: Path) -> None:
    socket_path = tmp_path / "events.sock"
    server = EventSocketServer(socket_path, lambda _payload: None)
    replacement = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)

    try:
        server.start()
        socket_path.unlink()
        replacement.bind(str(socket_path))

        server.stop()

        assert socket_path.exists()
    finally:
        replacement.close()
        socket_path.unlink(missing_ok=True)
