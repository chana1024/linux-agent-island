import errno
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


def test_event_socket_server_replaces_stale_socket_when_connect_succeeds_but_send_refused(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    socket_path = tmp_path / "events.sock"
    socket_path.write_text("")
    server = EventSocketServer(socket_path, lambda _payload: None)

    class ProbeSocket:
        def connect(self, _path: str) -> None:
            return None

        def send(self, _payload: bytes) -> int:
            raise OSError(errno.ECONNREFUSED, "Connection refused")

        def close(self) -> None:
            return None

    class BoundSocket:
        def bind(self, path: str) -> None:
            Path(path).touch()

        def close(self) -> None:
            return None

        def recv(self, _size: int) -> bytes:
            raise OSError("closed")

    sockets = [ProbeSocket(), BoundSocket()]

    def fake_socket(_family: int, _kind: int):
        return sockets.pop(0)

    monkeypatch.setattr(socket, "socket", fake_socket)

    class DummyThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self) -> None:
            return None

    monkeypatch.setattr("linux_agent_island.runtime.events.threading.Thread", DummyThread)

    server.start()

    assert socket_path.exists()


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
